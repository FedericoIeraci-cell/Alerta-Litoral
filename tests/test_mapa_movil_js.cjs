// DOM/Leaflet acotados: ejercitan los eventos reales, sin navegador ni red.
const assert = require('node:assert/strict');
const vm = require('node:vm');
const source = require('node:fs').readFileSync(0, 'utf8');
function escenario(movil) {
  let document;
  class Element {
    constructor(tag) { this.tag = tag; this.children = []; this.listeners = {}; this.dataset = {};
      this.style = {}; this.attrs = {}; this.value = ''; this.hidden = false; this.isConnected = true; }
    append(...nodes) { this.children.push(...nodes); }
    appendChild(node) { this.append(node); return node; }
    replaceChildren() { this.children = []; }
    setAttribute(k, v) { this.attrs[k] = v; }
    addEventListener(k, f) { (this.listeners[k] ||= []).push(f); }
    fire(k, extras = {}) { for(const f of this.listeners[k] || []) f({target: this, ...extras}); }
    focus() { document.activeElement = this; }
    showModal() { this.open = true; }
    close() { this.open = false; this.fire('close'); }
  }
  const ids = {};
  document = {activeElement: null, createElement: tag => new Element(tag),
    getElementById: id => ids[id] ||= new Element('div')};
  const puntos = [
    {nombre: 'Goya', provincia: 'Corrientes', lat: -29, lon: -59, nivel: 'ROJO',
     color: '#f00', estado: 'Atención prioritaria', popup: '<p>Motivo con fecha original</p>'},
    {nombre: 'Chajarí', provincia: 'Entre Ríos', lat: -30, lon: -58, nivel: 'SIN DATOS',
     color: '#888', estado: 'Sin datos suficientes', popup: '<p>Sin datos no es cero</p>'},
    {nombre: 'Rafaela', provincia: 'Santa Fe', lat: -31, lon: -61, nivel: 'VERDE',
     color: '#0a0', estado: 'Seguimiento', popup: '<p>Seguimiento</p>'}];
  const datos = {puntos, geometrias: {}, ajustar_a: 'puntos', fondo_osm: false,
    estados: puntos.map(p => ({nivel: p.nivel, texto: p.estado, color: p.color}))};
  document.getElementById('mapa-litoral-datos').textContent = JSON.stringify(datos);
  const markers = [], layers = new Set();
  const bounds = {isValid: () => true}, provinceBounds = {isValid: () => true};
  const group = {addTo() {return this;}, addLayer(m) {layers.add(m); return this;},
    removeLayer(m) {layers.delete(m); return this;}, getBounds: () => bounds};
  const map = {attributionControl: {setPrefix() {}}, invalidateSize() {},
    fitBounds(value) {this.lastBounds = value;}, setView() {}};
  const control = () => ({addTo() {return this;}});
  const L = {svg() {}, canvas() {}, map: () => map, control: {zoom: control, scale: control},
    geoJSON: () => ({addTo() {return this;}, getBounds: () => provinceBounds}), featureGroup: () => group,
    Control: {extend: spec => class { addTo() {spec.onAdd(); return this;} }},
    DomUtil: {create: tag => new Element(tag)}, DomEvent: {disableClickPropagation() {}},
    circleMarker: () => {
      const node = new Element('path'), events = {};
      const marker = {addTo(g) {g.addLayer(this); return this;}, bindPopup() {}, bindTooltip() {},
        on(k, f) {events[k] = f;}, getElement: () => node, openPopup() {this.popup = true;},
        closePopup() {this.popup = false;}, fire(k) {events[k]?.();}};
      markers.push(marker); return marker;
    }};
  let onResize;
  vm.runInNewContext(source, {document, L, ResizeObserver: class {
    constructor(callback) {onResize = callback;} observe() {}
  }, window: {matchMedia: () => ({matches: movil}), addEventListener() {}}});
  assert.equal(ids['mapa-litoral'].dataset.estado, 'listo');
  assert.equal(layers.size, 3);
  assert.equal(ids['mapa-lista'].children.length, 3);
  ids['mapa-buscar'].value = 'CHAJARI'; ids['mapa-buscar'].fire('input');
  assert.equal(layers.size, 1, 'La búsqueda ignora tildes y mayúsculas');
  assert.equal(ids['mapa-cuenta'].textContent, '1 de 3 localidades · Filtro visual');
  onResize([{contentRect: {width: 390, height: 270}}]);
  assert.equal(map.lastBounds, bounds, 'Cambiar tamaño conserva el encuadre filtrado');
  ids['mapa-vista-lista'].fire('click');
  assert.equal(ids['mapa-lista'].hidden, false);
  assert.equal(ids['mapa-panel'].hidden, true);
  const fila = ids['mapa-lista'].children[0]; fila.focus(); fila.fire('click');
  assert.equal(ids['mapa-detalle'].open, true);
  assert.equal(ids['mapa-detalle-titulo'].textContent, 'Chajarí');
  assert.equal(ids['mapa-detalle-contenido'].innerHTML, '<p>Sin datos no es cero</p>');
  ids['mapa-cerrar'].fire('click');
  assert.equal(ids['mapa-detalle'].open, false);
  assert.equal(document.activeElement, fila, 'Cerrar devuelve el foco a la lista');
  ids['mapa-buscar'].value = ''; ids['mapa-nivel'].value = 'ROJO'; ids['mapa-nivel'].fire('change');
  assert.equal(layers.size, 1);
  assert.equal(ids['mapa-lista'].children[0].attrs['aria-label'], 'Goya: Atención prioritaria. Abrir detalle');
  ids['mapa-buscar'].value = 'ninguna'; ids['mapa-buscar'].fire('input');
  assert.equal(layers.size, 0);
  assert.match(ids['mapa-lista'].children[0].textContent, /No hay coincidencias/);
  ids['mapa-buscar'].value = ''; ids['mapa-nivel'].value = ''; ids['mapa-nivel'].fire('change');
  ids['mapa-vista-mapa'].fire('click');
  assert.equal(layers.size, 3); assert.equal(ids['mapa-panel'].hidden, false);
  const nodo = markers[0].getElement(); nodo.focus();
  nodo.fire('keydown', {key: 'Enter', preventDefault() {}});
  assert.equal(ids['mapa-detalle-titulo'].textContent, 'Goya');
  ids['mapa-detalle'].close();
  assert.equal(document.activeElement, nodo);
  markers[1].fire('click');
  assert.equal(Boolean(ids['mapa-detalle'].open), movil, 'El toque móvil abre la ficha amplia');
  if (movil) {
    ids['mapa-detalle'].close();
    assert.equal(document.activeElement, markers[1].getElement(), 'Cerrar vuelve al nodo tocado');
  }
  assert.deepEqual(puntos.map(p => p.nivel), ['ROJO', 'SIN DATOS', 'VERDE']);
}
escenario(true); escenario(false);
console.log('OK: búsqueda, filtros, vistas, ficha, foco y niveles conservados.');
