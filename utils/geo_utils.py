"""
Geometry utilities for RunRealm territory engine.

All functions degrade gracefully if Shapely is not installed.
"""
import json
import math
from datetime import datetime
from typing import Optional

try:
    from shapely.geometry import LineString, MultiPolygon, Polygon, mapping
    from shapely.ops import unary_union
    _SHAPELY = True
except ImportError:
    _SHAPELY = False


# ── Basic geo math ────────────────────────────────────────────────────────────

def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    )
    return R * 2 * math.asin(math.sqrt(a))


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    return haversine_km(lat1, lon1, lat2, lon2) * 1000.0


def within_radius(lat1: float, lon1: float, lat2: float, lon2: float, radius_km: float) -> bool:
    return haversine_km(lat1, lon1, lat2, lon2) <= radius_km


def route_total_distance_m(points: list[dict]) -> float:
    total = 0.0
    for i in range(len(points) - 1):
        total += haversine_m(
            points[i]["latitude"], points[i]["longitude"],
            points[i + 1]["latitude"], points[i + 1]["longitude"],
        )
    return total


# ── Speed tier classification ──────────────────────────────────────────────────
# CLEAN     : ≤ 20 km/h  — running or casual cycling
# SUSPICIOUS: 20–35 km/h — fast bike / e-scooter; flagged but not rejected alone
# VEHICLE   : > 35 km/h  — clearly motorised; penalised heavily
# TELEPORT  : large jump in very short time — GPS spoof or device glitch
# SKIP      : Δt < 1 s   — too short to compute meaningful speed

SPEED_CLEAN_MAX     = 20.0   # km/h
SPEED_SUSPICIOUS_MAX = 35.0  # km/h
TELEPORT_DIST_M     = 200.0
TELEPORT_TIME_S     = 3.0


def classify_route_speeds(points: list[dict]) -> dict:
    """
    Analyse every consecutive GPS segment and return a detailed speed profile.

    Returns
    -------
    dict with keys:
        counts        : {"CLEAN", "SUSPICIOUS", "VEHICLE", "TELEPORT", "SKIP"}
        vehicle_ratio : fraction of measurable segments that are VEHICLE
        avg_speed_kmh : average speed across all measurable segments
        max_speed_kmh : highest single-segment speed
        total_m       : total route distance
        teleports     : number of teleport events
        speed_detail  : list of per-segment dicts (for storage / diagnostics)
    """
    counts = {"CLEAN": 0, "SUSPICIOUS": 0, "VEHICLE": 0, "TELEPORT": 0, "SKIP": 0}
    total_m = 0.0
    speeds: list[float] = []
    speed_detail: list[dict] = []

    for i in range(1, len(points)):
        p0, p1 = points[i - 1], points[i]
        dist_m = haversine_m(
            p0["latitude"], p0["longitude"],
            p1["latitude"], p1["longitude"],
        )
        total_m += dist_m

        try:
            t0 = datetime.fromisoformat(p0["recorded_at"].replace("Z", "+00:00"))
            t1 = datetime.fromisoformat(p1["recorded_at"].replace("Z", "+00:00"))
            dt_s = (t1 - t0).total_seconds()
        except Exception:
            dt_s = 2.0

        # Teleportation: big spatial jump in very short time
        if dist_m > TELEPORT_DIST_M and 0 < dt_s < TELEPORT_TIME_S:
            counts["TELEPORT"] += 1
            speed_detail.append({"seg": i, "classification": "TELEPORT", "dist_m": round(dist_m, 1), "dt_s": round(dt_s, 2), "speed_kmh": None})
            continue

        # Too short a time window to compute reliable speed
        if dt_s < 1.0:
            counts["SKIP"] += 1
            speed_detail.append({"seg": i, "classification": "SKIP", "dist_m": round(dist_m, 1), "dt_s": round(dt_s, 3), "speed_kmh": None})
            continue

        speed_kmh = (dist_m / dt_s) * 3.6
        speeds.append(speed_kmh)

        if speed_kmh <= SPEED_CLEAN_MAX:
            cls = "CLEAN"
            counts["CLEAN"] += 1
        elif speed_kmh <= SPEED_SUSPICIOUS_MAX:
            cls = "SUSPICIOUS"
            counts["SUSPICIOUS"] += 1
        else:
            cls = "VEHICLE"
            counts["VEHICLE"] += 1

        speed_detail.append({"seg": i, "classification": cls, "dist_m": round(dist_m, 1), "dt_s": round(dt_s, 2), "speed_kmh": round(speed_kmh, 2)})

    measurable = counts["CLEAN"] + counts["SUSPICIOUS"] + counts["VEHICLE"]
    vehicle_ratio = counts["VEHICLE"] / measurable if measurable > 0 else 0.0
    avg_speed = sum(speeds) / len(speeds) if speeds else 0.0
    max_speed = max(speeds) if speeds else 0.0

    return {
        "counts": counts,
        "vehicle_ratio": round(vehicle_ratio, 4),
        "avg_speed_kmh": round(avg_speed, 2),
        "max_speed_kmh": round(max_speed, 2),
        "total_m": round(total_m, 1),
        "teleports": counts["TELEPORT"],
        "speed_detail": speed_detail,
    }


