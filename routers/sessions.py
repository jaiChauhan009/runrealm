from datetime import date, timezone

from fastapi import APIRouter, Depends, HTTPException
from supabase import Client

from auth import get_current_user
from cache import cache_invalidate
from database import get_db
from schemas import EndSessionRequest, RoutePointRequest, StartSessionRequest, ok
from utils import xp_calculator as xp

router = APIRouter()


def _update_streak(db: Client, uid: str, activity_date: date) -> int:
    """Update streak and return new current_streak value."""
    from datetime import timedelta

    res = db.table("streaks").select("*").eq("user_id", uid).execute()
    row = res.data[0] if res.data else {}
    last_str = row.get("last_activity_date")
    last = date.fromisoformat(last_str) if last_str else None
    current = row.get("current_streak", 0)
    best = row.get("best_streak", 0)

    if last is None:
        new_streak = 1
    elif activity_date == last:
        return current  # same day, no change
    elif activity_date == last + timedelta(days=1):
        new_streak = current + 1
    else:
        new_streak = 1

    new_best = max(best, new_streak)
    streak_data = {
        "current_streak": new_streak,
        "best_streak": new_best,
        "last_activity_date": activity_date.isoformat(),
    }

    if row.get("id"):
        db.table("streaks").update(streak_data).eq("id", row["id"]).execute()
    else:
        db.table("streaks").insert({"user_id": uid, **streak_data}).execute()

    db.table("user_profiles").update({
        "current_streak": new_streak,
        "best_streak": new_best,
    }).eq("user_id", uid).execute()
    return new_streak


def _award_xp(db: Client, uid: str, amount: int, txn_type: str, ref_id: str, desc: str):
    db.table("xp_transactions").insert({
        "user_id": uid,
        "amount": amount,
        "transaction_type": txn_type,
        "reference_id": ref_id,
        "description": desc,
    }).execute()
    profile = db.table("user_profiles").select("xp_points").eq("user_id", uid).single().execute().data or {}
    new_xp = (profile.get("xp_points") or 0) + amount
    db.table("user_profiles").update({"xp_points": new_xp, "level": xp.level_from_xp(new_xp)}).eq("user_id", uid).execute()


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
def end_session(
    session_id: str,
    body: EndSessionRequest,
    user=Depends(get_current_user),
    db: Client = Depends(get_db),
):
    uid = user.id
    sess = db.table("run_sessions").select("*").eq("id", session_id).eq("user_id", uid).execute()
    if not sess.data:
        raise HTTPException(400, "Session not found")

    s = sess.data[0]
    start = s.get("start_time")
    duration = 0
    if start:
        from datetime import datetime
        st = datetime.fromisoformat(start.replace("Z", "+00:00"))
        et = body.endTime
        if et.tzinfo is None:
            et = et.replace(tzinfo=timezone.utc)
        duration = int((et - st).total_seconds())

    activity_date = body.endTime.date()
    streak = _update_streak(db, uid, activity_date)
    xp_earned = xp.for_run(body.distanceKm, streak)

    update = {
        "end_time": body.endTime.isoformat(),
        "distance_km": body.distanceKm,
        "avg_pace_min_per_km": body.avgPaceMinPerKm,
        "max_speed_kmh": body.maxSpeedKmh,
        "calories_burned": body.caloriesBurned or 0,
        "elevation_gain_m": body.elevationGainM or 0,
        "route_geo_json": body.routeGeoJson,
        "status": "COMPLETED",
        "duration_seconds": duration,
        "xp_earned": xp_earned,
        "synced": True,
    }
    res = db.table("run_sessions").update(update).eq("id", session_id).execute()

    _award_xp(db, uid, xp_earned, "RUN_COMPLETE", session_id,
              f"Completed {body.distanceKm:.2f} km run")

    # Update profile totals (single round trip)
    profile = (
        db.table("user_profiles")
        .select("total_runs, total_calories, total_distance_km")
        .eq("user_id", uid)
        .single()
        .execute()
        .data or {}
    )
    db.table("user_profiles").update({
        "total_runs": (profile.get("total_runs") or 0) + 1,
        "total_calories": (profile.get("total_calories") or 0) + (body.caloriesBurned or 0),
        "total_distance_km": round((profile.get("total_distance_km") or 0) + body.distanceKm, 3),
    }).eq("user_id", uid).execute()

    # Activity feed
    db.table("activity_feed").insert({
        "user_id": uid,
        "activity_type": "RUN_COMPLETED",
        "reference_id": session_id,
        "message": f"Completed a {body.distanceKm:.2f} km run",
        "metadata_json": f'{{"distanceKm":{body.distanceKm},"durationSec":{duration},"calories":{body.caloriesBurned or 0}}}',
        "is_public": True,
    }).execute()

    # Invalidate dashboard cache so next fetch reflects new XP, streak, and stats
    cache_invalidate(f"dashboard:{uid}")

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
    start = page * size
    end = start + size - 1

    res = (
        db.table("run_sessions")
        .select("*", count="exact")
        .eq("user_id", uid)
        .order("start_time", desc=True)
        .range(start, end)
        .execute()
    )
    total = res.count or 0
    return ok({
        "content": res.data or [],
        "totalElements": total,
        "totalPages": -(-total // size),
        "number": page,
        "size": size,
    })


@router.get("/{session_id}")
def get_session(session_id: str, user=Depends(get_current_user), db: Client = Depends(get_db)):
    uid = user.id
    res = db.table("run_sessions").select("*").eq("id", session_id).eq("user_id", uid).execute()
    if not res.data:
        raise HTTPException(400, "Session not found")
    return ok(res.data[0])
