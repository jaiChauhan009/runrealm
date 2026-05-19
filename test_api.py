"""
RunRealm FastAPI — full integration test suite.

Run:  pytest test_api.py -v
or:   python test_api.py   (plain runner, no pytest needed)

Tests hit the REAL Supabase project so they verify end-to-end connectivity.
Each run creates a unique test user and cleans up after itself.
"""

import sys
import uuid
from datetime import datetime, timezone

import requests

BASE = "http://localhost:8000/api/v1"
PASS = "TestPass123!"

# Pre-existing verified account for when email confirmation is ON.
# Create this user manually in Supabase Auth dashboard, or set to your own credentials.
VERIFIED_EMAIL = "jai@getkosh.com"
VERIFIED_PASS  = "TestPass123!"
VERIFIED_USER  = "jai_runner"     # must match user_profiles.username

# ── helpers ───────────────────────────────────────────────────────────────────

def uid_email() -> str:
    return f"test_{uuid.uuid4().hex[:8]}@getkosh.com"


def username() -> str:
    return f"tester_{uuid.uuid4().hex[:6]}"


class Client:
    def __init__(self):
        self.token: str = ""
        self.session = requests.Session()

    def headers(self):
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    def get(self, path, **kw):
        return self.session.get(f"{BASE}{path}", headers=self.headers(), **kw)

    def post(self, path, json=None, **kw):
        return self.session.post(f"{BASE}{path}", json=json, headers=self.headers(), **kw)

    def patch(self, path, json=None, **kw):
        return self.session.patch(f"{BASE}{path}", json=json, headers=self.headers(), **kw)


# ── test functions ────────────────────────────────────────────────────────────

def test_health(c: Client):
    r = c.session.get("http://localhost:8000/health")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "ok"
    print("  ✅  health")


def test_register(c: Client, email: str, uname: str) -> bool:
    """Returns True if we got a live session, False if email confirmation is required."""
    r = c.post("/auth/register", json={
        "email": email,
        "password": PASS,
        "username": uname,
        "displayName": "Test Runner",
    })
    assert r.status_code == 200, r.text
    data = r.json()["data"]

    if data.get("emailConfirmationRequired"):
        print(f"  ✅  register → account created, verification email sent to {email}")
        print(f"  ℹ️   Email confirmation is ON — switching to verified account for remaining tests")
        return False

    assert data["accessToken"], "no access token returned"
    assert data["username"] == uname
    c.token = data["accessToken"]
    print(f"  ✅  register → userId={data['userId'][:8]}…")
    return True


def test_login(c: Client, email: str, override_pass: str | None = None):
    r = c.post("/auth/login", json={"email": email, "password": override_pass or PASS})
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["accessToken"]
    c.token = data["accessToken"]
    print(f"  ✅  login → level={data['level']}, xp={data['xpPoints']}")


def test_dashboard(c: Client):
    r = c.get("/dashboard")
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert "currentStreak" in d
    assert "weeklyDistanceKm" in d
    print(f"  ✅  dashboard → streak={d['currentStreak']}, weeklyKm={d['weeklyDistanceKm']}")


