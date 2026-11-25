import logging
from fastapi import APIRouter, HTTPException, Request, Query
from typing import Literal
from core.rate_limit import limiter

from services.barcode_service import fetch_product_by_barcode, is_valid_gtin, localize_product
from services.providers.barcode_providers import CatalogUnavailable
from config.settings import RATE_LIMIT_BARCODE

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/barcode", tags=["Barcode"])


@router.get(
    "/health",
    summary="Barcode service status"
)
async def barcode_health():
    return {
        "status": "healthy",
        "service": "barcode",
        "message": "Barcode service working"
    }


@router.get(
    "/{barcode}",
    summary="Search product by barcode",
    description="Queries Open Facts and UPCitemdb for product and packaging information"
)
@limiter.limit(RATE_LIMIT_BARCODE)
async def get_product_by_barcode(
    request: Request,
    barcode: str,
    language: Literal["es", "en"] = Query("es")
):
    if not is_valid_gtin(barcode):
        raise HTTPException(status_code=400, detail="Invalid barcode")
    
    try:
        product = await fetch_product_by_barcode(barcode)
    except CatalogUnavailable:
        raise HTTPException(status_code=503, detail="Product catalogs temporarily unavailable", headers={"Retry-After": "60"})
    
    if not product:
        raise HTTPException(
            status_code=404,
            detail="Product not found. Try another code."
        )
    
    logger.info(f"Product found: {product.get('name')} ({barcode})")
    return localize_product(product, language)
