"""
RunRealm — Full API Audit + Response Time Benchmark
Tests every endpoint for correctness and measures p50/p95/max latency.
Usage: python bench_all.py https://runrealm.onrender.com
"""
import sys, time, uuid, random, string, math, statistics, datetime, json
import requests

BASE = (sys.argv[1].rstrip("/") + "/api/v1") if len(sys.argv) > 1 else "http://localhost:8000/api/v1"
TIMINGS: dict[str, list[float]] = {}


# ── helpers ──────────────────────────────────────────────────────────────────

def rnd(n=8):
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))


def call(method, path, label, *, headers=None, json=None, params=None, expected=200):
    url = f"{BASE}{path}"
    t0 = time.time()
    r = getattr(requests, method)(url, headers=headers, json=json, params=params, timeout=40)
    ms = (time.time() - t0) * 1000
    TIMINGS.setdefault(label, []).append(ms)
    if r.status_code != expected:
        body = ""
        try: body = r.json()
        except: body = r.text[:300]
        raise AssertionError(f"{label}: expected {expected}, got {r.status_code} — {body}")
    try:
        d = r.json()
        return d.get("data", d), ms
    except:
        return r.text, ms


def register_user(suffix=""):
    tag = rnd()
    email = f"bench_{tag}{suffix}@test.com"
    pw = "BenchPass123!"
    d, _ = call("post", "/auth/register", "auth/register",
                 json={"email": email, "password": pw, "username": f"bench_{tag}"})
    return d["accessToken"], d["userId"], f"bench_{tag}"


def h(token):
    return {"Authorization": f"Bearer {token}"}


def now_iso(offset_minutes=0):
    return (datetime.datetime.now(datetime.timezone.utc) +
            datetime.timedelta(minutes=offset_minutes)).isoformat()


def circle_pts(lat, lon, radius_m=200, n=24):
    """Generate n GPS points in a closed circle."""
    pts = []
    for i in range(n + 1):
        ang = 2 * math.pi * i / n
        dlat = (radius_m / 111000) * math.cos(ang)
        dlon = (radius_m / (111000 * math.cos(math.radians(lat)))) * math.sin(ang)
        spd = 4.5 + random.uniform(-0.5, 0.5)
        pts.append({
            "latitude": lat + dlat, "longitude": lon + dlon,
            "altitude": 50.0, "speedKmh": spd, "accuracyM": 5.0,
            "sequenceNumber": i,
            "recordedAt": (datetime.datetime.now(datetime.timezone.utc) +
                           datetime.timedelta(seconds=i * 35)).isoformat(),
        })
    return pts


def create_session(token, distance_km=3.0, pts=None):
    lid = str(uuid.uuid4())
    start = now_iso(-60)
    d, _ = call("post", "/sessions/start", "sessions/start",
                 headers=h(token), json={"localId": lid, "activityType": "RUNNING", "startTime": start})
    sid = d["id"]
    if pts:
        rows = [{"latitude": p["latitude"], "longitude": p["longitude"],
                 "recordedAt": p["recordedAt"], "sequenceNumber": p["sequenceNumber"]} for p in pts]
        for i in range(0, len(rows), 50):
            call("post", f"/sessions/{sid}/points", "sessions/points",
                 headers=h(token), json=rows[i:i+50])
    call("post", f"/sessions/{sid}/end", "sessions/end",
         headers=h(token), json={"endTime": now_iso(), "distanceKm": distance_km,
                                  "caloriesBurned": int(distance_km*65), "maxSpeedKmh": 10.5})
    return sid


# ── test sections ────────────────────────────────────────────────────────────

def section(name):
    print(f"\n{'─'*60}")
    print(f"  {name}")
    print(f"{'─'*60}")


def ok(label):
    print(f"  ✅  {label}")


def FAIL(label, detail=""):
    print(f"  ❌  FAIL: {label}  {detail}")


# 1. HEALTH
section("1. Health")
t0 = time.time()
r = requests.get(BASE.replace("/api/v1", "") + "/health", timeout=30)
ms = (time.time()-t0)*1000
TIMINGS.setdefault("health", []).append(ms)
assert r.json()["status"] == "ok", f"health failed: {r.text}"
ok(f"health → {r.json()['status']}  ({ms:.0f}ms)")

# 2. AUTH
section("2. Auth")
tok_a, uid_a, uname_a = register_user()
ok(f"register → uid={uid_a[:8]}… username={uname_a}  ({TIMINGS['auth/register'][-1]:.0f}ms)")

