from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from supabase import Client

from auth import get_current_user
from database import get_db
from schemas import ok

router = APIRouter()


@router.get("")
def list_notifications(
    page: int = 0,
    size: int = 30,
    user=Depends(get_current_user),
    db: Client = Depends(get_db),
):
    uid = user.id
    start = page * size
    res = (
        db.table("notifications")
        .select("*", count="exact")
        .eq("user_id", uid)
        .order("created_at", desc=True)
        .range(start, start + size - 1)
        .execute()
    )
    total = res.count or 0
    return ok({"content": res.data or [], "totalElements": total})


@router.get("/unread-count")
def unread_count(user=Depends(get_current_user), db: Client = Depends(get_db)):
    uid = user.id
    res = db.table("notifications").select("id", count="exact").eq("user_id", uid).eq("is_read", False).execute()
    return ok({"unreadCount": res.count or 0})


@router.post("/read-all")
def mark_all_read(user=Depends(get_current_user), db: Client = Depends(get_db)):
    uid = user.id
    db.table("notifications").update({"is_read": True}).eq("user_id", uid).eq("is_read", False).execute()
    return ok(None, "All notifications marked read")


@router.post("/{notification_id}/read")
def mark_one_read(notification_id: str, user=Depends(get_current_user), db: Client = Depends(get_db)):
    uid = user.id
    res = db.table("notifications").select("id").eq("id", notification_id).eq("user_id", uid).execute()
    if not res.data:
        raise HTTPException(400, "Notification not found")
    db.table("notifications").update({"is_read": True}).eq("id", notification_id).execute()
    return ok(None, "Marked as read")
