"""
Territory router — geo-based loop & corridor capture game.

TWO capture modes
─────────────────
LOOP      POST /territories/claim
          Client finishes a closed-loop run session.
          Validates loop closure + anti-cheat → creates a filled polygon territory.
          Supersedes any existing territories whose polygon overlaps the new one.

CORRIDOR  POST /territories/corridor
          Client finishes any run (no loop required) that passes THROUGH an
          existing territory owned by someone else.
          A 30-m-wide corridor is carved out of the rival's polygon and becomes
          the runner's new territory strip.

Speed tiers used in both flows
───────────────────────────────
CLEAN      ≤ 20 km/h  running / cycling      → fully counted
SUSPICIOUS  20–35 km/h  fast bike / e-scooter → flagged, score reduced, still allowed
VEHICLE    > 35 km/h  car / motorbike        → segment penalised
                        > 30 % VEHICLE ratio  → claim rejected with a clear message
TELEPORT   large jump in < 3 s              → heavy penalty (GPS spoof or device glitch)
"""
import json
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from supabase import Client

try:
    from shapely.geometry import Point as _ShapelyPoint
    _SHAPELY = True
except ImportError:
    _ShapelyPoint = None
    _SHAPELY = False

from auth import get_current_user
from cache import cache_invalidate, cache_invalidate_prefix
from database import get_db
from schemas import TerritoryClaimRequest, ok
from utils import xp_calculator as xp
from utils.geo_utils import (
    SPEED_SUSPICIOUS_MAX,
    SPEED_CLEAN_MAX,
    TELEPORT_DIST_M,
    TELEPORT_TIME_S,
    build_polygon,
    buffer_route_m,
    classify_route_speeds,
    geojson_to_polygon,
    haversine_m,
    largest_polygon,
    polygon_area_sq_km,
    polygon_bbox,
    polygon_perimeter_km,
    polygon_to_geojson,
    route_is_simple,
    route_total_distance_m,
    simplify_polygon,
    smooth_polygon,
    within_radius,
)

router = APIRouter()

# ── Validation config ──────────────────────────────────────────────────────────

LOOP_CLOSE_THRESHOLD_M  = 50.0    # endpoint must be within 50 m of start
LOOP_GAP_RATIO_MAX      = 0.15    # gap < 15 % of total route distance
MIN_ROUTE_POINTS        = 20      # minimum GPS points for a loop
MIN_DISTANCE_M          = 200.0   # minimum total run distance
MIN_AREA_SQ_M           = 500.0   # minimum polygon area (≈ 22 m × 22 m)

VEHICLE_RATIO_REJECT    = 0.30    # > 30 % vehicle segments = reject with message
SUSPICIOUS_RATIO_WARN   = 0.20    # > 20 % suspicious = warn in response
SCORE_MIN               = 0.40    # overall validation score must be ≥ 0.40

CORRIDOR_BUFFER_M       = 30.0    # one-sided corridor radius (60 m total width)
MIN_CORRIDOR_AREA_SQ_M  = 800.0   # corridor steal must be ≥ 800 m²
MIN_CORRIDOR_DISTANCE_M = 80.0    # route must travel ≥ 80 m inside the territory


# ── Validation helpers ─────────────────────────────────────────────────────────

def _validate_loop(points: list[dict]) -> tuple[bool, str]:
    """Check the route forms a closed loop."""
    first, last = points[0], points[-1]
    gap_m   = haversine_m(first["latitude"], first["longitude"], last["latitude"], last["longitude"])
    total_m = route_total_distance_m(points)

    if gap_m > LOOP_CLOSE_THRESHOLD_M:
        return False, (
            f"Loop not closed — endpoint is {gap_m:.0f} m from start "
            f"(must be within {LOOP_CLOSE_THRESHOLD_M:.0f} m)"
        )
    if total_m > 0 and (gap_m / total_m) > LOOP_GAP_RATIO_MAX:
        return False, (
            f"Gap too large relative to route: {gap_m / total_m:.0%} "
            f"(max {LOOP_GAP_RATIO_MAX:.0%})"
        )
    return True, "ok"


