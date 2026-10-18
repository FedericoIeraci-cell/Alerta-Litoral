"""Mapa público 2D, con geometrías y referencias incluidas en el HTML.

Leaflet 1.9.4 se distribuye en assets/leaflet; no se descarga código al abrir
el mapa. Provincias y puntos usan SVG (o Canvas 2D si el navegador lo requiere).
El fondo raster de OpenStreetMap es opcional e independiente de esas capas.
Este módulo no consulta servicios, importa Streamlit ni calcula riesgo.
"""
from base64 import b64encode
from functools import lru_cache
from html import escape
import json
import math
from pathlib import Path
import re


VERSION_MAPA = '2026-10-18.1'
_ASSETS = Path(__file__).resolve().parent / 'assets' / 'leaflet'
_ESTADOS = {
    'ROJO': ('#dc2626', 'Atención prioritaria'),
    'AMARILLO': ('#eab308', 'Prepararse'),
    'VERDE': ('#16a34a', 'Seguimiento'),
    'SIN DATOS': ('#64748b', 'Sin datos suficientes'),
}


def _coordenada(valor, limite):
    if isinstance(valor, bool):
        return None
    try:
        numero = float(valor)
    except (TypeError, ValueError, OverflowError):
        return None
    return numero if math.isfinite(numero) and abs(numero) <= limite else None


def _geometria_publica(geometria):
    """Conserva Polygon/MultiPolygon y sus huecos; descarta estructuras inválidas."""
    if not isinstance(geometria, dict) or geometria.get('type') not in ('Polygon', 'MultiPolygon'):
        return None
    tipo = geometria['type']
    poligonos = [geometria.get('coordinates')] if tipo == 'Polygon' else geometria.get('coordinates')
    if not isinstance(poligonos, (list, tuple)) or not poligonos:
        return None
    salida = []
    for poligono in poligonos:
        if not isinstance(poligono, (list, tuple)) or not poligono:
            return None
        anillos = []
        for anillo in poligono:
            if not isinstance(anillo, (list, tuple)) or len(anillo) < 4:
                return None
            puntos = []
            for punto in anillo:
                if not isinstance(punto, (list, tuple)) or len(punto) < 2:
                    return None
                lon, lat = _coordenada(punto[0], 180), _coordenada(punto[1], 90)
                if lon is None or lat is None:
                    return None
                puntos.append([lon, lat])
            if puntos[0] != puntos[-1]:
                return None
            anillos.append(puntos)
        salida.append(anillos)
    return {'type': tipo, 'coordinates': salida[0] if tipo == 'Polygon' else salida}


def construir_datos_mapa(df, geometrias, tarjeta_nodo=None, *,
                         ajustar_a='geometrias', titulo='Mapa de riesgo del Litoral',
                         leyenda='Evaluación integrada'):
    """Prepara sólo datos públicos; conserva puntos sin índice y todos los estados.

    ``tarjeta_nodo`` recibe una copia de la fila y devuelve HTML ya escapado por
    el formateador de la app. Si se omite, se utiliza un texto mínimo escapado.
    No se modifica el dataframe ni se reclasifican los valores de riesgo.
    """
    if ajustar_a not in ('geometrias', 'puntos'):
        raise ValueError("ajustar_a debe ser 'geometrias' o 'puntos'")
    filas = df.to_dict('records') if hasattr(df, 'to_dict') else list(df or [])
    puntos, omitidos = [], 0
    for fila in filas:
        lat, lon = _coordenada(fila.get('lat'), 90), _coordenada(fila.get('lon'), 180)
        if lat is None or lon is None:
            omitidos += 1
            continue
        nombre = str(fila.get('localidad') or fila.get('nombre') or 'Referencia')
        nivel = str(fila.get('nivel') or 'SIN DATOS')
        color, estado = _ESTADOS.get(nivel, _ESTADOS['SIN DATOS'])
        popup = (str(tarjeta_nodo(dict(fila))) if tarjeta_nodo else
                 f'<b>{escape(nombre)}</b><br>{escape(nivel)}')
        puntos.append({'nombre': nombre, 'lat': lat, 'lon': lon, 'nivel': nivel,
                       'color': color, 'estado': estado, 'popup': popup})
    features, geometrias_omitidas = [], 0
    for nombre, geometria in (geometrias or {}).items():
        publica = _geometria_publica(geometria)
        if publica is None:
            geometrias_omitidas += 1
            continue
        features.append({'type': 'Feature', 'properties': {'nombre': str(nombre)},
                         'geometry': publica})
    return {
        'titulo': str(titulo), 'leyenda': str(leyenda), 'ajustar_a': ajustar_a,
        'puntos': puntos, 'geometrias': {'type': 'FeatureCollection', 'features': features},
        'omitidos': omitidos, 'geometrias_omitidas': geometrias_omitidas,
        'estados': [{'nivel': nivel, 'color': color, 'texto': texto}
                    for nivel, (color, texto) in _ESTADOS.items()],
    }


