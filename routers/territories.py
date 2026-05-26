"""
Territory router — geo-based loop capture game.

Technologies: Shapely (pure Python geometry), haversine, bounding-box DB queries.
No PostGIS, no Redis, no WebSockets required.

Claim flow:
  1. Client finishes a run session → calls POST /territories/claim with sessionId
  2. Server loads route_points, validates loop closure, runs anti-cheat
  3. Builds Shapely polygon, checks area + self-intersection
  4. Finds overlapping ACTIVE territories (bbox pre-filter + Shapely intersects)
  5. Creates new territory, supersedes overlapping ones, awards XP
"""
import json
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from supabase import Client

from auth import get_current_user
from cache import cache_invalidate
from database import get_db
from schemas import TerritoryClaimRequest, ok
from utils import xp_calculator as xp
from utils.geo_utils import (
    build_polygon,
    geojson_to_polygon,
    haversine_km,
    haversine_m,
    polygon_area_sq_km,
    polygon_bbox,
    polygon_perimeter_km,
    polygon_to_geojson,
    route_is_simple,
    route_total_distance_m,
    within_radius,
)

router = APIRouter()


# ── Validation config ──────────────────────────────────────────────────────────

LOOP_CLOSE_THRESHOLD_M = 50.0   # endpoint must be within 50 m of start
LOOP_GAP_RATIO_MAX     = 0.15   # gap < 15 % of total route distance
MIN_ROUTE_POINTS       = 20     # minimum GPS points
MIN_DISTANCE_M         = 200.0  # minimum total run distance
MIN_AREA_SQ_M          = 500.0  # minimum polygon area (≈ 22 m × 22 m)
MAX_SPEED_KMH          = 35.0   # sprinter pace — above this = suspected vehicle
TELEPORT_DIST_M        = 200.0  # suspicious instant jump
TELEPORT_TIME_S        = 3.0    # time window for that jump


# ── Validation helpers ─────────────────────────────────────────────────────────

def _validate_loop(points: list[dict]) -> tuple[bool, str]:
    """Check the route forms a closed loop."""
    first, last = points[0], points[-1]
    gap_m = haversine_m(
        first["latitude"], first["longitude"],
        last["latitude"], last["longitude"],
    )
    total_m = route_total_distance_m(points)

    if gap_m > LOOP_CLOSE_THRESHOLD_M:
        return False, f"Loop not closed: endpoint is {gap_m:.0f} m from start (need < {LOOP_CLOSE_THRESHOLD_M:.0f} m)"

    if total_m > 0 and (gap_m / total_m) > LOOP_GAP_RATIO_MAX:
        return False, (
            f"Gap too large vs route: {gap_m / total_m:.0%} "
            f"(max {LOOP_GAP_RATIO_MAX:.0%})"
        )

    return True, "ok"


def _validate_anticheat(points: list[dict]) -> tuple[bool, str, float, dict]:
    """
    Speed-based anti-cheat.
    Returns (passed, reason, validation_score 0-1, stats_dict).
    """
    if len(points) < MIN_ROUTE_POINTS:
        return False, f"Too few GPS points: {len(points)} (need {MIN_ROUTE_POINTS})", 0.0, {}

    total_m = route_total_distance_m(points)
    if total_m < MIN_DISTANCE_M:
        return False, f"Run too short: {total_m:.0f} m (need {MIN_DISTANCE_M:.0f} m)", 0.0, {}

    speeds_kmh: list[float] = []
    penalty_flags = 0

    for i in range(1, len(points)):
        p0, p1 = points[i - 1], points[i]
        dist_m = haversine_m(p0["latitude"], p0["longitude"], p1["latitude"], p1["longitude"])

        try:
            t0 = datetime.fromisoformat(p0["recorded_at"].replace("Z", "+00:00"))
            t1 = datetime.fromisoformat(p1["recorded_at"].replace("Z", "+00:00"))
            dt_s = max((t1 - t0).total_seconds(), 0.1)
        except Exception:
            dt_s = 2.0  # fallback if timestamp missing

        speed_kmh = (dist_m / dt_s) * 3.6
        speeds_kmh.append(speed_kmh)

        # Teleportation: big jump in very short time
        if dist_m > TELEPORT_DIST_M and dt_s < TELEPORT_TIME_S:
            penalty_flags += 3
        elif speed_kmh > MAX_SPEED_KMH:
            penalty_flags += 1

    if not speeds_kmh:
        return False, "No valid GPS segments", 0.0, {}

    avg_kmh = sum(speeds_kmh) / len(speeds_kmh)
    max_kmh = max(speeds_kmh)
    fast_ratio = sum(1 for s in speeds_kmh if s > MAX_SPEED_KMH) / len(speeds_kmh)

    if fast_ratio > 0.50:
        return False, (
            f"Vehicle movement detected: {fast_ratio:.0%} of segments exceed "
            f"{MAX_SPEED_KMH:.0f} km/h"
        ), 0.0, {"avgSpeedKmh": avg_kmh, "maxSpeedKmh": max_kmh}

    # Score: start at 1.0, subtract penalty fraction
    score = max(0.0, 1.0 - (penalty_flags / max(len(points), 1)) * 0.5)
    if score < 0.30:
        return False, "Too many speed anomalies — possible GPS spoofing", score, {
            "avgSpeedKmh": avg_kmh, "maxSpeedKmh": max_kmh,
        }

    return True, "ok", score, {
        "avgSpeedKmh": round(avg_kmh, 2),
        "maxSpeedKmh": round(max_kmh, 2),
        "totalDistanceM": round(total_m, 1),
        "pointCount": len(points),
    }


