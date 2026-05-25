"""
Map router — four concerns:

1. Running path      GET /map/route/{session_id}
2. Nearby users      GET /map/nearby-users
3. Location update   POST /map/location
4. Territory layers  GET /map/territories/live      — Point features (centers), lightweight
                     GET /map/territories/polygons  — Polygon + Point features per territory,
                                                      used for the filled-area + owner-initial
                                                      CircleLayer on the map
"""

import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from supabase import Client

from auth import get_current_user
from database import get_db
from schemas import ok
from utils.geo_utils import haversine_km, within_radius

router = APIRouter()


# ── request model ─────────────────────────────────────────────────────────────

class LocationUpdate(BaseModel):
    latitude: float
    longitude: float


# ── helpers ───────────────────────────────────────────────────────────────────

def _bounding_box(points: list[dict], pad_km: float = 0.5):
    """Return (min_lat, max_lat, min_lon, max_lon) with optional padding."""
    lats = [p["latitude"] for p in points]
    lons = [p["longitude"] for p in points]
    deg = pad_km / 111.0
    return (
        min(lats) - deg, max(lats) + deg,
        min(lons) - deg, max(lons) + deg,
    )


def _geojson_linestring(points: list[dict]) -> dict:
    coords = [
        [p["longitude"], p["latitude"]]
        + ([p["altitude"]] if p.get("altitude") else [])
        for p in points
    ]
    return {
        "type": "LineString",
        "coordinates": coords,
    }


def _pace_color(avg_pace: float | None) -> str:
    """Map pace (min/km) → hex colour from the RunRealm design palette."""
    if avg_pace is None:
        return "#CCFF00"
    if avg_pace < 4.5:
        return "#CCFF00"   # elite  — neon green
    if avg_pace < 6.0:
        return "#00E5FF"   # good   — cyan
    if avg_pace < 8.0:
        return "#8A2BE2"   # steady — purple
    return "#FF6B6B"       # slow   — red


# ── endpoints ─────────────────────────────────────────────────────────────────

