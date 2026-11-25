from unittest.mock import AsyncMock, patch
import pytest
from services.geocoding_service import GeocodingUnavailable
from services.providers.barcode_providers import CatalogUnavailable

HEADERS = {"X-API-Key": "test-api-key"}

def test_place_search_contract(client):
    service = AsyncMock()
    service.search.return_value = [{"id": "1", "name": "Cartago", "latitude": 9.86, "longitude": -83.92}]
    with patch("services.geocoding_service.get_geocoding_service", return_value=service):
        response = client.get("/location/search", params={"q": " Cartago  Costa Rica ", "language": "es"}, headers=HEADERS)
    assert response.status_code == 200
    assert response.json()["results"][0]["name"] == "Cartago"
    service.search.assert_awaited_once_with("Cartago Costa Rica", "es")

@pytest.mark.parametrize("query", ["", "a", "   ", "x" * 181])
def test_invalid_search_does_not_reach_provider(client, query):
    with patch("services.geocoding_service.get_geocoding_service") as factory:
        assert client.get("/location/search", params={"q": query}, headers=HEADERS).status_code == 422
        factory.assert_not_called()

def test_search_outage_is_retryable(client):
    service = AsyncMock()
    service.search.side_effect = GeocodingUnavailable()
    with patch("services.geocoding_service.get_geocoding_service", return_value=service):
        response = client.get("/location/search?q=Cartago", headers=HEADERS)
    assert response.status_code == 503

@pytest.mark.parametrize("code", ["123456789", "3017620422004", "abcdefgh", "00000000"])
def test_invalid_barcode_does_not_reach_catalog(client, code):
    with patch("routes.barcode.fetch_product_by_barcode", new_callable=AsyncMock) as lookup:
        assert client.get(f"/barcode/{code}", headers=HEADERS).status_code == 400
        lookup.assert_not_called()

def test_barcode_outage_is_not_a_product_404(client):
    with patch("routes.barcode.fetch_product_by_barcode", new=AsyncMock(side_effect=CatalogUnavailable())):
        assert client.get("/barcode/3017620422003", headers=HEADERS).status_code == 503