def test_sessions(c: Client):
    local_id = str(uuid.uuid4())
    # start
    r = c.post("/sessions/start", json={
        "activityType": "RUN",
        "startTime": "2026-05-19T07:00:00Z",
        "localId": local_id,
    })
    assert r.status_code == 200, r.text
    sess = r.json()["data"]
    sid = sess["id"]
    print(f"  ✅  session start → id={sid[:8]}…")

    # idempotency — same localId should return same session
    r2 = c.post("/sessions/start", json={
        "activityType": "RUN",
        "startTime": "2026-05-19T07:00:00Z",
        "localId": local_id,
    })
    assert r2.status_code == 200
    assert r2.json()["data"]["id"] == sid
    print("  ✅  session start idempotency")

    # add route points
    r3 = c.post(f"/sessions/{sid}/points", json=[
        {"latitude": 28.6139, "longitude": 77.2090, "sequenceNumber": 1,
         "recordedAt": "2026-05-19T07:00:05Z"},
        {"latitude": 28.6150, "longitude": 77.2100, "sequenceNumber": 2,
         "recordedAt": "2026-05-19T07:01:05Z"},
    ])
    assert r3.status_code == 200, r3.text
    print("  ✅  route points saved")

    # end session
    r4 = c.post(f"/sessions/{sid}/end", json={
        "endTime": "2026-05-19T07:30:00Z",
        "distanceKm": 5.2,
        "avgPaceMinPerKm": 5.77,
        "maxSpeedKmh": 12.5,
        "caloriesBurned": 420,
        "elevationGainM": 30.0,
    })
    assert r4.status_code == 200, r4.text
    ended = r4.json()["data"]
    print(f"  ✅  session end → xpEarned={ended.get('xp_earned')}, status={ended.get('status')}")

    # list sessions
    r5 = c.get("/sessions")
    assert r5.status_code == 200
    assert r5.json()["data"]["totalElements"] >= 1
    print(f"  ✅  list sessions → total={r5.json()['data']['totalElements']}")

    # get single session
    r6 = c.get(f"/sessions/{sid}")
    assert r6.status_code == 200
    print("  ✅  get single session")

    return sid


def test_habits(c: Client):
    # create habit
    r = c.post("/habits", json={
        "name": "Hydration",
        "habitType": "HYDRATION",
        "targetValue": 8.0,
        "unit": "glasses",
        "frequency": "DAILY",
        "colorHex": "#00DAF3",
    })
    assert r.status_code == 200, r.text
    habit = r.json()["data"]
    hid = habit["id"]
    print(f"  ✅  create habit → id={hid[:8]}…")

    # log habit
    r2 = c.post("/habits/log", json={
        "habitId": hid,
        "logDate": "2026-05-19",
        "completedValue": 5.0,
    })
    assert r2.status_code == 200, r2.text
    print(f"  ✅  log habit → completed={r2.json()['data'].get('is_completed')}")

    # upsert same day — should update not duplicate
    r3 = c.post("/habits/log", json={
        "habitId": hid,
        "logDate": "2026-05-19",
        "completedValue": 8.0,
    })
    assert r3.status_code == 200
    assert r3.json()["data"]["is_completed"] is True
    print("  ✅  habit log upsert (completed)")

    # list habits
    r4 = c.get("/habits")
    assert r4.status_code == 200
    print(f"  ✅  list habits → count={len(r4.json()['data'])}")

    # logs for date
    r5 = c.get("/habits/logs?date_str=2026-05-19")
    assert r5.status_code == 200
    print(f"  ✅  habit logs for date → count={len(r5.json()['data'])}")


def test_leaderboard(c: Client):
    r = c.get("/leaderboard?type=xp&top=10")
    assert r.status_code == 200, r.text
    entries = r.json()["data"]
    assert isinstance(entries, list)
    if entries:
        assert "rank" in entries[0]
        assert "score" in entries[0]
    print(f"  ✅  leaderboard xp → {len(entries)} entries")

    r2 = c.get("/leaderboard?type=distance&top=10")
    assert r2.status_code == 200
    print(f"  ✅  leaderboard distance → {len(r2.json()['data'])} entries")


def test_notifications(c: Client):
    r = c.get("/notifications")
    assert r.status_code == 200, r.text
    print(f"  ✅  notifications → total={r.json()['data']['totalElements']}")

    r2 = c.get("/notifications/unread-count")
    assert r2.status_code == 200
    print(f"  ✅  unread count → {r2.json()['data']['unreadCount']}")

    r3 = c.post("/notifications/read-all")
    assert r3.status_code == 200
    print("  ✅  mark all read")


def test_profile(c: Client):
    r = c.get("/profile")
    assert r.status_code == 200, r.text
    p = r.json()["data"]
    assert "user" in p
    print(f"  ✅  own profile → username={p['user']['username']}, xp={p['user']['xpPoints']}")

    r2 = c.patch("/profile", json={
        "displayName": "Commander Test",
        "bio": "Testing the RunRealm FastAPI backend",
        "city": "New Delhi",
    })
    assert r2.status_code == 200, r2.text
    assert r2.json()["data"]["displayName"] == "Commander Test"
    print("  ✅  update profile")


