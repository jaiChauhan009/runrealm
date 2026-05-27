import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from supabase import Client

from auth import get_current_user
from cache import cache_get, cache_invalidate, cache_set
from database import get_db
from schemas import EndSessionRequest, RoutePointRequest, StartSessionRequest, ok
from utils import xp_calculator as xp

router = APIRouter()
_pool = ThreadPoolExecutor(max_workers=6)


def _compute_streak(row: dict, activity_date: date) -> tuple[int, int]:
    """Return (new_streak, new_best) without touching the DB."""
    last_str = row.get("last_activity_date")
    last = date.fromisoformat(last_str) if last_str else None
    current = row.get("current_streak", 0)
    best = row.get("best_streak", 0)

    if last is None:
        new_streak = 1
    elif activity_date == last:
        new_streak = current          # same day — no change
    elif activity_date == last + timedelta(days=1):
        new_streak = current + 1
    else:
        new_streak = 1

    return new_streak, max(best, new_streak)


@router.post("/start")
def start_session(body: StartSessionRequest, user=Depends(get_current_user), db: Client = Depends(get_db)):
    uid = user.id

    # Idempotent via localId
    existing = db.table("run_sessions").select("*").eq("local_id", body.localId).eq("user_id", uid).execute()
    if existing.data:
        return ok(existing.data[0])

    row = {
        "user_id": uid,
        "local_id": body.localId,
        "activity_type": body.activityType,
        "start_time": body.startTime.isoformat(),
        "status": "ACTIVE",
        "distance_km": 0.0,
        "synced": True,
    }
    res = db.table("run_sessions").insert(row).execute()
    return ok(res.data[0])


@router.post("/{session_id}/end")
async def end_session(
    session_id: str,
    body: EndSessionRequest,
    background_tasks: BackgroundTasks,
    user=Depends(get_current_user),
    db: Client = Depends(get_db),
):
    uid = user.id
    loop = asyncio.get_event_loop()

    # Round 1 — read session + streak + profile in parallel (3 → 1 wall-clock round)
    sess_res, streak_res, profile_res = await asyncio.gather(
        loop.run_in_executor(_pool, lambda: db.table("run_sessions").select("*").eq("id", session_id).eq("user_id", uid).execute()),
        loop.run_in_executor(_pool, lambda: db.table("streaks").select("*").eq("user_id", uid).execute()),
        loop.run_in_executor(_pool, lambda: db.table("user_profiles").select("xp_points,total_runs,total_calories,total_distance_km").eq("user_id", uid).single().execute()),
    )

    if not sess_res.data:
        raise HTTPException(400, "Session not found")

    s         = sess_res.data[0]
    streak_row = streak_res.data[0] if streak_res.data else {}
    profile    = profile_res.data or {}

    # Compute duration
    from datetime import datetime as _dt
    start_str = s.get("start_time")
    duration = 0
    if start_str:
        st = _dt.fromisoformat(start_str.replace("Z", "+00:00"))
        et = body.endTime
        if et.tzinfo is None:
            et = et.replace(tzinfo=timezone.utc)
        duration = int((et - st).total_seconds())

    # Compute streak + XP
    activity_date    = body.endTime.date()
    new_streak, new_best = _compute_streak(streak_row, activity_date)
    xp_earned        = xp.for_run(body.distanceKm, new_streak)
    new_xp           = (profile.get("xp_points") or 0) + xp_earned

    dist_km  = body.distanceKm
    calories = body.caloriesBurned or 0

    session_update = {
        "end_time": body.endTime.isoformat(),
        "distance_km": dist_km,
        "avg_pace_min_per_km": body.avgPaceMinPerKm,
        "max_speed_kmh": body.maxSpeedKmh,
        "calories_burned": calories,
        "elevation_gain_m": body.elevationGainM or 0,
        "route_geo_json": body.routeGeoJson,
        "status": "COMPLETED",
        "duration_seconds": duration,
        "xp_earned": xp_earned,
        "synced": True,
    }

    streak_update = {
        "current_streak": new_streak,
        "best_streak": new_best,
        "last_activity_date": activity_date.isoformat(),
    }

    profile_update = {
        "xp_points":        new_xp,
        "level":            xp.level_from_xp(new_xp),
        "current_streak":   new_streak,
        "best_streak":      new_best,
        "total_runs":       (profile.get("total_runs") or 0) + 1,
        "total_calories":   (profile.get("total_calories") or 0) + calories,
        "total_distance_km": round((profile.get("total_distance_km") or 0) + dist_km, 3),
    }

    # Round 2 — all writes in parallel (4 → 1 wall-clock round)
    def _update_streak_table():
        if streak_row.get("id"):
            db.table("streaks").update(streak_update).eq("id", streak_row["id"]).execute()
        else:
            db.table("streaks").insert({"user_id": uid, **streak_update}).execute()

    res, *_ = await asyncio.gather(
        loop.run_in_executor(_pool, lambda: db.table("run_sessions").update(session_update).eq("id", session_id).execute()),
        loop.run_in_executor(_pool, _update_streak_table),
        loop.run_in_executor(_pool, lambda: db.table("xp_transactions").insert({
            "user_id": uid, "amount": xp_earned,
            "transaction_type": "RUN_COMPLETE", "reference_id": session_id,
            "description": f"Completed {dist_km:.2f} km run",
        }).execute()),
        loop.run_in_executor(_pool, lambda: db.table("user_profiles").update(profile_update).eq("user_id", uid).execute()),
    )

    background_tasks.add_task(
        lambda: db.table("activity_feed").insert({
            "user_id": uid, "activity_type": "RUN_COMPLETED",
            "reference_id": session_id,
            "message": f"Completed a {dist_km:.2f} km run",
            "metadata_json": f'{{"distanceKm":{dist_km},"durationSec":{duration},"calories":{calories}}}',
            "is_public": True,
        }).execute()
    )

    cache_invalidate(f"dashboard:{uid}")
    cache_invalidate(f"profile:{uid}")
    cache_invalidate(f"sessions:{uid}:p0")

    return ok(res.data[0])


