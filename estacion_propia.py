"""Observaciones personales opcionales. Sin dependencias del motor de riesgo.

Contrato ALA-PWS/1: cada variable conserva valor, unidad y fecha originales.
El transporte Ecowitt V3 es reemplazable por otro adaptador al mismo contrato.
Nunca se completa una ausencia con cero ni se habilita el uso en alertas.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import math
import re
from typing import Protocol

import requests

API_URL = "https://api.ecowitt.net/api/v3/device/real_time"
MAX_BYTES = 128_000
STALE_SECONDS = 600
FUTURE_SECONDS = 120

# Rango amplio de plausibilidad, no umbrales de alerta ni precisión certificada.
# Nombre: (unidad canónica, mínimo, máximo, salto en <=10 min a revisar).
FIELDS = {
    "temperature": ("°C", -60, 65, 10),
    "humidity": ("%", 0, 100, 40),
    "pressure_absolute": ("hPa", 300, 1100, 10),
    "pressure_relative": ("hPa", 800, 1100, 10),
    "wind_speed": ("m/s", 0, 100, None),
    "wind_gust": ("m/s", 0, 120, None),
    "wind_direction": ("°", 0, 360, None),
    "rain_rate": ("mm/h", 0, 600, None),
    "rain_daily": ("mm", 0, 2000, None),
    "rain_piezo_rate": ("mm/h", 0, 600, None),
    "rain_piezo_daily": ("mm", 0, 2000, None),
}
PATHS = {
    "temperature": ("outdoor", "temperature"),
    "humidity": ("outdoor", "humidity"),
    "pressure_absolute": ("pressure", "absolute"),
    "pressure_relative": ("pressure", "relative"),
    "wind_speed": ("wind", "wind_speed"),
    "wind_gust": ("wind", "wind_gust"),
    "wind_direction": ("wind", "wind_direction"),
    "rain_rate": ("rainfall", "rain_rate"),
    "rain_daily": ("rainfall", "daily"),
    "rain_piezo_rate": ("rainfall_piezo", "rain_rate"),
    "rain_piezo_daily": ("rainfall_piezo", "daily"),
}


class StationError(ValueError):
    """Sólo códigos constantes; no incluir respuestas, URLs o credenciales."""


class ObservationConnector(Protocol):
    def fetch(self, *, now: datetime | None = None) -> dict:
        """Devuelve ALA-PWS/1 sin controles temporales almacenados en caché."""
        ...


def utc(value):
    """Acepta ISO con zona o epoch en segundos. Nunca adivina una zona."""
    try:
        if isinstance(value, bool) or value is None:
            return None
        if isinstance(value, datetime):
            parsed = value
        elif isinstance(value, (int, float)) or re.fullmatch(r"[0-9]{10}(?:\.[0-9]+)?", str(value)):
            parsed = datetime.fromtimestamp(float(value), timezone.utc)
        else:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def _now(value=None):
    result = datetime.now(timezone.utc) if value is None else utc(value)
    if result is None:
        raise StationError("fecha_referencia_invalida")
    return result


def _scalar(value):
    if isinstance(value, str):
        return value[:64]
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        try:
            return value if math.isfinite(value) else str(value)
        except OverflowError:
            return str(value)[:64]
    return None


def _convert(value, unit, target):
    if isinstance(value, bool) or value is None:
        raise ValueError
    number = float(value)
    if not math.isfinite(number):
        raise ValueError
    normalized = str(unit).strip().replace("º", "°").replace("℃", "°C").replace("℉", "°F")
    conversions = {
        "°C": {"°C": (1, 0), "°F": (5 / 9, -32 * 5 / 9)},
        "%": {"%": (1, 0)},
        "hPa": {"hPa": (1, 0), "inHg": (33.8638866667, 0), "mmHg": (1.333223874, 0)},
        "m/s": {"m/s": (1, 0), "mph": (.44704, 0), "km/h": (1 / 3.6, 0), "knots": (.514444444, 0)},
        "°": {"°": (1, 0)},
        "mm": {"mm": (1, 0), "in": (25.4, 0)},
        "mm/h": {"mm/h": (1, 0), "mm/hr": (1, 0), "in/h": (25.4, 0), "in/hr": (25.4, 0)},
    }
    if normalized not in conversions[target]:
        raise StationError("unidad_desconocida")
    scale, offset = conversions[target][normalized]
    return number * scale + offset


def adapt_ecowitt(payload, station_id, *, received_at=None, simulated=False):
    """Adapta exclusivamente campos documentados; descarta claves y ubicación."""
    if not isinstance(payload, dict) or type(payload.get("code")) is not int or payload["code"] != 0:
        raise StationError("respuesta_proveedor_invalida")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise StationError("respuesta_proveedor_invalida")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", station_id):
        raise StationError("identificador_invalido")
    measurements = {}
    for name, (group, field) in PATHS.items():
        part = data.get(group, {})
        item = part.get(field) if isinstance(part, dict) else None
        if isinstance(item, dict):
            measurements[name] = {"value": _scalar(item.get("value")),
                                  "unit": _scalar(item.get("unit")),
                                  "observed_at": _scalar(item.get("time"))}
    return {"schema_version": 1, "station_id": station_id,
            "source_kind": "simulated" if simulated else "personal_observation",
            "provider": "ecowitt_v3", "received_at": _now(received_at).isoformat(),
            "risk_eligible": False, "measurements": measurements}


def assess(record, *, now=None, previous=None):
    """Valida contrato, vigencia por sensor y anomalías. No corrige extremos."""
    now = _now(now)
    if (not isinstance(record, dict) or type(record.get("schema_version")) is not int
            or record["schema_version"] != 1
            or record.get("source_kind") not in ("personal_observation", "simulated")
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", str(record.get("station_id", "")))
            or not isinstance(record.get("measurements"), dict)
            or utc(record.get("received_at")) is None):
        raise StationError("contrato_invalido")
    comparable = (previous and previous.get("station_id") == record["station_id"]
                  and previous.get("source_kind") == record["source_kind"]
                  and previous.get("provider") == record.get("provider"))
    previous_fields = previous.get("measurements", {}) if comparable else {}
    output = {}
    for name, (unit, low, high, jump) in FIELDS.items():
        raw = record["measurements"].get(name)
        flags, value, date = [], None, None
        if not isinstance(raw, dict):
            raw = {}
            flags.append("sin_dato")
        else:
            try:
                value = _convert(raw.get("value"), raw.get("unit"), unit)
                if not low <= value <= high:
                    flags.append("fuera_de_rango")
            except StationError:
                flags.append("unidad_desconocida")
            except (ValueError, TypeError, OverflowError):
                flags.append("valor_invalido")
            date = utc(raw.get("observed_at"))
            if date is None:
                flags.append("fecha_invalida")
            elif (date - now).total_seconds() > FUTURE_SECONDS:
                flags.append("fecha_futura")
            elif (now - date).total_seconds() > STALE_SECONDS:
                flags.append("dato_vencido")
        old = previous_fields.get(name, {})
        old_date = utc(old.get("observed_at"))
        old_value = old.get("value")
        if date and old_date and value is not None and old_value is not None:
            dt = (date - old_date).total_seconds()
            if dt < 0:
                flags.append("fuera_de_orden")
            elif dt == 0 and value != old_value:
                flags.append("conflicto_misma_fecha")
            elif dt == 0:
                flags.extend(f for f in old.get("flags", []) if f in
                             ("salto_a_revisar", "conflicto_misma_fecha", "reinicio_acumulado", "fuera_de_orden"))
            elif 0 < dt <= STALE_SECONDS and old.get("usable") and jump is not None and abs(value - old_value) > jump:
                flags.append("salto_a_revisar")
            if dt > 0 and name in ("rain_daily", "rain_piezo_daily") and value < old_value:
                # No se convierte una caída de contador en lluvia negativa.
                # También se marca medianoche: la zona del gateway no se presupone.
                flags.append("reinicio_acumulado")
        output[name] = {"value": value, "unit": unit,
                        "observed_at": date.isoformat() if date else None,
                        "age_seconds": (now - date).total_seconds() if date else None,
                        "raw_value": _scalar(raw.get("value")),
                        "raw_unit": _scalar(raw.get("unit")),
                        "raw_observed_at": _scalar(raw.get("observed_at")),
                        "flags": flags, "usable": not flags}
    usable = sum(x["usable"] for x in output.values())
    present = sum("sin_dato" not in x["flags"] for x in output.values())
    if not present:
        status = "SIN_DATOS"
    elif not usable:
        status = "SIN_DATOS_UTILIZABLES"
    elif any(x["flags"] and "sin_dato" not in x["flags"] for x in output.values()):
        status = "REVISAR"
    else:
        status = "DATOS_RECIBIDOS"
    return {"schema_version": 1, "station_id": record["station_id"],
            "source_kind": record["source_kind"], "provider": str(record.get("provider", ""))[:64],
            "received_at": utc(record["received_at"]).isoformat(),
            "checked_at": now.isoformat(), "risk_eligible": False,
            "representativeness": "punto_local_sin_validar", "status": status,
            "measurements": output}


def advance_reference(previous, quality):
    """Mantiene por sensor la referencia más reciente sin retroceder por cortes."""
    result = deepcopy(quality)
    old = (previous.get("measurements", {}) if previous
           and all(previous.get(k) == quality.get(k) for k in
                   ("station_id", "source_kind", "provider")) else {})
    result["measurements"] = deepcopy(old)
    for name, item in quality["measurements"].items():
        date = utc(item["observed_at"])
        before = old.get(name, {})
        old_date = utc(before.get("observed_at"))
        if date is None or item["value"] is None or any(f in item["flags"] for f in
                ("fecha_futura", "fuera_de_rango", "salto_a_revisar")):
            continue
        if old_date and date < old_date:
            continue
        if old_date == date and "conflicto_misma_fecha" in item["flags"]:
            result["measurements"][name] = {**deepcopy(before), "usable": False,
                "flags": sorted(set(before.get("flags", []) + ["conflicto_misma_fecha"]))}
        else:
            result["measurements"][name] = deepcopy(item)
    return result


class EcowittConnector:
    """Consulta acotada por HTTPS; no registra ni devuelve URLs con API keys."""
    def __init__(self, config, session=None):
        self.config = config
        self.session = session or requests

    def fetch(self, *, now=None):
        config = self.config
        if not all(isinstance(config.get(k), str) and config[k].strip()
                   for k in ("station_id", "application_key", "api_key", "mac")):
            raise StationError("configuracion_incompleta")
        if not re.fullmatch(r"(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}", config["mac"]):
            raise StationError("configuracion_invalida")
        params = {k: config[k] for k in ("application_key", "api_key", "mac")}
        params.update(call_back="outdoor,pressure,wind,rainfall,rainfall_piezo",
                      temp_unitid=1, pressure_unitid=3, wind_speed_unitid=6, rainfall_unitid=12)
        try:
            with self.session.get(API_URL, params=params, timeout=(3, 8),
                                  allow_redirects=False, stream=True) as response:
                if response.status_code == 429:
                    raise StationError("limite_consultas")
                if response.status_code != 200:
                    raise StationError("error_http")
                body = bytearray()
                for chunk in response.iter_content(8192):
                    body.extend(chunk)
                    if len(body) > MAX_BYTES:
                        raise StationError("respuesta_demasiado_grande")
                import json
                payload = json.loads(body)
        except requests.exceptions.RequestException:
            raise StationError("sin_conexion") from None
        except (ValueError, TypeError, UnicodeError) as error:
            if isinstance(error, StationError):
                raise
            raise StationError("respuesta_invalida") from None
        return adapt_ecowitt(payload, config["station_id"], received_at=now)


def configuration(values):
    """Secrets/entorno, desactivado por defecto y sin detección automática."""
    enabled = values.get("ALA_PWS_ENABLED", False)
    return {"enabled": enabled is True or str(enabled).lower() == "true",
            "provider": values.get("ALA_PWS_PROVIDER", "ecowitt_v3"),
            "station_id": values.get("ALA_PWS_STATION_ID", "terraza_01"),
            "application_key": values.get("ECOWITT_APPLICATION_KEY", ""),
            "api_key": values.get("ECOWITT_API_KEY", ""),
            "mac": values.get("ECOWITT_MAC", "")}


def acquire(config, *, now=None, session=None):
    if not config.get("enabled"):
        return {"status": "DESACTIVADA", "record": None}
    if config.get("provider") != "ecowitt_v3":
        return {"status": "proveedor_no_soportado", "record": None}
    try:
        record = EcowittConnector(config, session).fetch(now=now)
        return {"status": "RECIBIDA", "record": record}
    except StationError as error:
        return {"status": str(error), "record": None}
