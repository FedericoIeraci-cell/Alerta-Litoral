"use strict";
const {test} = require("node:test");
const assert = require("node:assert/strict");
const vm = require("node:vm");
const fs = require("node:fs");
const path = require("node:path");
const {execFileSync} = require("node:child_process");
const code = fs.readFileSync(path.join(__dirname, "..", "assets", "viirs", "viirs.js"), "utf8");
const ID = "VIIRS_Combined_Flood_2-Day";
const ID3 = "VIIRS_Combined_Flood_3-Day";
const HOST = "https://gibs.earthdata.nasa.gov";
const NOW = Date.parse("2026-10-23T20:00:00Z");
// Respuesta real de Domains, consultada el 23/10/2026; no confiere cobertura local.
const FIXTURE = "<Domains xmlns:ows='http://www.opengis.net/ows/1.1'><SpaceDomain><BoundingBox miny='-20037508.342789' minx='-20037508.342789' crs='urn:ogc:def:crs:EPSG::3857' maxy='20037508.342789' maxx='20037508.342789'/></SpaceDomain><DimensionDomain><ows:Identifier>time</ows:Identifier><Domain>2026-10-16/2026-10-23/P1D</Domain><Size>1</Size></DimensionDomain></Domains>";
const domain = (value, size = 1) => "<Domains xmlns:ows='http://www.opengis.net/ows/1.1'><DimensionDomain><ows:Identifier>time</ows:Identifier><Domain>" +
  value + "</Domain><Size>" + size + "</Size></DimensionDomain></Domains>";

// DOMParser es nativo en el navegador. Las pruebas usan un parser XML real de
// Python estándar para validar sintaxis y un adaptador DOM, sin dependencias npm.
class TestDOMParser {
  parseFromString(value) {
    const program = "import sys,json,xml.etree.ElementTree as E\n" +
      "def encode(e):\n return {'name':e.tag.split('}')[-1], 'text':''.join(e.itertext()), 'children':[encode(c) for c in e]}\n" +
      "try:\n result=encode(E.fromstring(sys.stdin.read()))\n" +
      "except E.ParseError:\n result={'name':'parsererror','text':'invalid XML','children':[]}\n" +
      "print(json.dumps(result))\n";
    const raw = JSON.parse(execFileSync("python3", ["-c", program], {input: value, encoding: "utf8"}));
    function node(data) {
      const children = data.children.map(node);
      return {localName: data.name, textContent: data.text, children,
        getElementsByTagNameNS(namespace, name) {
          return children.flatMap(c => (c.localName === name ? [c] : []).concat(c.getElementsByTagNameNS(namespace, name)));
        }};
    }
    const root = node(raw);
    return {documentElement: root, getElementsByTagNameNS(namespace, name) {
      return (root.localName === name ? [root] : []).concat(root.getElementsByTagNameNS(namespace, name));
    }};
  }
}
global.DOMParser = TestDOMParser;
const viirs = require("../assets/viirs/viirs.js");

test("fechas diarias estrictas: no se inventan horas ni se aceptan fechas imposibles", () => {
  for (const value of [null, "", "2026-02-30", "2026-13-01", "20261022.000000",
    "2026-10-22T00:00:00Z", "2026-10-22<script>"]) assert.equal(viirs.parseTime(value), null);
  assert.equal(viirs.parseTime("2024-02-29").toISOString(), "2024-02-29T00:00:00.000Z");
});

test("Domains real limita la selección a días UTC cerrados publicados", () => {
  const meta = viirs.metadata(FIXTURE, ID, NOW);
  assert.equal(meta.time, "2026-10-22");
  assert.equal(meta.availableDates.length, 7);
  assert.equal(meta.availableDates[0], "2026-10-16");
  assert.ok(!meta.availableDates.includes("2026-10-23"), "el día en proceso nunca es opción");
  assert.equal(meta.days, 2); assert.equal(meta.maxZoom, 9);
  assert.equal(viirs.metadata(domain("2026-10-20"), ID3, NOW).days, 3);
  assert.equal(viirs.metadata(domain("2026-10-20"), ID, NOW + 2 * 86400000).ageDays, 5,
    "volver a consultar no rejuvenece la fecha del compuesto");
});

