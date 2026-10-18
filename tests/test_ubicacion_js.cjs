"use strict";
const {test} = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const {webcrypto} = require("node:crypto");
const frontend = path.join(__dirname, "..", "ubicacion_local_frontend");
const geo = require(path.join(frontend, "geo.js"));
const catalog = JSON.parse(fs.readFileSync(path.join(frontend, "catalogo.json"), "utf8")).referencias;
const policy = {max_accuracy_m: 2000, search_km: {ala: 50, zc: 15}};
const coords = (latitude, longitude, accuracy = 20) => ({latitude, longitude, accuracy});
const at = id => {const n = catalog.find(n => n.id === id); return coords(n.lat, n.lon);};

test("ciudades sintéticas eligen su referencia en ambas redes", () => {
  for (const id of ["ala-rosario", "ala-corrientes", "ala-resistencia", "ala-puerto-iguazu", "zc-zarate-centro", "zc-campana-centro"]) {
    const result = geo.chooseReference(at(id), catalog, id.split("-")[0], policy);
    assert.deepEqual(result, {status: "ok", reference_id: id, distance_km: 0});
  }
});

test("la otra red se ofrece sólo por referencia cercana; lejos no se elige", () => {
  assert.equal(geo.chooseReference(at("zc-campana-centro"), catalog, "ala", policy).status, "other_app");
  assert.equal(geo.chooseReference(at("ala-rosario"), catalog, "zc", policy).status, "other_app");
  for (const point of [coords(-32.89, -68.83), coords(40.4168, -3.7038), coords(0, 0)]) {
    const result = geo.chooseReference(point, catalog, "ala", policy);
    assert.equal(result.status, "outside");
    assert.ok(result.distance_km > 50);
    assert.ok(Number.isInteger(result.distance_km));
  }
});

test("precisión amplia y referencias vecinas ambiguas conservan elección manual", () => {
  assert.deepEqual(geo.chooseReference({...at("ala-rosario"), accuracy: 2001}, catalog, "ala", policy), {status: "imprecise"});
  const center = catalog.find(n => n.id === "zc-campana-centro");
  const coast = catalog.find(n => n.id === "zc-campana-costanera");
  const midpoint = coords((center.lat + coast.lat) / 2, (center.lon + coast.lon) / 2, 800);
  assert.deepEqual(geo.chooseReference(midpoint, catalog, "zc", policy), {status: "ambiguous"});
  assert.deepEqual(geo.chooseReference({...at("ala-reconquista"), accuracy: 1800}, catalog, "ala", policy), {status: "ambiguous"});
});

test("el círculo de incertidumbre que cruza el alcance no se anuncia fuera de cobertura", () => {
  const only = [{id: "ala-prueba", app: "ala", lat: 0, lon: 0}];
  const latitude = kilometers => kilometers / 6371.0088 * 180 / Math.PI;
  assert.equal(geo.chooseReference(coords(latitude(49), 0, 500), only, "ala", policy).status, "ok");
  assert.deepEqual(geo.chooseReference(coords(latitude(49.8), 0, 500), only, "ala", policy), {status: "imprecise"});
  assert.deepEqual(geo.chooseReference(coords(latitude(50.2), 0, 500), only, "ala", policy), {status: "imprecise"});
  assert.equal(geo.chooseReference(coords(latitude(51), 0, 500), only, "ala", policy).status, "outside");
});

test("entradas no válidas no producen referencia ni distancia", () => {
  for (const point of [null, coords(NaN, 0), coords(0, Infinity), coords(-91, 0), coords(0, 181),
    coords(true, 0), coords("-34", 0), coords(0, 0, -1), coords(0, 0, NaN)]) {
    assert.deepEqual(geo.chooseReference(point, catalog, "ala", policy), {status: "position_unavailable"});
  }
  assert.deepEqual(geo.chooseReference(at("ala-rosario"), [], "ala", policy), {status: "position_unavailable"});
  assert.deepEqual(geo.chooseReference(at("ala-rosario"), [...catalog, catalog[0]], "ala", policy), {status: "position_unavailable"});
  assert.equal(geo.errorStatus(1), "permission_denied");
  assert.equal(geo.errorStatus(2), "position_unavailable");
  assert.equal(geo.errorStatus(3), "timeout");
});

