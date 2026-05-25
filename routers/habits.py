from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException
from supabase import Client

from auth import get_current_user
from cache import cache_invalidate
from database import get_db
from schemas import HabitCreateRequest, HabitLogRequest, ok
from utils import xp_calculator as xp

router = APIRouter()


@router.get("")
def list_habits(user=Depends(get_current_user), db: Client = Depends(get_db)):
    uid = user.id
    res = db.table("habits").select("*").eq("user_id", uid).eq("is_active", True).execute()
    return ok(res.data or [])


@router.post("")
def create_habit(body: HabitCreateRequest, user=Depends(get_current_user), db: Client = Depends(get_db)):
    uid = user.id
    row = {
        "user_id": uid,
        "name": body.name,
        "description": body.description,
        "habit_type": body.habitType,
        "target_value": body.targetValue,
        "unit": body.unit,
        "frequency": body.frequency,
        "icon": body.icon,
        "color_hex": body.colorHex,
        "is_active": True,
    }
    res = db.table("habits").insert(row).execute()
    return ok(res.data[0])


@router.post("/log")
def log_habit(body: HabitLogRequest, user=Depends(get_current_user), db: Client = Depends(get_db)):
    uid = user.id

    habit = db.table("habits").select("*").eq("id", body.habitId).eq("user_id", uid).execute()
    if not habit.data:
        raise HTTPException(400, "Habit not found")

    h = habit.data[0]
    target = h.get("target_value") or 1.0
    is_completed = body.completedValue >= target

    streak_row = db.table("streaks").select("current_streak").eq("user_id", uid).single().execute().data or {}
    current_streak = streak_row.get("current_streak", 0)
    xp_earned = xp.for_habit(current_streak) if is_completed else 0

    row = {
        "habit_id": body.habitId,
        "user_id": uid,
        "log_date": body.logDate.isoformat(),
        "completed_value": body.completedValue,
        "is_completed": is_completed,
        "xp_earned": xp_earned,
        "notes": body.notes,
        "local_id": body.localId,
        "synced": True,
    }

    # Upsert on (habit_id, log_date)
    existing = (
        db.table("habit_logs")
        .select("id")
        .eq("habit_id", body.habitId)
        .eq("log_date", body.logDate.isoformat())
        .execute()
    )
    if existing.data:
        res = db.table("habit_logs").update(row).eq("id", existing.data[0]["id"]).execute()
    else:
        res = db.table("habit_logs").insert(row).execute()

    if is_completed and xp_earned > 0:
        profile = db.table("user_profiles").select("xp_points").eq("user_id", uid).single().execute().data or {}
        new_xp = (profile.get("xp_points") or 0) + xp_earned
        db.table("user_profiles").update({
            "xp_points": new_xp,
            "level": xp.level_from_xp(new_xp),
        }).eq("user_id", uid).execute()

    # Invalidate dashboard cache so today's habit progress reflects immediately
    cache_invalidate(f"dashboard:{uid}")

    return ok(res.data[0])


@router.get("/stats")
def habit_stats(user=Depends(get_current_user), db: Client = Depends(get_db)):
    uid = user.id
    today = date.today()

    habits_res = db.table("habits").select("id").eq("user_id", uid).eq("is_active", True).execute()
    habit_ids = [h["id"] for h in (habits_res.data or [])]
    total_habits = len(habit_ids)

    if total_habits == 0:
        empty = {"completedCount": 0, "totalCount": 0, "percentage": 0}
        return ok({"daily": empty, "weekly": empty, "monthly": empty, "overallScore": 0})

    def _pct(completed: int, total: int) -> float:
        return round((completed / total) * 100, 1) if total > 0 else 0.0

    # Daily
    logs_today = (
        db.table("habit_logs").select("habit_id")
        .eq("user_id", uid).eq("log_date", today.isoformat()).eq("is_completed", True)
        .execute()
    )
    daily_done = len(logs_today.data or [])

    # Weekly (last 7 days)
    week_start = (today - timedelta(days=6)).isoformat()
    logs_week = (
        db.table("habit_logs").select("habit_id, log_date")
        .eq("user_id", uid).gte("log_date", week_start).eq("is_completed", True)
        .execute()
    )
    weekly_possible = total_habits * 7
    weekly_done = len(logs_week.data or [])

    # Monthly (last 30 days)
    month_start = (today - timedelta(days=29)).isoformat()
    logs_month = (
        db.table("habit_logs").select("habit_id")
        .eq("user_id", uid).gte("log_date", month_start).eq("is_completed", True)
        .execute()
    )
    monthly_possible = total_habits * 30
    monthly_done = len(logs_month.data or [])

    daily_pct   = _pct(daily_done, total_habits)
    weekly_pct  = _pct(weekly_done, weekly_possible)
    monthly_pct = _pct(monthly_done, monthly_possible)
    overall     = round((daily_pct * 0.4 + weekly_pct * 0.35 + monthly_pct * 0.25), 1)

    return ok({
        "daily":   {"completedCount": daily_done,   "totalCount": total_habits,    "percentage": daily_pct},
        "weekly":  {"completedCount": weekly_done,  "totalCount": weekly_possible,  "percentage": weekly_pct},
        "monthly": {"completedCount": monthly_done, "totalCount": monthly_possible, "percentage": monthly_pct},
        "overallScore": overall,
    })


@router.get("/logs")
def habit_logs(
    date_str: str | None = None,
    user=Depends(get_current_user),
    db: Client = Depends(get_db),
):
    uid = user.id
    target_date = date_str or date.today().isoformat()
    res = db.table("habit_logs").select("*").eq("user_id", uid).eq("log_date", target_date).execute()
    return ok(res.data or [])
