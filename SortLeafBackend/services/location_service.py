"""
Service to handle locations and recycling points.
Uses Overpass API to query OpenStreetMap.
"""

import logging
from collections import OrderedDict
import math
import httpx
from typing import List, Optional, Dict, Any
from datetime import datetime
from time import monotonic

from models.location_models import RecyclingPoint, Coordinates
from exceptions.location_exceptions import OverpassAPIError
from config.settings import (
    CIRCUIT_BREAKER_RESET_TIMEOUT,
    LOCATION_CACHE_TTL,
    HTTP_TIMEOUT_LOCATION,
    OVERPASS_QUERY_TIMEOUT,
    LOCATION_DEFAULT_RADIUS,
    OVERPASS_SERVERS
)

_retry_after: Dict[str, float] = {}

logger = logging.getLogger(__name__)
EARTH_RADIUS_METERS = 6_371_000
WORLD_DISTANCE = math.ceil(math.pi * EARTH_RADIUS_METERS)
QUERY_BATCH_SIZE = 1500


class CrowdedSearch(Exception):
    """The provider truncated a batch, so its ordering cannot establish the nearest points."""



_cache: OrderedDict[tuple, tuple] = OrderedDict()


def _get_cache_key(lat: float, lon: float, radius: int) -> tuple:
    # A nearby center can have a different tenth point at the edge of the search.
    return lat, lon, radius


def _is_cache_valid(cache_entry: tuple) -> bool:
    """Checks if a cache entry is still valid."""
    if not cache_entry:
        return False
    _, timestamp = cache_entry
    return datetime.now() - timestamp < LOCATION_CACHE_TTL


