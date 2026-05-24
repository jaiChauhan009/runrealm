from datetime import datetime, timezone
from math import ceil
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from supabase import Client

from auth import get_current_user
from database import get_db
from schemas import LeagueCreateRequest, ok

router = APIRouter()


# ── helpers ───────────────────────────────────────────────────────────────────

def _get_league_or_404(league_id: str, db: Client) -> dict:
    res = db.table("leagues").select("*").eq("id", league_id).execute()
    if not res.data:
        raise HTTPException(404, "League not found")
    return res.data[0]


def _get_my_role(league_id: str, uid: str, db: Client) -> Optional[str]:
    res = (
        db.table("league_members")
        .select("role")
        .eq("league_id", league_id)
        .eq("user_id", uid)
        .execute()
    )
    return res.data[0]["role"] if res.data else None


def _require_admin(league_id: str, uid: str, db: Client) -> str:
    role = _get_my_role(league_id, uid, db)
    if not role:
        raise HTTPException(403, "Not a member")
    if role not in ("CREATOR", "LEADER"):
        raise HTTPException(403, "Admin access required")
    return role


def _member_count(league_id: str, db: Client) -> int:
    res = db.table("league_members").select("user_id").eq("league_id", league_id).execute()
    return len(res.data or [])


def _enrich_members(members: list, db: Client) -> list:
    if not members:
        return []
    user_ids = [m["user_id"] for m in members]
    profiles_res = (
        db.table("user_profiles")
        .select("user_id,username,display_name,avatar_url,level,xp_points")
        .in_("user_id", user_ids)
        .execute()
    )
    profile_map = {p["user_id"]: p for p in (profiles_res.data or [])}
    result = []
    for m in members:
        p = profile_map.get(m["user_id"], {})
        result.append({
            "userId": m["user_id"],
            "role": m["role"],
            "joinedAt": m.get("joined_at"),
            "username": p.get("username"),
            "displayName": p.get("display_name"),
            "avatarUrl": p.get("avatar_url"),
            "level": p.get("level", 1),
            "xpPoints": p.get("xp_points", 0),
        })
    return result


def _league_summary(league: dict, member_count: int, my_role: Optional[str]) -> dict:
    return {
        "id": league["id"],
        "name": league["name"],
        "description": league.get("description"),
        "scope": league["scope"],
        "creatorId": league["creator_id"],
        "socialLinks": league.get("social_links") or [],
        "createdAt": league.get("created_at"),
        "memberCount": member_count,
        "myRole": my_role,
    }


# ── endpoints ─────────────────────────────────────────────────────────────────

@router.get("")
def list_leagues(
    scope: Optional[str] = None,
    user=Depends(get_current_user),
    db: Client = Depends(get_db),
):
    uid = user.id
    query = db.table("leagues").select("*").order("created_at", desc=True)
    if scope:
        query = query.eq("scope", scope.upper())
    leagues = query.execute().data or []

    if not leagues:
        return ok([])

    league_ids = [l["id"] for l in leagues]

    # Member counts — one query, counted in Python
    all_members_res = (
        db.table("league_members")
        .select("league_id")
        .in_("league_id", league_ids)
        .execute()
    )
    member_counts: dict[str, int] = {}
    for m in (all_members_res.data or []):
        lid = m["league_id"]
        member_counts[lid] = member_counts.get(lid, 0) + 1

    # My memberships
    my_res = (
        db.table("league_members")
        .select("league_id,role")
        .eq("user_id", uid)
        .in_("league_id", league_ids)
        .execute()
    )
    my_roles = {m["league_id"]: m["role"] for m in (my_res.data or [])}

    return ok([
        _league_summary(l, member_counts.get(l["id"], 0), my_roles.get(l["id"]))
        for l in leagues
    ])