# login
d, ms = call("post", "/auth/login", "auth/login",
             json={"email": f"{uname_a.replace('bench_','bench_')}@test.com".replace("bench_bench_", "bench_"),
                   "password": "BenchPass123!"})
# just re-login with the user we just made
email_a = f"bench_{uname_a.split('_',1)[1]}@test.com"
d, ms = call("post", "/auth/login", "auth/login",
             json={"email": email_a, "password": "BenchPass123!"})
ok(f"login → accessToken present={bool(d.get('accessToken'))}  ({ms:.0f}ms)")

# refresh
d2, ms = call("post", f"/auth/refresh?refresh_token={d.get('refreshToken','')}", "auth/refresh",
              json=None)
ok(f"refresh → new token present={bool(d2.get('accessToken') or d2.get('access_token'))}  ({ms:.0f}ms)")

tok_b, uid_b, uname_b = register_user()
tok_c, uid_c, uname_c = register_user()
ok(f"registered 2 more users (B={uid_b[:8]}…, C={uid_c[:8]}…)")

# 3. PROFILE
section("3. Profile")
d, ms = call("get", "/profile", "profile/get", headers=h(tok_a))
assert "userId" in d, f"missing userId in profile: {d}"
ok(f"GET /profile → username={d['username']}  ({ms:.0f}ms)")

d2, ms = call("get", "/profile", "profile/get", headers=h(tok_a))
ok(f"GET /profile (2nd, may cache) → ({ms:.0f}ms)")

d, ms = call("patch", "/profile", "profile/update", headers=h(tok_a),
             json={"displayName": "Bench Alice", "bio": "test bio"})
ok(f"PATCH /profile → ({ms:.0f}ms)")

d, ms = call("get", f"/profile/{uid_b}", "profile/get_other", headers=h(tok_a))
assert "userId" in d
ok(f"GET /profile/{{uid_b}} → ({ms:.0f}ms)")

# 4. DASHBOARD
section("4. Dashboard")
d, ms = call("get", "/dashboard", "dashboard", headers=h(tok_a))
assert "currentStreak" in d and "totalXp" in d
ok(f"GET /dashboard (cold) → streak={d['currentStreak']}, xp={d['totalXp']}  ({ms:.0f}ms)")
d2, ms2 = call("get", "/dashboard", "dashboard/cached", headers=h(tok_a))
assert d == d2, "dashboard cache inconsistency"
ok(f"GET /dashboard (cached) → ({ms2:.0f}ms)  speedup={ms/ms2:.1f}x")

# 5. SESSIONS
section("5. Sessions")
d, ms = call("get", "/sessions", "sessions/list", headers=h(tok_a))
total_before = d.get("totalElements", 0)
ok(f"GET /sessions → totalElements={total_before}  ({ms:.0f}ms)")

sid1 = create_session(tok_a, distance_km=5.2)
ok(f"POST /sessions/start + end → sid={sid1[:8]}…  "
   f"(start={TIMINGS['sessions/start'][-1]:.0f}ms, end={TIMINGS['sessions/end'][-1]:.0f}ms)")

d, ms = call("get", f"/sessions/{sid1}", "sessions/get", headers=h(tok_a))
assert d["status"] == "COMPLETED" and d["xp_earned"] > 0
ok(f"GET /sessions/{{id}} → status={d['status']}, xp={d['xp_earned']}, dist={d['distance_km']}km  ({ms:.0f}ms)")

d, ms = call("get", "/sessions", "sessions/list", headers=h(tok_a))
assert d["totalElements"] >= 1
ok(f"GET /sessions (after run) → total={d['totalElements']}  ({ms:.0f}ms)")

# second session to verify streak + cumulative XP
sid2 = create_session(tok_a, distance_km=3.0)
d2, ms = call("get", "/dashboard", "dashboard", headers=h(tok_a))
ok(f"Dashboard after 2 sessions → streak={d2['currentStreak']}, xp={d2['totalXp']}, weekKm={d2['weeklyDistanceKm']}  ({ms:.0f}ms)")
assert d2["weeklyDistanceKm"] >= 8.0, f"weekly km should be ≥8, got {d2['weeklyDistanceKm']}"
assert d2["totalXp"] > 0

# 6. HABITS
section("6. Habits")
d, ms = call("post", "/habits", "habits/create", headers=h(tok_a),
             json={"name": "Morning Run", "habitType": "BOOLEAN",
                   "targetValue": 1, "frequency": "DAILY"})
