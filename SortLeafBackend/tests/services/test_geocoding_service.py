import asyncio
import time
from unittest.mock import AsyncMock, patch
import httpx
import pytest
from services.geocoding_service import GeocodingService, GeocodingUnavailable


@pytest.mark.asyncio
async def test_geocoding_caches_and_normalizes_identical_queries():
    service = GeocodingService("https://example.com/search")
    response = httpx.Response(200, json=[{"place_id": 1, "display_name": "Cartago, Costa Rica", "lat": "9.86", "lon": "-83.92"}], request=httpx.Request("GET", service.url))
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.get.return_value = response
    with patch("httpx.AsyncClient", return_value=client):
        result = await service.search("Cartago Costa Rica", "es")
        cached = await service.search(" cartago   costa rica ", "es")
    assert result == cached
    assert result[0]["latitude"] == 9.86
    client.get.assert_awaited_once()


@pytest.mark.asyncio
async def test_concurrent_searches_respect_global_spacing():
    service = GeocodingService("https://example.com/search")
    starts = []
    async def request(*args, **kwargs):
        starts.append(time.monotonic())
        return httpx.Response(200, json=[], request=httpx.Request("GET", service.url))
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.get.side_effect = request
    with patch("httpx.AsyncClient", return_value=client):
        await asyncio.gather(service.search("Cartago", "es"), service.search("Heredia", "es"))
    assert starts[1] - starts[0] >= 1


@pytest.mark.asyncio
async def test_provider_outage_cools_down_and_is_not_cached_as_empty():
    service = GeocodingService("https://example.com/search")
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.get.return_value = httpx.Response(429, request=httpx.Request("GET", service.url))
    with patch("httpx.AsyncClient", return_value=client):
        for _ in range(2):
            with pytest.raises(GeocodingUnavailable):
                await service.search("Cartago", "es")
    client.get.assert_awaited_once()