@router.post("")
def create_league(
    body: LeagueCreateRequest,
    user=Depends(get_current_user),
    db: Client = Depends(get_db),
):
    uid = user.id
    league_res = db.table("leagues").insert({
        "name": body.name,
        "description": body.description,
        "scope": body.scope,
        "creator_id": uid,
        "social_links": [s.model_dump() for s in body.socialLinks] if body.socialLinks else [],
    }).execute()
    league = league_res.data[0]

    db.table("league_members").insert({
        "league_id": league["id"],
        "user_id": uid,
        "role": "CREATOR",
    }).execute()

    return ok(_league_summary(league, 1, "CREATOR"))


@router.get("/{league_id}")
def get_league(
    league_id: str,
    user=Depends(get_current_user),
    db: Client = Depends(get_db),
):
    uid = user.id
    league = _get_league_or_404(league_id, db)

    members_res = (
        db.table("league_members")
        .select("*")
        .eq("league_id", league_id)
        .order("joined_at")
        .execute()
    )
    members = _enrich_members(members_res.data or [], db)
    member_count = len(members)
    my_role = next((m["role"] for m in members if m["userId"] == uid), None)

    # Pending join requests — visible to admins only
    pending_requests = []
    if my_role in ("CREATOR", "LEADER"):
        req_res = (
            db.table("league_join_requests")
            .select("*")
            .eq("league_id", league_id)
            .eq("status", "PENDING")
            .execute()
        )
        req_data = req_res.data or []
        if req_data:
            req_ids = [r["user_id"] for r in req_data]
            req_profiles_res = (
                db.table("user_profiles")
                .select("user_id,username,display_name,avatar_url,level")
                .in_("user_id", req_ids)
                .execute()
            )
            req_profile_map = {p["user_id"]: p for p in (req_profiles_res.data or [])}
            for r in req_data:
                p = req_profile_map.get(r["user_id"], {})
                pending_requests.append({
                    "userId": r["user_id"],
                    "requestedAt": r.get("requested_at"),
                    "username": p.get("username"),
                    "displayName": p.get("display_name"),
                    "avatarUrl": p.get("avatar_url"),
                    "level": p.get("level", 1),
                })

    # Delete votes
    votes_res = (
        db.table("league_delete_votes")
        .select("user_id")
        .eq("league_id", league_id)
        .execute()
    )
    votes_data = votes_res.data or []
    delete_votes = len(votes_data)
    delete_votes_needed = ceil(member_count / 2) if member_count else 1
    my_vote_for_delete = any(v["user_id"] == uid for v in votes_data)

    return ok({
        "league": _league_summary(league, member_count, my_role),
        "members": members,
        "pendingRequests": pending_requests,
        "deleteVotes": delete_votes,
        "deleteVotesNeeded": delete_votes_needed,
        "myVoteForDelete": my_vote_for_delete,
    })


@router.post("/{league_id}/join-request")
def request_to_join(
    league_id: str,
    user=Depends(get_current_user),
    db: Client = Depends(get_db),
):
    uid = user.id
    _get_league_or_404(league_id, db)

    if _get_my_role(league_id, uid, db):
        raise HTTPException(400, "Already a member")

    existing = (
        db.table("league_join_requests")
        .select("status")
        .eq("league_id", league_id)
        .eq("user_id", uid)
        .execute()
    )
    if existing.data and existing.data[0]["status"] == "PENDING":
        raise HTTPException(400, "Join request already pending")

    db.table("league_join_requests").upsert({
        "league_id": league_id,
        "user_id": uid,
        "status": "PENDING",
        "requested_at": datetime.now(timezone.utc).isoformat(),
    }, on_conflict="league_id,user_id").execute()

    return ok(None, "Join request submitted")


@router.post("/{league_id}/join-requests/{user_id}/accept")
def accept_join_request(
    league_id: str,
    user_id: str,
    user=Depends(get_current_user),
    db: Client = Depends(get_db),
):
    _require_admin(league_id, user.id, db)

    req = (
        db.table("league_join_requests")
        .select("status")
        .eq("league_id", league_id)
        .eq("user_id", user_id)
        .eq("status", "PENDING")
        .execute()
    )
    if not req.data:
        raise HTTPException(404, "Pending request not found")

    db.table("league_join_requests").update({"status": "ACCEPTED"}).eq("league_id", league_id).eq("user_id", user_id).execute()
    db.table("league_members").upsert({
        "league_id": league_id,
        "user_id": user_id,
        "role": "MEMBER",
    }, on_conflict="league_id,user_id").execute()

    return ok(None, "Member added")


