from datetime import date, timedelta

from fastapi import APIRouter, Depends
from supabase import Client

from auth import get_current_user
from database import get_db
from schemas import ok
from utils import xp_calculator as xp

router = APIRouter()


@router.get("")
def dashboard(user=Depends(get_current_user), db: Client = Depends(get_db)):
    uid = user.id

    profile = db.table("user_profiles").select("*").eq("user_id", uid).single().execute().data or {}
    streak = db.table("streaks").select("*").eq("user_id", uid).single().execute().data or {}

    total_xp = profile.get("xp_points", 0)
    level = xp.level_from_xp(total_xp)
    xp_next = xp.xp_to_next_level(total_xp)

    # Weekly distance/calories
    week_start = (date.today() - timedelta(days=6)).isoformat()
    sessions_res = (
        db.table("run_sessions")
        .select("distance_km, calories_burned, start_time, activity_type, id, duration_seconds")
        .eq("user_id", uid)
        .eq("status", "COMPLETED")
        .gte("start_time", week_start)
        .order("start_time", desc=True)
        .execute()
    )
    sessions = sessions_res.data or []
    weekly_distance = sum(s.get("distance_km", 0) or 0 for s in sessions)
    weekly_calories = sum(s.get("calories_burned", 0) or 0 for s in sessions)

    # Territory stats
    territory_count = (
        db.table("territories").select("id", count="exact").eq("captured_by", uid).execute()
    )
    territories_captured = territory_count.count or 0

    # Today's habits
    today = date.today().isoformat()
    habits_res = db.table("habits").select("*").eq("user_id", uid).eq("is_active", True).execute()
    logs_res = db.table("habit_logs").select("*").eq("user_id", uid).eq("log_date", today).execute()
    logs_by_habit = {l["habit_id"]: l for l in (logs_res.data or [])}

    today_habits = []
    for h in (habits_res.data or []):
        log = logs_by_habit.get(h["id"], {})
        today_habits.append({
            "habitId": h["id"],
            "name": h["name"],
            "habitType": h["habit_type"],
            "targetValue": h.get("target_value"),
            "completedValue": log.get("completed_value", 0),
            "completed": log.get("is_completed", False),
            "unit": h.get("unit"),
            "colorHex": h.get("color_hex"),
        })

    # Recent activities (last 5 completed sessions)
    recent = sessions[:5]
    recent_activities = [
        {
            "sessionId": s["id"],
            "activityType": s.get("activity_type"),
            "distanceKm": s.get("distance_km", 0),
            "durationSeconds": s.get("duration_seconds", 0),
            "caloriesBurned": s.get("calories_burned", 0),
            "startTime": s.get("start_time"),
        }
        for s in recent
    ]

    return ok({
        "currentStreak": streak.get("current_streak", 0),
        "bestStreak": streak.get("best_streak", 0),
        "weeklyDistanceKm": round(weekly_distance, 2),
        "weeklyCalories": weekly_calories,
        "totalXp": total_xp,
        "level": level,
        "xpToNextLevel": xp_next,
        "territoryOwnedSqKm": profile.get("territory_owned_sq_km", 0),
        "territoriesCaptured": territories_captured,
        "todayHabits": today_habits,
        "recentActivities": recent_activities,
    })