# ── Shapely-based polygon helpers ─────────────────────────────────────────────

def build_polygon(points: list[dict]) -> "Optional[Polygon]":
    """
    Build a Shapely Polygon from ordered GPS dicts.
    Ring is closed automatically. Returns None on degenerate geometry.
    """
    if not _SHAPELY or len(points) < 3:
        return None
    coords = [(p["longitude"], p["latitude"]) for p in points]
    if coords[0] != coords[-1]:
        coords.append(coords[0])
    try:
        poly = Polygon(coords)
        if not poly.is_valid:
            poly = poly.buffer(0)
        return poly if (poly.is_valid and not poly.is_empty) else None
    except Exception:
        return None


def polygon_area_sq_km(poly: "Polygon") -> float:
    """Degrees² → km², accurate within ~1 % for small areas."""
    if not _SHAPELY:
        return 0.0
    center_lat = poly.centroid.y
    lat_km = 111.32
    lon_km = 111.32 * math.cos(math.radians(center_lat))
    return abs(poly.area) * lat_km * lon_km


def polygon_perimeter_km(poly: "Polygon") -> float:
    if not _SHAPELY:
        return 0.0
    exterior = list(poly.exterior.coords)
    total = 0.0
    for i in range(len(exterior) - 1):
        lon1, lat1 = exterior[i][0], exterior[i][1]
        lon2, lat2 = exterior[i + 1][0], exterior[i + 1][1]
        total += haversine_km(lat1, lon1, lat2, lon2)
    return total


def polygon_bbox(poly: "Polygon") -> tuple[float, float, float, float]:
    """Returns (min_lat, max_lat, min_lon, max_lon)."""
    minx, miny, maxx, maxy = poly.bounds
    return miny, maxy, minx, maxx


def polygon_to_geojson(poly: "Polygon") -> str:
    if not _SHAPELY:
        return "{}"
    return json.dumps(mapping(poly))


def geojson_to_polygon(geojson: dict) -> "Optional[Polygon]":
    if not _SHAPELY:
        return None
    try:
        if geojson.get("type") == "Polygon":
            exterior = [(c[0], c[1]) for c in geojson["coordinates"][0]]
            return Polygon(exterior)
    except Exception:
        pass
    return None


def route_is_simple(points: list[dict]) -> bool:
    """Return True if the route LineString has no self-intersections."""
    if not _SHAPELY or len(points) < 2:
        return True
    coords = [(p["longitude"], p["latitude"]) for p in points]
    try:
        return LineString(coords).is_simple
    except Exception:
        return True


# ── Polygon smoothing & simplification ───────────────────────────────────────

def _chaikin_smooth(coords: list[tuple], iterations: int) -> list[tuple]:
    """
    Chaikin's corner-cutting algorithm.
    Each iteration replaces every edge with two new points at 25 % and 75 %.
    After N iterations the curve approximates a quadratic B-spline.
    """
    for _ in range(iterations):
        new_coords = [coords[0]]
        for i in range(len(coords) - 1):
            p0, p1 = coords[i], coords[i + 1]
            new_coords.append((0.75 * p0[0] + 0.25 * p1[0], 0.75 * p0[1] + 0.25 * p1[1]))
            new_coords.append((0.25 * p0[0] + 0.75 * p1[0], 0.25 * p0[1] + 0.75 * p1[1]))
        new_coords.append(coords[-1])
        coords = new_coords
    return coords