def _json_html(datos):
    # Un nombre o un popup no puede cerrar el bloque JSON e introducir scripts.
    return (json.dumps(datos, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
            .replace('&', '\\u0026').replace('<', '\\u003c').replace('>', '\\u003e')
            .replace('\u2028', '\\u2028').replace('\u2029', '\\u2029'))


@lru_cache(maxsize=1)
def _leer_leaflet():
    css = (_ASSETS / 'leaflet.css').read_text(encoding='utf-8')
    # El iframe srcdoc no tiene una ruta pública de assets. Las imágenes de la
    # hoja oficial se incluyen como data URI, sin resolver rutas contra la app.
    def imagen(match):
        nombre = match.group(1)
        contenido = b64encode((_ASSETS / 'images' / nombre).read_bytes()).decode('ascii')
        return 'url("data:image/png;base64,' + contenido + '")'
    css = re.sub(r'url\(images/([\w-]+\.png)\)', imagen, css)
    js = (_ASSETS / 'leaflet.js').read_text(encoding='utf-8')
    # El archivo vendorizado permanece intacto; el mapa no solicita sourcemaps.
    js = re.sub(r'(?m)^//# sourceMappingURL=.*$', '', js)
    return css, js


_SCRIPT_MAPA = r"""
(function () {
  'use strict';
  const datos = JSON.parse(document.getElementById('mapa-litoral-datos').textContent);
  const aviso = document.getElementById('mapa-aviso');
  const mostrarAviso = (mensaje) => {
    aviso.textContent = mensaje;
    aviso.hidden = false;
  };
  const texto = (valor) => {
    const elemento = document.createElement('span');
    elemento.textContent = valor;
    return elemento;
  };
  try {
    const mapa = L.map('mapa-litoral', {
      renderer: L.svg() || L.canvas(), preferCanvas: false,
      zoomControl: false, scrollWheelZoom: false, zoomSnap: 0.25,
      minZoom: 2, maxZoom: 18, zoomAnimation: false,
      fadeAnimation: false, markerZoomAnimation: false
    });
    mapa.attributionControl.setPrefix('<a href="https://leafletjs.com" target="_blank" rel="noopener noreferrer">Leaflet</a>');
    L.control.zoom({zoomInTitle: 'Acercar', zoomOutTitle: 'Alejar'}).addTo(mapa);
    L.control.scale({imperial: false, maxWidth: 100}).addTo(mapa);

    // Las capas de datos se agregan antes del fondo y nunca dependen de sus imágenes.
    const provincias = L.geoJSON(datos.geometrias, {
      style: {color: '#53677c', weight: 1.2, fillColor: '#dce7d8', fillOpacity: 0.22},
      onEachFeature: function (feature, layer) {
        layer.bindTooltip(texto(feature.properties.nombre), {
          permanent: true, direction: 'center', className: 'mapa-provincia', opacity: 0.85
        });
      }
    }).addTo(mapa);
    const referencias = L.featureGroup().addTo(mapa);
    datos.puntos.forEach(function (punto) {
      const marcador = L.circleMarker([punto.lat, punto.lon], {
        radius: 7, color: '#ffffff', weight: 1.6,
        fillColor: punto.color, fillOpacity: 0.96, bubblingMouseEvents: false
      }).addTo(referencias);
      marcador.bindPopup(punto.popup, {
        maxWidth: 360, minWidth: 210, maxHeight: 350, autoPanPadding: [18, 24]
      });
      marcador.bindTooltip(texto(punto.nombre + ' · ' + punto.estado), {direction: 'top'});
      marcador.on('add', function () { accesible(); });
      function accesible() {
        const nodo = marcador.getElement();
        if (!nodo || nodo.dataset.referencia) return;
        nodo.dataset.referencia = punto.nombre;
        nodo.dataset.nivel = punto.nivel;
        nodo.setAttribute('tabindex', '0');
        nodo.setAttribute('role', 'button');
        nodo.setAttribute('aria-label', punto.nombre + ': ' + punto.estado + '. Abrir detalle');
        nodo.addEventListener('keydown', function (evento) {
          if (evento.key === 'Enter' || evento.key === ' ') {
            evento.preventDefault(); marcador.openPopup();
          }
        });
      }
      accesible();
    });

    let limites = datos.ajustar_a === 'geometrias' ? provincias.getBounds() : referencias.getBounds();
    if (!limites.isValid()) limites = referencias.getBounds();
    if (!limites.isValid()) limites = provincias.getBounds();
    const encuadrar = () => {
      mapa.invalidateSize({pan: false});
      if (limites.isValid()) {
        mapa.fitBounds(limites, {padding: [28, 38], maxZoom: datos.ajustar_a === 'puntos' ? 13 : 7});
      } else {
        mapa.setView([-29, -59], 4);
        mostrarAviso('No hay referencias con coordenadas válidas para dibujar. Revisá los datos de la ficha.');
      }
    };
    encuadrar();

    const Encuadre = L.Control.extend({
      options: {position: 'topleft'},
      onAdd: function () {
        const caja = L.DomUtil.create('div', 'leaflet-bar mapa-encuadre');
        const boton = document.createElement('button');
        boton.type = 'button'; boton.textContent = 'Ver todo';
        boton.title = 'Volver a encuadrar todas las referencias';
        boton.onclick = encuadrar;
        caja.appendChild(boton);
        L.DomEvent.disableClickPropagation(caja);
        return caja;
      }
    });
    new Encuadre().addTo(mapa);
    const Leyenda = L.Control.extend({
      options: {position: 'bottomright'},
      onAdd: function () {
        const caja = L.DomUtil.create('div', 'mapa-leyenda');
        const titulo = document.createElement('strong');
        titulo.textContent = datos.leyenda; caja.appendChild(titulo);
        datos.estados.forEach(function (estado) {
          const fila = document.createElement('span');
          const punto = document.createElement('i');
          punto.style.backgroundColor = estado.color; punto.setAttribute('aria-hidden', 'true');
          fila.appendChild(punto); fila.appendChild(texto(estado.texto)); caja.appendChild(fila);
        });
        L.DomEvent.disableClickPropagation(caja);
        return caja;
      }
    });
    new Leyenda().addTo(mapa);

    if (datos.omitidos || datos.geometrias_omitidas) {
      const mensajes = [];
      if (datos.omitidos) mensajes.push(datos.omitidos + ' referencia(s) sin coordenadas válidas');
      if (datos.geometrias_omitidas) mensajes.push(datos.geometrias_omitidas + ' límite(s) sin geometría válida');
      mostrarAviso('No se dibujan: ' + mensajes.join(' · ') + '.');
    }
    if (datos.fondo_osm) {
      const fondo = L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
        maxZoom: 19, attribution: '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener noreferrer">OpenStreetMap</a>'
      });
      fondo.on('tileerror', function () {
        document.getElementById('mapa-fondo').hidden = false;
      });
      fondo.addTo(mapa);
    }
    // Reencuadrar al cambiar el ancho también cubre iframes inicialmente ocultos
    // dentro de una pestaña. No hay temporizador, geolocalización ni seguimiento.
    if (typeof ResizeObserver !== 'undefined') {
      let ancho = 0, alto = 0;
      const observador = new ResizeObserver(function (entradas) {
        const rect = entradas[0].contentRect;
        if (rect.width > 0 && rect.height > 0 && (rect.width !== ancho || rect.height !== alto)) {
          ancho = rect.width; alto = rect.height; encuadrar();
        }
      });
      observador.observe(document.getElementById('mapa-litoral'));
    } else {
      window.addEventListener('resize', encuadrar);
    }
    document.getElementById('mapa-litoral').dataset.estado = 'listo';
  } catch (error) {
    mostrarAviso('No se pudo dibujar el mapa en este navegador. Los datos y el selector de localidades siguen disponibles debajo.');
    document.getElementById('mapa-litoral').dataset.estado = 'error';
  }
})();
"""


def html_mapa_litoral(df, geometrias, tarjeta_nodo=None, *, altura=540,
                      ajustar_a='geometrias', titulo='Mapa de riesgo del Litoral',
                      leyenda='Evaluación integrada', fondo_osm=True):
    """Devuelve un documento para ``components.html(..., height=altura)``.

    En Zárate–Campana se pasa ``geometrias={}``, ``ajustar_a='puntos'`` y el
    callback/leyenda meteorológicos propios. Las coordenadas son las del
    catálogo público de la app, nunca la ubicación precisa del usuario.
    """
    if isinstance(altura, bool) or not isinstance(altura, int) or not 250 <= altura <= 1200:
        raise ValueError('altura debe ser un entero entre 250 y 1200 píxeles')
    datos = construir_datos_mapa(df, geometrias, tarjeta_nodo, ajustar_a=ajustar_a,
                                titulo=titulo, leyenda=leyenda)
    datos['fondo_osm'] = bool(fondo_osm)
    css, js = _leer_leaflet()
    return f'''<!doctype html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: https://tile.openstreetmap.org; connect-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'">
<title>{escape(str(titulo))}</title><style>{css}</style><style>
html,body{{margin:0;padding:0;width:100%;height:{altura}px;overflow:hidden;font-family:system-ui,sans-serif;color:#17324d}}
#mapa-litoral{{width:100%;height:100%;background:#edf3f5;border-radius:10px}}
.leaflet-container{{font-family:system-ui,sans-serif;font-size:13px}}
.leaflet-popup-content{{line-height:1.45;overflow-wrap:anywhere}}
.leaflet-interactive:focus{{outline:3px solid #0f172a;outline-offset:3px}}
.mapa-provincia{{background:transparent;border:0;box-shadow:none;color:#253d50;font-size:11px;font-weight:650;text-shadow:0 1px 2px #fff,1px 0 2px #fff;white-space:nowrap}}
.mapa-provincia:before{{display:none}}
.mapa-leyenda{{background:#fffffff2;padding:9px 11px;border:1px solid #cad5df;border-radius:8px;box-shadow:0 1px 5px #0002;font-size:12px;line-height:1.5}}
.mapa-leyenda strong{{display:block;font-size:12px;max-width:190px;margin-bottom:3px}}
.mapa-leyenda>span{{display:flex;align-items:center;gap:7px}}
.mapa-leyenda i{{width:10px;height:10px;border:1px solid #fff;border-radius:50%;display:inline-block;flex:none}}
.mapa-encuadre button{{border:0;padding:7px;background:white;color:#17324d;cursor:pointer;font-size:11px;font-weight:650}}
.mapa-aviso{{position:absolute;left:80px;right:12px;top:10px;z-index:1000;background:#fff9e6;border:1px solid #c89d2b;border-radius:6px;padding:7px 10px;font-size:12px;line-height:1.4}}
#mapa-fondo{{position:absolute;left:12px;bottom:38px;z-index:900;max-width:230px;background:#fffffff0;border-radius:5px;padding:5px 7px;font-size:11px;pointer-events:none}}
@media(max-width:520px){{.mapa-leyenda{{font-size:10px;padding:6px 8px}}.mapa-leyenda strong{{font-size:10px;max-width:145px}}.mapa-provincia{{font-size:9px}}#mapa-fondo{{max-width:155px;font-size:10px}}}}
</style></head><body>
<div id="mapa-litoral" role="region" aria-label="{escape(str(titulo), quote=True)}" data-estado="cargando"></div>
<div id="mapa-aviso" class="mapa-aviso" role="status" hidden></div>
<div id="mapa-fondo" role="status" hidden>Fondo de calles incompleto; los puntos y límites incluidos siguen visibles.</div>
<noscript>Activá JavaScript para ver el mapa. El selector y los datos de las localidades están debajo.</noscript>
<script id="mapa-litoral-datos" type="application/json">{_json_html(datos)}</script>
<script>{js}</script><script>{_SCRIPT_MAPA}</script>
</body></html>'''
