# Catálogo de reciclaje de Costa Rica

El archivo `costa-rica-recycling.json` contiene los puntos de reciclaje registrados en OpenStreetMap dentro del [extracto de Costa Rica de Geofabrik](https://download.geofabrik.de/central-america/costa-rica.html). Incluye los identificadores de OSM, coordenadas, etiquetas de reciclaje y el perímetro del extracto. El campo `as_of` indica la fecha de los datos.

© OpenStreetMap contributors. La base derivada se distribuye bajo la [Open Database License 1.0](https://opendatacommons.org/licenses/odbl/1-0/). [Información de atribución](https://www.openstreetmap.org/copyright).

Para actualizarla, descarga `costa-rica-latest.osm.pbf` y `costa-rica.poly` de Geofabrik. Desde la carpeta del backend, en un entorno de desarrollo con `osmium` instalado, ejecuta `python tools/build_recycling_catalog.py costa-rica-latest.osm.pbf costa-rica.poly`. El despliegue solo necesita el archivo JSON resultante.
