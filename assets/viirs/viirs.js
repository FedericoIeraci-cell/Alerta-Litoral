/* VIIRS Flood / SSEC-CIMSS. Capa visual opcional: no reclasifica nodos. */
(function (root) {
  'use strict';
  const HOST = 'https://floods.ssec.wisc.edu';
  const PRODUCTS = Object.freeze({
    'RIVER-FLDglobal-composite1': {label: 'Composición de 1 día', days: 1},
    'RIVER-FLDglobal-composite': {label: 'Composición de 5 días', days: 5}
  });
  const DAY = 86400000;

  function productInfo(id) {
    if (!Object.hasOwn(PRODUCTS, id)) throw new Error('Producto VIIRS no reconocido.');
    return PRODUCTS[id];
  }

  function parseTime(value) {
    if (typeof value !== 'string' || !/^20\d{6}\.\d{6}$/.test(value)) return null;
    const parts = [value.slice(0, 4), value.slice(4, 6), value.slice(6, 8),
      value.slice(9, 11), value.slice(11, 13), value.slice(13, 15)].map(Number);
    const date = new Date(Date.UTC(parts[0], parts[1] - 1, ...parts.slice(2)));
    if (date.getUTCFullYear() !== parts[0] || date.getUTCMonth() !== parts[1] - 1 ||
        date.getUTCDate() !== parts[2] || date.getUTCHours() !== parts[3] ||
        date.getUTCMinutes() !== parts[4] || date.getUTCSeconds() !== parts[5]) return null;
    return date;
  }

  function metadata(payload, id, now = Date.now()) {
    const info = productInfo(id);
    if (!Number.isFinite(now)) throw new Error('No se pudo verificar la fecha de consulta.');
    const matches = Array.isArray(payload) ? payload.filter(p => p && p.id === id) : [];
    const item = matches.length === 1 ? matches[0] : null;
    if (!item || item.type !== 'raster' || item.released !== true || !Array.isArray(item.times)) {
      throw new Error('El proveedor no confirmó el producto VIIRS solicitado.');
    }
    const times = item.times.map(value => ({value, date: parseTime(value)}))
      .filter(t => t.date && t.date.getTime() <= now + 300000)
      .sort((a, b) => b.date - a.date);
    if (!times.length) throw new Error('VIIRS sin fecha verificable; no se muestra una capa sin fechar.');
    const latest = times[0];
    // Es la fecha nominal del compuesto, no una hora de pasada ni de publicación.
    return {id, label: info.label, time: latest.value, date: latest.date,
      ageDays: Math.floor((now - latest.date.getTime()) / DAY),
      maxZoom: Number.isInteger(item.maxzoom) && item.maxzoom >= 2 && item.maxzoom <= 12 ? item.maxzoom : 7};
  }

  function tileUrl(meta) {
    productInfo(meta.id);
    if (!parseTime(meta.time)) throw new Error('Fecha VIIRS inválida.');
    return HOST + '/api/image?products=' + encodeURIComponent(meta.id + '_' + meta.time.replace('.', '_')) +
      '&x={x}&y={y}&z={z}';
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
      enabledLabel.appendChild(document.createTextNode(' Mostrar agua detectada · VIIRS'));
      const selectionLabel = add('label', 'Período del producto');
      const select = add('select', '', selectionLabel);
      Object.entries(PRODUCTS).forEach(([id, info]) => {
        const option = add('option', info.label, select); option.value = id;
      });
      const opacityLabel = add('label', 'Opacidad: ');
      const output = add('output', '65 %', opacityLabel);
      const opacity = add('input', '', opacityLabel); opacity.type = 'range';
      opacity.min = '15'; opacity.max = '100'; opacity.value = '65'; opacity.setAttribute('aria-label', 'Opacidad VIIRS');
      const refresh = add('button', 'Actualizar capa'); refresh.type = 'button'; refresh.disabled = true;
      const status = add('p', 'Capa desactivada. Activala para consultar el producto disponible.');
      status.setAttribute('role', 'status'); status.className = 'viirs-status';
      const dates = add('p'); dates.className = 'viirs-dates';
      const note = add('p', 'Observación satelital regional (~375 m), no pronóstico. Nubes, sombras y falta de cobertura pueden ocultar agua. Una zona sin color no confirma ausencia de inundación.');
      note.className = 'viirs-note';
      const legendBox = add('details'); legendBox.className = 'viirs-legend';
      add('summary', 'Leyenda del proveedor', legendBox);
      const legend = add('img', '', legendBox); legend.alt = 'Leyenda de clases de agua, inundación, nubes y otras superficies de VIIRS';
      legend.hidden = true;
      const legendError = add('p', '', legendBox); legendError.hidden = true;
      const source = add('a', 'Fuente y visor SSEC / NOAA ↗');
      source.href = HOST + '/?products=RIVER-FLDglobal-composite1&center=-29,-59&zoom=5';
      source.target = '_blank'; source.rel = 'noopener noreferrer';
      L.DomEvent.disableClickPropagation(box); L.DomEvent.disableScrollPropagation(box);
      box.addEventListener('keydown', e => e.stopPropagation());
      let layer = null, generation = 0, request = null, failedTiles = false;

      function clear() {
        generation++;
        if (request) { request.abort(); request = null; }
        if (layer) { map.removeLayer(layer); layer = null; }
        legend.hidden = true; legend.removeAttribute('src'); legendError.hidden = true;
        dates.textContent = ''; failedTiles = false;
      }

      async function load() {
        clear();
        refresh.disabled = !enabled.checked;
        if (!enabled.checked) {
          status.textContent = 'Capa desactivada.'; return;
        }
        const token = generation, id = select.value;
        status.textContent = 'Consultando fecha del producto VIIRS…';
        const controller = new AbortController(); request = controller;
        const timeout = setTimeout(() => controller.abort(), 12000);
        try {
          productInfo(id);
          const response = await fetch(HOST + '/api/products?products=' + encodeURIComponent(id),
            {signal: controller.signal, credentials: 'omit', referrerPolicy: 'no-referrer'});
          if (!response.ok) throw new Error('No se pudo consultar VIIRS (HTTP ' + response.status + ').');
          const body = await response.text();
          if (body.length > 1000000) throw new Error('Respuesta VIIRS demasiado grande.');
          const queried = Date.now();
          const meta = metadata(JSON.parse(body), id, queried);
          if (token !== generation || !enabled.checked) return;
          const nominal = meta.date.toLocaleDateString('es-AR', {timeZone: 'UTC'});
          const consulted = new Date(queried).toLocaleString('es-AR', {timeZone: 'America/Argentina/Buenos_Aires', hour12: false});
          dates.textContent = meta.label + ' · fecha nominal (UTC): ' + nominal +
            '. No identifica la hora exacta de observación de cada píxel. Consulta: ' + consulted + ' (Argentina).';
          const ageNote = meta.ageDays >= 2 ? ' Producto de hace ' + meta.ageDays + ' días; revisar antigüedad.' : '';
          status.textContent = 'Solicitando imágenes; la cobertura local aún no está comprobada.' + ageNote;
          if (!map.getPane('viirs')) {
            map.createPane('viirs'); map.getPane('viirs').style.zIndex = '350';
            map.getPane('viirs').style.pointerEvents = 'none';
          }
          layer = L.tileLayer(tileUrl(meta), {pane: 'viirs', opacity: Number(opacity.value) / 100,
            maxNativeZoom: meta.maxZoom, maxZoom: 18, noWrap: true,
            attribution: '<a href="https://www.ssec.wisc.edu/flood-map-demo/" target="_blank" rel="noopener noreferrer">VIIRS · NOAA / GMU / SSEC-CIMSS</a>'});
          layer.on('tileload', () => {
            if (token === generation && !failedTiles) {
              status.textContent = 'Imágenes recibidas; la cobertura local puede ser parcial.' + ageNote;
            }
          });
          layer.on('tileerror', () => {
            if (token === generation) {
              failedTiles = true;
              status.textContent = 'Capa incompleta: falló la carga de imágenes. No permite descartar agua.' + ageNote;
            }
          });
          layer.addTo(map);
          legend.onload = () => { if (token === generation && enabled.checked) legend.hidden = false; };
          legend.onerror = () => {
            if (token === generation) {
              legend.hidden = true; legendError.hidden = false;
              legendError.textContent = 'Leyenda no disponible. Consultá las clases en el visor de la fuente.';
            }
          };
          legend.src = HOST + '/api/legend?products=' + encodeURIComponent(id);
          source.href = HOST + '/?products=' + encodeURIComponent(id + '_' + meta.time.replace('.', '_')) +
            '&center=-29,-59&zoom=5';
        } catch (error) {
          if (token === generation && enabled.checked) {
            status.textContent = error.name === 'AbortError' ?
              'VIIRS no respondió a tiempo. No se muestra una capa sin verificar.' :
              'VIIRS no disponible: no se pudo verificar el producto y su fecha. Podés reintentar.';
          }
        } finally {
          clearTimeout(timeout);
          if (request === controller) request = null;
        }
      }

      enabled.addEventListener('change', load);
      select.addEventListener('change', () => { if (enabled.checked) load(); });
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

  const api = {metadata, parseTime, tileUrl, attach};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.AlaViirs = api;
})(typeof window !== 'undefined' ? window : globalThis);
