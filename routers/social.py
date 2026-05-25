from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from supabase import Client

from auth import get_current_user
from database import get_db
from schemas import ok

router = APIRouter()


def _profile_map(user_ids: list[str], db: Client) -> dict:
    """Fetch profile fields for a list of user_ids in one query."""
    if not user_ids:
        return {}
    res = (
        db.table("user_profiles")
        .select("user_id, username, display_name, avatar_url, level, xp_points")
        .in_("user_id", user_ids)
        .execute()
    )
    return {p["user_id"]: p for p in (res.data or [])}


def _to_friend_dto(uid: str, profile: dict) -> dict:
    return {
        "userId": uid,
        "username": profile.get("username", ""),
        "displayName": profile.get("display_name"),
        "avatarUrl": profile.get("avatar_url"),
        "level": profile.get("level", 1),
        "xpPoints": profile.get("xp_points", 0),
    }


@router.get("/feed")
def activity_feed(
    page: int = 0,
    size: int = 20,
    user=Depends(get_current_user),
    db: Client = Depends(get_db),
):
    uid = user.id

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
        .select("*", count="exact")
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
        .select("user_id, friend_id")
        .eq("status", "ACCEPTED")
        .or_(f"user_id.eq.{uid},friend_id.eq.{uid}")
        .execute()
    )
    rows = res.data or []
    if not rows:
        return ok([])

    other_ids = [r["friend_id"] if r["user_id"] == uid else r["user_id"] for r in rows]
    profiles = _profile_map(other_ids, db)
    return ok([_to_friend_dto(oid, profiles.get(oid, {})) for oid in other_ids])


@router.get("/friends/pending")
def pending_requests(user=Depends(get_current_user), db: Client = Depends(get_db)):
    uid = user.id
    res = (
        db.table("user_friends")
        .select("user_id")
        .eq("friend_id", uid)
        .eq("status", "PENDING")
        .execute()
    )
    rows = res.data or []
    if not rows:
        return ok([])

    requester_ids = [r["user_id"] for r in rows]
    profiles = _profile_map(requester_ids, db)
    return ok([_to_friend_dto(rid, profiles.get(rid, {})) for rid in requester_ids])


@router.get("/friends/sent")
def sent_requests(user=Depends(get_current_user), db: Client = Depends(get_db)):
    uid = user.id
    res = (
        db.table("user_friends")
        .select("friend_id")
        .eq("user_id", uid)
        .eq("status", "PENDING")
        .execute()
    )
    rows = res.data or []
    if not rows:
        return ok([])

    target_ids = [r["friend_id"] for r in rows]
    profiles = _profile_map(target_ids, db)
    return ok([_to_friend_dto(tid, profiles.get(tid, {})) for tid in target_ids])
