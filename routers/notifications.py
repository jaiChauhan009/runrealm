from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from supabase import Client

from auth import get_current_user
from database import get_db
from schemas import NotificationTodoActionRequest, ok

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


@router.post("/{notification_id}/todo-action")
def notification_todo_action(
    notification_id: str,
    body: NotificationTodoActionRequest,
    user=Depends(get_current_user),
    db: Client = Depends(get_db),
):
    """
    Update a todo's status directly from a notification.
    The notification must have notification_type='TODO_REMINDER' and
    reference_id pointing to the todo's id.
    Also marks the notification as read.
    """
    uid = user.id
    notif_res = (
        db.table("notifications")
        .select("id, reference_id, notification_type")
        .eq("id", notification_id)
        .eq("user_id", uid)
        .execute()
    )
    if not notif_res.data:
        raise HTTPException(404, "Notification not found")

    notif = notif_res.data[0]
    if notif.get("notification_type") != "TODO_REMINDER":
        raise HTTPException(400, "Notification is not a todo reminder")

    todo_id = notif.get("reference_id")
    if not todo_id:
        raise HTTPException(400, "Notification has no linked todo")

    todo_res = (
        db.table("daily_todos")
        .select("id")
        .eq("id", todo_id)
        .eq("user_id", uid)
        .execute()
    )
    if not todo_res.data:
        raise HTTPException(404, "Linked todo not found")

    is_completed = body.status == "DONE"
    update = {
        "status": body.status,
        "is_completed": is_completed,
        "completed_at": datetime.now(timezone.utc).isoformat() if is_completed else None,
    }
    todo_updated = db.table("daily_todos").update(update).eq("id", todo_id).execute()

    db.table("notifications").update({"is_read": True}).eq("id", notification_id).execute()

    return ok({"todo": todo_updated.data[0]}, f"Todo marked as {body.status}")
