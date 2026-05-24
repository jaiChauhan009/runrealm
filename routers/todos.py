from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from supabase import Client

from auth import get_current_user
from database import get_db
from schemas import TodoCreateRequest, TodoStatusRequest, TodoUpdateRequest, ok

router = APIRouter()


def _get_owned_todo(todo_id: str, uid: str, db: Client):
    res = db.table("daily_todos").select("*").eq("id", todo_id).eq("user_id", uid).execute()
    if not res.data:
        raise HTTPException(404, "Todo not found")
    return res.data[0]


@router.get("")
def list_todos(
    todo_date: str | None = None,
    user=Depends(get_current_user),
    db: Client = Depends(get_db),
):
    uid = user.id
    target = todo_date or date.today().isoformat()
    res = (
        db.table("daily_todos")
        .select("*")
        .eq("user_id", uid)
        .eq("todo_date", target)
        .order("created_at")
        .execute()
    )
    return ok(res.data or [])


@router.post("")
def create_todo(body: TodoCreateRequest, user=Depends(get_current_user), db: Client = Depends(get_db)):
    uid = user.id
    row = {
        "user_id": uid,
        "title": body.title,
        "description": body.description,
        "todo_date": (body.todoDate or date.today()).isoformat(),
        "category": body.category or "GENERAL",
        "status": "PENDING",
        "is_completed": False,
        "scheduled_at": body.scheduledAt.isoformat() if body.scheduledAt else None,
    }
    res = db.table("daily_todos").insert(row).execute()
    return ok(res.data[0])


@router.patch("/{todo_id}/status")
def set_todo_status(
    todo_id: str,
    body: TodoStatusRequest,
    user=Depends(get_current_user),
    db: Client = Depends(get_db),
):
    """
    Generic status setter — accepts PENDING | DONE | CANCELLED | DEFERRED.
    This is the primary endpoint for Done / Cancel / Do Later / Undo actions.
    """
    uid = user.id
    _get_owned_todo(todo_id, uid, db)

    is_completed = body.status == "DONE"
    update = {
        "status": body.status,
        "is_completed": is_completed,
        "completed_at": datetime.now(timezone.utc).isoformat() if is_completed else None,
    }
    res = db.table("daily_todos").update(update).eq("id", todo_id).execute()
    return ok(res.data[0])


@router.patch("/{todo_id}/complete")
def complete_todo(todo_id: str, user=Depends(get_current_user), db: Client = Depends(get_db)):
    uid = user.id
    _get_owned_todo(todo_id, uid, db)
    res = db.table("daily_todos").update({
        "status": "DONE",
        "is_completed": True,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }).eq("id", todo_id).execute()
    return ok(res.data[0])


@router.patch("/{todo_id}/incomplete")
def incomplete_todo(todo_id: str, user=Depends(get_current_user), db: Client = Depends(get_db)):
    uid = user.id
    _get_owned_todo(todo_id, uid, db)
    res = db.table("daily_todos").update({
        "status": "PENDING",
        "is_completed": False,
        "completed_at": None,
    }).eq("id", todo_id).execute()
    return ok(res.data[0])


@router.patch("/{todo_id}/cancel")
def cancel_todo(todo_id: str, user=Depends(get_current_user), db: Client = Depends(get_db)):
    uid = user.id
    _get_owned_todo(todo_id, uid, db)
    res = db.table("daily_todos").update({
        "status": "CANCELLED",
        "is_completed": False,
        "completed_at": None,
    }).eq("id", todo_id).execute()
    return ok(res.data[0])


@router.patch("/{todo_id}/defer")
def defer_todo(todo_id: str, user=Depends(get_current_user), db: Client = Depends(get_db)):
    uid = user.id
    _get_owned_todo(todo_id, uid, db)
    res = db.table("daily_todos").update({
        "status": "DEFERRED",
        "is_completed": False,
        "completed_at": None,
    }).eq("id", todo_id).execute()
    return ok(res.data[0])


@router.patch("/{todo_id}")
def update_todo(todo_id: str, body: TodoUpdateRequest, user=Depends(get_current_user), db: Client = Depends(get_db)):
    uid = user.id
    existing = _get_owned_todo(todo_id, uid, db)
    update = {}
    if body.title is not None:
        update["title"] = body.title
    if body.description is not None:
        update["description"] = body.description
    if body.category is not None:
        update["category"] = body.category
    if not update:
        return ok(existing)
    res = db.table("daily_todos").update(update).eq("id", todo_id).execute()
    return ok(res.data[0])


@router.delete("/{todo_id}")
def delete_todo(todo_id: str, user=Depends(get_current_user), db: Client = Depends(get_db)):
    uid = user.id
    _get_owned_todo(todo_id, uid, db)
    db.table("daily_todos").delete().eq("id", todo_id).execute()
    return ok(None, "Todo deleted")


@router.get("/stats")
def todo_stats(user=Depends(get_current_user), db: Client = Depends(get_db)):
    uid = user.id
    today = date.today()

    def _pct(done: int, total: int) -> float:
        return round((done / total) * 100, 1) if total > 0 else 0.0

    def _fetch(start: str, end: str):
        res = (
            db.table("daily_todos").select("is_completed")
            .eq("user_id", uid)
            .gte("todo_date", start)
            .lte("todo_date", end)
            .execute()
        )
        rows = res.data or []
        total = len(rows)
        done  = sum(1 for r in rows if r["is_completed"])
        return done, total

    d_done, d_total = _fetch(today.isoformat(), today.isoformat())

    week_start = (today - timedelta(days=6)).isoformat()
    w_done, w_total = _fetch(week_start, today.isoformat())

    month_start = (today - timedelta(days=29)).isoformat()
    m_done, m_total = _fetch(month_start, today.isoformat())

    daily_pct   = _pct(d_done, d_total)
    weekly_pct  = _pct(w_done, w_total)
    monthly_pct = _pct(m_done, m_total)
    overall     = round((daily_pct * 0.4 + weekly_pct * 0.35 + monthly_pct * 0.25), 1)

    return ok({
        "daily":   {"completedCount": d_done, "totalCount": d_total, "percentage": daily_pct},
        "weekly":  {"completedCount": w_done, "totalCount": w_total, "percentage": weekly_pct},
        "monthly": {"completedCount": m_done, "totalCount": m_total, "percentage": monthly_pct},
        "overallScore": overall,
    })
