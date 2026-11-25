"""Free catalogue lookups. Missing entries and provider outages are distinct."""
import logging
import time
from typing import Any, Dict, Optional
import httpx
from config.settings import get_settings, HTTP_TIMEOUT_BARCODE, UPCITEMDB_URL

logger = logging.getLogger(__name__)
USER_AGENT = "SortLeaf/1.0 (+https://luiscarlosp.github.io/SortLeafFrontend/)"

class CatalogUnavailable(Exception):
    pass

class CatalogProvider:
    def __init__(self, base_url: str, timeout: float):
        self._base_url = base_url
        self._timeout = min(timeout, 12)
        self._retry_at = 0.0

    async def _get(self, url: str, params: dict) -> Optional[dict]:
        if time.monotonic() < self._retry_at:
            raise CatalogUnavailable("Catalog cooling down")
        try:
            async with httpx.AsyncClient(timeout=self._timeout, follow_redirects=True,
                                         headers={"User-Agent": USER_AGENT, "Accept": "application/json"}) as client:
                response = await client.get(url, params=params)
                if response.status_code == 404:
                    return None
                if response.status_code == 429:
                    try:
                        wait = float(response.headers.get("Retry-After", "60"))
                    except (ValueError, TypeError):
                        wait = 60
                    self._retry_at = time.monotonic() + max(60, wait)
                response.raise_for_status()
                data = response.json()
                if not isinstance(data, dict):
                    raise ValueError("Invalid catalog response")
                return data
        except (httpx.HTTPError, ValueError) as error:
            logger.warning("Product catalog unavailable (%s)", type(error).__name__)
            self._retry_at = max(self._retry_at, time.monotonic() + 30)
            raise CatalogUnavailable("Catalog temporarily unavailable") from error

class OpenFoodFactsProvider(CatalogProvider):
    def __init__(self, base_url: Optional[str] = None, timeout: float = HTTP_TIMEOUT_BARCODE):
        super().__init__(base_url or get_settings().OPEN_FACTS_V3_URL, timeout)

    @property
    def name(self) -> str:
        return "openfoodfacts"

    async def fetch_product(self, barcode: str) -> Optional[Dict[str, Any]]:
        data = await self._get(f"{self._base_url.rstrip('/')}/{barcode}", {
            "product_type": "all", "lc": "es",
            "fields": "code,product_type,product_name,product_name_es,product_name_en,brands,image_front_url,packaging,packaging_text,packagings,categories",
        })
        if not data or data.get("status") not in (1, "success", "success_with_warnings"):
            return None
        product = data.get("product")
        if not isinstance(product, dict) or not product:
            return None
        catalog = {"beauty": "openbeautyfacts", "petfood": "openpetfoodfacts", "product": "openproductsfacts"}.get(product.get("product_type"), "openfoodfacts")
        # Only actual packaging material tags; a glass cleaner need not be in glass.
        materials = []
        for component in product.get("packagings") or []:
            material = component.get("material") if isinstance(component, dict) else None
            if isinstance(material, dict) and material.get("id"):
                materials.append(material["id"])
        return {
            "found": True, "barcode": barcode,
            "name": product.get("product_name_es") or product.get("product_name") or product.get("product_name_en") or barcode,
            "brand": product.get("brands") or "",
            "image_url": product.get("image_front_url") or None,
            "packaging": product.get("packaging") or product.get("packaging_text") or "",
            "packaging_materials": materials,
            "categories": product.get("categories") or "",
            "source": catalog,
            "source_url": f"https://world.{catalog}.org/product/{barcode}",
        }

class UPCItemDBProvider(CatalogProvider):
    def __init__(self, base_url: str = UPCITEMDB_URL, timeout: float = HTTP_TIMEOUT_BARCODE):
        super().__init__(base_url, timeout)

    @property
    def name(self) -> str:
        return "upcitemdb"

    async def fetch_product(self, barcode: str) -> Optional[Dict[str, Any]]:
        data = await self._get(self._base_url, {"upc": barcode})
        if not data:
            return None
        if data.get("code") != "OK":
            self._retry_at = time.monotonic() + 60
            raise CatalogUnavailable("UPC catalogue limit or service error")
        items = data.get("items") or []
        if not items:
            return None
        item = items[0]
        images = item.get("images") or []
        return {
            "found": True, "barcode": barcode,
            "name": item.get("title") or barcode, "brand": item.get("brand") or "",
            "image_url": images[0] if images else None,
            "packaging": "", "categories": item.get("category") or "",
            "source": self.name, "source_url": f"https://www.upcitemdb.com/upc/{barcode}",
        }