@router.get("/route/{session_id}")
def get_route(session_id: str, user=Depends(get_current_user), db: Client = Depends(get_db)):
    """
    Returns a GeoJSON FeatureCollection ready to hand to Google Maps / Mapbox:
      • Feature 1 — LineString of the full GPS path, styled by pace
      • Feature 2 — Point for run start
      • Feature 3 — Point for run finish
      • Features 4…N — Territory polygons inside the route bounding box
    """
    uid = user.id

    # Fetch session
    sess_res = db.table("run_sessions").select("*").eq("id", session_id).execute()
    if not sess_res.data:
        raise HTTPException(400, "Session not found")
    session = sess_res.data[0]
    if session["user_id"] != uid:
        raise HTTPException(403, "Not your session")

    # Fetch ordered GPS points
    pts_res = (
        db.table("route_points")
        .select("latitude,longitude,altitude,speed_kmh,recorded_at,sequence_number")
        .eq("session_id", session_id)
        .order("sequence_number")
        .execute()
    )
    points = pts_res.data or []

    features = []

    if points:
        # ── LineString path ──────────────────────────────────────────────
        features.append({
            "type": "Feature",
            "geometry": _geojson_linestring(points),
            "properties": {
                "type": "run_path",
                "sessionId": session_id,
                "activityType": session.get("activity_type", "RUN"),
                "distanceKm": session.get("distance_km", 0),
                "durationSeconds": session.get("duration_seconds", 0),
                "caloriesBurned": session.get("calories_burned", 0),
                "xpEarned": session.get("xp_earned", 0),
                "avgPaceMinPerKm": session.get("avg_pace_min_per_km"),
                "strokeColor": _pace_color(session.get("avg_pace_min_per_km")),
                "strokeWidth": 4,
            },
        })

        # ── Start marker ─────────────────────────────────────────────────
        start = points[0]
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [start["longitude"], start["latitude"]]},
            "properties": {
                "type": "run_start",
                "label": "Start",
                "iconColor": "#00E5FF",
                "recordedAt": start.get("recorded_at"),
            },
        })

        # ── Finish marker ─────────────────────────────────────────────────
        finish = points[-1]
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [finish["longitude"], finish["latitude"]]},
            "properties": {
                "type": "run_finish",
                "label": "Finish",
                "iconColor": "#CCFF00",
                "recordedAt": finish.get("recorded_at"),
            },
        })

        # ── Territories in bounding box ───────────────────────────────────
        min_lat, max_lat, min_lon, max_lon = _bounding_box(points)
        terr_res = (
            db.table("territories")
            .select("id,name,center_lat,center_lon,boundary_geo_json,captured_by,capture_count,area_sq_km,point_value")
            .gte("center_lat", min_lat).lte("center_lat", max_lat)
            .gte("center_lon", min_lon).lte("center_lon", max_lon)
            .execute()
        )
        for t in (terr_res.data or []):
            owned_by_me = t.get("captured_by") == uid
            features.append({
                "type": "Feature",
                "geometry": (
                    {"type": "Point", "coordinates": [t["center_lon"], t["center_lat"]]}
                ),
                "properties": {
                    "type": "territory",
                    "territoryId": t["id"],
                    "name": t["name"],
                    "capturedBy": t.get("captured_by"),
                    "ownedByMe": owned_by_me,
                    "captureCount": t.get("capture_count", 0),
                    "areaSqKm": t.get("area_sq_km", 0),
                    "pointValue": t.get("point_value", 100),
                    "fillColor": "#CCFF00" if owned_by_me else "#8A2BE2",
                    "fillOpacity": 0.35,
                    "strokeColor": "#CCFF00" if owned_by_me else "#8A2BE2",
                },
            })

    return ok({
        "type": "FeatureCollection",
        "features": features,
        "meta": {
            "sessionId": session_id,
            "totalPoints": len(points),
            "distanceKm": session.get("distance_km", 0),
            "durationSeconds": session.get("duration_seconds", 0),
            "status": session.get("status"),
            "xpEarned": session.get("xp_earned", 0),
        },
    })


@router.post("/location")
def update_location(
    body: LocationUpdate,
    user=Depends(get_current_user),
    db: Client = Depends(get_db),
):
    """Update current user's last known position (call every ~10 s during a run)."""
    uid = user.id
    db.table("user_profiles").update({
        "last_lat": body.latitude,
        "last_lon": body.longitude,
        "last_location_at": datetime.now(timezone.utc).isoformat(),
    }).eq("user_id", uid).execute()
    return ok(None, "Location updated")


@router.get("/nearby-users")
def nearby_users(
    lat: float,
    lon: float,
    radiusKm: float = 5.0,
    user=Depends(get_current_user),
    db: Client = Depends(get_db),
):
    """
    Find other runners whose last_location is within `radiusKm` of (lat, lon).
    Excludes the calling user and already-accepted friends.
    Returns list sorted by distance, ready for 'People Near You' friend suggestions.
    """
    uid = user.id
    radius = min(radiusKm, 50.0)   # cap at 50 km
    deg = radius / 111.0

    # Bounding-box pre-filter from DB
    res = (
        db.table("user_profiles")
        .select("user_id,username,display_name,avatar_url,level,xp_points,last_lat,last_lon,last_location_at,is_public")
        .gte("last_lat", lat - deg).lte("last_lat", lat + deg)
        .gte("last_lon", lon - deg).lte("last_lon", lon + deg)
        .neq("user_id", uid)
        .eq("is_public", True)
        .execute()
    )
    candidates = res.data or []

    # Already friends
    friends_res = (
        db.table("user_friends")
        .select("friend_id,user_id")
        .or_(f"user_id.eq.{uid},friend_id.eq.{uid}")
        .execute()
    )
    friend_ids = set()
    for f in (friends_res.data or []):
        friend_ids.add(f["friend_id"] if f["user_id"] == uid else f["user_id"])

    # Precise Haversine filter
    nearby = []
    for p in candidates:
        if p["user_id"] in friend_ids:
            continue
        if not p.get("last_lat") or not p.get("last_lon"):
            continue
        dist = haversine_km(lat, lon, p["last_lat"], p["last_lon"])
        if dist <= radius:
            nearby.append({
                "userId": p["user_id"],
                "username": p["username"],
                "displayName": p.get("display_name"),
                "avatarUrl": p.get("avatar_url"),
                "level": p.get("level", 1),
                "xpPoints": p.get("xp_points", 0),
                "distanceKm": round(dist, 2),
                "lastSeenAt": p.get("last_location_at"),
                "alreadyFriend": False,
            })

    nearby.sort(key=lambda x: x["distanceKm"])

    return ok({
        "users": nearby,
        "totalNearby": len(nearby),
        "radiusKm": radius,
        "centerLat": lat,
        "centerLon": lon,
    })