def smooth_polygon(poly: "Polygon", iterations: int = 2) -> "Polygon":
    """
    Apply Chaikin smoothing to a polygon's exterior ring.
    Makes territory polygons look organic instead of angular on the map.
    Falls back to the original polygon if smoothing breaks validity.
    """
    if not _SHAPELY:
        return poly
    coords = list(poly.exterior.coords)[:-1]  # exclude closing duplicate
    smoothed = _chaikin_smooth(coords, iterations)
    smoothed.append(smoothed[0])  # re-close
    try:
        new_poly = Polygon(smoothed)
        if not new_poly.is_valid:
            new_poly = new_poly.buffer(0)
        return new_poly if (new_poly.is_valid and not new_poly.is_empty) else poly
    except Exception:
        return poly


def simplify_polygon(poly: "Polygon", tolerance: float = 0.00005) -> "Polygon":
    """
    Douglas-Peucker simplification — reduces vertex count for faster map rendering.
    tolerance ≈ 0.00005 degrees ≈ 5 m; safe for neighbourhood-scale polygons.
    """
    if not _SHAPELY:
        return poly
    try:
        s = poly.simplify(tolerance, preserve_topology=True)
        return s if (s.is_valid and not s.is_empty) else poly
    except Exception:
        return poly


def largest_polygon(geom: "Polygon | MultiPolygon") -> "Optional[Polygon]":
    """
    Return the largest-area simple Polygon from a Polygon or MultiPolygon.
    Used after difference() which can produce a MultiPolygon when a corridor
    bisects a territory.
    """
    if not _SHAPELY:
        return None
    if isinstance(geom, Polygon):
        return geom if not geom.is_empty else None
    if isinstance(geom, MultiPolygon):
        parts = [g for g in geom.geoms if isinstance(g, Polygon) and not g.is_empty]
        return max(parts, key=lambda p: p.area) if parts else None
    return None


# ── Corridor helpers ──────────────────────────────────────────────────────────

def buffer_route_m(points: list[dict], buffer_m: float) -> "Optional[Polygon]":
    """
    Create a corridor polygon by buffering a GPS linestring.
    buffer_m is the one-sided radius (total corridor width = 2 × buffer_m).

    The degree-scale of one metre depends on latitude, so we compute
    the average latitude of the route for an accurate conversion.
    """
    if not _SHAPELY or len(points) < 2:
        return None
    coords = [(p["longitude"], p["latitude"]) for p in points]
    avg_lat = sum(p["latitude"] for p in points) / len(points)

    # 1 metre in degrees (approximate but accurate to < 0.1 % for small areas)
    lat_deg_per_m = 1.0 / 111_320.0
    lon_deg_per_m = 1.0 / (111_320.0 * math.cos(math.radians(avg_lat)))
    deg_per_m = (lat_deg_per_m + lon_deg_per_m) / 2.0
    buffer_deg = buffer_m * deg_per_m

    try:
        line = LineString(coords)
        # cap_style=2 → flat end caps  join_style=2 → mitre joins
        corridor = line.buffer(buffer_deg, cap_style=2, join_style=2)
        if not corridor.is_valid:
            corridor = corridor.buffer(0)
        return corridor if not corridor.is_empty else None
    except Exception:
        return None


def smooth_linestring_points(points: list[dict], iterations: int = 2) -> list[dict]:
    """
    Apply Chaikin smoothing to GPS route points for nicer polyline rendering.
    Returns a new list of {"latitude": …, "longitude": …} dicts.
    The first and last points are preserved unchanged.
    """
    if len(points) < 3:
        return points
    coords = [(p["longitude"], p["latitude"]) for p in points]
    smoothed = _chaikin_smooth(coords, iterations)
    return [{"latitude": lat, "longitude": lon} for lon, lat in smoothed]