# ── XP helper ─────────────────────────────────────────────────────────────────

def _territory_xp(area_sq_km: float, rivals_captured: int) -> int:
    base = xp.for_territory()           # 50 XP
    area_bonus = int(area_sq_km * 10)   # +10 XP per 0.1 km²
    rival_bonus = rivals_captured * 25  # +25 XP per rival territory taken
    return base + area_bonus + rival_bonus


def _award_xp(db: Client, uid: str, amount: int, ref_id: str, desc: str):
    db.table("xp_transactions").insert({
        "user_id": uid,
        "amount": amount,
        "transaction_type": "TERRITORY_CAPTURE",
        "reference_id": ref_id,
        "description": desc,
    }).execute()
    profile = (
        db.table("user_profiles")
        .select("xp_points")
        .eq("user_id", uid)
        .single()
        .execute()
        .data or {}
    )
    new_xp = (profile.get("xp_points") or 0) + amount
    db.table("user_profiles").update({
        "xp_points": new_xp,
        "level": xp.level_from_xp(new_xp),
    }).eq("user_id", uid).execute()


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.post("/claim")
def claim_territory(
    body: TerritoryClaimRequest,
    user=Depends(get_current_user),
    db: Client = Depends(get_db),
):
    """
    Validate a completed run session and create a polygon territory if the route
    forms a valid closed loop. All geometry and anti-cheat logic is server-side.
    """
    uid = user.id

    # 1. Verify session ownership and completion
    sess_res = (
        db.table("run_sessions")
        .select("id, user_id, status, distance_km")
        .eq("id", body.sessionId)
        .eq("user_id", uid)
        .execute()
    )
    if not sess_res.data:
        raise HTTPException(400, "Session not found")
    if sess_res.data[0].get("status") != "COMPLETED":
        raise HTTPException(400, "Session is not completed yet")

    # 2. Idempotency — reject duplicate claims for the same session
    dup = (
        db.table("territories")
        .select("id")
        .eq("session_id", body.sessionId)
        .execute()
    )
    if dup.data:
        raise HTTPException(400, "Territory already claimed for this session")

    # 3. Load ordered GPS route points
    pts_res = (
        db.table("route_points")
        .select("latitude, longitude, recorded_at, sequence_number")
        .eq("session_id", body.sessionId)
        .order("sequence_number")
        .execute()
    )
    points = pts_res.data or []

    if len(points) < MIN_ROUTE_POINTS:
        raise HTTPException(422, f"Not enough GPS points: {len(points)} (need {MIN_ROUTE_POINTS})")

    # 4. Loop closure check
    loop_ok, loop_reason = _validate_loop(points)
    if not loop_ok:
        raise HTTPException(422, f"Route is not a closed loop: {loop_reason}")

    # 5. Anti-cheat
    ac_ok, ac_reason, score, stats = _validate_anticheat(points)
    if not ac_ok:
        raise HTTPException(422, f"Anti-cheat validation failed: {ac_reason}")

    # 6. Self-intersection check (figure-8 loops are invalid)
    if not route_is_simple(points):
        raise HTTPException(422, "Route self-intersects — run a single clean loop")

    # 7. Build Shapely polygon
    poly = build_polygon(points)
    if poly is None:
        raise HTTPException(422, "Could not build a valid polygon from the route")

    # 8. Area check
    area_sq_km = polygon_area_sq_km(poly)
    area_sq_m = area_sq_km * 1_000_000
    if area_sq_m < MIN_AREA_SQ_M:
        raise HTTPException(
            422,
            f"Polygon too small: {area_sq_m:.0f} m² (need at least {MIN_AREA_SQ_M:.0f} m²)",
        )

    perimeter_km = polygon_perimeter_km(poly)
    min_lat, max_lat, min_lon, max_lon = polygon_bbox(poly)
    center_lat = poly.centroid.y
    center_lon = poly.centroid.x
    geojson_str = polygon_to_geojson(poly)

    # 9. Find overlapping ACTIVE territories (bounding-box pre-filter, then Shapely)
    bbox_res = (
        db.table("territories")
        .select("id, captured_by, area_sq_km, boundary_geo_json, min_lat, max_lat, min_lon, max_lon")
        .eq("status", "ACTIVE")
        .lte("min_lat", max_lat)
        .gte("max_lat", min_lat)
        .lte("min_lon", max_lon)
        .gte("max_lon", min_lon)
        .execute()
    )
    overlapping: list[dict] = []
    for t in (bbox_res.data or []):
        raw = t.get("boundary_geo_json")
        if not raw:
            continue
        try:
            geom = json.loads(raw) if isinstance(raw, str) else raw
            existing = geojson_to_polygon(geom)
            if existing and poly.intersects(existing):
                inter_area = polygon_area_sq_km(poly.intersection(existing))
                if inter_area > 0.0001:   # overlap > 100 m²
                    overlapping.append(t)
        except Exception:
            continue

    # 10. Insert new territory
    point_value = max(50, int(area_sq_m / 100))  # 1 pt per 100 m², min 50
    insert_res = db.table("territories").insert({
        "captured_by": uid,
        "session_id": body.sessionId,
        "name": f"Territory",
        "boundary_geo_json": geojson_str,
        "center_lat": round(center_lat, 6),
        "center_lon": round(center_lon, 6),
        "min_lat": round(min_lat, 6),
        "max_lat": round(max_lat, 6),
        "min_lon": round(min_lon, 6),
        "max_lon": round(max_lon, 6),
        "area_sq_km": round(area_sq_km, 6),
        "perimeter_km": round(perimeter_km, 4),
        "capture_count": 1,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "status": "ACTIVE",
        "avg_speed_kmh": stats.get("avgSpeedKmh"),
        "max_speed_kmh": stats.get("maxSpeedKmh"),
        "point_count": len(points),
        "validation_score": round(score, 3),
        "point_value": point_value,
    }).execute()

    if not insert_res.data:
        raise HTTPException(500, "Failed to create territory — please try again")

    new_territory = insert_res.data[0]
    territory_id = new_territory["id"]

    # 11. Supersede overlapping territories and update their owners' stats
    # Pre-compute per-rival area loss so we can batch the profile READ (1 query instead of N).
    rival_area_lost: dict[str, float] = {}
    for t in overlapping:
        prev_uid = t.get("captured_by")
        if prev_uid and prev_uid != uid:
            rival_area_lost[prev_uid] = rival_area_lost.get(prev_uid, 0) + (t.get("area_sq_km") or 0)

    # Batch-fetch all rival profiles in a single query
    rival_profile_map: dict[str, dict] = {}
    if rival_area_lost:
        rp_res = (
            db.table("user_profiles")
            .select("user_id, territory_owned_sq_km")
            .in_("user_id", list(rival_area_lost.keys()))
            .execute()
        )
        rival_profile_map = {p["user_id"]: p for p in (rp_res.data or [])}

    rivals_captured = len(rival_area_lost)

    # Update each overlapping territory (status + capture_count differ per row — must be individual)
    for t in overlapping:
        db.table("territories").update({
            "status": "SUPERSEDED",
            "capture_count": (t.get("capture_count") or 0) + 1,
        }).eq("id", t["id"]).execute()

    # Update each rival's profile area (values differ per rival — must be individual)
    for rival_uid, lost_area in rival_area_lost.items():
        prev = rival_profile_map.get(rival_uid, {})
        new_area = max(0.0, (prev.get("territory_owned_sq_km") or 0) - lost_area)
        db.table("user_profiles").update({
            "territory_owned_sq_km": round(new_area, 4),
        }).eq("user_id", rival_uid).execute()
        cache_invalidate(f"dashboard:{rival_uid}")

    # 12. Update claimant's profile stats
    my_profile = (
        db.table("user_profiles")
        .select("territories_captured, territory_owned_sq_km")
        .eq("user_id", uid)
        .single()
        .execute()
        .data or {}
    )
    db.table("user_profiles").update({
        "territories_captured": (my_profile.get("territories_captured") or 0) + 1,
        "territory_owned_sq_km": round(
            (my_profile.get("territory_owned_sq_km") or 0) + area_sq_km, 4
        ),
    }).eq("user_id", uid).execute()

    # 13. Award XP
    xp_earned = _territory_xp(area_sq_km, rivals_captured)
    _award_xp(db, uid, xp_earned, territory_id,
              f"Claimed {area_sq_m:.0f} m² territory loop")

    # 14. Activity feed
    db.table("activity_feed").insert({
        "user_id": uid,
        "activity_type": "TERRITORY_CAPTURED",
        "reference_id": territory_id,
        "message": f"Claimed {area_sq_m:.0f} m² of territory!",
        "metadata_json": json.dumps({
            "areaSqKm": area_sq_km,
            "perimeterKm": perimeter_km,
            "rivalsCapured": rivals_captured,
            "xpEarned": xp_earned,
        }),
        "is_public": True,
    }).execute()

    # 15. Invalidate dashboard cache
    cache_invalidate(f"dashboard:{uid}")

    suffix = (
        f" Captured {rivals_captured} rival territor{'ies' if rivals_captured > 1 else 'y'}!"
        if rivals_captured > 0 else ""
    )
    return ok({
        "territory": {
            "id": territory_id,
            "areaSqKm": round(area_sq_km, 6),
            "areaSqM": round(area_sq_m, 0),
            "perimeterKm": round(perimeter_km, 3),
            "centerLat": round(center_lat, 6),
            "centerLon": round(center_lon, 6),
            "capturedAt": new_territory.get("captured_at"),
            "validationScore": round(score, 3),
            "pointValue": point_value,
        },
        "xpEarned": xp_earned,
        "territoriesCaptured": rivals_captured,
        "message": f"Territory claimed! {area_sq_m:.0f} m²" + suffix,
    }, "Territory claimed!")