@router.get("/territories/live")
def live_territories(
    lat: float,
    lon: float,
    radiusKm: float = 3.0,
    user=Depends(get_current_user),
    db: Client = Depends(get_db),
):
    """
    Live map view — returns territories near (lat, lon) enriched with owner profile.
    Used to render the conquest layer on the running map in real time.
    """
    uid = user.id
    deg = radiusKm / 111.0

    terr_res = (
        db.table("territories")
        .select("*")
        .gte("center_lat", lat - deg).lte("center_lat", lat + deg)
        .gte("center_lon", lon - deg).lte("center_lon", lon + deg)
        .execute()
    )
    territories = [
        t for t in (terr_res.data or [])
        if within_radius(lat, lon, t["center_lat"], t["center_lon"], radiusKm)
    ]

    # Enrich with owner usernames
    owner_ids = list({t["captured_by"] for t in territories if t.get("captured_by")})
    owner_map = {}
    if owner_ids:
        profiles = (
            db.table("user_profiles")
            .select("user_id,username,avatar_url,level")
            .in_("user_id", owner_ids)
            .execute()
        )
        owner_map = {p["user_id"]: p for p in (profiles.data or [])}

    features = []
    for t in territories:
        owner = owner_map.get(t.get("captured_by"), {})
        owned_by_me = t.get("captured_by") == uid
        features.append({
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [t["center_lon"], t["center_lat"]],
            },
            "properties": {
                "type": "territory",
                "territoryId": t["id"],
                "name": t["name"],
                "areaSqKm": t.get("area_sq_km", 0),
                "pointValue": t.get("point_value", 100),
                "captureCount": t.get("capture_count", 0),
                "capturedAt": t.get("captured_at"),
                "ownedByMe": owned_by_me,
                "unclaimed": t.get("captured_by") is None,
                "owner": {
                    "userId": owner.get("user_id"),
                    "username": owner.get("username", "Unclaimed"),
                    "avatarUrl": owner.get("avatar_url"),
                    "level": owner.get("level", 0),
                } if owner else None,
                # Colours matching RunRealm design system
                "fillColor": (
                    "#CCFF00" if owned_by_me
                    else "#050505" if t.get("captured_by") is None
                    else "#8A2BE2"
                ),
                "strokeColor": (
                    "#CCFF00" if owned_by_me
                    else "#00E5FF" if t.get("captured_by") is None
                    else "#8A2BE2"
                ),
                "fillOpacity": 0.4,
            },
        })

    return ok({
        "type": "FeatureCollection",
        "features": features,
        "meta": {
            "total": len(features),
            "mine": sum(1 for f in features if f["properties"]["ownedByMe"]),
            "unclaimed": sum(1 for f in features if f["properties"]["unclaimed"]),
            "contested": sum(1 for f in features if not f["properties"]["ownedByMe"] and not f["properties"]["unclaimed"]),
        },
    })