hid = d["id"]
ok(f"POST /habits → id={hid[:8]}…  ({ms:.0f}ms)")

d, ms = call("get", "/habits", "habits/list", headers=h(tok_a))
assert len(d) >= 1
ok(f"GET /habits → count={len(d)}  ({ms:.0f}ms)")
d2, ms2 = call("get", "/habits", "habits/list/cached", headers=h(tok_a))
ok(f"GET /habits (cached) → ({ms2:.0f}ms)  speedup={ms/max(ms2,1):.1f}x")

today_str = datetime.date.today().isoformat()
d, ms = call("post", "/habits/log", "habits/log", headers=h(tok_a),
             json={"habitId": hid, "logDate": today_str, "completedValue": 1.0, "localId": str(uuid.uuid4())})
assert d["is_completed"] == True
ok(f"POST /habits/log → completed={d['is_completed']}, xp={d['xp_earned']}  ({ms:.0f}ms)")

d, ms = call("get", "/habits/stats", "habits/stats", headers=h(tok_a))
assert "daily" in d
ok(f"GET /habits/stats → daily={d['daily']['percentage']}%, overall={d['overallScore']}  ({ms:.0f}ms)")
d2, ms2 = call("get", "/habits/stats", "habits/stats/cached", headers=h(tok_a))
ok(f"GET /habits/stats (cached) → ({ms2:.0f}ms)  speedup={ms/max(ms2,1):.1f}x")

d, ms = call("get", "/habits/logs", "habits/logs", headers=h(tok_a), params={"date_str": today_str})
assert len(d) >= 1
ok(f"GET /habits/logs → count={len(d)}  ({ms:.0f}ms)")

# 7. TODOS
section("7. Todos")
d, ms = call("post", "/todos", "todos/create", headers=h(tok_a),
             json={"title": "Bench todo", "priority": "HIGH",
                   "todoDate": today_str, "scheduledAt": "07:00"})
tid = d["id"]
ok(f"POST /todos → id={tid[:8]}…  ({ms:.0f}ms)")

d, ms = call("get", "/todos", "todos/list", headers=h(tok_a), params={"date": today_str})
assert len(d) >= 1
ok(f"GET /todos → count={len(d)}  ({ms:.0f}ms)")

d, ms = call("patch", f"/todos/{tid}/complete", "todos/complete", headers=h(tok_a))
ok(f"PATCH /todos/complete → status={d.get('status')}  ({ms:.0f}ms)")

d, ms = call("patch", f"/todos/{tid}/incomplete", "todos/incomplete", headers=h(tok_a))
ok(f"PATCH /todos/incomplete → status={d.get('status')}  ({ms:.0f}ms)")

d, ms = call("patch", f"/todos/{tid}/defer", "todos/defer", headers=h(tok_a))
ok(f"PATCH /todos/defer → ({ms:.0f}ms)")

d, ms = call("patch", f"/todos/{tid}", "todos/update", headers=h(tok_a),
             json={"title": "Updated bench todo", "priority": "LOW"})
ok(f"PATCH /todos/{{id}} → ({ms:.0f}ms)")

d, ms = call("get", "/todos/stats", "todos/stats", headers=h(tok_a), params={"date": today_str})
assert "daily" in d
ok(f"GET /todos/stats → daily={d['daily'].get('percentage',0)}%  ({ms:.0f}ms)")

d, ms = call("delete", f"/todos/{tid}", "todos/delete", headers=h(tok_a))
ok(f"DELETE /todos/{{id}} → ({ms:.0f}ms)")

# 8. LEADERBOARD
section("8. Leaderboard")
for lb_type, scope in [("xp", "GLOBAL"), ("distance", "GLOBAL"), ("xp", "LOCAL")]:
    d, ms = call("get", "/leaderboard", f"leaderboard/{lb_type}/{scope}", headers=h(tok_a),
                 params={"type": lb_type, "scope": scope, "size": 10})
    count = len(d.get("content", d) if isinstance(d, dict) else d)
    ok(f"GET /leaderboard type={lb_type} scope={scope} → {count} entries  ({ms:.0f}ms)")

_, ms2 = call("get", "/leaderboard", "leaderboard/xp/GLOBAL/cached", headers=h(tok_a),
              params={"type": "xp", "scope": "GLOBAL", "size": 10})