@router.delete("/{league_id}/join-requests/{user_id}")
def reject_join_request(
    league_id: str,
    user_id: str,
    user=Depends(get_current_user),
    db: Client = Depends(get_db),
):
    _require_admin(league_id, user.id, db)
    db.table("league_join_requests").update({"status": "REJECTED"}).eq("league_id", league_id).eq("user_id", user_id).execute()
    return ok(None, "Request rejected")


@router.delete("/{league_id}/members/{user_id}")
def remove_member(
    league_id: str,
    user_id: str,
    user=Depends(get_current_user),
    db: Client = Depends(get_db),
):
    _require_admin(league_id, user.id, db)

    target_role = _get_my_role(league_id, user_id, db)
    if not target_role:
        raise HTTPException(404, "Member not found")
    if target_role == "CREATOR":
        raise HTTPException(400, "Cannot remove the creator")

    db.table("league_members").delete().eq("league_id", league_id).eq("user_id", user_id).execute()
    return ok(None, "Member removed")


@router.post("/{league_id}/members/{user_id}/promote")
def promote_member(
    league_id: str,
    user_id: str,
    user=Depends(get_current_user),
    db: Client = Depends(get_db),
):
    uid = user.id
    my_role = _get_my_role(league_id, uid, db)
    if my_role != "CREATOR":
        raise HTTPException(403, "Only the creator can promote members")

    target_role = _get_my_role(league_id, user_id, db)
    if not target_role:
        raise HTTPException(404, "Member not found")
    if target_role == "CREATOR":
        raise HTTPException(400, "Cannot promote the creator")

    db.table("league_members").update({"role": "LEADER"}).eq("league_id", league_id).eq("user_id", user_id).execute()
    return ok(None, "Promoted to LEADER")


@router.post("/{league_id}/leave")
def leave_league(
    league_id: str,
    user=Depends(get_current_user),
    db: Client = Depends(get_db),
):
    uid = user.id
    role = _get_my_role(league_id, uid, db)
    if not role:
        raise HTTPException(400, "Not a member")
    if role == "CREATOR":
        raise HTTPException(400, "Creator cannot leave — delete the league instead")

    db.table("league_members").delete().eq("league_id", league_id).eq("user_id", uid).execute()
    return ok(None, "Left league")


@router.delete("/{league_id}")
def delete_league(
    league_id: str,
    user=Depends(get_current_user),
    db: Client = Depends(get_db),
):
    uid = user.id
    league = _get_league_or_404(league_id, db)
    if league["creator_id"] != uid:
        raise HTTPException(403, "Only the creator can delete the league")

    db.table("leagues").delete().eq("id", league_id).execute()
    return ok(None, "League deleted")


@router.post("/{league_id}/vote-delete")
def vote_delete(
    league_id: str,
    user=Depends(get_current_user),
    db: Client = Depends(get_db),
):
    uid = user.id
    if not _get_my_role(league_id, uid, db):
        raise HTTPException(403, "Not a member")

    db.table("league_delete_votes").upsert({
        "league_id": league_id,
        "user_id": uid,
        "voted_at": datetime.now(timezone.utc).isoformat(),
    }).execute()

    votes_res = db.table("league_delete_votes").select("user_id").eq("league_id", league_id).execute()
    vote_count = len(votes_res.data or [])
    member_count = _member_count(league_id, db)
    needed = ceil(member_count / 2) if member_count else 1

    if vote_count >= needed:
        db.table("leagues").delete().eq("id", league_id).execute()
        return ok({"deleted": True, "votes": vote_count, "needed": needed}, "League deleted by vote")

    return ok({"deleted": False, "votes": vote_count, "needed": needed}, f"Vote recorded ({vote_count}/{needed})")