function browser({permission = "prompt", secure = true, allowed = true, unsupported = false} = {}) {
  const messages = [], calls = [], handlers = {}, timers = new Map();
  let nextTimer = 0, permissionCalls = 0;
  const button = {textContent: "", disabled: true, addEventListener: (name, handler) => {button[name] = handler;}};
  const status = {textContent: "", hidden: true};
  const origin = "https://ejemplo.streamlit.app";
  const parent = {postMessage: (value, target) => {
    assert.equal(target, origin);
    messages.push(JSON.parse(JSON.stringify(value)));
  }};
  const document = {getElementById: id => id === "locate" ? button : status, body: {scrollHeight: 70},
    documentElement: {style: {setProperty() {}}}, featurePolicy: {allowsFeature: () => allowed}};
  const navigator = {permissions: {query: async () => {permissionCalls++; return {state: permission};}}};
  if (!unsupported) navigator.geolocation = {getCurrentPosition: (success, error, options) => calls.push({success, error, options})};
  const window = {parent, location: {origin}, isSecureContext: secure, crypto: webcrypto,
    setTimeout: (fn, ms) => {const id = ++nextTimer; timers.set(id, {fn, ms}); return id;},
    clearTimeout: id => timers.delete(id), addEventListener: (name, handler) => {handlers[name] = handler;}};
  vm.runInNewContext(fs.readFileSync(path.join(frontend, "component.js"), "utf8"),
    {window, document, navigator, ReferenciaCercana: geo, Uint8Array, Math, Set, Number, Array});
  let args = {catalog, app: "ala", policy, session_id: "1".repeat(24), revision: 0, auto_allowed: true};
  return {messages, calls, button, status, timers,
    render: changes => {args = {...args, ...changes}; handlers.message({source: parent, origin, data: {type: "streamlit:render", args}});},
    foreign: () => handlers.message({source: {}, origin: "https://otro.example", data: {type: "streamlit:render", args}}),
    values: () => messages.filter(m => m.type === "streamlit:setComponentValue").map(m => m.value),
    permissionCalls: () => permissionCalls};
}
const tick = () => new Promise(resolve => setImmediate(resolve));

test("el primer permiso requiere clic; el protocolo nunca publica coordenadas ni Error", async () => {
  const b = browser();
  b.render(); await tick();
  assert.equal(b.calls.length, 0);
  b.foreign();
  b.button.click();
  assert.equal(b.calls.length, 1);
  assert.equal(b.calls[0].options.maximumAge, 0);
  b.calls[0].success({coords: {...at("ala-rosario"), altitude: 15, speed: 1}, timestamp: 1234567});
  const result = b.values().at(-1);
  assert.equal(result.status, "ok");
  assert.deepEqual(Object.keys(result).sort(), ["distance_km", "event_id", "reference_id", "revision", "session_id", "status"]);
  const payload = JSON.stringify(b.messages);
  for (const forbidden of ["latitude", "longitude", "accuracy", "altitude", "timestamp", "coords", "speed"]) assert.ok(!payload.includes(forbidden));
});

test("un permiso ya concedido permite una consulta automática y ninguna en los reruns", async () => {
  const b = browser({permission: "granted"});
  b.render(); await tick();
  assert.equal(b.calls.length, 1);
  b.render({auto_allowed: false});
  b.calls[0].success({coords: at("ala-rosario")});
  for (let i = 0; i < 3; i++) {b.render(); await tick();}
  assert.equal(b.calls.length, 1);
  assert.equal(b.permissionCalls(), 1);
});

test("permiso denegado, indisponibilidad y timeout no se reintentan automáticamente", async () => {
  for (const [code, expected] of [[1, "permission_denied"], [2, "position_unavailable"], [3, "timeout"]]) {
    const b = browser({permission: "denied"}); b.render(); await tick();
    assert.equal(b.calls.length, 0);
    b.button.click();
    b.calls[0].error({code, message: "mensaje que no debe salir del navegador", coords: at("ala-rosario")});
    assert.equal(b.values().at(-1).status, expected);
    assert.ok(!JSON.stringify(b.messages).includes("mensaje que no debe"));
    b.render({auto_allowed: false}); await tick();
    assert.equal(b.calls.length, 1);
  }
});

test("un cambio manual cancela aplicación tardía; un nuevo clic explícito vuelve a consultar", async () => {
  const b = browser(); b.render(); await tick();
  b.button.click();
  const before = b.values().length;
  b.render({revision: 1, auto_allowed: false});
  b.calls[0].success({coords: at("ala-rosario")});
  assert.equal(b.values().length, before);
  b.button.click();
  assert.equal(b.calls.length, 2);
  b.calls[1].success({coords: at("ala-corrientes")});
  assert.equal(b.values().at(-1).reference_id, "ala-corrientes");
  assert.equal(b.values().at(-1).revision, 1);
});

test("política bloqueada, contexto inseguro y API ausente permiten selección manual", async () => {
  for (const [config, expected] of [[{allowed: false}, "policy_blocked"], [{secure: false}, "insecure"], [{unsupported: true}, "unsupported"]]) {
    const b = browser(config); b.render(); await tick(); b.button.click();
    assert.equal(b.values().at(-1).status, expected);
    assert.equal(b.calls.length, 0);
    assert.equal(b.button.disabled, false);
  }
});

test("permiso sin resolver termina una vez y descarta una posición recibida después", async () => {
  const b = browser(); b.render(); await tick(); b.button.click();
  const timer = [...b.timers.values()].find(t => t.ms === 15000);
  timer.fn();
  assert.equal(b.values().at(-1).status, "timeout");
  const count = b.values().length;
  b.calls[0].success({coords: at("ala-rosario")});
  assert.equal(b.values().length, count);
  assert.equal(b.button.disabled, false);
});