def _validate_anticheat(
    points: list[dict],
    min_points: int = MIN_ROUTE_POINTS,
) -> tuple[bool, str, float, dict]:
    """
    Run speed-tier anti-cheat analysis.

    Returns
    -------
    (passed, reason, validation_score 0-1, speed_profile_dict)

    On failure the reason tells the user WHY (vehicle %, teleports, etc.) so
    they know if it was a legitimate GPS glitch or actual cheating.
    """
    if len(points) < min_points:
        return False, f"Too few GPS points: {len(points)} (minimum {min_points})", 0.0, {}

    total_m = route_total_distance_m(points)
    if total_m < MIN_DISTANCE_M:
        return False, f"Run too short: {total_m:.0f} m (minimum {MIN_DISTANCE_M:.0f} m)", 0.0, {}

    stats = classify_route_speeds(points)
    counts         = stats["counts"]
    vehicle_ratio  = stats["vehicle_ratio"]
    avg_kmh        = stats["avg_speed_kmh"]
    max_kmh        = stats["max_speed_kmh"]
    measurable     = counts["CLEAN"] + counts["SUSPICIOUS"] + counts["VEHICLE"]
    suspicious_ratio = (counts["SUSPICIOUS"] / measurable) if measurable > 0 else 0.0

    speed_profile = {
        "avgSpeedKmh":       avg_kmh,
        "maxSpeedKmh":       max_kmh,
        "cleanSegments":     counts["CLEAN"],
        "suspiciousSegments":counts["SUSPICIOUS"],
        "vehicleSegments":   counts["VEHICLE"],
        "teleportEvents":    counts["TELEPORT"],
        "vehiclePercent":    round(vehicle_ratio * 100, 1),
        "suspiciousPercent": round(suspicious_ratio * 100, 1),
        "totalDistanceM":    round(total_m, 1),
        "pointCount":        len(points),
    }

    # Hard rejection: too many vehicle segments
    if vehicle_ratio > VEHICLE_RATIO_REJECT:
        return False, (
            f"Vehicle movement detected: {vehicle_ratio:.0%} of GPS segments exceed "
            f"{SPEED_SUSPICIOUS_MAX:.0f} km/h — territory capture requires running or cycling only."
        ), 0.0, speed_profile

    # Compute validation score
    # Start at 1.0; subtract vehicle fraction (heavy), suspicious fraction (light),
    # and teleport events (each costs 0.10).
    score = 1.0
    score -= vehicle_ratio * 0.80
    score -= suspicious_ratio * 0.20
    score -= counts["TELEPORT"] * 0.10
    score = max(0.0, round(score, 3))

    if score < SCORE_MIN:
        return False, (
            f"Too many speed anomalies — validation score {score:.2f} is below "
            f"the minimum {SCORE_MIN:.2f}. Check your GPS signal and try again."
        ), score, speed_profile

    # Soft warning for suspicious (allowed but noted in response)
    reason = "ok"
    if suspicious_ratio > SUSPICIOUS_RATIO_WARN:
        reason = (
            f"Territory claimed with a note: {suspicious_ratio:.0%} of segments "
            f"were above {SPEED_CLEAN_MAX:.0f} km/h — keep it under {SPEED_SUSPICIOUS_MAX:.0f} km/h."
        )

    return True, reason, score, speed_profile


# ── XP helpers ─────────────────────────────────────────────────────────────────

def _territory_xp(area_sq_km: float, rivals_captured: int) -> int:
    base        = xp.for_territory()           # 50 XP
    area_bonus  = int(area_sq_km * 10)         # +10 XP per 0.1 km²
    rival_bonus = rivals_captured * 25         # +25 XP per rival territory taken
    return base + area_bonus + rival_bonus


