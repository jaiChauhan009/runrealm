from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from supabase import Client

from auth import get_current_user
from database import get_db
from schemas import TodoCreateRequest, TodoStatusRequest, TodoUpdateRequest, ok

router = APIRouter()


# ── helpers ───────────────────────────────────────────────────────────────────

def _owned_update(todo_id: str, uid: str, update: dict, db: Client) -> dict:
    """UPDATE with ownership guard in one round trip. Raises 404 if not found."""
    res = db.table("daily_todos").update(update).eq("id", todo_id).eq("user_id", uid).execute()
    if not res.data:
        raise HTTPException(404, "Todo not found")
    return res.data[0]


# ── endpoints ─────────────────────────────────────────────────────────────────

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
    uid = user.id
    is_completed = body.status == "DONE"
    return ok(_owned_update(todo_id, uid, {
        "status": body.status,
        "is_completed": is_completed,
        "completed_at": datetime.now(timezone.utc).isoformat() if is_completed else None,
    }, db))


@router.patch("/{todo_id}/complete")
def complete_todo(todo_id: str, user=Depends(get_current_user), db: Client = Depends(get_db)):
    return ok(_owned_update(todo_id, user.id, {
        "status": "DONE",
        "is_completed": True,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }, db))


@router.patch("/{todo_id}/incomplete")
def incomplete_todo(todo_id: str, user=Depends(get_current_user), db: Client = Depends(get_db)):
    return ok(_owned_update(todo_id, user.id, {
        "status": "PENDING",
        "is_completed": False,
        "completed_at": None,
    }, db))


@router.patch("/{todo_id}/cancel")
def cancel_todo(todo_id: str, user=Depends(get_current_user), db: Client = Depends(get_db)):
    return ok(_owned_update(todo_id, user.id, {
        "status": "CANCELLED",
        "is_completed": False,
        "completed_at": None,
    }, db))


@router.patch("/{todo_id}/defer")
def defer_todo(todo_id: str, user=Depends(get_current_user), db: Client = Depends(get_db)):
    return ok(_owned_update(todo_id, user.id, {
        "status": "DEFERRED",
        "is_completed": False,
        "completed_at": None,
    }, db))


@router.patch("/{todo_id}")
def update_todo(todo_id: str, body: TodoUpdateRequest, user=Depends(get_current_user), db: Client = Depends(get_db)):
    uid = user.id
    update: dict = {}
    if body.title is not None:
        update["title"] = body.title
    if body.description is not None:
        update["description"] = body.description
    if body.category is not None:
        update["category"] = body.category
    if not update:
        res = db.table("daily_todos").select("*").eq("id", todo_id).eq("user_id", uid).execute()
        if not res.data:
            raise HTTPException(404, "Todo not found")
        return ok(res.data[0])
    return ok(_owned_update(todo_id, uid, update, db))


@router.delete("/{todo_id}")
def delete_todo(todo_id: str, user=Depends(get_current_user), db: Client = Depends(get_db)):
    uid = user.id
    db.table("daily_todos").delete().eq("id", todo_id).eq("user_id", uid).execute()
    return ok(None, "Todo deleted")


@router.get("/stats")
def todo_stats(user=Depends(get_current_user), db: Client = Depends(get_db)):
    uid = user.id
    today = date.today()
    month_start = (today - timedelta(days=29)).isoformat()
    today_str = today.isoformat()
    week_start = (today - timedelta(days=6)).isoformat()

    # One query for 30 days — aggregate daily / weekly / monthly in Python
    res = (
        db.table("daily_todos")
        .select("todo_date, is_completed")
        .eq("user_id", uid)
        .gte("todo_date", month_start)
        .lte("todo_date", today_str)
        .execute()
    )
    rows = res.data or []

    d_total = d_done = w_total = w_done = 0
    for r in rows:
        td = r["todo_date"]
        done = r["is_completed"]
        if td == today_str:
            d_total += 1
            if done:
                d_done += 1
        if week_start <= td <= today_str:
            w_total += 1
            if done:
                w_done += 1

    m_total = len(rows)
    m_done = sum(1 for r in rows if r["is_completed"])

    def _pct(done: int, total: int) -> float:
        return round((done / total) * 100, 1) if total > 0 else 0.0

    daily_pct   = _pct(d_done, d_total)
    weekly_pct  = _pct(w_done, w_total)
    monthly_pct = _pct(m_done, m_total)
    overall     = round(daily_pct * 0.4 + weekly_pct * 0.35 + monthly_pct * 0.25, 1)

    return ok({
        "daily":   {"completedCount": d_done, "totalCount": d_total, "percentage": daily_pct},
        "weekly":  {"completedCount": w_done, "totalCount": w_total, "percentage": weekly_pct},
        "monthly": {"completedCount": m_done, "totalCount": m_total, "percentage": monthly_pct},
        "overallScore": overall,
    })
