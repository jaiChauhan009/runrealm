import json
import math
from typing import Optional

try:
    from shapely.geometry import LineString, Polygon, mapping
    _SHAPELY = True
except ImportError:
    _SHAPELY = False


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


# ── Shapely-based polygon helpers ─────────────────────────────────────────────

def build_polygon(points: list[dict]) -> "Optional[Polygon]":
    """
    Build a Shapely Polygon from ordered GPS dicts with 'latitude'/'longitude'.
    The ring is closed automatically. Returns None if Shapely unavailable or
    if the geometry is degenerate after auto-repair.
    """
    if not _SHAPELY or len(points) < 3:
        return None
    # Shapely uses (x=lon, y=lat) convention
    coords = [(p["longitude"], p["latitude"]) for p in points]
    if coords[0] != coords[-1]:
        coords.append(coords[0])
    try:
        poly = Polygon(coords)
        if not poly.is_valid:
            poly = poly.buffer(0)   # auto-repair minor topology issues
        if poly.is_empty or not poly.is_valid:
            return None
        return poly
    except Exception:
        return None


def polygon_area_sq_km(poly: "Polygon") -> float:
    """
    Convert a Shapely Polygon's area (degrees²) to km².
    Uses the centroid latitude to scale the longitude degree.
    Accurate within ~1 % for areas up to several km².
    """
    if not _SHAPELY:
        return 0.0
    center_lat = poly.centroid.y
    lat_km = 111.32
    lon_km = 111.32 * math.cos(math.radians(center_lat))
    return abs(poly.area) * lat_km * lon_km


def polygon_perimeter_km(poly: "Polygon") -> float:
    """Sum haversine distances around the exterior ring."""
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
    """Serialize a Shapely Polygon to a GeoJSON geometry string."""
    if not _SHAPELY:
        return "{}"
    return json.dumps(mapping(poly))


def geojson_to_polygon(geojson: dict) -> "Optional[Polygon]":
    """Parse a GeoJSON Polygon geometry dict into a Shapely Polygon."""
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


def route_total_distance_m(points: list[dict]) -> float:
    total = 0.0
    for i in range(len(points) - 1):
        total += haversine_m(
            points[i]["latitude"], points[i]["longitude"],
            points[i + 1]["latitude"], points[i + 1]["longitude"],
        )
    return total
