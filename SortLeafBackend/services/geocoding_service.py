"""Submit-only geocoding, cached and serialized to respect the public service."""
import asyncio
import math
import time
from collections import OrderedDict
from copy import deepcopy
from functools import lru_cache
import httpx
from config.settings import get_settings
from services.providers.barcode_providers import USER_AGENT


class GeocodingUnavailable(Exception):
    pass


class GeocodingService:
    def __init__(self, url: str):
        self.url = url
        self._cache = OrderedDict()
        self._lock = asyncio.Lock()
        self._next_request = 0.0

    async def search(self, query: str, language: str) -> list[dict]:
        key = (" ".join(query.split()).casefold(), language)
        async with self._lock:
            cached = self._cache.get(key)
            if cached and cached[0] > time.monotonic():
                self._cache.move_to_end(key)
                return deepcopy(cached[1])
            # One worker is required with the public endpoint: one request/sec
            # across this application, including concurrent users. No autocomplete.
            wait = self._next_request - time.monotonic()
            if wait > 2:
                raise GeocodingUnavailable("Geocoder is cooling down")
            if wait > 0:
                await asyncio.sleep(wait)
            self._next_request = time.monotonic() + 1.1
            try:
                async with httpx.AsyncClient(timeout=12, headers={"User-Agent": USER_AGENT}) as client:
                    response = await client.get(self.url, params={"q": query, "format": "jsonv2", "limit": 5, "accept-language": language})
                    if response.status_code == 429:
                        try:
                            cooldown = max(60, float(response.headers.get("Retry-After", "60")))
                        except (TypeError, ValueError):
                            cooldown = 60
                        self._next_request = time.monotonic() + cooldown
                    response.raise_for_status()
                    data = response.json()
                    if not isinstance(data, list):
                        raise ValueError("Invalid geocoder response")
                    results = []
                    for item in data:
                        latitude, longitude = float(item["lat"]), float(item["lon"])
                        if not (math.isfinite(latitude) and math.isfinite(longitude) and -90 <= latitude <= 90 and -180 <= longitude <= 180):
                            continue
                        results.append({"id": str(item["place_id"]), "name": item["display_name"], "latitude": latitude, "longitude": longitude})
            except (httpx.HTTPError, ValueError, KeyError, TypeError) as error:
                self._next_request = max(self._next_request, time.monotonic() + 10)
                raise GeocodingUnavailable("Location search is temporarily unavailable") from error
            self._cache[key] = (time.monotonic() + 86400, results)
            if len(self._cache) > 512:
                self._cache.popitem(last=False)
            return deepcopy(results)


@lru_cache(maxsize=1)
def get_geocoding_service() -> GeocodingService:
    return GeocodingService(get_settings().GEOCODING_URL)