def calculate_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Calculates the distance between two points using the Haversine formula.
    
    Args:
        lat1, lon1: Coordinates of the first point
        lat2, lon2: Coordinates of the second point
        
    Returns:
        Distance in meters
    """
    
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    
    a = (math.sin(delta_phi / 2) ** 2 +
         math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2)
    a = min(1.0, max(0.0, a))
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    
    return EARTH_RADIUS_METERS * c


MATERIAL_TAGS = {
    'recycling:plastic': 'plastic',
    'recycling:plastic_bottles': 'plastic',
    'recycling:plastic_packaging': 'plastic',
    'recycling:glass': 'glass',
    'recycling:glass_bottles': 'glass',
    'recycling:paper': 'paper',
    'recycling:newspaper': 'paper',
    'recycling:magazines': 'paper',
    'recycling:cardboard': 'cardboard',
    'recycling:metal': 'metal',
    'recycling:cans': 'metal',
    'recycling:aluminium': 'metal',
    'recycling:scrap_metal': 'metal',
    'recycling:batteries': 'batteries',
    'recycling:electrical_appliances': 'electronics',
    'recycling:small_electrical_appliances': 'electronics',
    'recycling:computers': 'electronics',
    'recycling:mobile_phones': 'electronics',
    'recycling:clothes': 'clothes',
    'recycling:shoes': 'clothes',
    'recycling:organic': 'organic',
    'recycling:green_waste': 'organic',
    'recycling:cooking_oil': 'oil',
    'recycling:engine_oil': 'oil',
}


def _parse_recycling_types(tags: Dict[str, str]) -> List[str]:
    """
    Parses OSM tags to extract recyclable material types.
    """
    types = []

    for tag, material_type in MATERIAL_TAGS.items():
        if tags.get(tag) == 'yes' and material_type not in types:
            types.append(material_type)
    
    if not types:
        amenity = tags.get('amenity', '')
        recycling_type = tags.get('recycling_type', '')
        
        if 'container' in recycling_type:
            types = ['general']
        elif amenity == 'recycling':
            types = ['general']
    
    return types if types else ['general']


def _parse_osm_element(element: Dict[str, Any], user_lat: float, user_lon: float) -> Optional[RecyclingPoint]:
    """
    Converts an OSM element to RecyclingPoint.
    """
    try:
        tags = element.get('tags', {})
        
        if element['type'] == 'node':
            lat = element['lat']
            lon = element['lon']
        else:
            center = element.get('center', {})
            lat = center.get('lat')
            lon = center.get('lon')
            if lat is None or lon is None:
                return None
        
        name = tags.get('name', '')
        if not name:
            operator = tags.get('operator', '')
            recycling_type = tags.get('recycling_type', 'recycling')
            if operator:
                name = f"{operator} - {recycling_type.title()}"
            else:
                name = f"Recycling Point ({recycling_type.title()})"
        
        address_parts = []
        if tags.get('addr:street'):
            street = tags['addr:street']
            if tags.get('addr:housenumber'):
                street += f" {tags['addr:housenumber']}"
            address_parts.append(street)
        if tags.get('addr:city'):
            address_parts.append(tags['addr:city'])
        address = ', '.join(address_parts) if address_parts else None
        
        distance = calculate_distance(user_lat, user_lon, lat, lon)
        
        return RecyclingPoint(
            id=f"{element['type']}_{element['id']}",
            name=name,
            latitude=lat,
            longitude=lon,
            address=address,
            types=_parse_recycling_types(tags),
            opening_hours=tags.get('opening_hours'),
            phone=tags.get('phone') or tags.get('contact:phone'),
            website=tags.get('website') or tags.get('contact:website'),
            distance=round(distance, 2),
            operator=tags.get('operator')
        )
    except Exception as e:
        logger.warning(f"Error parsing OSM element {element.get('id')}: {e}")
        return None


def search_bounds(latitude: float, longitude: float, radius: float) -> tuple[float, float, float, float]:
    angle = radius / EARTH_RADIUS_METERS
    delta_lat = math.degrees(angle)
    south, north = max(-90, latitude - delta_lat), min(90, latitude + delta_lat)
    if south == -90 or north == 90:
        return south, -180, north, 180
    delta_lon = math.degrees(math.asin(min(1, math.sin(angle) / math.cos(math.radians(latitude)))))
    return south, (longitude - delta_lon + 180) % 360 - 180, north, (longitude + delta_lon + 180) % 360 - 180


async def fetch_recycling_points(
    latitude: float,
    longitude: float,
    radius: int = LOCATION_DEFAULT_RADIUS,
    types_filter: Optional[List[str]] = None,
    nearest: bool = False,
) -> List[RecyclingPoint]:
    """
    Searches for nearby recycling points using Overpass API.
    
    Args:
        latitude: Latitude of search center
        longitude: Longitude of search center
        radius: Search radius in meters
        types_filter: Optional list of material types to filter
        
    Returns:
        List of RecyclingPoint sorted by distance
    """
    cache_key = _get_cache_key(latitude, longitude, radius)
    material_selector = ""
    if nearest and types_filter and "general" not in types_filter and not _is_cache_valid(_cache.get(cache_key)):
        tags = [tag for tag, material in MATERIAL_TAGS.items() if material in types_filter]
        if not tags:
            return []
        material_selector = f'[~"^({"|".join(tags)})$"~"^yes$"]'
        cache_key += tuple(tags)
    
    if cache_key in _cache and _is_cache_valid(_cache[cache_key]):
        logger.info(f"Cache hit for {cache_key}")
        cached_elements, _ = _cache[cache_key]
        _cache.move_to_end(cache_key)
        elements = cached_elements
    else:
        # Fetch an enclosing box with Overpass's spatial index. Apply the exact
        # great-circle distance below. A west > east box crosses the date line.
        south, west, north, east = search_bounds(latitude, longitude, radius)
        query = f"""
        [out:json][timeout:{OVERPASS_QUERY_TIMEOUT}][bbox:{south},{west},{north},{east}];
        (
          nwr["amenity"="recycling"]{material_selector};
          nwr["recycling_type"]{material_selector};
        );
        out center {QUERY_BATCH_SIZE + 1 if nearest else ""};
        """
        
        data = None
        last_error = None
        
        servers = [url for url in OVERPASS_SERVERS if _retry_after.get(url, 0) <= monotonic()]
        attempt_timeout = HTTP_TIMEOUT_LOCATION / max(1, len(servers))
        for server_url in servers:
            try:
                async with httpx.AsyncClient(timeout=attempt_timeout) as client:
                    response = await client.post(server_url, data={'data': query}, headers={
                        'Accept': 'application/json',
                        'User-Agent': 'SortLeaf/1.0 (+https://luiscarlosp.github.io/SortLeafFrontend/)'
                    })
                    response.raise_for_status()
                    payload = response.json()
                    if payload.get("remark") or not isinstance(payload.get("elements"), list):
                        raise OverpassAPIError("Incomplete map provider response")
                    data = payload
                    logger.info(f"Successful query to {server_url}")
                    break
            except (httpx.HTTPError, OverpassAPIError, ValueError) as e:
                _retry_after[server_url] = monotonic() + CIRCUIT_BREAKER_RESET_TIMEOUT
                logger.warning("Map provider unavailable: %s", server_url)
                last_error = e
        
        if data is None:
            logger.error(f"All Overpass servers failed. Last error: {last_error}")
            raise OverpassAPIError("Could not connect to map service. Please try again.")
        
        elements = data.get('elements', [])
        if nearest and len(elements) > QUERY_BATCH_SIZE:
            raise CrowdedSearch()
        logger.info(f"Found {len(elements)} elements in OSM")
        
        # Keep frequent small searches reusable across filters on the free instance.
        if len(elements) <= QUERY_BATCH_SIZE:
            _cache[cache_key] = (elements, datetime.now())
            if len(_cache) > 48:
                _cache.popitem(last=False)
    
    points = []
    for element in elements:
        point = _parse_osm_element(element, latitude, longitude)
        if point and point.distance <= radius:
            points.append(point)
    
    if types_filter:
        points = [
            p for p in points
            if any(t in p.types for t in types_filter) or (not nearest and 'general' in p.types)
        ]
    
    points.sort(key=lambda p: (p.distance, p.id))
    
    logger.info(f"Returning {len(points)} recycling points")
    return points


def clear_cache():
    """Clears the recycling points cache."""
    _cache.clear()
    logger.info("Location cache cleared")


class LocationService:
    """Singleton service for location operations."""
    
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    async def get_recycling_points(
        self,
        latitude: float,
        longitude: float,
        radius: int = LOCATION_DEFAULT_RADIUS,
        types_filter: Optional[List[str]] = None,
        nearest: bool = False,
    ) -> List[RecyclingPoint]:
        """Gets nearby recycling points."""
        return await fetch_recycling_points(latitude, longitude, radius, types_filter, nearest)
    
    def calculate_distance(
        self,
        from_coords: Coordinates,
        to_coords: Coordinates
    ) -> float:
        """Calculates the distance between two coordinates."""
        return calculate_distance(
            from_coords.latitude,
            from_coords.longitude,
            to_coords.latitude,
            to_coords.longitude
        )


def get_location_service() -> LocationService:
    """Gets the location service instance."""
    return LocationService()
