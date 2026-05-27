import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException
from supabase import Client

from auth import get_current_user
from cache import cache_get, cache_invalidate, cache_set
from database import get_db
from schemas import HabitCreateRequest, HabitLogRequest, ok
from utils import xp_calculator as xp

router = APIRouter()
_pool = ThreadPoolExecutor(max_workers=4)


@router.get("")
def list_habits(user=Depends(get_current_user), db: Client = Depends(get_db)):
    uid = user.id
    cache_key = f"habits:{uid}"
    cached = cache_get(cache_key)
    if cached:
        return cached
    res = db.table("habits").select("*").eq("user_id", uid).eq("is_active", True).execute()
    result = ok(res.data or [])
    cache_set(cache_key, result, ttl_seconds=60)
    return result


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
    cache_invalidate(f"habits:{uid}")
    return ok(res.data[0])


@router.post("/log")
async def log_habit(body: HabitLogRequest, user=Depends(get_current_user), db: Client = Depends(get_db)):
    uid = user.id
    loop = asyncio.get_event_loop()

    # Fetch habit + streak + existing log in parallel (3 independent queries)
    habit_res, streak_res, existing_res = await asyncio.gather(
        loop.run_in_executor(_pool, lambda: db.table("habits").select("*").eq("id", body.habitId).eq("user_id", uid).execute()),
        loop.run_in_executor(_pool, lambda: db.table("streaks").select("current_streak").eq("user_id", uid).single().execute()),
        loop.run_in_executor(_pool, lambda: (
            db.table("habit_logs").select("id")
            .eq("habit_id", body.habitId)
            .eq("log_date", body.logDate.isoformat())
            .execute()
        )),
    )

    if not habit_res.data:
        raise HTTPException(400, "Habit not found")

    h = habit_res.data[0]
    target = h.get("target_value") or 1.0
    is_completed = body.completedValue >= target
    current_streak = (streak_res.data or {}).get("current_streak", 0)
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

    if existing_res.data:
        res = db.table("habit_logs").update(row).eq("id", existing_res.data[0]["id"]).execute()
    else:
        res = db.table("habit_logs").insert(row).execute()

    if is_completed and xp_earned > 0:
        profile = db.table("user_profiles").select("xp_points").eq("user_id", uid).single().execute().data or {}
        new_xp = (profile.get("xp_points") or 0) + xp_earned
        db.table("user_profiles").update({
            "xp_points": new_xp,
            "level": xp.level_from_xp(new_xp),
        }).eq("user_id", uid).execute()

    cache_invalidate(f"dashboard:{uid}")
    cache_invalidate(f"habit_stats:{uid}")

    return ok(res.data[0])


@router.get("/stats")
async def habit_stats(user=Depends(get_current_user), db: Client = Depends(get_db)):
    uid = user.id
    cache_key = f"habit_stats:{uid}"
    cached = cache_get(cache_key)
    if cached:
        return cached

    today = date.today()
    week_start = (today - timedelta(days=6)).isoformat()
    month_start = (today - timedelta(days=29)).isoformat()
    loop = asyncio.get_event_loop()

    # Run all 4 independent queries in parallel
    habits_res, logs_today, logs_week, logs_month = await asyncio.gather(
        loop.run_in_executor(_pool, lambda: db.table("habits").select("id").eq("user_id", uid).eq("is_active", True).execute()),
        loop.run_in_executor(_pool, lambda: db.table("habit_logs").select("habit_id").eq("user_id", uid).eq("log_date", today.isoformat()).eq("is_completed", True).execute()),
        loop.run_in_executor(_pool, lambda: db.table("habit_logs").select("habit_id").eq("user_id", uid).gte("log_date", week_start).eq("is_completed", True).execute()),
        loop.run_in_executor(_pool, lambda: db.table("habit_logs").select("habit_id").eq("user_id", uid).gte("log_date", month_start).eq("is_completed", True).execute()),
    )

    total_habits = len(habits_res.data or [])
    if total_habits == 0:
        empty = {"completedCount": 0, "totalCount": 0, "percentage": 0}
        return ok({"daily": empty, "weekly": empty, "monthly": empty, "overallScore": 0})

    def _pct(completed: int, total: int) -> float:
        return round((completed / total) * 100, 1) if total > 0 else 0.0

    daily_done   = len(logs_today.data or [])
    weekly_done  = len(logs_week.data or [])
    monthly_done = len(logs_month.data or [])
    weekly_possible  = total_habits * 7
    monthly_possible = total_habits * 30

    daily_pct   = _pct(daily_done, total_habits)
    weekly_pct  = _pct(weekly_done, weekly_possible)
    monthly_pct = _pct(monthly_done, monthly_possible)
    overall     = round((daily_pct * 0.4 + weekly_pct * 0.35 + monthly_pct * 0.25), 1)

    result = ok({
        "daily":   {"completedCount": daily_done,   "totalCount": total_habits,    "percentage": daily_pct},
        "weekly":  {"completedCount": weekly_done,  "totalCount": weekly_possible,  "percentage": weekly_pct},
        "monthly": {"completedCount": monthly_done, "totalCount": monthly_possible, "percentage": monthly_pct},
        "overallScore": overall,
    })
    cache_set(cache_key, result, ttl_seconds=300)
    return result


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
