from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from supabase import Client

from auth import get_current_user
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

    return ok(res.data[0])


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
