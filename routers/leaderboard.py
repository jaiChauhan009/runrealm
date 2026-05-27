from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from supabase import Client

from auth import get_current_user
from cache import cache_get, cache_set
from database import get_db
from schemas import ok

router = APIRouter()


def _resolve_scope(
    scope: str,
    league_id: Optional[str],
    uid: str,
    db: Client,
) -> Optional[list[str]]:
    """
    Returns a list of user_ids to filter to, or None meaning no filter (all users).
    - GLOBAL → None
    - LOCAL/STATE/COUNTRY → users sharing the calling user's city
    - LEAGUE → members of league_id
    """
    scope = scope.upper()
    if scope == "GLOBAL":
        return None
    if scope == "LEAGUE":
        if not league_id:
            raise HTTPException(400, "league_id required for LEAGUE scope")
        members = (
            db.table("league_members")
            .select("user_id")
            .eq("league_id", league_id)
            .execute()
        )
        return [m["user_id"] for m in (members.data or [])]
    # Geographic scopes — match by city (STATE/COUNTRY fall back to city until richer geo data exists)
    my_profile = (
        db.table("user_profiles")
        .select("city")
        .eq("user_id", uid)
        .execute()
    )
    city = (my_profile.data or [{}])[0].get("city") if my_profile.data else None
    if not city:
        return None  # No city on profile — degrade to global
    matched = (
        db.table("user_profiles")
        .select("user_id")
        .eq("city", city)
        .limit(500)
        .execute()
    )
    return [p["user_id"] for p in (matched.data or [])]


@router.get("")
def leaderboard(
    type: str = "xp",
    top: int = 50,
    scope: str = "GLOBAL",
    league_id: Optional[str] = None,
    user=Depends(get_current_user),
    db: Client = Depends(get_db),
):
    if type not in ("xp", "distance"):
        raise HTTPException(400, "type must be 'xp' or 'distance'")

    top = min(top, 100)
    # GLOBAL and LEAGUE scopes are user-independent; LOCAL/STATE/COUNTRY depend on
    # the caller's city so the key must include their uid to avoid cross-user cache hits.
    scope_upper = scope.upper()
    scope_key = "" if scope_upper in ("GLOBAL", "LEAGUE") else user.id
    cache_key = f"leaderboard:{scope_upper}:{league_id or ''}:{type}:{top}:{scope_key}"
    cached = cache_get(cache_key)
    if cached:
        return cached

    scope_ids = _resolve_scope(scope_upper, league_id, user.id, db)

    if type == "xp":
        query = (
            db.table("user_profiles")
            .select("user_id, username, display_name, avatar_url, level, xp_points")
            .order("xp_points", desc=True)
            .limit(top)
        )
        if scope_ids is not None:
            if not scope_ids:
                return ok([])
            query = query.in_("user_id", scope_ids)
        res = query.execute()
        entries = [
            {
                "rank": i + 1,
                "userId": r["user_id"],
                "username": r["username"],
                "displayName": r.get("display_name"),
                "avatarUrl": r.get("avatar_url"),
                "level": r.get("level", 1),
                "score": r["xp_points"] or 0,
            }
            for i, r in enumerate(res.data or [])
        ]

    else:  # distance — DB-side SUM via RPC (see migrations/add_distance_leaderboard_fn.sql)
        if scope_ids is not None and not scope_ids:
            return ok([])

        params: dict = {"scope_ids": scope_ids}  # None → no filter (global)
        res = db.rpc("get_distance_leaderboard", params).execute()

        rows = (res.data or [])[:top]

        if rows:
            top_uids = [r["user_id"] for r in rows]
            profiles = (
                db.table("user_profiles")
                .select("user_id, username, display_name, avatar_url, level")
                .in_("user_id", top_uids)
                .execute()
            )
            profile_map = {p["user_id"]: p for p in (profiles.data or [])}
        else:
            profile_map = {}

        entries = [
            {
                "rank": i + 1,
                "userId": r["user_id"],
                "username": profile_map.get(r["user_id"], {}).get("username", "Unknown"),
                "displayName": profile_map.get(r["user_id"], {}).get("display_name"),
                "avatarUrl": profile_map.get(r["user_id"], {}).get("avatar_url"),
                "level": profile_map.get(r["user_id"], {}).get("level", 1),
                "score": round(r.get("total_km") or 0, 2),
            }
            for i, r in enumerate(rows)
        ]

    result = ok(entries)
    # XP changes on every session; distance changes less often — cache longer
    ttl = 120 if type == "xp" else 300
    cache_set(cache_key, result, ttl_seconds=ttl)
    return result