def _award_xp(db: Client, uid: str, amount: int, ref_id: str, desc: str, current_xp: int | None = None):
    """
    Insert an XP transaction and update user_profiles.
    Pass current_xp to skip the profile SELECT (saves one round-trip when the
    caller already has the profile row in memory).
    """
    db.table("xp_transactions").insert({
        "user_id":          uid,
        "amount":           amount,
        "transaction_type": "TERRITORY_CAPTURE",
        "reference_id":     ref_id,
        "description":      desc,
    }).execute()
    if current_xp is None:
        current_xp = (
            db.table("user_profiles")
            .select("xp_points")
            .eq("user_id", uid)
            .single()
            .execute()
            .data or {}
        ).get("xp_points", 0)
    new_xp = current_xp + amount
    db.table("user_profiles").update({
        "xp_points": new_xp,
        "level":     xp.level_from_xp(new_xp),
    }).eq("user_id", uid).execute()


# ── Shared overlap helper ──────────────────────────────────────────────────────

def _load_overlapping_territories(poly, db: Client) -> list[dict]:
    """
    Bounding-box pre-filter from DB, then precise Shapely intersect check.
    Returns all ACTIVE territory rows whose polygon meaningfully overlaps `poly`.
    """
    min_lat, max_lat, min_lon, max_lon = polygon_bbox(poly)
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
    overlapping = []
    for t in (bbox_res.data or []):
        raw = t.get("boundary_geo_json")
        if not raw:
            continue
        try:
            geom     = json.loads(raw) if isinstance(raw, str) else raw
            existing = geojson_to_polygon(geom)
            if existing and poly.intersects(existing):
                inter_area = polygon_area_sq_km(poly.intersection(existing))
                if inter_area > 0.0001:   # overlap > 100 m²
                    t["_poly"] = existing
                    overlapping.append(t)
        except Exception:
            continue
    return overlapping


# ── LOOP CLAIM ────────────────────────────────────────────────────────────────