ok(f"GET /leaderboard (cached) → ({ms2:.0f}ms)  speedup={TIMINGS['leaderboard/xp/GLOBAL'][-1]/max(ms2,1):.1f}x")

_, ms = call("get", "/leaderboard", "leaderboard/bad_type", headers=h(tok_a),
             params={"type": "INVALID"}, expected=400)
ok(f"GET /leaderboard type=INVALID → 400 correctly rejected  ({ms:.0f}ms)")

# 9. SOCIAL
section("9. Social")
# A → B friend request
call("post", f"/social/friends/{uid_b}/request", "social/friend_request", headers=h(tok_a))
ok(f"A→B friend request  ({TIMINGS['social/friend_request'][-1]:.0f}ms)")

d, ms = call("get", "/social/friends/pending", "social/pending", headers=h(tok_b))
assert any(f["userId"] == uid_a for f in d)
ok(f"B sees pending from A  ({ms:.0f}ms)")

call("post", f"/social/friends/{uid_a}/accept", "social/accept", headers=h(tok_b))
ok(f"B accepts A  ({TIMINGS['social/accept'][-1]:.0f}ms)")

d, ms = call("get", "/social/friends", "social/friends_list", headers=h(tok_a))
assert any(f["userId"] == uid_b for f in d)
ok(f"A's friends includes B → count={len(d)}  ({ms:.0f}ms)")
d2, ms2 = call("get", "/social/friends", "social/friends_list/cached", headers=h(tok_a))
ok(f"GET /social/friends (cached) → ({ms2:.0f}ms)  speedup={ms/max(ms2,1):.1f}x")

# A → C, C declines
call("post", f"/social/friends/{uid_c}/request", "social/friend_request", headers=h(tok_a))
call("post", f"/social/friends/{uid_a}/decline", "social/decline", headers=h(tok_c))
d, ms = call("get", "/social/friends/sent", "social/sent", headers=h(tok_a))
ok(f"GET /social/friends/sent → {len(d)} pending  ({ms:.0f}ms)")

# self-request rejected
call("post", f"/social/friends/{uid_a}/request", "social/self_request",
     headers=h(tok_a), expected=400)
ok(f"Self-request → 400 correct")

# activity feed
d, ms = call("get", "/social/feed", "social/feed", headers=h(tok_a))
ok(f"GET /social/feed → {len(d.get('content',[]))} items  ({ms:.0f}ms)")
d2, ms2 = call("get", "/social/feed", "social/feed/cached", headers=h(tok_a))
ok(f"GET /social/feed (cached) → ({ms2:.0f}ms)  speedup={ms/max(ms2,1):.1f}x")

# unfriend
call("delete", f"/social/friends/{uid_b}", "social/unfriend", headers=h(tok_a))
d, ms = call("get", "/social/friends", "social/friends_list", headers=h(tok_a))
assert not any(f["userId"] == uid_b for f in d)
ok(f"Unfriend B → verified  ({ms:.0f}ms)")

# 10. NOTIFICATIONS
section("10. Notifications")
d, ms = call("get", "/notifications", "notifs/list", headers=h(tok_a))
ok(f"GET /notifications → total={d.get('totalElements',0)}  ({ms:.0f}ms)")
d2, ms2 = call("get", "/notifications", "notifs/list/cached", headers=h(tok_a))
ok(f"GET /notifications (cached) → ({ms2:.0f}ms)  speedup={ms/max(ms2,1):.1f}x")

d, ms = call("get", "/notifications/unread-count", "notifs/unread", headers=h(tok_a))
ok(f"GET /notifications/unread-count → {d.get('count',d)}  ({ms:.0f}ms)")

call("post", "/notifications/read-all", "notifs/read_all", headers=h(tok_a))
ok(f"POST /notifications/read-all  ({TIMINGS['notifs/read_all'][-1]:.0f}ms)")

# 11. CONTENT
section("11. Content")
for path, label in [("/content/quote", "content/quote"), ("/content/quote/random", "content/quote/random"),
                    ("/content/tip", "content/tip"), ("/content/tip/random", "content/tip/random"),
                    ("/content/feed", "content/feed")]:
    d, ms = call("get", path, label, headers=h(tok_a))
    ok(f"GET {path} → ({ms:.0f}ms)")

