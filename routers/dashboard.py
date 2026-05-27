import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

from fastapi import APIRouter, Depends
from supabase import Client

from auth import get_current_user
from cache import cache_get, cache_set
from database import get_db
from schemas import ok
from utils import xp_calculator as xp

router = APIRouter()

# Shared thread pool for parallelising sync Supabase calls inside async handlers
_pool = ThreadPoolExecutor(max_workers=6)


@router.get("")
async def dashboard(user=Depends(get_current_user), db: Client = Depends(get_db)):
    uid = user.id

    # Serve cached response if still fresh (30-second TTL per user)
    cache_key = f"dashboard:{uid}"
    cached = cache_get(cache_key)
    if cached:
        return cached

    loop = asyncio.get_event_loop()
    today = date.today().isoformat()
    week_start = (date.today() - timedelta(days=6)).isoformat()

    # Fire all 6 independent Supabase queries in parallel
    profile, streak, sessions, territory_count, habits, today_logs = await asyncio.gather(
        loop.run_in_executor(
            _pool,
            lambda: db.table("user_profiles").select("*").eq("user_id", uid).single().execute().data or {},
        ),
        loop.run_in_executor(
            _pool,
            lambda: db.table("streaks").select("*").eq("user_id", uid).single().execute().data or {},
        ),
        loop.run_in_executor(
            _pool,
            lambda: (
                db.table("run_sessions")
                .select("distance_km, calories_burned, start_time, activity_type, id, duration_seconds")
                .eq("user_id", uid)
                .eq("status", "COMPLETED")
                .gte("start_time", week_start)
                .order("start_time", desc=True)
                .execute()
                .data or []
            ),
        ),
        loop.run_in_executor(
            _pool,
            lambda: db.table("territories").select("id", count="exact").eq("captured_by", uid).eq("status", "ACTIVE").execute().count or 0,
        ),
        loop.run_in_executor(
            _pool,
            lambda: db.table("habits").select("*").eq("user_id", uid).eq("is_active", True).execute().data or [],
        ),
        loop.run_in_executor(
            _pool,
            lambda: db.table("habit_logs").select("*").eq("user_id", uid).eq("log_date", today).execute().data or [],
        ),
    )

    # Aggregate results
    total_xp = profile.get("xp_points", 0)

    weekly_distance = sum(s.get("distance_km", 0) or 0 for s in sessions)
    weekly_calories = sum(s.get("calories_burned", 0) or 0 for s in sessions)

    logs_by_habit = {log["habit_id"]: log for log in today_logs}
    today_habits = [
        {
            "habitId": h["id"],
            "name": h["name"],
            "habitType": h["habit_type"],
            "targetValue": h.get("target_value"),
            "completedValue": logs_by_habit.get(h["id"], {}).get("completed_value", 0),
            "completed": logs_by_habit.get(h["id"], {}).get("is_completed", False),
            "unit": h.get("unit"),
            "colorHex": h.get("color_hex"),
        }
        for h in habits
    ]

    recent_activities = [
        {
            "sessionId": s["id"],
            "activityType": s.get("activity_type"),
            "distanceKm": s.get("distance_km", 0),
            "durationSeconds": s.get("duration_seconds", 0),
            "caloriesBurned": s.get("calories_burned", 0),
            "startTime": s.get("start_time"),
        }
        for s in sessions[:5]
    ]

    result = ok({
        "currentStreak": streak.get("current_streak", 0),
        "bestStreak": streak.get("best_streak", 0),
        "weeklyDistanceKm": round(weekly_distance, 2),
        "weeklyCalories": weekly_calories,
        "totalXp": total_xp,
        "level": xp.level_from_xp(total_xp),
        "xpToNextLevel": xp.xp_to_next_level(total_xp),
        "territoryOwnedSqKm": profile.get("territory_owned_sq_km", 0),
        "territoriesCaptured": territory_count,
        "todayHabits": today_habits,
        "recentActivities": recent_activities,
    })
    cache_set(cache_key, result, ttl_seconds=30)
    return result
