"use strict";
const {test} = require("node:test");
const assert = require("node:assert/strict");
const vm = require("node:vm");
const fs = require("node:fs");
const path = require("node:path");
const code = fs.readFileSync(path.join(__dirname, "..", "assets", "viirs", "viirs.js"), "utf8");
const viirs = require("../assets/viirs/viirs.js");
const ID = "RIVER-FLDglobal-composite1";
const PRIMARY = "https://floods.ssec.wisc.edu";
const SECONDARY = "https://realearth.ssec.wisc.edu";
const NOW = Date.parse("2026-10-21T17:00:00Z");
const product = (extra = {}) => [{id: ID, type: "raster", released: true,
  maxzoom: 7, times: ["20261019.000000", "20261020.000000"], ...extra}];

test("se rechazan fechas imposibles, ausentes y formatos no documentados", () => {
  for (const value of [null, "", "20260230.000000", "20261301.000000", "20261020.240000",
    "20261020.006000", "20261020.000060", "2026-10-20", "20261020.000000<script>"]) {
    assert.equal(viirs.parseTime(value), null);
  }
  assert.equal(viirs.parseTime("20240229.000000").toISOString(), "2024-02-29T00:00:00.000Z");
});

test("un HTTP correcto sin producto inequívoco y fecha verificable no acredita capa", () => {
  for (const data of [null, {}, [], product({times: []}), product({times: ["20261022.000000"]}),
    product({id: "otro"}), product({released: false}), product({type: "shape"}),
    [...product(), ...product()]]) {
    assert.throws(() => viirs.metadata(data, ID, NOW));
  }
  assert.throws(() => viirs.metadata(product(), "__proto__", NOW));
});

test("la imagen queda fijada a su fecha y la consulta no rejuvenece el compuesto", () => {
  const before = JSON.stringify(product());
  const meta = viirs.metadata(product({times: ["20261020.000000", "20261019.000000", "20261025.000000"]}), ID, NOW);
  assert.equal(meta.time, "20261020.000000");
  assert.ok(viirs.tileUrl(meta).includes(ID + "_20261020_000000"));
  assert.equal(viirs.metadata(product(), ID, NOW + 4 * 86400000).ageDays, 5);
  assert.equal(JSON.stringify(product()), before);
  assert.throws(() => viirs.tileUrl({...meta, id: "https://invalid.example"}));
  assert.throws(() => viirs.tileUrl({...meta, host: "https://invalid.example"}));
});

function frontend() {
  const elements = [], calls = [], layers = [], timers = new Map();
  let number = 0;
  class Element {
    constructor(tag) {this.tag = tag; this.children = []; this.handlers = {}; this.style = {}; this.value = ""; elements.push(this);}
    appendChild(el) {this.children.push(el); if (this.tag === "select" && this.children.length === 1) this.value = el.value; return el;}
    addEventListener(name, fn) {this.handlers[name] = fn;}
    setAttribute(name, value) {this[name] = value;}
    removeAttribute(name) {delete this[name];}
  }
  const panes = {};
  const mapEvents = {};
  const map = {height: 270, getSize() {return {y: this.height};},
    removeLayer(layer) {layer.removed = true;}, getPane: id => panes[id],
    createPane(id) {panes[id] = {style: {}};}, on(name, fn) {mapEvents[name] = fn;}};
  const L = {control() {return {addTo(m) {this.box = this.onAdd(m);}};},
    DomUtil: {create: tag => new Element(tag)}, DomEvent: {disableClickPropagation() {}, disableScrollPropagation() {}},
    tileLayer(url, options) {
      const layer = {url, options, handlers: {}, on(name, fn) {this.handlers[name] = fn; return this;},
        addTo() {return this;}, setOpacity(value) {this.opacity = value;}};
      layers.push(layer); return layer;
    }};
  const context = {module: {exports: {}}, console, Date, AbortController,
    document: {createElement: tag => new Element(tag), createTextNode: text => ({textContent: text})},
    fetch(url, options) {return new Promise((resolve, reject) => calls.push({url, options, resolve, reject}));},
    setTimeout(fn, ms) {const id = ++number; timers.set(id, {fn, ms}); return id;}, clearTimeout(id) {timers.delete(id);}};
  vm.runInNewContext(code, context);
  context.module.exports.attach(map, L);
  const one = tag => elements.find(e => e.tag === tag);
  const checkbox = elements.find(e => e.type === "checkbox");
  // El stub no implementa la selección automática del primer option al cambiar su value.
  one("select").value = ID;
  return {elements, calls, layers, checkbox, select: one("select"), status: elements.find(e => e.className === "viirs-status"),
    dates: elements.find(e => e.className === "viirs-dates"), summary: one("summary"),
    panel: elements.find(e => e.className === "viirs-panel"), source: one("a"), legend: one("img"), timers, map, mapEvents};
}
const tick = () => new Promise(resolve => setImmediate(resolve));
const answer = (call, data = product(), ok = true) => call.resolve({ok, status: ok ? 200 : 503, text: async () => JSON.stringify(data)});