@router.get("/nearby")
def nearby_territories(
    lat: float,
    lon: float,
    radiusKm: float = 5.0,
    user=Depends(get_current_user),
    db: Client = Depends(get_db),
):
    """Territories near a GPS coordinate, enriched with owner data."""
    radius = min(radiusKm, 50.0)
    deg = radius / 111.0
    res = (
        db.table("territories")
        .select("*")
        .gte("center_lat", lat - deg).lte("center_lat", lat + deg)
        .gte("center_lon", lon - deg).lte("center_lon", lon + deg)
        .execute()
    )
    territories = [
        t for t in (res.data or [])
        if within_radius(lat, lon, t["center_lat"], t["center_lon"], radius)
    ]
    return ok(territories)


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
        .eq("status", "ACTIVE")
        .order("captured_at", desc=True)
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


@router.post("/{territory_id}/capture")
def capture_territory(
    territory_id: str,
    sessionId: str | None = None,
    user=Depends(get_current_user),
    db: Client = Depends(get_db),
):
    """Legacy manual-capture endpoint — kept for backward compatibility."""
    uid = user.id

    territory = db.table("territories").select("*").eq("id", territory_id).execute()
    if not territory.data:
        raise HTTPException(400, "Territory not found")
    t = territory.data[0]
    previous_owner = t.get("captured_by")

    db.table("territories").update({
        "captured_by": uid,
        "capture_count": (t.get("capture_count") or 0) + 1,
    }).eq("id", territory_id).execute()

    cap_res = db.table("territory_captures").insert({
        "territory_id": territory_id,
        "user_id": uid,
        "session_id": sessionId,
        "previous_owner_id": previous_owner,
        "xp_earned": xp.for_territory(),
    }).execute()

    profile = (
        db.table("user_profiles")
        .select("territories_captured, territory_owned_sq_km")
        .eq("user_id", uid)
        .single()
        .execute()
        .data or {}
    )
    db.table("user_profiles").update({
        "territories_captured": (profile.get("territories_captured") or 0) + 1,
        "territory_owned_sq_km": (profile.get("territory_owned_sq_km") or 0) + (t.get("area_sq_km") or 0),
    }).eq("user_id", uid).execute()

    cap_id = cap_res.data[0]["id"] if cap_res.data else territory_id
    _award_xp(db, uid, xp.for_territory(), cap_id, "Territory captured")

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
        "previousOwnerId": previous_owner,
        "xpEarned": xp.for_territory(),
    }, "Territory captured!")