@router.get("/territories/polygons")
def territory_polygons(
    lat: float,
    lon: float,
    radiusKm: float = 5.0,
    user=Depends(get_current_user),
    db: Client = Depends(get_db),
):
    """
    Returns nearby territories as a GeoJSON FeatureCollection with two features
    per territory:

      • type="territory_polygon" — Polygon geometry from boundary_geo_json.
        Used as a FillLayer to show the captured area.

      • type="territory_label"   — Point geometry at the territory center.
        Properties include ownerInitial for the CircleLayer label.

    Both features share the same territoryId so the client can pair them.
    Territories without boundary_geo_json are returned as Point-only.
    """
    uid = user.id
    deg = min(radiusKm, 50.0) / 111.0

    terr_res = (
        db.table("territories")
        .select("id,name,center_lat,center_lon,boundary_geo_json,captured_by,area_sq_km,point_value,capture_count,captured_at")
        .gte("center_lat", lat - deg).lte("center_lat", lat + deg)
        .gte("center_lon", lon - deg).lte("center_lon", lon + deg)
        .execute()
    )
    territories = [
        t for t in (terr_res.data or [])
        if within_radius(lat, lon, t["center_lat"], t["center_lon"], radiusKm)
    ]

    owner_ids = list({t["captured_by"] for t in territories if t.get("captured_by")})
    owner_map = {}
    if owner_ids:
        profiles = (
            db.table("user_profiles")
            .select("user_id,username,display_name,avatar_url,level,xp_points")
            .in_("user_id", owner_ids)
            .execute()
        )
        owner_map = {p["user_id"]: p for p in (profiles.data or [])}

    features = []
    for t in territories:
        owner_id = t.get("captured_by")
        owner = owner_map.get(owner_id, {})
        owned_by_me = owner_id == uid
        unclaimed = owner_id is None

        fill_color = (
            "#CCFF00" if owned_by_me
            else "#050505" if unclaimed
            else "#8A2BE2"
        )
        stroke_color = (
            "#CCFF00" if owned_by_me
            else "#00E5FF" if unclaimed
            else "#8A2BE2"
        )

        username = owner.get("username", "")
        owner_initial = username[0].upper() if username else "?"

        shared_props = {
            "territoryId": t["id"],
            "name": t["name"],
            "areaSqKm": t.get("area_sq_km", 0),
            "pointValue": t.get("point_value", 100),
            "captureCount": t.get("capture_count", 0),
            "capturedAt": t.get("captured_at"),
            "ownedByMe": owned_by_me,
            "unclaimed": unclaimed,
            "fillColor": fill_color,
            "strokeColor": stroke_color,
            "fillOpacity": 0.35,
            "owner": {
                "userId": owner.get("user_id"),
                "username": username or None,
                "displayName": owner.get("display_name"),
                "avatarUrl": owner.get("avatar_url"),
                "level": owner.get("level", 0),
                "xpPoints": owner.get("xp_points", 0),
            } if owner else None,
        }

        # Polygon feature (filled area)
        raw_boundary = t.get("boundary_geo_json")
        if raw_boundary:
            boundary_geom = (
                json.loads(raw_boundary)
                if isinstance(raw_boundary, str)
                else raw_boundary
            )
            features.append({
                "type": "Feature",
                "geometry": boundary_geom,
                "properties": {**shared_props, "type": "territory_polygon"},
            })

        # Point feature (owner-initial circle)
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [t["center_lon"], t["center_lat"]]},
            "properties": {
                **shared_props,
                "type": "territory_label",
                "ownerInitial": owner_initial,
            },
        })

    # Return raw GeoJSON — the Android client uses bodyAsText() and feeds this
    # directly into MapLibre's GeoJsonSource.setGeoJson(), which requires a
    # plain FeatureCollection string, not the ok()-wrapped envelope.
    return JSONResponse(content={
        "type": "FeatureCollection",
        "features": features,
        "meta": {
            "total": len(territories),
            "mine": sum(1 for t in territories if t.get("captured_by") == uid),
            "unclaimed": sum(1 for t in territories if not t.get("captured_by")),
            "contested": sum(1 for t in territories if t.get("captured_by") and t["captured_by"] != uid),
            "radiusKm": radiusKm,
            "centerLat": lat,
            "centerLon": lon,
        },
    })
