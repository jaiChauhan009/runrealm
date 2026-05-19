from fastapi import APIRouter, Depends, HTTPException
from supabase import Client

from auth import get_current_user
from database import get_db
from schemas import ok

router = APIRouter()


@router.get("/feed")
def activity_feed(
    page: int = 0,
    size: int = 20,
    user=Depends(get_current_user),
    db: Client = Depends(get_db),
):
    uid = user.id

    # Get friend IDs
    friends_res = (
        db.table("user_friends")
        .select("friend_id, user_id")
        .eq("status", "ACCEPTED")
        .or_(f"user_id.eq.{uid},friend_id.eq.{uid}")
        .execute()
    )
    friend_ids = set()
    for f in (friends_res.data or []):
        friend_ids.add(f["friend_id"] if f["user_id"] == uid else f["user_id"])
    friend_ids.add(uid)

    start = page * size
    res = (
        db.table("activity_feed")
        .select("*, user:user_profiles!activity_feed_user_id_fkey(id,username,avatar_url)", count="exact")
        .in_("user_id", list(friend_ids))
        .eq("is_public", True)
        .order("created_at", desc=True)
        .range(start, start + size - 1)
        .execute()
    )
    total = res.count or 0
    return ok({
        "content": res.data or [],
        "totalElements": total,
        "totalPages": -(-total // size),
        "number": page,
    })


@router.post("/friends/{friend_id}/request")
def send_friend_request(friend_id: str, user=Depends(get_current_user), db: Client = Depends(get_db)):
    uid = user.id
    target = db.table("user_profiles").select("id").eq("user_id", friend_id).execute()
    if not target.data:
        raise HTTPException(400, "User not found")

    existing = (
        db.table("user_friends")
        .select("id")
        .eq("user_id", uid)
        .eq("friend_id", friend_id)
        .execute()
    )
    if existing.data:
        return ok(None, "Friend request already sent")

    db.table("user_friends").insert({
        "user_id": uid,
        "friend_id": friend_id,
        "status": "PENDING",
    }).execute()
    return ok(None, "Friend request sent")


@router.post("/friends/{friend_id}/accept")
def accept_friend_request(friend_id: str, user=Depends(get_current_user), db: Client = Depends(get_db)):
    uid = user.id
    from datetime import datetime, timezone

    req = (
        db.table("user_friends")
        .select("id")
        .eq("user_id", friend_id)
        .eq("friend_id", uid)
        .eq("status", "PENDING")
        .execute()
    )
    if not req.data:
        raise HTTPException(400, "Friend request not found")

    db.table("user_friends").update({
        "status": "ACCEPTED",
        "accepted_at": datetime.now(timezone.utc).isoformat(),
    }).eq("id", req.data[0]["id"]).execute()
    return ok(None, "Friend request accepted")


@router.get("/friends")
def list_friends(user=Depends(get_current_user), db: Client = Depends(get_db)):
    uid = user.id
    res = (
        db.table("user_friends")
        .select("*")
        .eq("status", "ACCEPTED")
        .or_(f"user_id.eq.{uid},friend_id.eq.{uid}")
        .execute()
    )
    return ok(res.data or [])


@router.get("/friends/pending")
def pending_requests(user=Depends(get_current_user), db: Client = Depends(get_db)):
    uid = user.id
    res = (
        db.table("user_friends")
        .select("*")
        .eq("friend_id", uid)
        .eq("status", "PENDING")
        .execute()
    )
    return ok(res.data or [])
