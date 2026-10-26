/* VIIRS Flood / NASA GIBS. Capa visual opcional: no reclasifica nodos. */
(function (root) {
  'use strict';
  const HOST = 'https://gibs.earthdata.nasa.gov';
  const REQUEST_TIMEOUT_MS = 12000;
  const PRODUCTS = Object.freeze({
    'VIIRS_Combined_Flood_2-Day': {label: 'Composición de 2 días · NASA', days: 2},
    'VIIRS_Combined_Flood_3-Day': {label: 'Composición de 3 días · NASA', days: 3}
  });
  const DAY = 86400000;
  const TILE_MATRIX = 'GoogleMapsCompatible_Level9';

  function productInfo(id) {
    if (!Object.hasOwn(PRODUCTS, id)) throw new Error('Producto VIIRS no reconocido.');
    return PRODUCTS[id];
  }

  function parseTime(value) {
    if (typeof value !== 'string' || !/^20\d{2}-\d{2}-\d{2}$/.test(value)) return null;
    const parts = value.split('-').map(Number);
    const date = new Date(Date.UTC(parts[0], parts[1] - 1, parts[2]));
    if (date.getUTCFullYear() !== parts[0] || date.getUTCMonth() !== parts[1] - 1 ||
        date.getUTCDate() !== parts[2]) return null;
    return date;
  }

  function queryDates(now) {
    if (!Number.isFinite(now)) throw new Error('No se pudo verificar la fecha de consulta.');
    const end = new Date(now).toISOString().slice(0, 10);
    return {start: new Date(parseTime(end).getTime() - 7 * DAY).toISOString().slice(0, 10), end};
  }

  function metadataUrl(id, now = Date.now()) {
    productInfo(id);
    const dates = queryDates(now);
    return HOST + '/wmts/epsg3857/best/1.0.0/' + id + '/default/' + TILE_MATRIX +
      '/all/' + dates.start + '--' + dates.end + '.xml';
  }

  function metadata(payload, id, now = Date.now()) {
    const info = productInfo(id);
    const bounds = queryDates(now);
    if (typeof payload !== 'string' || payload.length > 1000000 ||
        new root.TextEncoder().encode(payload).byteLength > 1000000 || /<!DOCTYPE|<!ENTITY/i.test(payload)) {
      throw new Error('La disponibilidad de VIIRS no es un XML válido.');
    }
    const xml = new root.DOMParser().parseFromString(payload, 'application/xml');
    const elements = (parent, name) => Array.from(parent.getElementsByTagNameNS('*', name));
    if (!xml.documentElement || xml.documentElement.localName !== 'Domains' ||
        elements(xml, 'parsererror').length) throw new Error('La disponibilidad de VIIRS no es un XML válido.');
    const dimensions = elements(xml, 'DimensionDomain');
    if (dimensions.length !== 1) throw new Error('NASA no confirmó una disponibilidad temporal inequívoca.');
    const identifiers = elements(dimensions[0], 'Identifier');
    const domains = elements(dimensions[0], 'Domain');
    const sizes = elements(dimensions[0], 'Size');
    if (identifiers.length !== 1 || identifiers[0].textContent.trim() !== 'time' ||
        domains.length !== 1 || sizes.length !== 1) throw new Error('NASA no confirmó las fechas de VIIRS.');
    const intervals = domains[0].textContent.trim().split(',');
    if (String(intervals.length) !== sizes[0].textContent.trim()) throw new Error('Disponibilidad VIIRS incompleta.');
    const available = new Set();
    for (const interval of intervals) {
      const fields = interval.trim().split('/');
      const start = parseTime(fields[0]);
      const end = fields.length === 1 ? start : parseTime(fields[1]);
      if (!start || !end || !([1, 3].includes(fields.length)) ||
          (fields.length === 3 && fields[2] !== 'P1D') || start > end ||
          fields[0] < bounds.start || end.toISOString().slice(0, 10) > bounds.end) {
        throw new Error('NASA devolvió fechas VIIRS ausentes, futuras o fuera de la consulta.');
      }
      for (let stamp = start.getTime(); stamp <= end.getTime(); stamp += DAY) {
        const value = new Date(stamp).toISOString().slice(0, 10);
        if (available.has(value)) throw new Error('NASA devolvió fechas VIIRS repetidas.');
        available.add(value);
      }
    }
    // El día UTC en proceso se excluye incluso si ya figura en la disponibilidad.
    const availableDates = [...available].filter(value => value < bounds.end).sort();
    if (!availableDates.length || availableDates.length > 8) throw new Error('VIIRS sin días UTC cerrados verificables.');
    const time = availableDates[availableDates.length - 1], date = parseTime(time);
    // Es la fecha nominal del compuesto, no una hora de pasada ni de publicación.
    return {id, label: info.label, days: info.days, time, date, availableDates,
      ageDays: Math.floor((now - date.getTime()) / DAY), maxZoom: 9};
  }

  function tileUrl(meta) {
    productInfo(meta.id);
    if (!parseTime(meta.time) || !Array.isArray(meta.availableDates) || !meta.availableDates.includes(meta.time)) {
      throw new Error('Fecha VIIRS sin disponibilidad verificada.');
    }
    return HOST + '/wmts/epsg3857/best/' + meta.id + '/default/' + meta.time + '/' + TILE_MATRIX + '/{z}/{y}/{x}.png';
  }

  function attach(map, L) {
    const control = L.control({position: 'topright'});
    control.onAdd = function () {
      const box = L.DomUtil.create('details', 'viirs-control');
      const summary = document.createElement('summary');
      summary.textContent = 'Agua por satélite'; box.appendChild(summary);
      const panel = document.createElement('div'); panel.className = 'viirs-panel'; box.appendChild(panel);
      // La barra de búsqueda reduce el mapa: el panel se ajusta a su alto real.
      const fitPanel = () => {
        const height = map.getSize().y;
        if (height > 0) panel.style.maxHeight = Math.max(80, height - 78) + 'px';
      };
      box.addEventListener('toggle', fitPanel);
      map.on('resize', fitPanel);
      fitPanel();
      const add = (tag, text, parent = panel) => {
        const el = document.createElement(tag); if (text) el.textContent = text; parent.appendChild(el); return el;
      };
      const enabledLabel = add('label'); enabledLabel.className = 'viirs-enabled';
      const enabled = add('input', '', enabledLabel); enabled.type = 'checkbox';
      enabledLabel.appendChild(document.createTextNode(' Mostrar agua · VIIRS NASA'));
      const status = add('p', 'Capa desactivada. Activala para consultar el producto disponible.');
      status.setAttribute('role', 'status'); status.setAttribute('aria-live', 'polite'); status.className = 'viirs-status';
      const dates = add('p'); dates.className = 'viirs-dates';
      add('p', 'Clases NASA: celeste = agua superficial; amarillo = inundación recurrente; rojo = inundación detectada; gris = datos insuficientes.').className = 'viirs-note';
      const setStatus = (state, message) => {
        const badges = {off: '', loading: 'Cargando', received: 'Imágenes recibidas',
          unavailable: 'No disponible', partial: 'Capa incompleta'};
        summary.textContent = 'Agua por satélite' + (badges[state] ? ' · ' + badges[state] : '');
        box.setAttribute('data-state', {off: 'off', loading: 'loading', received: 'ready',
          unavailable: 'error', partial: 'error'}[state]); status.textContent = message;
      };
      const selectionLabel = add('label', 'Período del producto');
      const select = add('select', '', selectionLabel);
      Object.entries(PRODUCTS).forEach(([id, info]) => {
        const option = add('option', info.label, select); option.value = id;
      });
      const dateLabel = add('label', 'Fecha de cierre del producto (UTC)');
      const dateSelect = add('select', '', dateLabel); dateSelect.disabled = true;
      const opacityLabel = add('label', 'Opacidad: ');
      const output = add('output', '65 %', opacityLabel);
      const opacity = add('input', '', opacityLabel); opacity.type = 'range';
      opacity.min = '15'; opacity.max = '100'; opacity.value = '65'; opacity.setAttribute('aria-label', 'Opacidad VIIRS');
      const refresh = add('button', 'Actualizar capa'); refresh.type = 'button'; refresh.disabled = true;
      add('p', 'NASA GIBS · VIIRS combinado de 2 o 3 días. Se muestran días UTC cerrados; el día en proceso no se usa. Esto no garantiza cobertura completa.').className = 'viirs-note';
      add('p', 'La composición reúne observaciones de varios días; puede incluir agua que ya retrocedió. No es una imagen en vivo.').className = 'viirs-note';
      add('p', 'Opacidad cambia la visibilidad de la imagen. Los colores del satélite no son los colores de alerta de los nodos.').className = 'viirs-note';
      const note = add('p', 'Observación satelital regional (~375 m), no pronóstico. Nubes, sombras y falta de cobertura pueden ocultar agua. Una zona sin color no confirma ausencia de inundación.');
      note.className = 'viirs-note';
      const legendBox = add('details'); legendBox.className = 'viirs-legend';
      add('summary', 'Leyenda del proveedor', legendBox);
      const legend = add('img', '', legendBox); legend.alt = 'Leyenda NASA: agua superficial, inundación recurrente, inundación detectada y datos insuficientes';
      legend.hidden = true;
      const legendError = add('p', '', legendBox); legendError.hidden = true;
      const source = add('a', 'Fuente y visor NASA Worldview ↗');
      source.href = 'https://worldview.earthdata.nasa.gov/';
      source.target = '_blank'; source.rel = 'noopener noreferrer';
      L.DomEvent.disableClickPropagation(box); L.DomEvent.disableScrollPropagation(box);
      box.addEventListener('keydown', e => e.stopPropagation());
      let layer = null, generation = 0, request = null, failedTiles = false, verified = null;

      function clear() {
        generation++;
        if (request) { request.abort(); request = null; }
        if (layer) { map.removeLayer(layer); layer = null; }
        legend.hidden = true; legend.removeAttribute('src'); legendError.hidden = true;
        dates.textContent = ''; failedTiles = false;
        source.href = 'https://worldview.earthdata.nasa.gov/';
      }

      function render(meta, queried, token) {
        if (!meta.availableDates.includes(meta.time)) throw new Error('Fecha VIIRS sin verificar.');
        const closing = parseTime(meta.time);
        const start = new Date(closing.getTime() - (meta.days - 1) * DAY);
        const publicDate = date => date.toLocaleDateString('es-AR', {timeZone: 'UTC'});
        const consulted = new Date(queried).toLocaleString('es-AR', {timeZone: 'America/Argentina/Buenos_Aires', hour12: false});
        dates.textContent = meta.label + ' · ventana nominal UTC: ' + publicDate(start) + ' a ' + publicDate(closing) +
          '. No identifica la hora exacta de observación de cada píxel. Consulta de disponibilidad: ' + consulted + ' (Argentina).';
        const age = Math.floor((Date.now() - closing.getTime()) / DAY);
        const ageNote = age >= 3 ? ' Cierre de hace ' + age + ' días; revisar antigüedad.' : '';
        setStatus('loading', 'Solicitando imágenes NASA de un período cerrado; la cobertura local aún no está comprobada.' + ageNote);
        if (!map.getPane('viirs')) {
          map.createPane('viirs'); map.getPane('viirs').style.zIndex = '350';
          map.getPane('viirs').style.pointerEvents = 'none';
        }
        layer = L.tileLayer(tileUrl(meta), {pane: 'viirs', opacity: Number(opacity.value) / 100,
          maxNativeZoom: meta.maxZoom, maxZoom: 18, noWrap: true,
          attribution: '<a href="https://nasa-gibs.github.io/gibs-api-docs/" target="_blank" rel="noopener noreferrer">VIIRS · NASA GIBS</a>'});
        layer.on('tileload', () => {
          if (token === generation && !failedTiles) {
            setStatus('received', 'Imágenes NASA recibidas; la cobertura local y la ausencia de agua no están confirmadas.' + ageNote);
          }
        });
        layer.on('tileerror', () => {
          if (token === generation) {
            failedTiles = true;
            setStatus('partial', 'Capa incompleta: falló la carga de imágenes. No permite descartar agua.' + ageNote);
          }
        });
        layer.addTo(map);
        legend.onload = () => { if (token === generation && enabled.checked) legend.hidden = false; };
        legend.onerror = () => {
          if (token === generation) {
            legend.hidden = true; legendError.hidden = false;
            legendError.textContent = 'Leyenda gráfica no disponible. Consultá las clases NASA indicadas en el panel.';
          }
        };
        legend.src = HOST + '/legends/MODIS_Flood_H.svg';
        source.href = 'https://worldview.earthdata.nasa.gov/?v=-70,-40,-48,-20&l=' +
          encodeURIComponent(meta.id) + '&t=' + meta.time;
      }

      async function load() {
        clear();
        verified = null; dateSelect.replaceChildren(); dateSelect.disabled = true;
        refresh.disabled = !enabled.checked;
        if (!enabled.checked) {
          setStatus('off', 'Capa desactivada.'); return;
        }
        const token = generation, id = select.value;
        const current = () => token === generation && enabled.checked;
        setStatus('loading', 'Consultando las fechas del producto VIIRS en NASA…');
        try {
          productInfo(id);
          const controller = new AbortController(); request = controller;
          let timedOut = false;
          const timeout = setTimeout(() => { timedOut = true; controller.abort(); }, REQUEST_TIMEOUT_MS);
          try {
            const requested = Date.now();
            const response = await fetch(metadataUrl(id, requested),
              {signal: controller.signal, credentials: 'omit', referrerPolicy: 'no-referrer'});
            if (!current()) return;
            if (!response.ok) {
              const error = new Error('HTTP'); error.httpStatus = response.status; throw error;
            }
            const body = await response.text();
            if (!current()) return;
            const queried = Date.now();
            const meta = metadata(body, id, requested);
            verified = {meta, queried};
            [...meta.availableDates].reverse().forEach(value => {
              const option = add('option', value, dateSelect); option.value = value;
            });
            dateSelect.value = meta.time; dateSelect.disabled = false;
            render(meta, queried, token);
          } catch (error) {
            if (!current()) return;
            const reason = timedOut || error.name === 'AbortError' ? 'tiempo de espera agotado' :
              Number.isInteger(error.httpStatus) ? 'error HTTP ' + error.httpStatus :
                error.name === 'TypeError' ? 'conexión no disponible' : 'producto o fecha sin verificar';
            setStatus('unavailable', 'VIIRS NASA no disponible: no se pudo verificar el producto y sus fechas. Motivo: ' + reason + '. Podés reintentar.');
          } finally {
            clearTimeout(timeout);
            if (request === controller) request = null;
          }
        } catch (error) {
          if (token === generation && enabled.checked) {
            setStatus('unavailable', 'VIIRS NASA no disponible: no se pudo verificar el producto y sus fechas. Podés reintentar.');
          }
        }
      }

      enabled.addEventListener('change', load);
      select.addEventListener('change', () => { if (enabled.checked) load(); });
      dateSelect.addEventListener('change', () => {
        if (!enabled.checked || !verified) return;
        clear();
        try { render({...verified.meta, time: dateSelect.value}, verified.queried, generation); }
        catch (error) { setStatus('unavailable', 'Fecha VIIRS no verificada. Actualizá las fechas del producto.'); }
      });
      refresh.addEventListener('click', load);
      opacity.addEventListener('input', () => {
        output.textContent = opacity.value + ' %';
        if (layer) layer.setOpacity(Number(opacity.value) / 100);
      });
      map.on('unload', clear);
      return box;
    };
    control.addTo(map);
    return control;
  }

  const api = {metadata, metadataUrl, parseTime, tileUrl, attach};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.AlaViirs = api;
})(typeof window !== 'undefined' ? window : globalThis);