test("XML, dominios y fechas ambiguos o inválidos no acreditan disponibilidad", () => {
  const invalid = [
    null, {}, "", "<html>not metadata</html>", "<Domains>", "<parsererror/>",
    "<!DOCTYPE Domains><Domains/>",
    domain(""), domain("2026-02-30"), domain("2026-10-22/2026-10-20/P1D"),
    domain("2026-10-20/2026-10-22/P1H"), domain("2026-10-22/2026-10-24/P1D"),
    domain("2026-10-23"), domain("2026-10-01/2026-10-22/P1D"),
    domain("2026-10-20,2026-10-20", 2), domain("2026-10-20", 2),
    domain("2026-10-20").replace("time", "elevation"),
    domain("2026-10-20").replace("</DimensionDomain>", "<ows:Identifier>time</ows:Identifier></DimensionDomain>"),
    domain("2026-10-20").replace("</DimensionDomain>", "<Domain>2026-10-21</Domain></DimensionDomain>"),
    domain("2026-10-20").replace("</Domains>", "<DimensionDomain><ows:Identifier>time</ows:Identifier><Domain>2026-10-21</Domain><Size>1</Size></DimensionDomain></Domains>"),
    " ".repeat(1000001), domain("2026-10-20") + "<!--" + "水".repeat(400000) + "-->"
  ];
  for (const xml of invalid) assert.throws(() => viirs.metadata(xml, ID, NOW));
  assert.throws(() => viirs.metadata(FIXTURE, "__proto__", NOW));
  assert.throws(() => viirs.metadata(FIXTURE, ID, Infinity));
});