test("el panel satelital cabe en el mapa reducido y redimensionar no consulta fuentes", () => {
  const f = frontend();
  const panel = f.elements.find(e => e.className === "viirs-panel");
  assert.equal(panel.style.maxHeight, "192px");
  f.map.height = 440; f.mapEvents.resize();
  assert.equal(panel.style.maxHeight, "362px");
  f.map.height = 0; f.mapEvents.resize();
  assert.equal(panel.style.maxHeight, "362px", "Ocultar el mapa no invalida el límite anterior");
  assert.equal(f.calls.length, 0);
  assert.ok(panel.children.indexOf(f.status) < panel.children.findIndex(e => e.tag === "label" && e.children.includes(f.select)),
    "El resultado se muestra antes del selector y la opacidad, junto al interruptor");
});

test("la primera vista no consulta VIIRS; una respuesta válida incorpora imagen y fecha nominal", async () => {
  const f = frontend(); assert.equal(f.calls.length, 0);
  f.checkbox.checked = true; f.checkbox.handlers.change();
  assert.equal(f.calls.length, 1);
  assert.equal(f.calls[0].options.credentials, "omit");
  assert.equal(f.calls[0].url, PRIMARY + "/api/products?products=" + ID + "&timespan=-7d");
  assert.match(f.summary.textContent, /Cargando/);
  answer(f.calls[0]); await tick();
  assert.equal(f.layers.length, 1);
  assert.ok(f.dates.textContent.includes("fecha nominal (UTC)"));
  assert.ok(f.dates.textContent.includes("No identifica la hora exacta"));
  assert.match(f.layers[0].url, /^https:\/\/floods\.ssec\.wisc\.edu\//);
  f.layers[0].handlers.tileload();
  assert.match(f.summary.textContent, /Imágenes recibidas/);
  assert.match(f.status.textContent, /cobertura local puede ser parcial/);
  f.layers[0].handlers.tileerror(); f.layers[0].handlers.tileload();
  assert.ok(f.status.textContent.includes("Capa incompleta"));
  assert.match(f.summary.textContent, /Capa incompleta/);
  const range = f.elements.find(e => e.type === "range"); range.value = "40"; range.handlers.input();
  assert.equal(f.layers[0].opacity, .4);
});

test("desactivar o cambiar producto descarta consultas tardías y quita la capa anterior", async () => {
  const f = frontend(); f.checkbox.checked = true; f.checkbox.handlers.change();
  const old = f.calls[0]; f.checkbox.checked = false; f.checkbox.handlers.change();
  assert.equal(old.options.signal.aborted, true);
  answer(old); await tick(); assert.equal(f.layers.length, 0);
  assert.equal(f.calls.length, 1, "Desactivar no inicia una consulta alternativa");
  assert.equal(f.status.textContent, "Capa desactivada.");
  f.checkbox.checked = true; f.checkbox.handlers.change(); answer(f.calls[1]); await tick();
  f.select.value = "RIVER-FLDglobal-composite"; f.select.handlers.change();
  assert.equal(f.layers[0].removed, true); assert.equal(f.dates.textContent, "");
  f.calls[2].reject(new Error("Red caída")); await tick();
  assert.equal(f.calls[3].url, SECONDARY + "/api/products?products=RIVER-FLDglobal-composite&timespan=-7d");
  f.calls[3].reject(new Error("Red caída")); await tick();
  assert.ok(f.status.textContent.includes("no se pudo verificar"));
});

test("fallos HTTP, JSON inválido, falta de fechas y timeout no dejan imagen ni fecha engañosa", async () => {
  for (const mode of ["http", "json", "missing", "timeout"]) {
    const f = frontend(); f.checkbox.checked = true; f.checkbox.handlers.change();
    if (mode === "http") answer(f.calls[0], product(), false);
    if (mode === "json") f.calls[0].resolve({ok: true, text: async () => "<html>error</html>"});
    if (mode === "missing") answer(f.calls[0], product({times: []}));
    if (mode === "timeout") { [...f.timers.values()][0].fn(); f.calls[0].reject(Object.assign(new Error(), {name: "AbortError"})); }
    await tick();
    assert.equal(f.calls.length, 2);
    if (mode === "http") answer(f.calls[1], product(), false);
    if (mode === "json") f.calls[1].resolve({ok: true, text: async () => "<html>error</html>"});
    if (mode === "missing") answer(f.calls[1], product({times: []}));
    if (mode === "timeout") { [...f.timers.values()][0].fn(); f.calls[1].reject(Object.assign(new Error(), {name: "AbortError"})); }
    await tick();
    assert.equal(f.layers.length, 0); assert.equal(f.dates.textContent, "");
    assert.ok(/no disponible|no respondió/.test(f.status.textContent));
    assert.match(f.summary.textContent, /No disponible/);
    assert.equal(f.timers.size, 0);
  }
});

test("si falla el primer servicio, imagen, leyenda y enlace usan el segundo servicio verificado", async () => {
  const f = frontend(); f.checkbox.checked = true; f.checkbox.handlers.change();
  answer(f.calls[0], product(), false); await tick();
  assert.equal(f.calls.length, 2);
  assert.equal(f.calls[1].url, SECONDARY + "/api/products?products=" + ID + "&timespan=-7d");
  answer(f.calls[1]); await tick();
  assert.equal(f.layers.length, 1);
  assert.equal(f.layers[0].url, SECONDARY + "/api/image?products=" + ID + "_20261020_000000&x={x}&y={y}&z={z}");
  assert.equal(f.legend.src, SECONDARY + "/api/legend?products=" + ID);
  assert.match(f.source.href, /^https:\/\/realearth\.ssec\.wisc\.edu\/\?products=RIVER-FLDglobal-composite1_20261020_000000/);
  assert.ok(f.dates.textContent.includes("20/10/2026"));
  assert.equal(f.timers.size, 0);
});

test("cada host tiene ocho segundos y no se prueban más de dos servicios", async () => {
  const f = frontend(); f.checkbox.checked = true; f.checkbox.handlers.change();
  let timer = [...f.timers.values()][0]; assert.equal(timer.ms, 8000); timer.fn();
  assert.equal(f.calls[0].options.signal.aborted, true);
  f.calls[0].reject(Object.assign(new Error(), {name: "AbortError"})); await tick();
  assert.equal(f.calls.length, 2);
  timer = [...f.timers.values()][0]; assert.equal(timer.ms, 8000); timer.fn();
  f.calls[1].reject(Object.assign(new Error(), {name: "AbortError"})); await tick();
  assert.equal(f.calls.length, 2); assert.equal(f.layers.length, 0);
  assert.equal(f.dates.textContent, ""); assert.match(f.status.textContent, /tiempo de espera agotado/);
});

test("desactivar durante la segunda consulta la aborta y descarta su respuesta tardía", async () => {
  const f = frontend(); f.checkbox.checked = true; f.checkbox.handlers.change();
  f.calls[0].reject(new TypeError("Failed to fetch")); await tick();
  assert.equal(f.calls.length, 2);
  f.checkbox.checked = false; f.checkbox.handlers.change();
  assert.equal(f.calls[1].options.signal.aborted, true);
  answer(f.calls[1]); await tick();
  assert.equal(f.calls.length, 2); assert.equal(f.layers.length, 0); assert.equal(f.dates.textContent, "");
  assert.equal(f.summary.textContent, "Agua por satélite");
});

test("una respuesta tardía del producto anterior no modifica la nueva consulta", async () => {
  const f = frontend(); f.checkbox.checked = true; f.checkbox.handlers.change();
  const previous = f.calls[0];
  f.select.value = "RIVER-FLDglobal-composite"; f.select.handlers.change();
  assert.equal(previous.options.signal.aborted, true);
  answer(previous); await tick();
  assert.equal(f.calls.length, 2); assert.equal(f.layers.length, 0);
  answer(f.calls[1], product({id: "RIVER-FLDglobal-composite"})); await tick();
  assert.equal(f.layers.length, 1); assert.match(f.layers[0].url, /RIVER-FLDglobal-composite_20261020_000000/);
  assert.match(f.dates.textContent, /Composición de 5 días/);
});