@router.post("/{session_id}/points")
def add_route_points(
    session_id: str,
    points: list[RoutePointRequest],
    user=Depends(get_current_user),
    db: Client = Depends(get_db),
):
    uid = user.id
    sess = db.table("run_sessions").select("id").eq("id", session_id).eq("user_id", uid).execute()
    if not sess.data:
        raise HTTPException(400, "Session not found")

    rows = [
        {
            "session_id": session_id,
            "user_id": uid,
            "local_id": p.localId,
            "latitude": p.latitude,
            "longitude": p.longitude,
            "altitude": p.altitude,
            "speed_kmh": p.speedKmh,
            "accuracy_m": p.accuracyM,
            "sequence_number": p.sequenceNumber,
            "recorded_at": p.recordedAt.isoformat(),
        }
        for p in points
    ]
    if rows:
        db.table("route_points").insert(rows).execute()

    return ok(None, "Points saved")


@router.get("")
def list_sessions(
    page: int = 0,
    size: int = 20,
    user=Depends(get_current_user),
    db: Client = Depends(get_db),
):
    uid = user.id
    cache_key = f"sessions:{uid}:p{page}"
    cached = cache_get(cache_key)
    if cached:
        return cached

    start = page * size
    end = start + size - 1

    # Exclude route_geo_json — can be hundreds of KB per session and isn't
    # needed in the list view.
    res = (
        db.table("run_sessions")
        .select(
            "id,user_id,local_id,activity_type,start_time,end_time,status,"
            "distance_km,avg_pace_min_per_km,max_speed_kmh,calories_burned,"
            "elevation_gain_m,duration_seconds,xp_earned,synced",
            count="exact",
        )
        .eq("user_id", uid)
        .order("start_time", desc=True)
        .range(start, end)
        .execute()
    )
    total = res.count or 0
    result = ok({
        "content": res.data or [],
        "totalElements": total,
        "totalPages": -(-total // size),
        "number": page,
        "size": size,
    })
    cache_set(cache_key, result, ttl_seconds=30)
    return result


@router.get("/{session_id}")
def get_session(session_id: str, user=Depends(get_current_user), db: Client = Depends(get_db)):
    uid = user.id
    res = db.table("run_sessions").select("*").eq("id", session_id).eq("user_id", uid).execute()
    if not res.data:
        raise HTTPException(400, "Session not found")
    return ok(res.data[0])
