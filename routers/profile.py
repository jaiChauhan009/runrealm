from fastapi import APIRouter, Depends, HTTPException
from supabase import Client

from auth import get_current_user
from database import get_db
from schemas import ProfileUpdateRequest, ok
from utils import xp_calculator as xp

router = APIRouter()


def _total_distance(user_id: str, db: Client) -> float:
    res = (
        db.table("run_sessions")
        .select("distance_km")
        .eq("user_id", user_id)
        .eq("status", "COMPLETED")
        .execute()
    )
    return round(sum((r.get("distance_km") or 0) for r in (res.data or [])), 2)


def _build_profile(profile: dict, total_distance_km: float = 0.0) -> dict:
    total_xp = profile.get("xp_points", 0) or 0
    return {
        "id": profile.get("id"),
        "userId": profile.get("user_id"),
        "username": profile.get("username"),
        "level": xp.level_from_xp(total_xp),
        "xpPoints": total_xp,
        "displayName": profile.get("display_name"),
        "avatarUrl": profile.get("avatar_url"),
        "bio": profile.get("bio"),
        "city": profile.get("city"),
        "totalRuns": profile.get("total_runs", 0),
        "totalCalories": profile.get("total_calories", 0),
        "totalDistanceKm": total_distance_km,
        "currentStreak": profile.get("current_streak", 0),
        "bestStreak": profile.get("best_streak", 0),
        "territoryOwnedSqKm": profile.get("territory_owned_sq_km", 0),
        "territoriesCaptured": profile.get("territories_captured", 0),
        "isPublic": profile.get("is_public", True),
        "updatedAt": profile.get("updated_at"),
        "socialLinks": {
            "instagram": profile.get("instagram_handle"),
            "twitter": profile.get("twitter_handle"),
            "strava": profile.get("strava_url"),
            "linkedin": profile.get("linkedin_url"),
        },
    }


@router.get("")
def my_profile(user=Depends(get_current_user), db: Client = Depends(get_db)):
    uid = user.id
    res = db.table("user_profiles").select("*").eq("user_id", uid).single().execute()
    if not res.data:
        raise HTTPException(400, "Profile not found")
    return ok(_build_profile(res.data, _total_distance(uid, db)))


@router.get("/{user_id}")
def public_profile(user_id: str, user=Depends(get_current_user), db: Client = Depends(get_db)):
    res = db.table("user_profiles").select("*").eq("user_id", user_id).single().execute()
    if not res.data:
        raise HTTPException(400, "Profile not found")
    p = res.data
    if not p.get("is_public", True) and p.get("user_id") != user.id:
        raise HTTPException(400, "Profile is private")
    return ok(_build_profile(p, _total_distance(user_id, db)))


@router.patch("")
def update_profile(
    body: ProfileUpdateRequest,
    user=Depends(get_current_user),
    db: Client = Depends(get_db),
):
    uid = user.id
    update: dict = {}
    if body.displayName is not None:
        update["display_name"] = body.displayName
    if body.bio is not None:
        update["bio"] = body.bio
    if body.city is not None:
        update["city"] = body.city
    if body.avatarUrl is not None:
        update["avatar_url"] = body.avatarUrl
    if body.isPublic is not None:
        update["is_public"] = body.isPublic
    if body.instagramHandle is not None:
        update["instagram_handle"] = body.instagramHandle
    if body.twitterHandle is not None:
        update["twitter_handle"] = body.twitterHandle
    if body.stravaUrl is not None:
        update["strava_url"] = body.stravaUrl
    if body.linkedinUrl is not None:
        update["linkedin_url"] = body.linkedinUrl

    if not update:
        res = db.table("user_profiles").select("*").eq("user_id", uid).single().execute()
        return ok(_build_profile(res.data))

    res = db.table("user_profiles").update(update).eq("user_id", uid).execute()
    if not res.data:
        raise HTTPException(400, "Profile not found")
    return ok(_build_profile(res.data[0]))