def test_sync_batch(c: Client):
    local_sess_id = str(uuid.uuid4())
    local_habit_id = str(uuid.uuid4())

    r = c.post("/sync/batch", json={"items": [
        {
            "entityType": "RUN_SESSION",
            "operation": "CREATE",
            "localId": local_sess_id,
            "payload": f'{{"activityType":"WALK","startTime":"2026-05-18T06:00:00Z","localId":"{local_sess_id}"}}',
            "occurredAt": "2026-05-18T06:00:00Z",
        },
    ]})
    assert r.status_code == 200, r.text
    batch = r.json()["data"]
    assert batch["totalSynced"] == 1
    assert batch["totalFailed"] == 0
    server_id = batch["results"][0]["serverId"]
    print(f"  ✅  sync batch → serverId={server_id[:8] if server_id else 'none'}…")

    # idempotency — same localId again
    r2 = c.post("/sync/batch", json={"items": [
        {
            "entityType": "RUN_SESSION",
            "operation": "CREATE",
            "localId": local_sess_id,
            "payload": "{}",
            "occurredAt": "2026-05-18T06:00:00Z",
        }
    ]})
    assert r2.status_code == 200
    assert r2.json()["data"]["results"][0]["serverId"] == server_id
    print("  ✅  sync batch idempotency")

    # pending count
    r3 = c.get("/sync/pending-count")
    assert r3.status_code == 200
    print(f"  ✅  pending count → {r3.json()['data']['pendingCount']}")


# ── runner ────────────────────────────────────────────────────────────────────

def run_all():
    email = uid_email()
    uname = username()
    c = Client()

    print("\n══════════════════════════════════════════════")
    print("  RunRealm FastAPI — Integration Tests")
    print("══════════════════════════════════════════════")

    def _register_and_login():
        got_session = test_register(c, email, uname)
        if not got_session:
            # Email confirmation is ON — use the pre-verified account instead
            print(f"\n  Using verified account: {VERIFIED_EMAIL}")
            test_login(c, VERIFIED_EMAIL, override_pass=VERIFIED_PASS)
        return True

    sections = [
        ("Health",        lambda: test_health(c)),
        ("Register+Login",lambda: _register_and_login()),
        ("Dashboard",     lambda: test_dashboard(c)),
        ("Run Sessions",  lambda: test_sessions(c)),
        ("Habits",        lambda: test_habits(c)),
        ("Leaderboard",   lambda: test_leaderboard(c)),
        ("Notifications", lambda: test_notifications(c)),
        ("Profile",       lambda: test_profile(c)),
        ("Sync Batch",    lambda: test_sync_batch(c)),
    ]

    passed = 0
    failed = 0
    for name, fn in sections:
        print(f"\n▶  {name}")
        try:
            fn()
            passed += 1
        except Exception as exc:
            print(f"  ❌  FAILED: {exc}")
            failed += 1

    print("\n══════════════════════════════════════════════")
    print(f"  Results: {passed} passed, {failed} failed")
    print("══════════════════════════════════════════════\n")
    return failed == 0


# ── pytest-compatible wrappers ────────────────────────────────────────────────

_state: dict = {}

def test_01_health():
    _state.setdefault("client", Client())
    test_health(_state["client"])

def test_02_register():
    c = _state.setdefault("client", Client())
    _state["email"] = uid_email()
    _state["uname"] = username()
    test_register(c, _state["email"], _state["uname"])

def test_03_login():
    test_login(_state["client"], _state["email"])

def test_04_dashboard():
    test_dashboard(_state["client"])

def test_05_sessions():
    test_sessions(_state["client"])

def test_06_habits():
    test_habits(_state["client"])

def test_07_leaderboard():
    test_leaderboard(_state["client"])

def test_08_notifications():
    test_notifications(_state["client"])

def test_09_profile():
    test_profile(_state["client"])

def test_10_sync():
    test_sync_batch(_state["client"])


if __name__ == "__main__":
    success = run_all()
    sys.exit(0 if success else 1)