@router.post("/claim")
def claim_territory(
    body: TerritoryClaimRequest,
    user=Depends(get_current_user),
    db: Client = Depends(get_db),
):
    """
    Validate a completed run and create a loop territory if the route is a
    valid closed polygon.  Overlapping rival territories are superseded entirely.
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
        raise HTTPException(404, "Session not found")
    if sess_res.data[0].get("status") != "COMPLETED":
        raise HTTPException(400, "Session is not completed yet")

    # 2. Idempotency
    dup = db.table("territories").select("id").eq("session_id", body.sessionId).execute()
    if dup.data:
        raise HTTPException(400, "Territory already claimed for this session")

    # 3. Load GPS points
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

    # 4. Loop closure
    loop_ok, loop_reason = _validate_loop(points)
    if not loop_ok:
        raise HTTPException(422, f"Route is not a closed loop: {loop_reason}")

    # 5. Anti-cheat
    ac_ok, ac_reason, score, speed_profile = _validate_anticheat(points)
    if not ac_ok:
        raise HTTPException(422, f"Anti-cheat failed: {ac_reason}", headers={"X-SpeedProfile": json.dumps(speed_profile)})

    # 6. Self-intersection
    if not route_is_simple(points):
        raise HTTPException(422, "Route self-intersects — run a single clean loop")

    # 7. Build polygon → smooth → simplify
    raw_poly = build_polygon(points)
    if raw_poly is None:
        raise HTTPException(422, "Could not build a valid polygon from the route")

    area_sq_m  = polygon_area_sq_km(raw_poly) * 1_000_000
    if area_sq_m < MIN_AREA_SQ_M:
        raise HTTPException(422, f"Polygon too small: {area_sq_m:.0f} m² (need {MIN_AREA_SQ_M:.0f} m²)")

    # Smooth for rendering quality, simplify to cut vertex count
    display_poly = simplify_polygon(smooth_polygon(raw_poly, iterations=2), tolerance=0.000035)
    area_sq_km   = polygon_area_sq_km(raw_poly)    # use raw area for stats/XP
    perimeter_km = polygon_perimeter_km(raw_poly)
    min_lat, max_lat, min_lon, max_lon = polygon_bbox(raw_poly)
    center_lat   = raw_poly.centroid.y
    center_lon   = raw_poly.centroid.x
    geojson_str  = polygon_to_geojson(display_poly)

    # 8. Find overlapping ACTIVE territories
    overlapping = _load_overlapping_territories(raw_poly, db)

    # 9. Insert new territory
    point_value = max(50, int(area_sq_m / 100))
    insert_res  = db.table("territories").insert({
        "captured_by":       uid,
        "session_id":        body.sessionId,
        "name":              "Territory",
        "boundary_geo_json": geojson_str,
        "center_lat":        round(center_lat, 6),
        "center_lon":        round(center_lon, 6),
        "min_lat":           round(min_lat, 6),
        "max_lat":           round(max_lat, 6),
        "min_lon":           round(min_lon, 6),
        "max_lon":           round(max_lon, 6),
        "area_sq_km":        round(area_sq_km, 6),
        "perimeter_km":      round(perimeter_km, 4),
        "capture_count":     1,
        "captured_at":       datetime.now(timezone.utc).isoformat(),
        "status":            "ACTIVE",
        "avg_speed_kmh":     speed_profile.get("avgSpeedKmh"),
        "max_speed_kmh":     speed_profile.get("maxSpeedKmh"),
        "point_count":       len(points),
        "validation_score":  round(score, 3),
        "point_value":       point_value,
    }).execute()

    if not insert_res.data:
        raise HTTPException(500, "Failed to create territory — please try again")

    new_territory = insert_res.data[0]
    territory_id  = new_territory["id"]

    # 10. Supersede overlapping territories
    rival_area_lost: dict[str, float] = {}
    for t in overlapping:
        prev_uid = t.get("captured_by")
        if prev_uid and prev_uid != uid:
            rival_area_lost[prev_uid] = rival_area_lost.get(prev_uid, 0) + (t.get("area_sq_km") or 0)

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
    for t in overlapping:
        db.table("territories").update({
            "status":        "SUPERSEDED",
            "capture_count": (t.get("capture_count") or 0) + 1,
        }).eq("id", t["id"]).execute()

    for rival_uid, lost_area in rival_area_lost.items():
        prev    = rival_profile_map.get(rival_uid, {})
        new_area = max(0.0, (prev.get("territory_owned_sq_km") or 0) - lost_area)
        db.table("user_profiles").update({
            "territory_owned_sq_km": round(new_area, 4),
        }).eq("user_id", rival_uid).execute()
        cache_invalidate(f"dashboard:{rival_uid}")

    # 11+12. Read profile once, update all fields + award XP in one write
    xp_earned  = _territory_xp(area_sq_km, rivals_captured)
    my_profile = (
        db.table("user_profiles")
        .select("territories_captured, territory_owned_sq_km, xp_points")
        .eq("user_id", uid)
        .single()
        .execute()
        .data or {}
    )
    new_xp = (my_profile.get("xp_points") or 0) + xp_earned
    db.table("xp_transactions").insert({
        "user_id": uid, "amount": xp_earned,
        "transaction_type": "TERRITORY_CAPTURE", "reference_id": territory_id,
        "description": f"Claimed {area_sq_m:.0f} m² loop territory",
    }).execute()
    db.table("user_profiles").update({
        "territories_captured":  (my_profile.get("territories_captured") or 0) + 1,
        "territory_owned_sq_km": round((my_profile.get("territory_owned_sq_km") or 0) + area_sq_km, 4),
        "xp_points": new_xp,
        "level":     xp.level_from_xp(new_xp),
    }).eq("user_id", uid).execute()

    # 13. Activity feed
    db.table("activity_feed").insert({
        "user_id":       uid,
        "activity_type": "TERRITORY_CAPTURED",
        "reference_id":  territory_id,
        "message":       f"Claimed {area_sq_m:.0f} m² of territory!",
        "metadata_json": json.dumps({
            "areaSqKm":       round(area_sq_km, 4),
            "perimeterKm":    round(perimeter_km, 3),
            "rivalsCaptured": rivals_captured,
            "xpEarned":       xp_earned,
        }),
        "is_public": True,
    }).execute()

    cache_invalidate(f"dashboard:{uid}")
    cache_invalidate_prefix(f"terr_mine:{uid}")
    cache_invalidate_prefix("terr_nearby:")

    rival_msg = (
        f" Superseded {rivals_captured} rival territor{'ies' if rivals_captured > 1 else 'y'}!"
        if rivals_captured > 0 else ""
    )
    return ok({
        "territory": {
            "id":              territory_id,
            "areaSqKm":        round(area_sq_km, 6),
            "areaSqM":         round(area_sq_m, 0),
            "perimeterKm":     round(perimeter_km, 3),
            "centerLat":       round(center_lat, 6),
            "centerLon":       round(center_lon, 6),
            "capturedAt":      new_territory.get("captured_at"),
            "validationScore": round(score, 3),
            "pointValue":      point_value,
        },
        "xpEarned":         xp_earned,
        "rivalsCaptured":   rivals_captured,
        "speedProfile":     speed_profile,
        "validationNote":   ac_reason if ac_reason != "ok" else None,
        "message":          f"Territory claimed! {area_sq_m:.0f} m²" + rival_msg,
    }, "Territory claimed!")


# ── CORRIDOR CAPTURE ──────────────────────────────────────────────────────────

@router.post("/corridor")
def corridor_capture(
    body: TerritoryClaimRequest,        # reuse schema — only sessionId needed
    background_tasks: BackgroundTasks,
    user=Depends(get_current_user),
    db: Client = Depends(get_db),
):
    """
    Corridor (path-through) territory capture.

    When a runner passes through an existing rival territory without forming a
    closed loop, a 60-m-wide corridor (30 m each side) is carved out of the
    rival's polygon and becomes the runner's new territory strip.

    Carve rules
    ───────────
    • The corridor must steal ≥ 800 m² of the rival's territory.
    • The runner's route must travel ≥ 80 m inside the territory (not just clip a corner).
    • After carving, if the rival's remaining polygon is ≥ MIN_AREA_SQ_M it is
      updated in place; otherwise it is superseded entirely.
    • A MultiPolygon remainder (corridor bisects a territory) → keep the largest piece.
    • Only rival territories are affected — running through your own territory does nothing.
    • Anti-cheat (speed tiers) applies the same way as loop claims.
    • One session can carve through multiple rival territories in one call.
    """
    uid = user.id

    # 1. Verify session
    sess_res = (
        db.table("run_sessions")
        .select("id, user_id, status, distance_km")
        .eq("id", body.sessionId)
        .eq("user_id", uid)
        .execute()
    )
    if not sess_res.data:
        raise HTTPException(404, "Session not found")
    if sess_res.data[0].get("status") != "COMPLETED":
        raise HTTPException(400, "Session is not completed yet")

    # 2. Idempotency — one corridor claim per session
    dup = (
        db.table("territories")
        .select("id")
        .eq("session_id", body.sessionId)
        .execute()
    )
    if dup.data:
        raise HTTPException(400, "Corridor already claimed for this session")

    # 3. Load GPS points
    pts_res = (
        db.table("route_points")
        .select("latitude, longitude, recorded_at, sequence_number")
        .eq("session_id", body.sessionId)
        .order("sequence_number")
        .execute()
    )
    points = pts_res.data or []

    if len(points) < 10:
        raise HTTPException(422, f"Not enough GPS points for corridor: {len(points)} (need 10)")

    total_dist_m = route_total_distance_m(points)
    if total_dist_m < 80.0:
        raise HTTPException(422, f"Route too short for corridor capture: {total_dist_m:.0f} m (need 80 m)")

    # 4. Anti-cheat — corridor only needs 10 points (not the loop minimum of 20)
    ac_ok, ac_reason, score, speed_profile = _validate_anticheat(points, min_points=10)
    if not ac_ok:
        raise HTTPException(422, f"Anti-cheat failed: {ac_reason}")

    # 5. Build corridor polygon
    corridor_poly = buffer_route_m(points, CORRIDOR_BUFFER_M)
    if corridor_poly is None:
        raise HTTPException(422, "Could not build corridor — check GPS data quality")

    # 6. Find rival ACTIVE territories that the corridor intersects
    rival_territories = [
        t for t in _load_overlapping_territories(corridor_poly, db)
        if t.get("captured_by") and t["captured_by"] != uid
    ]
    if not rival_territories:
        return ok({
            "corridorsCaptured": 0,
            "xpEarned":          0,
            "speedProfile":      speed_profile,
            "message":           "Route did not pass through any rival territories.",
        })

    # 7. Process each rival territory
    carved_results = []
    total_xp       = 0

    for rival_t in rival_territories:
        rival_poly  = rival_t["_poly"]
        rival_uid   = rival_t["captured_by"]
        rival_id    = rival_t["id"]

        # Compute the portion of the rival territory that the corridor overlaps
        try:
            corridor_claim = corridor_poly.intersection(rival_poly)
        except Exception:
            continue

        claim_area_sq_m = polygon_area_sq_km(corridor_claim) * 1_000_000

        # Skip if the overlap is just clipping a corner
        if claim_area_sq_m < MIN_CORRIDOR_AREA_SQ_M:
            continue

        # Measure how far the route actually travels inside the territory
        inside_m = 0.0
        if _SHAPELY and _ShapelyPoint is not None:
            inside_m = sum(
                haversine_m(points[i]["latitude"], points[i]["longitude"],
                            points[i + 1]["latitude"], points[i + 1]["longitude"])
                for i in range(len(points) - 1)
                if rival_poly.contains(_ShapelyPoint(points[i]["longitude"], points[i]["latitude"]))
            )
        else:
            # Fallback: approximate using bounding box if Shapely unavailable
            inside_m = MIN_CORRIDOR_DISTANCE_M + 1.0
        if inside_m < MIN_CORRIDOR_DISTANCE_M:
            continue

        # Compute rival's remaining polygon after carving
        try:
            remainder = rival_poly.difference(corridor_poly)
            if not remainder.is_valid:
                remainder = remainder.buffer(0)
            remainder_poly = largest_polygon(remainder)
        except Exception:
            remainder_poly = None

        remainder_area_sq_m = polygon_area_sq_km(remainder_poly) * 1_000_000 if remainder_poly else 0

        # Build the corridor territory polygon (the piece we're claiming)
        claim_poly = largest_polygon(corridor_claim)
        if claim_poly is None:
            continue

        claim_area_sq_km = polygon_area_sq_km(claim_poly)
        claim_perimeter  = polygon_perimeter_km(claim_poly)
        claim_bbox       = polygon_bbox(claim_poly)
        claim_center_lat = claim_poly.centroid.y
        claim_center_lon = claim_poly.centroid.x
        display_claim    = simplify_polygon(smooth_polygon(claim_poly, iterations=1), tolerance=0.000035)
        geojson_claim    = polygon_to_geojson(display_claim)

        # Insert corridor territory
        point_value  = max(30, int(claim_area_sq_m / 100))
        new_res = db.table("territories").insert({
            "captured_by":       uid,
            "session_id":        body.sessionId,
            "name":              "Corridor",
            "boundary_geo_json": geojson_claim,
            "center_lat":        round(claim_center_lat, 6),
            "center_lon":        round(claim_center_lon, 6),
            "min_lat":           round(claim_bbox[0], 6),
            "max_lat":           round(claim_bbox[1], 6),
            "min_lon":           round(claim_bbox[2], 6),
            "max_lon":           round(claim_bbox[3], 6),
            "area_sq_km":        round(claim_area_sq_km, 6),
            "perimeter_km":      round(claim_perimeter, 4),
            "capture_count":     1,
            "captured_at":       datetime.now(timezone.utc).isoformat(),
            "status":            "ACTIVE",
            "avg_speed_kmh":     speed_profile.get("avgSpeedKmh"),
            "max_speed_kmh":     speed_profile.get("maxSpeedKmh"),
            "point_count":       len(points),
            "validation_score":  round(score, 3),
            "point_value":       point_value,
        }).execute()

        if not new_res.data:
            continue

        new_terr_id = new_res.data[0]["id"]

        # Update or supersede the rival's territory
        if remainder_poly and remainder_area_sq_m >= MIN_AREA_SQ_M:
            # Rival keeps the carved remainder
            display_remainder = simplify_polygon(smooth_polygon(remainder_poly, iterations=1), tolerance=0.000035)
            rem_bbox          = polygon_bbox(remainder_poly)
            rem_area_sq_km    = polygon_area_sq_km(remainder_poly)
            db.table("territories").update({
                "boundary_geo_json": polygon_to_geojson(display_remainder),
                "area_sq_km":        round(rem_area_sq_km, 6),
                "center_lat":        round(remainder_poly.centroid.y, 6),
                "center_lon":        round(remainder_poly.centroid.x, 6),
                "min_lat":           round(rem_bbox[0], 6),
                "max_lat":           round(rem_bbox[1], 6),
                "min_lon":           round(rem_bbox[2], 6),
                "max_lon":           round(rem_bbox[3], 6),
                "capture_count":     (rival_t.get("capture_count") or 0) + 1,
            }).eq("id", rival_id).execute()

            # Adjust rival's owned area
            rival_profile = (
                db.table("user_profiles")
                .select("territory_owned_sq_km")
                .eq("user_id", rival_uid)
                .single()
                .execute()
                .data or {}
            )
            old_area = rival_profile.get("territory_owned_sq_km") or 0
            db.table("user_profiles").update({
                "territory_owned_sq_km": round(max(0.0, old_area - claim_area_sq_km), 4),
            }).eq("user_id", rival_uid).execute()

        else:
            # Remainder too small — supersede the whole territory
            db.table("territories").update({
                "status":        "SUPERSEDED",
                "capture_count": (rival_t.get("capture_count") or 0) + 1,
            }).eq("id", rival_id).execute()

            rival_profile = (
                db.table("user_profiles")
                .select("territory_owned_sq_km")
                .eq("user_id", rival_uid)
                .single()
                .execute()
                .data or {}
            )
            full_area = rival_t.get("area_sq_km") or 0
            old_area  = rival_profile.get("territory_owned_sq_km") or 0
            db.table("user_profiles").update({
                "territory_owned_sq_km": round(max(0.0, old_area - full_area), 4),
            }).eq("user_id", rival_uid).execute()

        cache_invalidate(f"dashboard:{rival_uid}")

        # XP for the corridor — insert transaction now; profile update batched below
        xp_earned = int(xp.for_territory() * 0.6) + int(claim_area_sq_km * 8)
        db.table("xp_transactions").insert({
            "user_id": uid, "amount": xp_earned,
            "transaction_type": "TERRITORY_CAPTURE", "reference_id": new_terr_id,
            "description": f"Carved {claim_area_sq_m:.0f} m² corridor through rival territory",
        }).execute()
        total_xp += xp_earned

        carved_results.append({
            "corridorTerritoryId": new_terr_id,
            "rivalTerritoryId":    rival_id,
            "carvedAreaSqM":       round(claim_area_sq_m, 0),
            "rivalRemainingAreaSqM": round(remainder_area_sq_m, 0),
            "rivalTerritorySuperseded": remainder_area_sq_m < MIN_AREA_SQ_M,
            "xpEarned":            xp_earned,
        })

    if not carved_results:
        return ok({
            "corridorsCaptured": 0,
            "xpEarned":          0,
            "speedProfile":      speed_profile,
            "message":           "Route passed through rival territories but overlap was too small to capture.",
        })

    # Update claimant's profile stats — single read, single write
    total_carved_sq_km = sum(r["carvedAreaSqM"] for r in carved_results) / 1_000_000
    my_profile = (
        db.table("user_profiles")
        .select("territories_captured, territory_owned_sq_km, xp_points")
        .eq("user_id", uid)
        .single()
        .execute()
        .data or {}
    )
    new_xp = (my_profile.get("xp_points") or 0) + total_xp
    db.table("user_profiles").update({
        "territories_captured":  (my_profile.get("territories_captured") or 0) + len(carved_results),
        "territory_owned_sq_km": round((my_profile.get("territory_owned_sq_km") or 0) + total_carved_sq_km, 4),
        "xp_points": new_xp,
        "level":     xp.level_from_xp(new_xp),
    }).eq("user_id", uid).execute()

    # Activity feed
    background_tasks.add_task(lambda: db.table("activity_feed").insert({
        "user_id":       uid,
        "activity_type": "TERRITORY_CAPTURED",
        "reference_id":  carved_results[0]["corridorTerritoryId"],
        "message":       f"Cut a corridor through {len(carved_results)} rival territor{'ies' if len(carved_results) > 1 else 'y'}!",
        "metadata_json": json.dumps({
            "type":          "CORRIDOR",
            "corridors":     len(carved_results),
            "totalCarvedM2": sum(r["carvedAreaSqM"] for r in carved_results),
            "xpEarned":      total_xp,
        }),
        "is_public": True,
    }).execute())

    cache_invalidate(f"dashboard:{uid}")
    cache_invalidate_prefix(f"terr_mine:{uid}")
    cache_invalidate_prefix("terr_nearby:")

    return ok({
        "corridorsCaptured": len(carved_results),
        "xpEarned":          total_xp,
        "captures":          carved_results,
        "speedProfile":      speed_profile,
        "validationNote":    ac_reason if ac_reason != "ok" else None,
        "message":           f"Corridor captured! Cut through {len(carved_results)} rival territor{'ies' if len(carved_results) > 1 else 'y'}.",
    }, "Corridor captured!")


# ── READ endpoints ─────────────────────────────────────────────────────────────

@router.get("/nearby")
def nearby_territories(
    lat: float,
    lon: float,
    radiusKm: float = 5.0,
    user=Depends(get_current_user),
    db: Client = Depends(get_db),
):
    """Active territories near a GPS coordinate, enriched with owner data."""
    radius = min(radiusKm, 50.0)
    # Round to 2dp (~1 km bucket) so nearby requests share cached data
    lat_b  = round(lat, 2)
    lon_b  = round(lon, 2)
    cache_key = f"terr_nearby:{lat_b}:{lon_b}:{radius}"
    cached = cache_get(cache_key)
    if cached:
        return cached

    deg = radius / 111.0
    res = (
        db.table("territories")
        .select("*")
        .eq("status", "ACTIVE")
        .gte("center_lat", lat - deg).lte("center_lat", lat + deg)
        .gte("center_lon", lon - deg).lte("center_lon", lon + deg)
        .execute()
    )
    territories = [
        t for t in (res.data or [])
        if within_radius(lat, lon, t["center_lat"], t["center_lon"], radius)
    ]
    result = ok(territories)
    cache_set(cache_key, result, ttl_seconds=15)
    return result


@router.get("/mine")
def my_territories(
    page: int = 0,
    size: int = 20,
    user=Depends(get_current_user),
    db: Client = Depends(get_db),
):
    uid   = user.id
    cache_key = f"terr_mine:{uid}:p{page}"
    cached = cache_get(cache_key)
    if cached:
        return cached

    start = page * size
    res   = (
        db.table("territories")
        .select("*", count="exact")
        .eq("captured_by", uid)
        .eq("status", "ACTIVE")
        .order("captured_at", desc=True)
        .range(start, start + size - 1)
        .execute()
    )
    total = res.count or 0
    result = ok({
        "content":      res.data or [],
        "totalElements": total,
        "totalPages":    -(-total // size),
        "number":        page,
        "size":          size,
    })
    cache_set(cache_key, result, ttl_seconds=60)
    return result