# 12. TERRITORIES
section("12. Territories")
lat0, lon0 = 12.9716, 77.5946
pts = circle_pts(lat0, lon0, radius_m=150, n=25)
sid_t = create_session(tok_a, distance_km=1.2, pts=pts)
ok(f"session for territory created  (start={TIMINGS['sessions/start'][-1]:.0f}ms, end={TIMINGS['sessions/end'][-1]:.0f}ms)")

d, ms = call("post", "/territories/claim", "territories/claim", headers=h(tok_a),
             json={"sessionId": sid_t,
                   "routePoints": [{"latitude": p["latitude"], "longitude": p["longitude"],
                                     "recordedAt": p["recordedAt"], "sequenceNumber": p["sequenceNumber"]}
                                    for p in pts],
                   "name": "Bench Territory"})
terr_id = d.get("territory", {}).get("id") or d.get("id", "?")
xp_gain = d.get("xpEarned", d.get("xp_earned", 0))
ok(f"POST /territories/claim → id={str(terr_id)[:8]}… xp=+{xp_gain}  ({ms:.0f}ms)")

# corridor (Bob)
from math import cos, radians
straight_pts = []
for i in range(15):
    frac = i / 14
    straight_pts.append({
        "latitude": lat0 - 0.002 + frac * 0.004,
        "longitude": lon0 - 0.0005,
        "altitude": 50.0, "speedKmh": 5.0, "accuracyM": 5.0,
        "sequenceNumber": i,
        "recordedAt": (datetime.datetime.now(datetime.timezone.utc) +
                       datetime.timedelta(seconds=i*45)).isoformat(),
    })
sid_b = create_session(tok_b, distance_km=0.8, pts=straight_pts)
d, ms = call("post", "/territories/corridor", "territories/corridor", headers=h(tok_b),
             json={"sessionId": sid_b,
                   "routePoints": [{"latitude": p["latitude"], "longitude": p["longitude"],
                                     "recordedAt": p["recordedAt"], "sequenceNumber": p["sequenceNumber"]}
                                    for p in straight_pts]})
ok(f"POST /territories/corridor → captured={d.get('captured',0)} xp=+{d.get('xpEarned',0)}  ({ms:.0f}ms)")

d, ms = call("get", "/territories/nearby", "territories/nearby", headers=h(tok_a),
             params={"lat": lat0, "lon": lon0, "radiusKm": 1.0})
ok(f"GET /territories/nearby → {len(d)} found  ({ms:.0f}ms)")
d2, ms2 = call("get", "/territories/nearby", "territories/nearby/cached", headers=h(tok_a),
               params={"lat": lat0, "lon": lon0, "radiusKm": 1.0})
ok(f"GET /territories/nearby (cached) → ({ms2:.0f}ms)  speedup={ms/max(ms2,1):.1f}x")

d, ms = call("get", "/territories/mine", "territories/mine", headers=h(tok_a))
ok(f"GET /territories/mine → total={d.get('totalElements',0)}  ({ms:.0f}ms)")

# 13. LEAGUES
section("13. Leagues")
d, ms = call("post", "/leagues", "leagues/create", headers=h(tok_a),
             json={"name": f"Bench League {rnd(4)}", "description": "test",
                   "leagueType": "PUBLIC", "maxMembers": 10})
lid2 = d["id"]
ok(f"POST /leagues → id={lid2[:8]}…  ({ms:.0f}ms)")

d, ms = call("get", f"/leagues/{lid2}", "leagues/get", headers=h(tok_a))
league_info = d.get("league", d)
assert league_info["name"].startswith("Bench"), f"league name: {league_info.get('name')}"
ok(f"GET /leagues/{{id}} → members={league_info.get('memberCount',0)}  ({ms:.0f}ms)")

d, ms = call("get", "/leagues", "leagues/list", headers=h(tok_a))
league_count = len(d.get("content", d) if isinstance(d, dict) else d)
ok(f"GET /leagues → {league_count} leagues  ({ms:.0f}ms)")

call("post", f"/leagues/{lid2}/join-request", "leagues/join_req", headers=h(tok_b))
ok(f"B join-request  ({TIMINGS['leagues/join_req'][-1]:.0f}ms)")

d, ms = call("post", f"/leagues/{lid2}/join-requests/{uid_b}/accept",
             "leagues/accept_req", headers=h(tok_a))
ok(f"A accepts B → ({ms:.0f}ms)")

call("post", f"/leagues/{lid2}/leave", "leagues/leave", headers=h(tok_b))
ok(f"B leaves  ({TIMINGS['leagues/leave'][-1]:.0f}ms)")

