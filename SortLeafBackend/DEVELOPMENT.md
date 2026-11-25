# Desarrollo local de SortLeaf

El frontend React usa `/chat/turn` para texto, imagen o ambos en una misma conversación. Las rutas anteriores de chat y clasificación se conservan.

## Entorno Windows

Se admite Python 3.12. `requirements.txt` selecciona `python-magic-bin` en Windows y evita instalar la versión antigua de GCS que no tiene rueda para Python 3.12. El modelo del proyecto se carga desde disco local.

```powershell
python -m venv .venv-sortleaf
.\.venv-sortleaf\Scripts\python.exe -m pip install -r requirements.txt
.\.venv-sortleaf\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 8000
```

FastAPI lee `SortLeaf/.env` desde cualquier directorio de inicio. En Render se usan las variables del servicio. Ajustes de desarrollo para la interfaz:

```dotenv
GROQ_MODEL=openai/gpt-oss-20b
ALLOWED_MIME_TYPES=image/jpeg,image/png,image/webp
```

El modelo anterior `llama-3.3-70b-versatile` devolvió 404 para la cuenta configurada. El reemplazo se verificó con el listado de modelos de esa cuenta y una respuesta real. Catálogo: https://console.groq.com/docs/models.

El mapa utiliza servidores públicos Overpass configurables, caché y límites de solicitudes existentes. El servidor VK Maps funcionó en la verificación local; la dirección de Kumi ahora es `overpass.private.coffee`. Directorio: https://wiki.openstreetmap.org/wiki/Overpass_API#Public_Overpass_API_instances. Son proveedores externos; su disponibilidad no está garantizada.

## Pruebas del cambio

```powershell
.\.venv-sortleaf\Scripts\python.exe -m pytest tests/services/test_chat_service.py tests/routes/test_chat.py tests/routes/test_location.py tests/conversation -q
```

26 pruebas pasaron. Verificación real adicional: texto, foto WebP con texto y continuidad de sesión; clasificación de plástico; dos puntos de reciclaje en el radio de 2 km alrededor del centro público de San José. No se usaron credenciales personales para probar autenticación ni se enviaron correos.

Las conversaciones del servidor aún viven en memoria: reiniciar el proceso invalida esas sesiones. El frontend conserva la copia local y permite abrir una nueva conversación. El clasificador identifica materiales, no realiza descripción visual general.

## Product catalogue and place search

`OPEN_FACTS_V3_URL` defaults to `https://world.openfoodfacts.org/api/v3/product`. Queries use `product_type=all`, follow the provider's redirects, identify SortLeaf via User-Agent and request only product/packaging fields. UPCitemdb remains the existing free trial fallback (100 requests/day); no subscription or paid key was added. Catalogue outages are distinct from missing products, and results are cached in memory (512 entries, positive 1h / negative 2m). GTIN checksums are validated before any external call.

`GET /barcode/{code}?language=es|en` returns source/source_url and packaging guidance. `recyclable` and `bin` remain null because a catalogue entry cannot establish local collection rules. Generic shapes and marketing categories no longer imply a material. `GET /barcode/health` is registered before the dynamic route.

`GET /location/search?q=Cartago&language=es` performs submit-only geocoding. `GEOCODING_URL` defaults to the public Nominatim search endpoint. Results are cached for 24h (512 entries); requests are serialized at least 1.1s apart with cooldown on errors. **Use one worker and one application instance with this public provider.** Multiple workers need a shared proxy/rate limiter or another provider; the in-process lock is not a distributed limit. The endpoint is configurable without code changes. No autocomplete or periodic searches. Attribution is displayed in the frontend. [Usage policy](https://operations.osmfoundation.org/policies/nominatim/).

Validation for this iteration (64 tests):

```powershell
.\.venv-sortleaf\Scripts\python.exe -m pytest tests/services/test_barcode_service.py tests/services/test_product_accuracy.py tests/services/test_geocoding_service.py tests/services/test_chat_service.py tests/routes/test_barcode.py tests/routes/test_tool_search.py tests/routes/test_location.py tests/routes/test_chat.py tests/conversation -q
```

Live checks: food entry via Open Facts, non-food book via UPCitemdb, Cartago search and two nearby collection points. These checks do not establish global catalogue coverage or physical-camera accuracy.
