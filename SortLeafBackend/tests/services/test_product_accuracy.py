from unittest.mock import AsyncMock, patch
import httpx
import pytest
from services.barcode_service import BarcodeService, PackagingAnalyzer, is_valid_gtin, localize_product
from services.providers.barcode_providers import OpenFoodFactsProvider, UPCItemDBProvider, CatalogUnavailable


@pytest.mark.parametrize("code,valid", [("96385074", True), ("036000291452", True), ("3017620422003", True), ("10012345000017", True), ("3017620422004", False), ("123456789", False), ("00000000", False), ("abcdefgh", False)])
def test_gtin_check_digit(code, valid):
    assert is_valid_gtin(code) is valid


def test_container_shapes_do_not_identify_a_material():
    assert PackagingAnalyzer().analyze("bottle, jar, bag, box, wrapper, can") == []
    assert PackagingAnalyzer().analyze("organic candy") == []
    assert PackagingAnalyzer().analyze("plastic-free, sin metal") == []


@pytest.mark.asyncio
async def test_packaging_components_combined_without_category_inference():
    provider = AsyncMock()
    provider.fetch_product.return_value = {"name": "Glass cleaner", "categories": "glass", "packaging": "", "packaging_materials": ["en:pet-1"]}
    service = BarcodeService([provider])
    result = await service.fetch_product("3017620422003")
    assert [info["material_key"] for info in result["recycling_info"]] == ["plastic"]
    result["name"] = "mutated"
    cached = await service.fetch_product("3017620422003")
    assert cached["name"] == "Glass cleaner"
    provider.fetch_product.assert_awaited_once()
    assert localize_product(cached, "es")["recycling_info"][0]["material"] == "Plástico"
    assert cached["recycling_info"][0]["material"] == "Plastic"


@pytest.mark.asyncio
async def test_partial_structured_packaging_does_not_hide_other_components():
    provider = AsyncMock()
    provider.fetch_product.return_value = {"packaging_materials": ["en:plastic"], "packaging": "glass jar, aluminium seal"}
    result = await BarcodeService([provider]).fetch_product("3017620422003")
    assert {info["material_key"] for info in result["recycling_info"]} == {"glass", "plastic", "metal"}


@pytest.mark.asyncio
async def test_outage_falls_back_but_is_not_reported_as_unknown_product():
    failing, fallback = AsyncMock(), AsyncMock()
    failing.fetch_product.side_effect = CatalogUnavailable()
    fallback.fetch_product.return_value = {"name": "Product", "packaging": ""}
    assert (await BarcodeService([failing, fallback]).fetch_product("3017620422003"))["name"] == "Product"
    fallback.fetch_product.return_value = None
    with pytest.raises(CatalogUnavailable):
        await BarcodeService([failing, fallback]).fetch_product("3017620422003")


@pytest.mark.asyncio
async def test_universal_catalog_includes_beauty_and_requests_redirects():
    provider = OpenFoodFactsProvider()
    response = httpx.Response(200, json={"status": "success", "product": {"product_type": "beauty", "product_name": "Soap", "packagings": [{"material": {"id": "en:paper"}}]}}, request=httpx.Request("GET", "https://example.com"))
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.get.return_value = response
    with patch("httpx.AsyncClient", return_value=client) as constructor:
        product = await provider.fetch_product("3017620422003")
        assert constructor.call_args.kwargs["follow_redirects"] is True
        assert client.get.call_args.kwargs["params"]["product_type"] == "all"
        assert product["source"] == "openbeautyfacts"
        assert product["packaging_materials"] == ["en:paper"]


@pytest.mark.asyncio
async def test_upc_description_is_not_packaging_and_empty_images_work():
    provider = UPCItemDBProvider()
    with patch.object(provider, "_get", new=AsyncMock(return_value={"code": "OK", "items": [{"title": "Glass cleaner", "description": "Cleans glass", "images": []}]})):
        result = await provider.fetch_product("3017620422003")
        assert result["packaging"] == ""
        assert result["image_url"] is None


@pytest.mark.asyncio
async def test_rate_limited_catalog_is_not_queried_again_immediately():
    provider = OpenFoodFactsProvider()
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.get.return_value = httpx.Response(429, headers={"Retry-After": "120"}, request=httpx.Request("GET", "https://example.com"))
    with patch("httpx.AsyncClient", return_value=client):
        for _ in range(2):
            with pytest.raises(CatalogUnavailable):
                await provider.fetch_product("3017620422003")
    client.get.assert_awaited_once()