call("delete", f"/leagues/{lid2}", "leagues/delete", headers=h(tok_a))
ok(f"DELETE /leagues/{{id}}  ({TIMINGS['leagues/delete'][-1]:.0f}ms)")

# 14. MAP
section("14. Map")
d, ms = call("post", "/map/location", "map/location", headers=h(tok_a),
             json={"latitude": lat0, "longitude": lon0, "accuracyM": 10.0, "isPublic": True})
ok(f"POST /map/location → ({ms:.0f}ms)")

d, ms = call("get", "/map/nearby-users", "map/nearby_users", headers=h(tok_a),
             params={"lat": lat0, "lon": lon0, "radiusKm": 5.0})
ok(f"GET /map/nearby-users → {len(d)} users  ({ms:.0f}ms)")

d, ms = call("get", f"/map/route/{sid1}", "map/route", headers=h(tok_a))
ok(f"GET /map/route/{{sid}} → points={len(d.get('points',[]))}  ({ms:.0f}ms)")
d2, ms2 = call("get", f"/map/route/{sid1}", "map/route/cached", headers=h(tok_a))
ok(f"GET /map/route (cached) → ({ms2:.0f}ms)  speedup={ms/max(ms2,1):.1f}x")

d, ms = call("get", "/map/territories/live", "map/terr_live", headers=h(tok_a),
             params={"lat": lat0, "lon": lon0, "radiusKm": 5.0})
ok(f"GET /map/territories/live → {len(d)} features  ({ms:.0f}ms)")

d, ms = call("get", "/map/territories/polygons", "map/terr_polygons", headers=h(tok_a),
             params={"lat": lat0, "lon": lon0, "radiusKm": 5.0})
ok(f"GET /map/territories/polygons → {len(d)} features  ({ms:.0f}ms)")

# 15. SYNC
section("15. Sync")
d, ms = call("get", "/sync/pending-count", "sync/pending", headers=h(tok_a))
ok(f"GET /sync/pending-count → {d}  ({ms:.0f}ms)")

d, ms = call("post", "/sync/batch", "sync/batch", headers=h(tok_a),
             json={"items": []})
ok(f"POST /sync/batch → ({ms:.0f}ms)")

# ── Response Time Report ─────────────────────────────────────────────────────
print(f"\n{'═'*70}")
print(f"  Response Time Report  (all times in ms)")
print(f"{'═'*70}")
print(f"  {'Endpoint':<38} {'calls':>5}  {'p50':>7}  {'p95':>7}  {'max':>7}  {'cached?':>8}")
print(f"  {'─'*38} {'─'*5}  {'─'*7}  {'─'*7}  {'─'*7}  {'─'*8}")

CACHE_PAIRS = {
    "dashboard/cached":          "dashboard",
    "habits/list/cached":        "habits/list",
    "habits/stats/cached":       "habits/stats",
    "notifs/list/cached":        "notifs/list",
    "social/friends_list/cached": "social/friends_list",
    "social/feed/cached":        "social/feed",
    "territories/nearby/cached": "territories/nearby",
    "map/route/cached":          "map/route",
    "leaderboard/XP/GLOBAL/cached": "leaderboard/XP/GLOBAL",
}

all_keys = sorted(TIMINGS.keys())
for key in all_keys:
    if key.endswith("/cached"):
        continue
    vals = TIMINGS[key]
    p50  = statistics.median(vals)
    p95  = sorted(vals)[int(len(vals)*0.95)] if len(vals) >= 20 else max(vals)
    mx   = max(vals)
    cached_key = next((c for c,b in CACHE_PAIRS.items() if b == key), None)
    cached_str = ""
    if cached_key and cached_key in TIMINGS:
        c_ms = TIMINGS[cached_key][0]
        cached_str = f"{c_ms:>5.0f}ms ✅" if c_ms < p50 * 0.8 else f"{c_ms:>5.0f}ms"
    print(f"  {key:<38} {len(vals):>5}  {p50:>7.0f}  {p95:>7.0f}  {mx:>7.0f}  {cached_str:>8}")

overall = [ms for v in TIMINGS.values() for ms in v]
print(f"\n  Overall: {len(overall)} calls  p50={statistics.median(overall):.0f}ms  "
      f"p95={sorted(overall)[int(len(overall)*0.95)]:.0f}ms  max={max(overall):.0f}ms")
print(f"{'═'*70}")
print(f"\n  ✅  All endpoints verified — no failures\n")
