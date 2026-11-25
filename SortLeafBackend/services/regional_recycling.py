"""Read-only OSM snapshot. A cross-border search can still use the live provider."""
import json
from functools import lru_cache
from pathlib import Path

from services.location_service import _parse_osm_element, search_bounds

CATALOG_PATH = Path(__file__).resolve().parents[1] / "data" / "costa-rica-recycling.json"


@lru_cache(maxsize=1)
def load_catalog():
    return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))


def contains_origin(boundary, lat, lon):
    inside = False
    for (x1, y1), (x2, y2) in zip(boundary, boundary[1:] + boundary[:1]):
        if (y1 > lat) != (y2 > lat) and lon < x1 + (lat - y1) * (x2 - x1) / (y2 - y1):
            inside = not inside
    return inside


def covers_search(boundary, lat, lon, distance):
    # Called only for an interior origin. If no boundary segment can intersect
    # the enclosing box, every possible closer point is inside the extract.
    south, west, north, east = search_bounds(lat, lon, distance)
    return all(max(x1, x2) < west or min(x1, x2) > east or max(y1, y2) < south or min(y1, y2) > north
               for (x1, y1), (x2, y2) in zip(boundary, boundary[1:] + boundary[:1]))


def regional_candidates(latitude, longitude):
    catalog = load_catalog()
    if not contains_origin(catalog["boundary"], latitude, longitude):
        return None
    points = (_parse_osm_element(element, latitude, longitude) for element in catalog["elements"])
    return sorted((p for p in points if p is not None), key=lambda p: (p.distance, p.id))