test("URLs son NASA, limitan la consulta y fijan sólo fechas verificadas y ejes WMTS", () => {
  assert.equal(viirs.metadataUrl(ID, NOW), HOST +
    "/wmts/epsg3857/best/1.0.0/" + ID + "/default/GoogleMapsCompatible_Level9/all/2026-10-16--2026-10-23.xml");
  const meta = viirs.metadata(FIXTURE, ID, NOW);
  assert.equal(viirs.tileUrl(meta), HOST + "/wmts/epsg3857/best/" + ID +
    "/default/2026-10-22/GoogleMapsCompatible_Level9/{z}/{y}/{x}.png");
  assert.throws(() => viirs.tileUrl({...meta, time: "2026-10-23"}));
  assert.throws(() => viirs.tileUrl({...meta, time: "2026-10-01"}));
  assert.throws(() => viirs.tileUrl({...meta, id: "https://invalid.example"}));
  assert.throws(() => viirs.metadataUrl("RIVER-FLDglobal-composite", NOW));
  assert.match(viirs.tileUrl({...meta, host: "https://invalid.example"}), /^https:\/\/gibs\.earthdata\.nasa\.gov\//);
});

function frontend() {
  const elements = [], calls = [], layers = [], timers = new Map();
  let number = 0, clock = NOW;
  class Element {
    constructor(tag) {this.tag = tag; this.children = []; this.handlers = {}; this.style = {}; this.value = ""; elements.push(this);}
    appendChild(el) {this.children.push(el); return el;}
    replaceChildren(...children) {this.children = children;}
    addEventListener(name, fn) {this.handlers[name] = fn;}
    setAttribute(name, value) {this[name] = value;}
    removeAttribute(name) {delete this[name];}
  }
  const panes = {}, mapEvents = {};
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
  class FixedDate extends Date {constructor(...args) {super(...(args.length ? args : [clock]));} static now() {return clock;}}
  const context = {module: {exports: {}}, console, Date: FixedDate, AbortController, TextEncoder, DOMParser: TestDOMParser,
    document: {createElement: tag => new Element(tag), createTextNode: text => ({textContent: text})},
    fetch(url, options) {return new Promise((resolve, reject) => calls.push({url, options, resolve, reject}));},
    setTimeout(fn, ms) {const id = ++number; timers.set(id, {fn, ms}); return id;}, clearTimeout(id) {timers.delete(id);}};
  vm.runInNewContext(code, context); context.module.exports.attach(map, L);
  const one = tag => elements.find(e => e.tag === tag);
  const selects = elements.filter(e => e.tag === "select");
  selects[0].value = ID;
  return {elements, calls, layers, checkbox: elements.find(e => e.type === "checkbox"), select: selects[0], dateSelect: selects[1],
    status: elements.find(e => e.className === "viirs-status"), dates: elements.find(e => e.className === "viirs-dates"),
    summary: one("summary"), panel: elements.find(e => e.className === "viirs-panel"),
    source: one("a"), legend: one("img"), refresh: one("button"), timers, map, mapEvents,
    setNow(value) {clock = value;}};
}
const tick = () => new Promise(resolve => setImmediate(resolve));
const answer = (call, data = FIXTURE, ok = true) => call.resolve({ok, status: ok ? 200 : 503, text: async () => data});
function start(f) {f.checkbox.checked = true; f.checkbox.handlers.change();}

test("sin activación no hay consultas; controles caben y el estado precede los selectores", () => {
  const f = frontend(); assert.equal(f.calls.length, 0); assert.equal(f.dateSelect.disabled, true);
  assert.equal(f.panel.style.maxHeight, "192px");
  f.map.height = 440; f.mapEvents.resize(); assert.equal(f.panel.style.maxHeight, "362px");
  f.map.height = 0; f.mapEvents.resize(); assert.equal(f.panel.style.maxHeight, "362px");
  assert.ok(f.panel.children.indexOf(f.status) < f.panel.children.findIndex(e => e.tag === "label" && e.children.includes(f.select)));
});

test("metadata NASA real produce una imagen fechada y fechas separadas de la consulta", async () => {
  const f = frontend(); start(f);
  assert.equal(f.calls.length, 1); assert.equal(f.calls[0].options.credentials, "omit");
  assert.equal(f.calls[0].url, viirs.metadataUrl(ID, NOW)); assert.match(f.summary.textContent, /Cargando/);
  answer(f.calls[0]); await tick();
  assert.equal(f.layers.length, 1); assert.equal(f.dateSelect.value, "2026-10-22");
  assert.equal(f.dateSelect.disabled, false); assert.equal(f.dateSelect.children.length, 7);
  assert.ok(!f.dateSelect.children.some(e => e.value === "2026-10-23"));
  assert.match(f.dates.textContent, /ventana nominal UTC: 21\/10\/2026 a 22\/10\/2026/);
  assert.match(f.dates.textContent, /Consulta de disponibilidad/);
  assert.match(f.dates.textContent, /No identifica la hora exacta/);
  assert.equal(f.legend.src, HOST + "/legends/MODIS_Flood_H.svg");
  assert.match(f.source.href, /l=VIIRS_Combined_Flood_2-Day&t=2026-10-22$/);
  assert.match(f.layers[0].url, /GoogleMapsCompatible_Level9\/\{z\}\/\{y\}\/\{x\}\.png$/);
  f.layers[0].handlers.tileload();
  assert.match(f.summary.textContent, /Imágenes recibidas/);
  assert.match(f.status.textContent, /cobertura local y la ausencia de agua no están confirmadas/);
  f.layers[0].handlers.tileerror(); f.layers[0].handlers.tileload();
  assert.match(f.summary.textContent, /Capa incompleta/, "tileload posterior no oculta errores");
  const range = f.elements.find(e => e.type === "range"); range.value = "40"; range.handlers.input();
  assert.equal(f.layers[0].opacity, .4);
});

test("selector usa sólo fechas verificadas, conserva fecha de consulta y cancela imágenes viejas", async () => {
  const f = frontend(); start(f); answer(f.calls[0]); await tick();
  const first = f.layers[0];
  f.dateSelect.value = "2026-10-20"; f.dateSelect.handlers.change();
  assert.equal(f.calls.length, 1, "elegir una fecha verificada no inventa ni vuelve a consultar metadata");
  assert.equal(first.removed, true); assert.match(f.layers[1].url, /\/2026-10-20\//);
  assert.match(f.dates.textContent, /19\/10\/2026 a 20\/10\/2026/);
  assert.match(f.dates.textContent, /23\/10\/2026/);
  first.handlers.tileerror(); assert.match(f.summary.textContent, /Cargando/);
  f.dateSelect.value = "2026-10-23"; f.dateSelect.handlers.change();
  assert.equal(f.layers[1].removed, true); assert.equal(f.layers.length, 2);
  assert.equal(f.dates.textContent, ""); assert.match(f.status.textContent, /Fecha VIIRS no verificada/);
});

test("cruzar medianoche UTC no cambia el intervalo solicitado ni inventa el cierre en proceso", async () => {
  const f = frontend();
  f.setNow(Date.parse("2026-10-23T23:59:59Z")); start(f);
  assert.match(f.calls[0].url, /2026-10-16--2026-10-23\.xml$/);
  f.setNow(Date.parse("2026-10-24T00:00:02Z")); answer(f.calls[0]); await tick();
  assert.equal(f.layers.length, 1); assert.equal(f.dateSelect.value, "2026-10-22");
  assert.ok(!f.dateSelect.children.some(e => e.value === "2026-10-23"));
  assert.match(f.dates.textContent, /Consulta de disponibilidad: 23\/10\/2026, 21:00:02/);
});

test("desactivar o cerrar el mapa aborta consulta y descarta respuestas tardías", async () => {
  for (const mode of ["off", "unload"]) {
    const f = frontend(); start(f);
    const old = f.calls[0];
    if (mode === "off") {f.checkbox.checked = false; f.checkbox.handlers.change();}
    else f.mapEvents.unload();
    assert.equal(old.options.signal.aborted, true);
    answer(old); await tick(); assert.equal(f.layers.length, 0); assert.equal(f.calls.length, 1);
    assert.equal(f.dates.textContent, "");
  }
});

test("HTTP, XML inválido, fechas no verificadas y respuestas enormes no dejan capas engañosas", async () => {
  for (const mode of ["http", "xml", "missing", "oversize"]) {
    const f = frontend(); start(f);
    answer(f.calls[0], mode === "xml" ? "<html>not metadata</html>" : mode === "missing" ? domain("2026-10-23") :
      mode === "oversize" ? "x".repeat(1000001) : FIXTURE, mode !== "http");
    await tick(); assert.equal(f.calls.length, 1); assert.equal(f.layers.length, 0);
    assert.equal(f.dates.textContent, ""); assert.equal(f.dateSelect.disabled, true);
    assert.match(f.summary.textContent, /No disponible/); assert.equal(f.timers.size, 0);
  }
});

test("el único servicio tiene doce segundos y el timeout aborta sin pruebas de hosts arbitrarios", async () => {
  const f = frontend(); start(f); const timer = [...f.timers.values()][0];
  assert.equal(timer.ms, 12000); timer.fn(); assert.equal(f.calls[0].options.signal.aborted, true);
  f.calls[0].reject(Object.assign(new Error(), {name: "AbortError"})); await tick();
  assert.equal(f.calls.length, 1); assert.equal(f.layers.length, 0);
  assert.match(f.status.textContent, /tiempo de espera agotado/); assert.equal(f.timers.size, 0);
});

test("respuesta del producto anterior nunca cambia la nueva composición ni su ventana", async () => {
  const f = frontend(); start(f); const previous = f.calls[0];
  f.select.value = ID3; f.select.handlers.change(); assert.equal(previous.options.signal.aborted, true);
  answer(previous); await tick(); assert.equal(f.layers.length, 0);
  answer(f.calls[1]); await tick(); assert.equal(f.layers.length, 1);
  assert.match(f.layers[0].url, /VIIRS_Combined_Flood_3-Day\/default\/2026-10-22/);
  assert.match(f.dates.textContent, /ventana nominal UTC: 20\/10\/2026 a 22\/10\/2026/);
});

test("las clases NASA y los límites temporales se explican sin terminología SSEC", () => {
  const f = frontend();
  const text = f.elements.map(e => e.textContent || "").join(" ");
  assert.match(text, /NASA GIBS/); assert.match(text, /día en proceso no se usa/);
  assert.match(text, /No es una imagen en vivo/); assert.match(text, /gris = datos insuficientes/);
  assert.doesNotMatch(text, /SSEC|5 días|1 día: máxima/);
});

