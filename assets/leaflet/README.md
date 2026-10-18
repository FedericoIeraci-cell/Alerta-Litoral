# Leaflet 1.9.4 incluido en la aplicación

Archivos oficiales de la versión estable 1.9.4, descargados el 18/10/2026.
No se descarga JavaScript ni CSS de un CDN al mostrar el mapa. El módulo
`mapa_litoral.py` inserta estas copias locales y convierte las imágenes de la
hoja de estilos en datos incluidos en el documento.

Origen de JS, CSS e imágenes: `https://unpkg.com/leaflet@1.9.4/dist/`.
La [página oficial de descargas](https://leafletjs.com/download.html) publica
esa distribución y estos hashes SHA-256, verificados antes de guardar:

| Archivo | SHA-256 en base64 |
| --- | --- |
| leaflet.js | 20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo= |
| leaflet.css | p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY= |

La licencia BSD de dos cláusulas se conserva íntegra en `LICENSE`, obtenida de
`https://raw.githubusercontent.com/Leaflet/Leaflet/v1.9.4/LICENSE`.

Se utiliza la API estable [Leaflet 1.9.4](https://leafletjs.com/reference.html):
`L.svg`, `L.geoJSON`, `L.circleMarker`, `fitBounds` y controles de zoom/escala.
Los datos y polígonos se dibujan sin WebGL. El fondo raster de OpenStreetMap
es opcional y mantiene su atribución; si no carga, las capas locales permanecen.

Las geometrías provinciales proceden del catálogo que entrega la app ALA
(GeoRef v2 / IGN); este componente no las descarga, reemplaza ni interpola.
Zárate–Campana puede usar el mismo componente sin polígonos y encuadrar sus
referencias públicas. No se consulta ni se transmite la ubicación del usuario.
