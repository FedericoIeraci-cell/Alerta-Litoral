/* Cálculo local sobre referencias públicas. No realiza consultas ni conserva posiciones. */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.ReferenciaCercana = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";
  const EARTH_KM = 6371.0088;
  const finite = value => typeof value === "number" && Number.isFinite(value);
  const radians = degrees => degrees * Math.PI / 180;

  function distanceKm(latitude, longitude, reference) {
    const dlat = radians(reference.lat - latitude);
    const dlon = radians(reference.lon - longitude);
    const a = Math.sin(dlat / 2) ** 2 + Math.cos(radians(latitude)) * Math.cos(radians(reference.lat)) * Math.sin(dlon / 2) ** 2;
    return EARTH_KM * 2 * Math.asin(Math.sqrt(Math.max(0, Math.min(1, a))));
  }

  function chooseReference(coords, catalog, app, policy) {
    if (!coords || !finite(coords.latitude) || Math.abs(coords.latitude) > 90 ||
        !finite(coords.longitude) || Math.abs(coords.longitude) > 180 ||
        !finite(coords.accuracy) || coords.accuracy < 0 || !Array.isArray(catalog) ||
        !["ala", "zc"].includes(app) || !policy || !finite(policy.max_accuracy_m)) {
      return {status: "position_unavailable"};
    }
    if (coords.accuracy > policy.max_accuracy_m) return {status: "imprecise"};
    const seen = new Set();
    const ranked = [];
    for (const node of catalog) {
      if (!node || typeof node.id !== "string" || seen.has(node.id) || !["ala", "zc"].includes(node.app) ||
          !finite(node.lat) || Math.abs(node.lat) > 90 || !finite(node.lon) || Math.abs(node.lon) > 180 ||
          !finite(policy.search_km?.[node.app]) || policy.search_km[node.app] <= 0) {
        return {status: "position_unavailable"};
      }
      seen.add(node.id);
      ranked.push({node, distance: distanceKm(coords.latitude, coords.longitude, node)});
    }
    ranked.sort((a, b) => a.distance - b.distance || a.node.id.localeCompare(b.node.id));
    if (!ranked.length) return {status: "position_unavailable"};
    const first = ranked[0];
    const accuracyKm = coords.accuracy / 1000;
    const limit = policy.search_km[first.node.app];
    const nearest = {reference_id: first.node.id, distance_km: Math.round(first.distance)};
    if (first.distance - accuracyKm > limit) return {status: "outside", ...nearest};
    if (first.distance + accuracyKm > limit) return {status: "imprecise"};
    if (ranked.length > 1 && ranked[1].distance - first.distance <= 2 * accuracyKm + 1e-9) {
      return {status: "ambiguous"};
    }
    return {status: first.node.app === app ? "ok" : "other_app", ...nearest};
  }

  function errorStatus(code) {
    return code === 1 ? "permission_denied" : code === 3 ? "timeout" : "position_unavailable";
  }

  return {distanceKm, chooseReference, errorStatus};
});
