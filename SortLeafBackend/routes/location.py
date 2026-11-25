"""
Routes for location functionality and recycling points.
"""

import asyncio
import math
import logging
import unicodedata
from typing import List, Optional, Literal
from fastapi import APIRouter, Query, HTTPException, Request
from fastapi.responses import JSONResponse
from core.rate_limit import limiter

from models.location_models import (
    RecyclingPoint,
    RecyclingPointsRequest,
    RecyclingPointsResponse,
    Coordinates
)
from services.location_service import CrowdedSearch, WORLD_DISTANCE, get_location_service
from services.regional_recycling import covers_search, load_catalog, regional_candidates
from exceptions.location_exceptions import (
    LocationError,
    OverpassAPIError,
    InvalidCoordinatesError,
    InvalidRadiusError
)
from config.settings import (
    RATE_LIMIT_LOCATION,
    HTTP_TIMEOUT_LOCATION,
    LOCATION_DEFAULT_RADIUS,
    LOCATION_MIN_RADIUS,
    LOCATION_MAX_RADIUS
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/location", tags=["Location"])


@router.get("/search", summary="Find a city, address or place")
@limiter.limit("10/minute")
async def search_places(request: Request, q: str = Query(..., min_length=3, max_length=180), language: Literal["es", "en"] = Query("es")):
    from services.geocoding_service import get_geocoding_service, GeocodingUnavailable
    query = " ".join(q.split())
    if len(query) < 3:
        raise HTTPException(status_code=422, detail="Enter at least three characters")
    try:
        results = await get_geocoding_service().search(query, language)
        return {"results": results, "attribution": "© OpenStreetMap contributors"}
    except GeocodingUnavailable:
        raise HTTPException(status_code=503, detail="Location search temporarily unavailable", headers={"Retry-After": "60"})


def validate_coordinates(latitude: float, longitude: float) -> None:
    """Validates that coordinates are in valid ranges."""
    if not (-90 <= latitude <= 90):
        raise InvalidCoordinatesError(f"Latitude {latitude} out of range (-90 to 90)")
    if not (-180 <= longitude <= 180):
        raise InvalidCoordinatesError(f"Longitude {longitude} out of range (-180 to 180)")


def validate_radius(radius: int) -> None:
    """Validates that the radius is in the allowed range."""
    if not (LOCATION_MIN_RADIUS <= radius <= LOCATION_MAX_RADIUS):
        raise InvalidRadiusError(f"Radius {radius} out of range ({LOCATION_MIN_RADIUS} to {LOCATION_MAX_RADIUS} meters)")


def _search_text(value: str) -> str:
    return "".join(char for char in unicodedata.normalize("NFD", value.casefold())
                   if not unicodedata.combining(char))


@router.get(
    "/recycling-points",
    response_model=RecyclingPointsResponse,
    summary="Search nearby recycling points",
    description="Finds the nearest matching recycling points. An explicit radius restricts the search to that area."
)
@limiter.limit(RATE_LIMIT_LOCATION)
async def get_recycling_points(
    request: Request,
    latitude: float = Query(..., ge=-90, le=90, description="Latitude of search center"),
    longitude: float = Query(..., ge=-180, le=180, description="Longitude of search center"),
    radius: Optional[int] = Query(None, ge=LOCATION_MIN_RADIUS, le=LOCATION_MAX_RADIUS, description="Optional fixed search radius in meters"),
    limit: Optional[int] = Query(None, ge=1, le=10, description="Maximum number of nearest matches"),
    q: str = Query("", max_length=180, description="Point name or address"),
    types: Optional[str] = Query(
        None,
        description="Material types separated by comma (plastic,glass,paper,metal,cardboard,electronics,batteries)"
    )
):
    automatic = radius is None
    target = (limit or 10) if automatic else limit
    materials = [t.strip().lower() for t in (types or "").split(",") if t.strip()]
    needle = _search_text(q.strip())
    service = get_location_service()
    origin = Coordinates(latitude=latitude, longitude=longitude)

    def select_matches(candidates):
        return [
            p for p in candidates
            if (not materials or target is None or any(t in p.types for t in materials))
            and (not needle or needle in _search_text(f"{p.name} {p.address or ''}"))
        ][:target]

    try:
        if not automatic:
            points = select_matches(await service.get_recycling_points(
                latitude=latitude, longitude=longitude, radius=radius, types_filter=materials or None,
            ))
            return RecyclingPointsResponse(count=len(points), radius=radius, center=origin, points=points)

        candidates = regional_candidates(latitude, longitude)
        catalog = load_catalog() if candidates is not None else None
        regional = select_matches(candidates or [])
        # A known tenth point bounds the work needed to certify a nearest-ten result.
        extent = math.ceil(regional[-1].distance) if len(regional) == target else WORLD_DISTANCE
        matches = regional
        completed_radius = 0
        search_radius = min(LOCATION_DEFAULT_RADIUS, extent)
        upper = extent
        partial = False
        try:
            async with asyncio.timeout(HTTP_TIMEOUT_LOCATION):
                while True:
                    if catalog and covers_search(catalog["boundary"], latitude, longitude, search_radius):
                        completed_radius = search_radius
                    else:
                        try:
                            live = await service.get_recycling_points(
                                latitude=latitude, longitude=longitude, radius=search_radius,
                                types_filter=materials or None, nearest=True,
                            )
                        except CrowdedSearch:
                            # Never rank an arbitrarily truncated provider batch.
                            upper = search_radius
                            if upper - completed_radius <= 1:
                                raise OverpassAPIError("Map provider result too dense")
                            search_radius = (completed_radius + upper) // 2
                            continue
                        by_id = {p.id: p for p in [*regional, *select_matches(live)]}
                        matches = sorted(by_id.values(), key=lambda p: (p.distance, p.id))[:target]
                        completed_radius = search_radius

                    if completed_radius == extent or (
                        len(matches) == target and matches[-1].distance <= completed_radius
                    ):
                        break
                    if await request.is_disconnected():
                        partial = True
                        break
                    if upper < extent:
                        search_radius = (completed_radius + upper + 1) // 2
                    else:
                        search_radius = min(extent, max(1, search_radius * 5))
        except (OverpassAPIError, TimeoutError) as exc:
            if not matches:
                raise OverpassAPIError("Map provider temporarily unavailable") from exc
            partial = True
        return RecyclingPointsResponse(
            count=len(matches), radius=completed_radius, partial=partial, center=origin, points=matches,
            catalog_date=catalog["as_of"][:10] if regional else None,
        )
    except LocationError as e:
        logger.error(f"Location error: {e}")
        raise HTTPException(status_code=e.code, detail=e.message)
    except Exception as e:
        logger.error(f"Unexpected error searching recycling points: {e}")
        raise HTTPException(
            status_code=500,
            detail="Internal error searching recycling points"
        )


@router.post(
    "/recycling-points/search",
    response_model=RecyclingPointsResponse,
    summary="Search recycling points (POST)",
    description="POST alternative to search recycling points"
)
@limiter.limit(RATE_LIMIT_LOCATION)
async def search_recycling_points(
    request: Request,
    search_request: RecyclingPointsRequest
):
    """
    Search recycling points using a JSON body.
    Useful for applications that prefer sending data via POST.
    """
    try:
        validate_coordinates(search_request.latitude, search_request.longitude)
        validate_radius(search_request.radius)
        
        location_service = get_location_service()
        points = await location_service.get_recycling_points(
            latitude=search_request.latitude,
            longitude=search_request.longitude,
            radius=search_request.radius,
            types_filter=search_request.types
        )
        
        return RecyclingPointsResponse(
            success=True,
            count=len(points),
            radius=search_request.radius,
            center=Coordinates(
                latitude=search_request.latitude,
                longitude=search_request.longitude
            ),
            points=points
        )
        
    except InvalidCoordinatesError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except InvalidRadiusError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except LocationError as e:
        raise HTTPException(status_code=e.code, detail=e.message)
    except Exception as e:
        logger.error(f"Error in POST search: {e}")
        raise HTTPException(
            status_code=500,
            detail="Internal error searching recycling points"
        )


@router.get(
    "/health",
    summary="Location service status",
    description="Verifies that the location service is working"
)
async def location_health():
    """Health endpoint for the location service."""
    return {
        "status": "healthy",
        "service": "location",
        "message": "Location service working correctly"
    }
