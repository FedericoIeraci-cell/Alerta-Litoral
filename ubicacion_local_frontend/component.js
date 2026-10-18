/* Componente V1 local: contrato documentado por Streamlit, API version 1.
 * Sólo publica estados, ID de referencia y distancia entera. Sin coordenadas,
 * objetos Position/Error, servicios de geocodificación ni almacenamiento web.
 */
(function () {
  "use strict";
  const button = document.getElementById("locate");
  const status = document.getElementById("status");
  const origin = window.location.origin;
  let args = null;
  let pending = null;
  let autoChecked = false;
  let lastHeight = 0;

  function send(type, data) {
    window.parent.postMessage({isStreamlitMessage: true, type, ...data}, origin);
  }

  function resize() {
    const height = Math.max(66, Math.ceil(document.body.scrollHeight) + 2);
    if (height !== lastHeight) {
      lastHeight = height;
      send("streamlit:setFrameHeight", {height});
    }
  }

  function notice(text) {
    status.textContent = text;
    status.hidden = !text;
    resize();
  }

  function identifier() {
    const bytes = new Uint8Array(16);
    if (window.crypto?.getRandomValues) window.crypto.getRandomValues(bytes);
    else for (let i = 0; i < bytes.length; i++) bytes[i] = Math.floor(Math.random() * 256);
    return Array.from(bytes, value => value.toString(16).padStart(2, "0")).join("");
  }

  function publish(result, context) {
    const value = {event_id: identifier(), session_id: context.session_id, revision: context.revision,
      status: result.status};
    if (["ok", "other_app", "outside"].includes(result.status)) {
      value.reference_id = result.reference_id;
      value.distance_km = result.distance_km;
    }
    send("streamlit:setComponentValue", {value, dataType: "json"});
  }

  function blockedStatus() {
    if (!window.isSecureContext) return "insecure";
    if (!navigator.geolocation?.getCurrentPosition) return "unsupported";
    try {
      const policy = document.permissionsPolicy || document.featurePolicy;
      if (policy?.allowsFeature && !policy.allowsFeature("geolocation")) return "policy_blocked";
    } catch (_) { /* Sin detección de política: la API dará un estado de error. */ }
    return null;
  }

  function requestPosition() {
    if (!args || pending) return;
    autoChecked = true;
    const context = {session_id: args.session_id, revision: args.revision,
      catalog: args.catalog, app: args.app, policy: args.policy};
    const blocked = blockedStatus();
    if (blocked) {
      notice("Ubicación no disponible; el selector manual sigue activo.");
      publish({status: blocked}, context);
      return;
    }
    const request = {context};
    pending = request;
    button.disabled = true;
    button.textContent = "Buscando ubicación…";
    notice("Podés seguir usando el selector manual.");
    publish({status: "pending"}, context);

    function complete(result) {
      if (pending !== request) return;
      pending = null;
      window.clearTimeout(request.timer);
      button.disabled = false;
      button.textContent = "Usar mi ubicación";
      if (!args || args.session_id !== context.session_id || args.revision !== context.revision) {
        notice("");
        return;
      }
      notice("");
      publish(result, context);
    }

    // Evita una espera indefinida si el navegador deja el permiso sin resolver.
    request.timer = window.setTimeout(() => complete({status: "timeout"}), 15000);
    try {
      navigator.geolocation.getCurrentPosition(
        position => {
          if (pending !== request) return;
          let result;
          try { result = ReferenciaCercana.chooseReference(position.coords, context.catalog, context.app, context.policy); }
          catch (_) { result = {status: "position_unavailable"}; }
          complete(result);
        },
        error => complete({status: ReferenciaCercana.errorStatus(error?.code)}),
        {enableHighAccuracy: true, timeout: 12000, maximumAge: 0}
      );
    } catch (_) { complete({status: "position_unavailable"}); }
  }

  async function checkExistingPermission() {
    if (autoChecked || !args?.auto_allowed) return;
    autoChecked = true;
    const context = {session_id: args.session_id, revision: args.revision};
    if (!navigator.permissions?.query || blockedStatus()) {
      publish({status: "ready"}, context);
      return;
    }
    try {
      const permission = await navigator.permissions.query({name: "geolocation"});
      if (!args || !args.auto_allowed || pending || context.session_id !== args.session_id || context.revision !== args.revision) return;
      if (permission.state === "granted") requestPosition();
      else publish({status: "ready"}, context);
    } catch (_) { publish({status: "ready"}, context); }
  }

  button.addEventListener("click", requestPosition);
  window.addEventListener("message", event => {
    if (event.source !== window.parent || event.origin !== origin || event.data?.type !== "streamlit:render") return;
    const incoming = event.data.args;
    if (!incoming || typeof incoming.session_id !== "string" || !Number.isInteger(incoming.revision)) return;
    if (args && incoming.session_id !== args.session_id) autoChecked = false;
    if (pending && (incoming.session_id !== pending.context.session_id || incoming.revision !== pending.context.revision)) {
      window.clearTimeout(pending.timer);
      pending = null;
      notice("");
    }
    args = incoming;
    button.disabled = !!event.data.disabled || !!pending;
    button.textContent = pending ? "Buscando ubicación…" : "Usar mi ubicación";
    const theme = event.data.theme;
    if (theme) {
      if (theme.textColor) document.documentElement.style.setProperty("--text", theme.textColor);
      if (theme.backgroundColor) document.documentElement.style.setProperty("--background", theme.backgroundColor);
    }
    resize();
    checkExistingPermission();
  });
  window.addEventListener("resize", resize);
  if (typeof ResizeObserver !== "undefined") new ResizeObserver(resize).observe(document.body);
  send("streamlit:componentReady", {apiVersion: 1});
  resize();
})();
