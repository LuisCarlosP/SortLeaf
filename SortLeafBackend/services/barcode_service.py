import re
import time
import unicodedata
from collections import OrderedDict
from copy import deepcopy
from functools import lru_cache
from typing import List, Dict, Any, Optional
from core.protocols.barcode import BarcodeProviderProtocol
from services.providers.barcode_providers import CatalogUnavailable

def is_valid_gtin(code: str) -> bool:
    if not re.fullmatch(r"(?:[0-9]{8}|[0-9]{12}|[0-9]{13}|[0-9]{14})", code) or not int(code):
        return False
    total = sum(int(digit) * (3 if index % 2 == 0 else 1) for index, digit in enumerate(reversed(code[:-1])))
    return (10 - total % 10) % 10 == int(code[-1])

MATERIALS = {
    "plastic": r"plastic|plastics|plastico|plasticos|pet|hdpe|ldpe|pp|polyethylene|polypropylene|polietileno|polipropileno",
    "glass": r"glass|vidrio|verre",
    "cardboard": r"cardboard|paperboard|carton",
    "paper": r"paper|papel|papier",
    "metal": r"metal|aluminium|aluminum|aluminio|steel|acero|tinplate|hojalata",
    "composite": r"tetra\s?pak|tetra\s?brik|multilayer|multicapa|composite",
}
TIPS = {
    "plastic": ("Plástico", "Plastic", "Vacía el envase y revisa su símbolo de resina. Confirma que tu punto acepta este tipo de plástico.", "Empty the packaging and check its resin symbol. Confirm your collection point accepts this plastic type."),
    "glass": ("Vidrio", "Glass", "Separa tapas y otros materiales. Confirma que se trata de vidrio de envase aceptado por tu punto.", "Separate lids and other materials. Confirm this is packaging glass accepted by your collection point."),
    "cardboard": ("Cartón", "Cardboard", "Mantén el cartón limpio y seco; pliégalo. Si tiene recubrimientos, consulta cómo separarlo.", "Keep cardboard clean and dry; flatten it. Check local instructions for coated packaging."),
    "paper": ("Papel", "Paper", "Separa el papel limpio y seco. El papel sucio o plastificado puede requerir otra recogida.", "Separate clean, dry paper. Dirty or plastic-coated paper may require different collection."),
    "metal": ("Metal", "Metal", "Vacía el envase y consulta qué metales acepta el punto de entrega.", "Empty the packaging and check which metals your collection point accepts."),
    "composite": ("Material compuesto", "Composite material", "El envase combina capas. Consulta si tu localidad tiene una recogida específica para este tipo de envase.", "This packaging combines layers. Check whether your area offers a specific collection for it."),
    "unknown": ("Material no identificado", "Unidentified material", "El catálogo no especifica el material del envase. Revisa sus símbolos o consulta al asistente con una foto.", "The catalogue does not specify the packaging material. Check its symbols or ask the assistant with a photo."),
}

class PackagingAnalyzer:
    def analyze(self, text_to_analyze: str) -> List[Dict[str, Any]]:
        normalized = "".join(char for char in unicodedata.normalize("NFD", text_to_analyze.lower()) if unicodedata.category(char) != "Mn")
        matches = []
        for key, pattern in MATERIALS.items():
            for match in re.finditer(r"(?<!\w)(?:" + pattern + r")(?!\w)", normalized):
                before, after = normalized[max(0, match.start() - 20):match.start()], normalized[match.end():]
                if re.search(r"\b(?:no|not|without|sin|sans)\s+$", before) or re.match(r"[ -]*free\b", after):
                    continue
                matches.append(key)
                break
        return [self._info(key) for key in matches]

    def _info(self, material: str) -> dict:
        return {"material": TIPS[material][1], "material_key": material, "recyclable": None, "bin": None,
                "tip": TIPS[material][3], "bin_type": "unknown"}

    def get_default_info(self) -> List[Dict[str, Any]]:
        return [self._info("unknown")]

def localize_product(product: dict, language: str) -> dict:
    result = deepcopy(product)
    for info in result.get("recycling_info", []):
        text = TIPS.get(info.get("material_key"))
        if text:
            info["material"] = text[0 if language == "es" else 1]
            info["tip"] = text[2 if language == "es" else 3]
    return result

class BarcodeService:
    def __init__(self, providers: List[BarcodeProviderProtocol], analyzer: Optional[PackagingAnalyzer] = None):
        self._providers = providers
        self._analyzer = analyzer or PackagingAnalyzer()
        self._cache = OrderedDict()

    async def fetch_product(self, barcode: str) -> Optional[Dict[str, Any]]:
        cached = self._cache.get(barcode)
        if cached and cached[0] > time.monotonic():
            self._cache.move_to_end(barcode)
            return deepcopy(cached[1])
        product = None
        unavailable = False
        for provider in self._providers:
            try:
                product = await provider.fetch_product(barcode)
            except CatalogUnavailable:
                unavailable = True
                continue
            if product:
                product = deepcopy(product)
                text = " ".join(product.get("packaging_materials") or []) + " " + product.get("packaging", "")
                product["recycling_info"] = self._analyzer.analyze(text) or self._analyzer.get_default_info()
                break
        if not product and unavailable:
            raise CatalogUnavailable("Some product catalogues are unavailable")
        self._cache[barcode] = (time.monotonic() + (3600 if product else 120), deepcopy(product))
        if len(self._cache) > 512:
            self._cache.popitem(last=False)
        return product

@lru_cache(maxsize=1)
def get_barcode_service() -> BarcodeService:
    from services.providers.barcode_providers import OpenFoodFactsProvider, UPCItemDBProvider
    return BarcodeService([OpenFoodFactsProvider(), UPCItemDBProvider()])

async def fetch_product_by_barcode(barcode: str) -> Optional[Dict[str, Any]]:
    return await get_barcode_service().fetch_product(barcode)

async def fetch_from_upcitemdb(barcode: str) -> Optional[Dict[str, Any]]:
    from services.providers.barcode_providers import UPCItemDBProvider
    return await UPCItemDBProvider().fetch_product(barcode)

def analyze_packaging(text_to_analyze: str) -> List[Dict[str, Any]]:
    return PackagingAnalyzer().analyze(text_to_analyze)
