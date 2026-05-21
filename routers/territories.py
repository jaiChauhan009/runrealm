from fastapi import APIRouter, Depends, HTTPException
from supabase import Client

from auth import get_current_user
from database import get_db
from schemas import ok
from utils import xp_calculator as xp
from utils.geo_utils import within_radius

router = APIRouter()


def _award_xp(db: Client, uid: str, amount: int, ref_id: str):
    db.table("xp_transactions").insert({
        "user_id": uid,
        "amount": amount,
        "transaction_type": "TERRITORY_CAPTURE",
        "reference_id": ref_id,
        "description": "Territory captured",
    }).execute()
    from utils import xp_calculator as xpc
    profile = db.table("user_profiles").select("xp_points").eq("user_id", uid).single().execute().data or {}
    new_xp = (profile.get("xp_points") or 0) + amount
    db.table("user_profiles").update({"xp_points": new_xp, "level": xpc.level_from_xp(new_xp)}).eq("user_id", uid).execute()


@router.get("/nearby")
def nearby_territories(
    lat: float,
    lon: float,
    radiusKm: float = 5.0,
    user=Depends(get_current_user),
    db: Client = Depends(get_db),
):
    # Rough bounding box to cut DB scan, then filter precisely
    deg_per_km = 1 / 111.0
    delta = radiusKm * deg_per_km
    res = (
        db.table("territories")
        .select("*")
        .gte("center_lat", lat - delta)
        .lte("center_lat", lat + delta)
        .gte("center_lon", lon - delta)
        .lte("center_lon", lon + delta)
        .execute()
    )
    territories = [
        t for t in (res.data or [])
        if within_radius(lat, lon, t["center_lat"], t["center_lon"], radiusKm)
    ]
    return ok(territories)


@router.post("/{territory_id}/capture")
def capture_territory(
    territory_id: str,
    sessionId: str | None = None,
    user=Depends(get_current_user),
    db: Client = Depends(get_db),
):
    uid = user.id

    territory = db.table("territories").select("*").eq("id", territory_id).execute()
    if not territory.data:
        raise HTTPException(400, "Territory not found")
    t = territory.data[0]

    previous_owner = t.get("captured_by")

    # Update territory ownership
    db.table("territories").update({
        "captured_by": uid,
        "capture_count": (t.get("capture_count") or 0) + 1,
    }).eq("id", territory_id).execute()

    # Record capture event
    cap_res = db.table("territory_captures").insert({
        "territory_id": territory_id,
        "user_id": uid,
        "session_id": sessionId,
        "previous_owner_id": previous_owner,
        "xp_earned": xp.for_territory(),
    }).execute()

    # Update profile territory count
    profile = db.table("user_profiles").select("territories_captured,territory_owned_sq_km").eq("user_id", uid).single().execute().data or {}
    db.table("user_profiles").update({
        "territories_captured": (profile.get("territories_captured") or 0) + 1,
        "territory_owned_sq_km": (profile.get("territory_owned_sq_km") or 0) + (t.get("area_sq_km") or 0),
    }).eq("user_id", uid).execute()

    cap_id = cap_res.data[0]["id"] if cap_res.data else territory_id
    _award_xp(db, uid, xp.for_territory(), cap_id)

    # Activity feed
    db.table("activity_feed").insert({
        "user_id": uid,
        "activity_type": "TERRITORY_CAPTURED",
        "reference_id": territory_id,
        "message": f"Captured {t.get('name', 'a territory')}!",
        "is_public": True,
    }).execute()

    return ok({
        "id": cap_id,
        "territory": {"id": territory_id, "name": t.get("name")},
        "user": {"id": uid},
        "previousOwnerId": previous_owner,
        "xpEarned": xp.for_territory(),
    }, "Territory captured!")


@router.get("/mine")
def my_territories(
    page: int = 0,
    size: int = 20,
    user=Depends(get_current_user),
    db: Client = Depends(get_db),
):
    uid = user.id
    start = page * size
    res = (
        db.table("territories")
        .select("*", count="exact")
        .eq("captured_by", uid)
        .range(start, start + size - 1)
        .execute()
    )
    total = res.count or 0
    return ok({
        "content": res.data or [],
        "totalElements": total,
        "totalPages": -(-total // size),
        "number": page,
        "size": size,
    })
