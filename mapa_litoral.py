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


VERSION_MAPA = '2026-10-21.movil'
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
        puntos.append({'nombre': nombre, 'provincia': str(fila.get('provincia') or ''),
                       'lat': lat, 'lon': lon, 'nivel': nivel,
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


@lru_cache(maxsize=1)
def _leer_viirs():
    return (_ASSETS.parent / 'viirs' / 'viirs.js').read_text(encoding='utf-8')


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
  const normalizar = valor => String(valor).normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
  const buscar = document.getElementById('mapa-buscar');
  const filtro = document.getElementById('mapa-nivel');
  const lista = document.getElementById('mapa-lista');
  const cuenta = document.getElementById('mapa-cuenta');
  const dialogo = document.getElementById('mapa-detalle');
  const cerrar = document.getElementById('mapa-cerrar');
  const detalle = document.getElementById('mapa-detalle-contenido');
  let ultimoFoco = null;
  const cerrarDetalle = () => { dialogo.close(); };
  cerrar.addEventListener('click', cerrarDetalle);
  dialogo.addEventListener('close', () => {
    // Quitar también los estilos de la ficha evita colorear otro popup abierto.
    detalle.replaceChildren();
    if (ultimoFoco && ultimoFoco.isConnected) ultimoFoco.focus();
  });
  dialogo.addEventListener('click', evento => { if (evento.target === dialogo) cerrarDetalle(); });
  const abrirDetalle = (punto, origen = document.activeElement) => {
    ultimoFoco = origen;
    document.getElementById('mapa-detalle-titulo').textContent = punto.nombre;
    // HTML escapado por el formateador público (igual contrato que los popups).
    detalle.innerHTML = punto.popup;
    dialogo.showModal();
    detalle.scrollTop = 0;
    cerrar.focus();
  };
  try {
    const mapa = L.map('mapa-litoral', {
      renderer: L.svg() || L.canvas(), preferCanvas: false,
      zoomControl: false, scrollWheelZoom: true, zoomSnap: 0.25,
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
    let reajustarFiltro = () => {};
    const marcadores = [];
    datos.puntos.forEach(function (punto) {
      const marcador = L.circleMarker([punto.lat, punto.lon], {
        radius: window.matchMedia('(pointer: coarse)').matches ? 11 : 8, color: '#ffffff', weight: 2,
        fillColor: punto.color, fillOpacity: 0.96, bubblingMouseEvents: false
      }).addTo(referencias);
      marcador.bindPopup(punto.popup, {
        maxWidth: 360, minWidth: 210, maxHeight: 350, autoPanPadding: [18, 24]
      });
      marcador.bindTooltip(texto(punto.nombre + ' · ' + punto.estado), {direction: 'top'});
      marcador.on('click', function () {
        if (window.matchMedia('(max-width: 620px)').matches) {
          marcador.closePopup(); abrirDetalle(punto, marcador.getElement());
        }
      });
      marcadores.push({punto, marcador});
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
            evento.preventDefault(); abrirDetalle(punto);
          }
        });
      }
      accesible();
    });

    // Los filtros sólo cambian la vista. Nunca modifican los datos ni el nivel.
    datos.estados.forEach(estado => {
      const opcion = document.createElement('option');
      opcion.value = estado.nivel;
      opcion.textContent = estado.texto + ' (' + datos.puntos.filter(p => p.nivel === estado.nivel).length + ')';
      filtro.appendChild(opcion);
    });
    function filtrar() {
      const consulta = normalizar(buscar.value.trim());
      lista.replaceChildren();
      let visibles = 0;
      marcadores.forEach(({punto, marcador}) => {
        const visible = (!filtro.value || punto.nivel === filtro.value) &&
          normalizar(punto.nombre + ' ' + punto.provincia).includes(consulta);
        if (!visible) { referencias.removeLayer(marcador); return; }
        referencias.addLayer(marcador); visibles += 1;
        const boton = document.createElement('button');
        boton.type = 'button'; boton.className = 'mapa-fila';
        const indicador = document.createElement('span');
        indicador.className = 'mapa-indicador'; indicador.style.backgroundColor = punto.color;
        indicador.setAttribute('aria-hidden', 'true');
        const nombre = document.createElement('strong'); nombre.textContent = punto.nombre;
        const estado = document.createElement('small');
        estado.textContent = punto.estado + (punto.provincia ? ' · ' + punto.provincia : '');
        const contenido = document.createElement('span'); contenido.append(nombre, estado);
        boton.append(indicador, contenido);
        boton.setAttribute('aria-label', punto.nombre + ': ' + punto.estado + '. Abrir detalle');
        boton.addEventListener('click', () => abrirDetalle(punto));
        lista.appendChild(boton);
      });
      if (!visibles) {
        const vacio = document.createElement('p');
        vacio.textContent = 'No hay coincidencias. Probá otro nombre o elegí todos los niveles.';
        lista.appendChild(vacio);
      }
      cuenta.textContent = visibles + ' de ' + datos.puntos.length + ' localidades · Filtro visual';
      reajustarFiltro();
    }
    buscar.addEventListener('input', filtrar);
    filtro.addEventListener('change', filtrar);
    filtrar();
    ['mapa', 'lista'].forEach(vista => {
      document.getElementById('mapa-vista-' + vista).addEventListener('click', () => {
        const esMapa = vista === 'mapa';
        document.getElementById('mapa-panel').hidden = !esMapa;
        lista.hidden = esMapa;
        document.getElementById('mapa-vista-mapa').setAttribute('aria-pressed', String(esMapa));
        document.getElementById('mapa-vista-lista').setAttribute('aria-pressed', String(!esMapa));
        if (esMapa) { mapa.invalidateSize({pan: false}); reajustarFiltro(); }
      });
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
    reajustarFiltro = () => {
      mapa.invalidateSize({pan: false});
      if (!buscar.value.trim() && !filtro.value) { encuadrar(); return; }
      const visibles = referencias.getBounds();
      if (visibles.isValid()) mapa.fitBounds(visibles, {padding: [40, 48], maxZoom: 12});
    };

    const Encuadre = L.Control.extend({
      options: {position: 'topleft'},
      onAdd: function () {
        const caja = L.DomUtil.create('div', 'leaflet-bar mapa-encuadre');
        const boton = document.createElement('button');
        boton.type = 'button'; boton.textContent = 'Ver todo';
        boton.title = 'Volver a encuadrar todas las referencias';
        boton.onclick = () => { buscar.value = ''; filtro.value = ''; filtrar(); encuadrar(); };
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
        const leyenda = document.createElement('details');
        leyenda.open = !window.matchMedia('(max-width: 620px)').matches;
        const titulo = document.createElement('summary');
        titulo.textContent = 'Colores · ' + datos.leyenda; leyenda.appendChild(titulo);
        datos.estados.forEach(function (estado) {
          const fila = document.createElement('span');
          const punto = document.createElement('i');
          punto.style.backgroundColor = estado.color; punto.setAttribute('aria-hidden', 'true');
          fila.appendChild(punto); fila.appendChild(texto(estado.texto)); leyenda.appendChild(fila);
        });
        caja.appendChild(leyenda);
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
    if (datos.capa_viirs) {
      // La consulta sólo comienza al activar la capa; sus fallos no ocultan nodos.
      try { window.AlaViirs.attach(mapa, L); }
      catch (error) { mostrarAviso('La capa VIIRS no está disponible; los nodos conservan su evaluación.'); }
    }
    // Reencuadrar al cambiar el ancho también cubre iframes inicialmente ocultos
    // dentro de una pestaña. No hay temporizador, geolocalización ni seguimiento.
    if (typeof ResizeObserver !== 'undefined') {
      let ancho = 0, alto = 0;
      const observador = new ResizeObserver(function (entradas) {
        const rect = entradas[0].contentRect;
        if (rect.width > 0 && rect.height > 0 && (rect.width !== ancho || rect.height !== alto)) {
          ancho = rect.width; alto = rect.height; reajustarFiltro();
        }
      });
      observador.observe(document.getElementById('mapa-litoral'));
    } else {
      window.addEventListener('resize', reajustarFiltro);
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
                      leyenda='Evaluación integrada', fondo_osm=True, capa_viirs=False):
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
    datos['capa_viirs'] = bool(capa_viirs)
    css, js = _leer_leaflet()
    viirs_js = _leer_viirs() if capa_viirs else ''
    origenes_viirs = 'https://gibs.earthdata.nasa.gov'
    fuente_viirs = ' ' + origenes_viirs if capa_viirs else ''
    conexiones = origenes_viirs if capa_viirs else "'none'"
    return f'''<!doctype html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: https://tile.openstreetmap.org{fuente_viirs}; connect-src {conexiones}; object-src 'none'; base-uri 'none'; form-action 'none'">
<title>{escape(str(titulo))}</title><style>{css}</style><style>
html,body{{margin:0;padding:0;width:100%;height:{altura}px;overflow:hidden;font-family:system-ui,sans-serif;color:#17324d;background:#fff}}
*{{box-sizing:border-box}}[hidden]{{display:none!important}}
#mapa-ui{{height:100%;display:flex;flex-direction:column;border:1px solid #cbd9e2;border-radius:12px;overflow:hidden}}
.mapa-herramientas{{padding:10px;background:#f4f8fb;border-bottom:1px solid #cbd9e2;flex-shrink:0}}
.mapa-herramientas label{{display:block;font-size:13px;font-weight:700;margin-bottom:5px}}
.mapa-herramientas input,.mapa-herramientas select,.mapa-herramientas button{{font:inherit;font-size:16px;min-height:44px;border:1px solid #a4b7c5;border-radius:8px;background:white;color:#17324d;padding:8px}}
#mapa-buscar{{width:100%;min-width:0}}.mapa-herramientas-fila{{display:flex;gap:8px;margin-top:8px}}
#mapa-nivel{{flex:1;min-width:0;width:0;font-size:14px}}.mapa-vistas{{display:flex;gap:4px}}
.mapa-vistas button{{font-size:14px;font-weight:700}}.mapa-vistas [aria-pressed="true"]{{background:#123d52;color:white;border-color:#123d52}}
button:focus-visible,input:focus-visible,select:focus-visible,summary:focus-visible{{outline:3px solid #137a96;outline-offset:2px}}
#mapa-panel{{position:relative;flex:1;min-height:0}}#mapa-lista{{flex:1;overflow:auto;padding:8px;min-height:0}}
.mapa-fila{{display:flex;align-items:center;gap:12px;width:100%;text-align:left;border:1px solid #d3dfe7;background:white;color:#17324d;border-radius:10px;min-height:64px;margin-bottom:6px;padding:12px;cursor:pointer;font:inherit}}
.mapa-fila strong,.mapa-fila small{{display:block}}.mapa-fila strong{{font-size:16px}}.mapa-fila small{{font-size:13px;margin-top:4px}}.mapa-indicador{{width:14px;height:14px;border-radius:50%;flex-shrink:0}}
#mapa-cuenta{{margin:0;padding:7px 10px;font-size:12px;background:#f4f8fb;flex-shrink:0}}
#mapa-detalle{{margin:auto auto 0;width:min(100%,600px);max-width:100%;max-height:82%;padding:0;border:1px solid #bacdd9;border-radius:16px 16px 0 0;color:#17324d;background:#fff;overflow:hidden}}
#mapa-detalle::backdrop{{background:#071f3299}}.mapa-detalle-cabecera{{display:flex;align-items:center;justify-content:space-between;padding:10px 14px;border-bottom:1px solid #d3dfe7;gap:8px}}
#mapa-detalle-titulo{{margin:0;font-size:19px}}#mapa-cerrar{{min-height:44px;min-width:66px;padding:8px;border:1px solid #a4b7c5;border-radius:8px;background:#f4f8fb;color:#17324d;font:600 14px system-ui;cursor:pointer}}
#mapa-detalle-contenido{{overflow:auto;max-height:{int(altura * .82) - 80}px;padding:14px;font-size:16px;line-height:1.55;overflow-wrap:anywhere}}
#mapa-detalle-contenido h3{{font-size:20px}}#mapa-detalle-contenido summary{{min-height:44px;padding:8px 0;cursor:pointer}}#mapa-detalle-contenido a{{display:inline-block;padding:6px 0;color:#075985}}
#mapa-detalle-contenido .ala-evi{{padding:0;border:0;margin:0;box-shadow:none}}
#mapa-litoral{{width:100%;height:100%;background:#edf3f5;border-radius:10px}}
.leaflet-container{{font-family:system-ui,sans-serif;font-size:13px}}
.leaflet-popup-content{{line-height:1.45;overflow-wrap:anywhere}}
.leaflet-interactive:focus{{outline:3px solid #0f172a;outline-offset:3px}}
.mapa-provincia{{background:transparent;border:0;box-shadow:none;color:#253d50;font-size:11px;font-weight:650;text-shadow:0 1px 2px #fff,1px 0 2px #fff;white-space:nowrap}}
.mapa-provincia:before{{display:none}}
.mapa-leyenda{{background:#fffffff2;padding:9px 11px;border:1px solid #cad5df;border-radius:8px;box-shadow:0 1px 5px #0002;font-size:12px;line-height:1.5}}
.mapa-leyenda summary{{font-size:12px;max-width:190px;cursor:pointer;min-height:30px;padding:5px 0}}
.mapa-leyenda details>span{{display:flex;align-items:center;gap:7px}}
.mapa-leyenda i{{width:10px;height:10px;border:1px solid #fff;border-radius:50%;display:inline-block;flex:none}}
.mapa-encuadre button{{border:0;padding:8px;background:white;color:#17324d;cursor:pointer;font-size:13px;font-weight:650;min-height:44px}}
.mapa-aviso{{position:absolute;left:80px;right:12px;top:10px;z-index:1000;background:#fff9e6;border:1px solid #c89d2b;border-radius:6px;padding:7px 10px;font-size:12px;line-height:1.4}}
#mapa-fondo{{position:absolute;left:12px;bottom:38px;z-index:900;max-width:230px;background:#fffffff0;border-radius:5px;padding:5px 7px;font-size:11px;pointer-events:none}}
.viirs-control{{box-sizing:border-box;width:270px;max-width:calc(100vw - 88px);background:#fff;color:#17324d;border:1px solid #cad5df;border-radius:8px;box-shadow:0 1px 5px #0002;font-size:12px}}
.viirs-control>summary{{box-sizing:border-box;min-height:44px;padding:12px;cursor:pointer;font-weight:700}}
.viirs-control[data-state="error"]>summary{{color:#991b1b;background:#fff1f2;border-radius:8px}}
.viirs-control[data-state="loading"]>summary{{color:#854d0e;background:#fffbeb;border-radius:8px}}
.viirs-control[data-state="error"] .viirs-status{{padding:8px;border:1px solid #f1a4aa;border-radius:6px;background:#fff1f2;color:#991b1b}}
.viirs-panel{{padding:0 12px 10px;max-height:{altura-100}px;overflow-y:auto;overscroll-behavior:contain}}
.viirs-panel label{{display:block;margin:6px 0;font-size:12px}}
.viirs-panel .viirs-enabled{{display:flex;gap:7px;align-items:center;min-height:44px;font-weight:650}}
.viirs-panel input[type=checkbox]{{width:20px;height:20px;flex:none}}
.viirs-panel select,.viirs-panel button{{box-sizing:border-box;display:block;width:100%;min-height:44px;background:#f4f7fa;color:#17324d;border:1px solid #849bb0;border-radius:5px;padding:6px;font:inherit}}
.viirs-panel input[type=range]{{display:block;width:100%;min-height:32px;margin:0}}
.viirs-panel p{{line-height:1.4;margin:8px 0}}
.viirs-panel a{{display:block;min-height:36px;color:#174da0;line-height:1.5}}
.viirs-status{{font-weight:650}}.viirs-note,.viirs-dates{{font-size:11px}}
.viirs-legend summary{{min-height:36px;cursor:pointer;padding-top:8px}}
.viirs-legend img{{max-width:100%;height:auto;background:white}}
.viirs-panel :focus-visible,.viirs-control>summary:focus-visible{{outline:3px solid #2563eb;outline-offset:2px}}
body:has(.viirs-control[open]) .mapa-leyenda{{visibility:hidden}}
@media(max-width:620px){{.mapa-leyenda{{font-size:12px;padding:4px 8px}}.mapa-leyenda summary{{max-width:175px;min-height:44px;display:flex;align-items:center}}.mapa-provincia{{font-size:10px}}#mapa-fondo{{max-width:155px;font-size:11px}}.leaflet-control-zoom a{{width:44px!important;height:44px!important;line-height:44px!important}}}}
</style></head><body>
<div id="mapa-ui">
<div class="mapa-herramientas" role="search" aria-label="Buscar y filtrar localidades">
<label for="mapa-buscar">Buscá tu localidad o provincia</label>
<input id="mapa-buscar" type="search" placeholder="Ej.: Goya, Corrientes" autocomplete="off" aria-controls="mapa-lista mapa-litoral">
<div class="mapa-herramientas-fila"><select id="mapa-nivel" aria-label="Filtrar por nivel"><option value="">Todos los niveles</option></select>
<div class="mapa-vistas" role="group" aria-label="Modo de consulta"><button type="button" id="mapa-vista-mapa" aria-pressed="true" aria-controls="mapa-panel">Mapa</button><button type="button" id="mapa-vista-lista" aria-pressed="false" aria-controls="mapa-lista">Lista</button></div></div></div>
<div id="mapa-panel">
<div id="mapa-litoral" role="region" aria-label="{escape(str(titulo), quote=True)}" data-estado="cargando"></div>
<div id="mapa-aviso" class="mapa-aviso" role="status" hidden></div>
<div id="mapa-fondo" role="status" hidden>Fondo de calles incompleto; los puntos y límites incluidos siguen visibles.</div>
</div><div id="mapa-lista" role="region" aria-label="Localidades y niveles" hidden></div><p id="mapa-cuenta" role="status" aria-live="polite"></p></div>
<dialog id="mapa-detalle" aria-labelledby="mapa-detalle-titulo"><div class="mapa-detalle-cabecera"><h2 id="mapa-detalle-titulo"></h2><button id="mapa-cerrar" type="button">Cerrar</button></div><div id="mapa-detalle-contenido"></div></dialog>
<noscript>Activá JavaScript para ver el mapa. El selector y los datos de las localidades están debajo.</noscript>
<script id="mapa-litoral-datos" type="application/json">{_json_html(datos)}</script>
<script>{js}</script><script>{viirs_js}</script><script>{_SCRIPT_MAPA}</script>
</body></html>'''

