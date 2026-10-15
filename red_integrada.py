"""Contrato público compartido ALA / Zárate–Campana, versión 1.

Sólo transporta evidencia y su cobertura. No envía mensajes, no emite órdenes,
no suma caudales de estaciones sucesivas ni convierte compuertas en alturas.
Las páginas consumen la misma instantánea publicada por el monitor ALA.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import math
import re
import threading
import time
import unicodedata
from urllib.parse import urlencode, urlparse
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Argentina/Buenos_Aires")
SCHEMA_VERSION = 1
CATALOGO_VERSION = 1
REPOSITORY = "FedericoIeraci-cell/Alerta-Litoral"
STATE_BRANCH = "ala-monitor-state"
SNAPSHOT_PATH = "red_integrada.json"
SNAPSHOT_MAX_AGE_HOURS = 3
API_METEO = "https://api.open-meteo.com/v1/ecmwf"
INA_WFS = "https://alerta.ina.gob.ar/geoserver/ows"
MAX_BYTES = 2_000_000
PRIVATE_KEYS = {"token", "chat_id", "destino", "mensaje", "telegram_bot_token", "telegram_chat_id", "github_token", "authorization", "password", "secret"}
_CACHE_RED = {}
_CACHE_LOCK = threading.Lock()
LIMITES = [
    "La vista común es evidencia de vigilancia; no es un modelo hidráulico calibrado ni una orden de evacuación.",
    "Una señal del Litoral no se transforma automáticamente en una alerta local de Zárate–Campana.",
    "Los aportes de represas y estaciones sucesivas no se suman; no se calcula una velocidad fija de llegada.",
    "Paraná y Uruguay mantienen ramas distintas antes del estuario; marea y viento también condicionan el Delta.",
    "Las escalas observadas 40/41 y las series de pronóstico 29534/3405 no tienen equivalencia documentada: sus umbrales no se cruzan.",
    "Los datos del área vecina se incorporan al informe común; no amplían los candidatos ni los destinatarios del canal Telegram ALA.",
]


def _ahora(referencia=None):
    if referencia is None:
        return datetime.now(timezone.utc)
    if isinstance(referencia, str):
        referencia = datetime.fromisoformat(referencia.replace("Z", "+00:00"))
    if not isinstance(referencia, datetime) or referencia.tzinfo is None or referencia.utcoffset() is None:
        raise ValueError("La referencia requiere fecha, hora y zona horaria.")
    return referencia.astimezone(timezone.utc)


def _fecha(valor):
    # Una fecha de calendario o una hora sin zona no se vuelve una observación actual.
    if isinstance(valor, datetime):
        dt = valor
    elif isinstance(valor, str) and re.match(r"^\d{4}-\d{2}-\d{2}T", valor.strip()):
        try:
            dt = datetime.fromisoformat(valor.strip().replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return None
    else:
        return None
    return dt.astimezone(timezone.utc) if dt.tzinfo is not None and dt.utcoffset() is not None else None


def _iso(valor):
    dt = _fecha(valor)
    return dt.isoformat() if dt is not None else None


def _texto(valor, max_len=800):
    return str(valor or "").strip()[:max_len]


def _normalizar(valor):
    value = unicodedata.normalize("NFKD", _texto(valor))
    return " ".join("".join(c for c in value if not unicodedata.combining(c)).lower().split())


def _numero(valor, minimo=None, maximo=None):
    if valor is None or isinstance(valor, bool):
        return None
    try:
        out = float(valor)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(out) or (minimo is not None and out < minimo) or (maximo is not None and out > maximo):
        return None
    return out


def _json_finito(valor):
    if isinstance(valor, dict):
        return {str(k): _json_finito(v) for k, v in valor.items()}
    if isinstance(valor, (list, tuple)):
        return [_json_finito(v) for v in valor]
    if isinstance(valor, datetime):
        return valor.isoformat()
    if isinstance(valor, float):
        return valor if math.isfinite(valor) else None
    if valor is None or isinstance(valor, (str, int, bool)):
        return valor
    try:
        return _json_finito(valor.item())
    except (AttributeError, ValueError, TypeError):
        return None


def _url(valor):
    text = _texto(valor, 2000)
    try:
        p = urlparse(text)
        return text if p.scheme == "https" and p.hostname and not p.username and not p.password else ""
    except ValueError:
        return ""


def _estado_fecha(valor, referencia, max_horas):
    fecha = _fecha(valor)
    if fecha is None:
        return {"edad_horas": None, "vigente": False, "estado_tiempo": "SIN_FECHA_HORA_VERIFICADA"}
    edad = (referencia - fecha).total_seconds() / 3600
    if edad < -5 / 60:
        estado = "FECHA_FUTURA"
    elif edad > max_horas:
        estado = "VENCIDO"
    else:
        estado = "VIGENTE"
    return {"edad_horas": round(edad, 2), "vigente": estado == "VIGENTE", "estado_tiempo": estado}


def _request_json(session, url, params=None, max_bytes=MAX_BYTES, timeout=(5, 18)):
    if session is None:
        import requests
        session = requests
    r = session.get(url, params=params, timeout=timeout,
                    headers={"User-Agent": "ALA-ZC-RedIntegrada/1.0 (fuentes publicas)"})
    r.raise_for_status()
    origin = urlparse(getattr(r, "url", url) or url)
    if origin.scheme != "https" or origin.hostname != urlparse(url).hostname:
        raise ValueError("La respuesta salió del origen público solicitado.")
    if len(r.content) > max_bytes:
        raise ValueError("La respuesta pública supera el tamaño admitido.")
    return r.json(), getattr(r, "url", url) or url


def _sin_campos_privados(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() in PRIVATE_KEYS:
                raise ValueError("El informe común contiene un campo privado no admitido")
            _sin_campos_privados(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _sin_campos_privados(item)


def _nodo_vacio(nodo, red="Zárate–Campana", error="Sin fuente consultada"):
    return {"red": red, **{k: nodo.get(k) for k in ("localidad", "provincia", "partido", "lat", "lon")},
            "lluvia_prevista_24_mm": None, "lluvia_prevista_72_mm": None,
            "lluvia_prevista_7d_mm": None, "lluvia_prevista_15d_mm": None,
            "lluvia_previa_7d_mm": None, "escurrimiento_previsto_72_mm": None,
            "humedad_0_7_m3_m3": None, "indice_experimental": None,
            "nivel_experimental": "SIN DATOS", "fecha_calculo": None,
            "fecha_consulta": None, "fecha_emision_modelo": None,
            "cobertura_horaria": {}, "estado_datos": "SIN DATOS", "error": error,
            "fuente": API_METEO, "modelo": "ECMWF IFS / Open-Meteo"}


def parsear_meteorologia_zc(nodo, payload, consultado, referencia=None):
    """Variables independientes y ventanas completas; nunca imputa un cero ausente."""
    ref = _ahora(referencia)
    salida = _nodo_vacio(nodo)
    salida["fecha_consulta"] = _iso(consultado)
    if not isinstance(payload, dict) or payload.get("error"):
        salida["error"] = _texto(payload.get("reason") or payload.get("error")) if isinstance(payload, dict) else "Respuesta no válida"
        return salida
    la, lo = _numero(payload.get("latitude")), _numero(payload.get("longitude"))
    if la is None or lo is None:
        salida["error"] = "Coordenadas de la celda sin verificar"
        return salida
    rad = math.pi / 180
    h = math.sin((la - nodo["lat"]) * rad / 2) ** 2 + math.cos(la * rad) * math.cos(nodo["lat"] * rad) * math.sin((lo - nodo["lon"]) * rad / 2) ** 2
    if 6371 * 2 * math.asin(math.sqrt(min(1, h))) > 40:
        salida["error"] = "La celda devuelta está fuera del entorno solicitado"
        return salida
    if payload.get("timezone") not in {"America/Argentina/Buenos_Aires", "America/Buenos_Aires"} or payload.get("utc_offset_seconds") != -10800:
        salida["error"] = "Zona horaria de la grilla no verificada"
        return salida
    hourly = payload.get("hourly") or {}
    units = payload.get("hourly_units") or {}
    times = []
    try:
        for value in hourly.get("time", []):
            date = datetime.fromisoformat(str(value))
            times.append(date.replace(tzinfo=TZ).astimezone(timezone.utc) if date.tzinfo is None else date.astimezone(timezone.utc))
    except (ValueError, TypeError):
        times = []
    if not times or len(times) > 800 or len(set(times)) != len(times) or any(b - a != timedelta(hours=1) for a, b in zip(times, times[1:])):
        salida["error"] = "Grilla horaria ausente, repetida o discontinua"
        return salida
    ref = ref.replace(minute=0, second=0, microsecond=0)
    indexes = {t: i for i, t in enumerate(times)}
    errors = []
    def acumulado(variable, horas, futuro=True):
        values = hourly.get(variable)
        if units.get(variable) != "mm" or not isinstance(values, list) or len(values) != len(times):
            return None, 0
        expected = [ref + timedelta(hours=i if futuro else -i) for i in range(1, horas + 1)]
        valid = [_numero(values[indexes[t]], 0, 1000) if t in indexes else None for t in expected]
        count = sum(v is not None for v in valid)
        return (round(sum(valid), 2) if count == horas else None), count
    for hours, field in [(24, "24"), (72, "72"), (168, "7d")]:
        value, count = acumulado("precipitation", hours)
        salida[f"lluvia_prevista_{field}_mm"] = value
        salida["cobertura_horaria"][f"lluvia_prevista_{field}"] = {"validas": count, "esperadas": hours}
    # forecast_days=15 incluye el día de consulta: no publica necesariamente
    # 360 horas desde este instante. Se suma sólo la cobertura efectiva recibida.
    effective_hours = max(0, min(360, int((times[-1] - ref).total_seconds() // 3600)))
    if effective_hours:
        salida["lluvia_prevista_15d_mm"], count = acumulado("precipitation", effective_hours)
        salida["cobertura_horaria"]["lluvia_prevista_15d"] = {"validas": count, "esperadas": effective_hours,
            "hasta": (ref + timedelta(hours=effective_hours)).isoformat(), "maximo_solicitado_horas": 360}
    salida["lluvia_previa_7d_mm"], count = acumulado("precipitation", 168, False)
    salida["cobertura_horaria"]["lluvia_previa_7d"] = {"validas": count, "esperadas": 168}
    salida["escurrimiento_previsto_72_mm"], count = acumulado("runoff", 72)
    salida["cobertura_horaria"]["escurrimiento_previsto_72"] = {"validas": count, "esperadas": 72}
    soil = hourly.get("soil_moisture_0_to_7cm")
    if units.get("soil_moisture_0_to_7cm") in {"m³/m³", "m3/m3"} and isinstance(soil, list) and len(soil) == len(times) and ref in indexes:
        salida["humedad_0_7_m3_m3"] = _numero(soil[indexes[ref]], 0, 1)
    salida["fecha_calculo"] = _iso(consultado)
    salida["cobertura_desde"] = times[0].isoformat()
    salida["cobertura_hasta"] = times[-1].isoformat()
    if salida["lluvia_prevista_72_mm"] is None:
        errors.append("Lluvia futura de 72 horas incompleta")
    if salida["escurrimiento_previsto_72_mm"] is None:
        errors.append("Escurrimiento no disponible para todas las 72 horas")
    if salida["humedad_0_7_m3_m3"] is None:
        errors.append("Humedad de suelo sin dato")
    salida["estado_datos"] = "PARCIAL" if errors else "VARIABLES DISPONIBLES"
    salida["error"] = "; ".join(errors)
    # No es un índice ALA calculado ni una evaluación MP-ZC: son variables para la vista común.
    return salida


def _rio_vacio(nombre, serie, prono):
    return {"red": "Zárate–Campana", "estacion": nombre, "serie_observada": serie,
            "serie_pronostico": prono, "altura_m": None, "alerta_m": None, "evacuacion_m": None,
            "cero_ign_m": None, "fecha_observada": None, "fecha_consulta": None,
            "fecha_emision": None, "pronostico": [], "fuente": INA_WFS,
            "fuente_pronostico": "", "escala_equivalente": False,
            "error": "Sin observación consultada", "error_pronostico": "Sin pronóstico consultado"}


def parsear_alturas_zc(payload, fuente, consultado, referencia=None):
    ref = _ahora(referencia)
    rios = [_rio_vacio("Zárate", 40, 29534), _rio_vacio("Campana", 41, 3405)]
    if not isinstance(payload, dict) or payload.get("type") != "FeatureCollection" or not isinstance(payload.get("features"), list):
        return rios
    for rio in rios:
        matching = [f.get("properties") for f in payload["features"] if isinstance(f, dict) and isinstance(f.get("properties"), dict) and f["properties"].get("series_id") == rio["serie_observada"]]
        if len(matching) != 1:
            rio["error"] = "Serie ausente o repetida en la fuente INA"
            continue
        p = matching[0]
        if _normalizar(p.get("nombre")) != _normalizar(rio["estacion"]) or p.get("tabla") != "alturas_prefe" or p.get("var_id") != 2:
            rio["error"] = "Identidad de estación, red o variable no verificadas"
            continue
        obs = []
        try:
            history = json.loads(p.get("timeseries") or "[]")
        except (ValueError, TypeError):
            history = []
        candidates = history if isinstance(history, list) else []
        candidates = candidates + [[p.get("fecha"), p.get("valor")]]
        for item in candidates:
            if not isinstance(item, list) or len(item) != 2:
                continue
            dt, value = _fecha(item[0]), _numero(item[1], -10, 100)
            if dt is not None and dt <= ref + timedelta(minutes=5) and value is not None:
                obs.append((dt, value))
        if obs:
            dt, value = max(obs, key=lambda item: item[0])
            rio.update(altura_m=value, fecha_observada=dt.isoformat(), fecha_consulta=_iso(consultado),
                       fuente=fuente, error="", zona_horaria_verificada=True)
        alerta, evacuacion, cero = _numero(p.get("nivel_de_alerta"), 0), _numero(p.get("nivel_de_evacuacion"), 0), _numero(p.get("cero_ign"))
        if alerta is not None and evacuacion is not None and cero is not None and evacuacion > alerta:
            rio.update(alerta_m=alerta, evacuacion_m=evacuacion, cero_ign_m=cero,
                       umbrales_solo_serie_observada=True, fecha_consulta_umbrales=_iso(consultado))
    return rios


def parsear_pronostico_zc(payload, serie, consultado, referencia=None):
    ref = _ahora(referencia)
    if not isinstance(payload, dict) or payload.get("cal_id") != 489 or not isinstance(payload.get("series"), list):
        raise ValueError("Producto INA no verificado")
    emitted = _fecha(payload.get("forecast_date"))
    if emitted is None or emitted > ref + timedelta(minutes=5):
        raise ValueError("Emisión sin hora/zona o futura")
    bands = {}
    for part in payload["series"]:
        if not isinstance(part, dict) or part.get("series_id") != serie or part.get("qualifier") not in {"main", "error_band_01", "error_band_99"}:
            continue
        if part.get("cor_id") != payload.get("id"):
            raise ValueError("Identidad de corrida incompatible")
        seen = set()
        for point in part.get("pronosticos", []):
            if not isinstance(point, dict):
                continue
            dt, value = _fecha(point.get("timestart")), _numero(point.get("valor"), -10, 100)
            if dt is None or value is None or dt < emitted or not ref <= dt <= ref + timedelta(hours=96):
                continue
            if dt in seen:
                raise ValueError("Punto pronosticado repetido")
            seen.add(dt)
            bands.setdefault(dt, {})[part["qualifier"]] = value
    out = []
    for dt, band in sorted(bands.items()):
        mid, low, high = band.get("main"), band.get("error_band_01"), band.get("error_band_99")
        if mid is None:
            continue
        if (low is not None and low > mid) or (high is not None and high < mid) or (low is not None and high is not None and low > high):
            low = high = None
        out.append({"fecha": dt.isoformat(), "central_m": mid, "inferior_m": low, "superior_m": high})
    if not out:
        raise ValueError("Sin puntos futuros de la serie configurada")
    return {"fecha_emision": emitted.isoformat(), "fecha_consulta_pronostico": _iso(consultado),
            "corrida_id": payload.get("id"), "pronostico": out, "error_pronostico": ""}


def consultar_zc_red(referencia=None, session=None):
    """Consulta diez coordenadas y fuentes INA locales; toda ausencia queda explícita."""
    ref = _ahora(referencia)
    consulta = ref.isoformat()
    nodos = [_nodo_vacio(n) for n in NODOS_ZC]
    fuentes = []
    def meteo_lote(lote):
        params = {"latitude": ",".join(str(n["lat"]) for n in lote), "longitude": ",".join(str(n["lon"]) for n in lote),
                  "hourly": "precipitation,runoff,soil_moisture_0_to_7cm", "past_days": 8, "forecast_days": 15,
                  "timezone": "America/Argentina/Buenos_Aires", "precipitation_unit": "mm", "cell_selection": "land"}
        try:
            payload, url = _request_json(session, API_METEO, params, timeout=(5, 45))
            records = payload if isinstance(payload, list) else [payload]
            if len(records) != len(lote):
                raise ValueError("Cantidad de coordenadas incompatible")
            return [parsear_meteorologia_zc(n, p, consulta, ref) for n, p in zip(lote, records)]
        except Exception as exc:
            return [_nodo_vacio(n, error="Consulta meteorológica no verificada: " + type(exc).__name__) for n in lote]
    with ThreadPoolExecutor(max_workers=3) as pool:
        nodos = [n for lote in pool.map(meteo_lote, [NODOS_ZC[:8], NODOS_ZC[8:]]) for n in lote]
    fuentes.append({"red": "Zárate–Campana", "organismo": "Open-Meteo / ECMWF", "url": API_METEO,
                    "consultado": consulta, "fecha_documento": None, "estado_publicado": "CONSULTA",
                    "error": "" if any(n["lluvia_prevista_72_mm"] is not None for n in nodos) else "Sin lluvia de 72 horas verificable"})
    params = {"service": "WFS", "version": "1.0.0", "request": "GetFeature", "typeName": "public2:ultimas_alturas_con_timeseries",
              "outputFormat": "application/json", "CQL_FILTER": "series_id IN (40,41)",
              "propertyName": "series_id,nombre,var_id,tabla,nivel_de_alerta,nivel_de_evacuacion,cero_ign,fecha,valor,timeseries"}
    try:
        payload, url = _request_json(session, INA_WFS, params)
        rios = parsear_alturas_zc(payload, url, consulta, ref)
    except Exception as exc:
        rios = [_rio_vacio("Zárate", 40, 29534), _rio_vacio("Campana", 41, 3405)]
        for rio in rios:
            rio["error"] = "Altura INA no verificada: " + type(exc).__name__
    def consultar_pronostico(rio):
        rio = dict(rio)
        series = rio["serie_pronostico"]
        url = "https://alerta.ina.gob.ar/a5/sim/calibrados/489/corridas/last?" + urlencode({"group_by_qualifier": "true", "includeProno": "true", "series_id": series})
        rio["fuente_pronostico"] = url
        try:
            payload, _ = _request_json(session, url)
            rio.update(parsear_pronostico_zc(payload, series, consulta, ref))
        except Exception as exc:
            rio["error_pronostico"] = "Pronóstico INA no verificado: " + type(exc).__name__
        return rio
    with ThreadPoolExecutor(max_workers=2) as pool:
        rios = list(pool.map(consultar_pronostico, rios))
    for rio in rios:
        url = rio["fuente_pronostico"]
        fuentes.extend([{"red": "Zárate–Campana", "organismo": "INA · " + rio["estacion"], "url": rio["fuente"],
                         "consultado": consulta, "fecha_documento": rio["fecha_observada"], "error": rio["error"]},
                        {"red": "Zárate–Campana", "organismo": "INA · pronóstico " + rio["estacion"], "url": url,
                         "consultado": consulta, "fecha_documento": rio["fecha_emision"], "error": rio["error_pronostico"]}])
    return {"nodos": nodos, "rios": rios, "fuentes": fuentes, "consultado": consulta}


def _frame_records(frame):
    if hasattr(frame, "to_dict"):
        return frame.to_dict("records")
    return frame if isinstance(frame, list) else []


def _normalizar_nodo_ala(nodo, fila):
    salida = _nodo_vacio(nodo, "Litoral", "Sin evaluación meteorológica de este punto")
    if not isinstance(fila, dict):
        return salida
    # La hora heredada es cálculo de la app; no se presenta como emisión ECMWF.
    fecha = _iso(fila.get("consulta_meteorologica"))
    precision = "hora de consulta meteorológica con zona publicada"
    if fecha is None and fila.get("actualizado"):
        try:
            fecha = datetime.strptime(str(fila["actualizado"]), "%d/%m/%Y %H:%M").replace(tzinfo=TZ).isoformat()
            precision = "hora de cálculo meteorológico de ALA; zona ART de la aplicación"
        except (ValueError, TypeError):
            pass
    salida.update(fecha_calculo=fecha, fecha_consulta=_iso(fila.get("consulta_meteorologica")),
                  fecha_evaluacion_integrada=_iso(fila.get("fecha_evaluacion_integrada")),
                  precision_calculo=precision, estado_datos=_texto(fila.get("estado_datos")), error=_texto(fila.get("error_datos")))
    completa = fila.get("cobertura_meteorologica") is True or str(fila.get("estado_datos")) == "OK"
    if completa:
        fields = {"lluvia_prevista_24_mm": "lluvia_futura_24", "lluvia_prevista_72_mm": "lluvia_futura_72",
                  "lluvia_prevista_7d_mm": "lluvia_futura_7d", "lluvia_previa_7d_mm": "lluvia_7d",
                  "escurrimiento_previsto_72_mm": "runoff_futuro_72", "humedad_0_7_m3_m3": "humedad_0_7",
                  "indice_experimental": "indice"}
        for target, source in fields.items():
            salida[target] = _numero(fila.get(source), 0, 1 if target == "humedad_0_7_m3_m3" else 100 if target == "indice_experimental" else 100000)
        # Meteorological/integrated levels retain their original meaning and identity.
        salida["nivel_experimental"] = _texto(fila.get("nivel")) or "SIN DATOS"
    else:
        salida["error"] = salida["error"] or "Variables incompletas: el informe común no completa valores ausentes con cero"
    salida["nivel_tipo"] = "nivel_experimental es legado del estado integrado ALA; usar nivel_meteorologico para el índice"
    salida["estado_integrado_ala"] = _texto(fila.get("nivel")) or "SIN DATOS"
    salida["nivel_meteorologico"] = _texto(fila.get("nivel_meteorologico")) or "SIN DATOS"
    salida["indice_meteorologico"] = salida.get("indice_experimental")
    salida["motivo_principal"] = _texto(fila.get("motivo_principal"), 3000)
    salida["desencadenantes"] = _json_finito(fila.get("desencadenantes") or [])
    salida["evidencias_alerta"] = _json_finito(fila.get("evidencias_alerta") or [])
    salida["explicacion_version"] = fila.get("explicacion_version")
    salida["fuentes_integracion"] = _texto(fila.get("hidrologia_fuentes"), 2000)
    return salida


def _normalizar_rio_ala(key, item):
    curves = []
    for point in item.get("pronostico_numerico", []):
        if not isinstance(point, dict):
            continue
        curves.append({"fecha": _iso(point.get("fecha")), "central_m": _numero(point.get("valor")),
                       "inferior_m": _numero(point.get("inferior")), "superior_m": _numero(point.get("superior")),
                       "fecha_emision": _iso(point.get("emision"))})
    emisiones = [p["fecha_emision"] for p in curves if p.get("fecha_emision")]
    fuente = _url(item.get("fuente"))
    observada = _iso(item.get("fecha"))
    # El lector anterior del reporte diario puede normalizar sólo una fecha a
    # medianoche ART. Esa normalización no acredita hora observada publicada.
    precision = "fecha_hora_publicada"
    if "/diario/reporte_diario" in fuente:
        precision = "hora_no_verificada_en_reporte_diario"
    return {"red": "Litoral", "estacion": _texto(item.get("nombre") or key), "serie_observada": item.get("series_id"),
            "serie_pronostico": item.get("series_id"), "altura_m": _numero(item.get("altura_m")),
            "alerta_m": _numero(item.get("alerta_m")), "evacuacion_m": _numero(item.get("evacuacion_m")),
            "fecha_observada": observada if precision == "fecha_hora_publicada" else None,
            "fecha_observada_original": _texto(item.get("fecha"))[:10] if precision != "fecha_hora_publicada" else _texto(item.get("fecha")),
            "precision_observada": precision, "fecha_documento_original": _texto(item.get("fecha_documento")),
            "fecha_emision": max(emisiones) if emisiones else None, "pronostico": curves,
            "fuente": fuente, "fuente_pronostico": fuente,
            "representatividad": _texto(item.get("representatividad")), "escala_equivalente": True,
            "error": "", "error_pronostico": "" if curves else "Sin puntos numéricos publicados"}


def _normalizar_represa(key, item):
    doc_date = _iso(item.get("fecha"))
    original = _texto(item.get("fecha"))
    if "/diario/reporte_diario" in _url(item.get("fuente")):
        doc_date, original = None, original[:10]
    campos = {"red": "Cuenca compartida", "nombre": _texto(item.get("nombre") or key),
              "fecha": doc_date, "fecha_original": original,
              "hasta": _iso(item.get("hasta")), "fuente": _url(item.get("fuente")),
              "tipo": _texto(item.get("tipo")), "texto": _texto(item.get("texto"), 5000)}
    for k in ("afluente_m3s", "erogado_m3s", "afluente_previsto_min_m3s", "afluente_previsto_max_m3s", "cota_embalse_m"):
        campos[k] = _numero(item.get(k), 0)
    return campos


def _normalizar_fuente(item):
    doc_date = _iso(item.get("fecha_documento"))
    original = _texto(item.get("fecha_documento"))
    if "/diario/reporte_diario" in _url(item.get("url")):
        doc_date, original = None, original[:10]
    return {"red": _texto(item.get("red") or "Cuenca compartida"),
            "organismo": _texto(item.get("organismo") or item.get("nombre") or item.get("id")),
            "url": _url(item.get("url")), "fecha_documento": doc_date,
            "fecha_documento_original": original,
            "consultado": _iso(item.get("consultado")), "valido_hasta": _iso(item.get("valido_hasta")),
            "estado_publicado": _texto(item.get("estado_publicado") or item.get("estado")),
            "error": _texto(item.get("error"))}


def construir_red_integrada(frame_ala, panorama_ala, zc=None, monitor=None, referencia=None, version_ala=""):
    """Crea el documento público sin tocar Telegram ni las variables de ninguna app."""
    ref = _ahora(referencia)
    panorama_ala = panorama_ala if isinstance(panorama_ala, dict) else {}
    zc = zc if isinstance(zc, dict) else {"nodos": [], "rios": [], "fuentes": []}
    records = _frame_records(frame_ala)
    index = {(_normalizar(row.get("localidad")), _normalizar(row.get("provincia"))): row for row in records if isinstance(row, dict)}
    ala = [_normalizar_nodo_ala(nodo, index.get((_normalizar(nodo["localidad"]), _normalizar(nodo["provincia"])))) for nodo in NODOS_ALA]
    zindex = {_normalizar(n.get("localidad")): n for n in zc.get("nodos", []) if isinstance(n, dict)}
    zrows = [zindex.get(_normalizar(n["localidad"]), _nodo_vacio(n)) for n in NODOS_ZC]
    monitor = monitor if isinstance(monitor, dict) else {}
    safe_monitor = {k: _json_finito(monitor.get(k)) for k in ("checked_at", "health", "scope", "frequency_minutes", "dry_run", "nodes_total", "nodes_valid", "nodes_red")}
    payload = {"schema_version": SCHEMA_VERSION, "catalogo_version": CATALOGO_VERSION,
               "generated_at": ref.isoformat(), "producer": {"app": "Alerta Litoral Agro · monitor", "version": _texto(version_ala)},
               "monitor": safe_monitor, "nodos": ala + zrows,
               "rios": [_normalizar_rio_ala(k, v) for k, v in panorama_ala.get("estaciones", {}).items() if isinstance(v, dict)] + list(zc.get("rios", [])),
               "represas": [_normalizar_represa(k, v) for k, v in panorama_ala.get("represas", {}).items() if isinstance(v, dict)],
               "fuentes": [_normalizar_fuente(f) for f in list(panorama_ala.get("fuentes", [])) + list(zc.get("fuentes", [])) if isinstance(f, dict)],
               "limitaciones": list(LIMITES), "permite_orden_evacuacion": False, "envia_telegram": False}
    return validar_red_integrada(_json_finito(payload), ref)


def validar_red_integrada(payload, referencia=None):
    """Versionado estricto; fechas y caducidad se recalculan al leer el informe."""
    ref = _ahora(referencia)
    _sin_campos_privados(payload)
    if not isinstance(payload, dict) or type(payload.get("schema_version")) is not int or payload["schema_version"] != SCHEMA_VERSION:
        raise ValueError("El informe no usa el contrato compartido versión 1")
    if payload.get("catalogo_version") != CATALOGO_VERSION:
        raise ValueError("Catálogo de puntos no compatible")
    generated = _fecha(payload.get("generated_at"))
    if generated is None or generated > ref + timedelta(minutes=5):
        raise ValueError("La generación del informe no tiene hora/zona válida o está en el futuro")
    for k in ("nodos", "rios", "represas", "fuentes"):
        if not isinstance(payload.get(k), list) or len(payload[k]) > 500:
            raise ValueError("Colección de informe no válida: " + k)
    expected = {(red, n["localidad"]): n for red, nodes in (("Litoral", NODOS_ALA), ("Zárate–Campana", NODOS_ZC)) for n in nodes}
    found = set()
    out = {k: deepcopy(payload.get(k)) for k in ("schema_version", "catalogo_version", "generated_at", "producer", "monitor", "nodos", "rios", "represas", "fuentes")}
    out["limitaciones"] = list(LIMITES)
    out["permite_orden_evacuacion"] = False
    out["envia_telegram"] = False
    status = _estado_fecha(generated, ref, SNAPSHOT_MAX_AGE_HOURS)
    out.update(snapshot_vigente=status["vigente"], snapshot_edad_horas=status["edad_horas"], snapshot_estado=status["estado_tiempo"])
    for node in out["nodos"]:
        if not isinstance(node, dict):
            raise ValueError("Punto no válido")
        identity = (node.get("red"), node.get("localidad"))
        if identity not in expected or identity in found:
            raise ValueError("Punto desconocido o repetido en el catálogo compartido")
        found.add(identity)
        geo = expected[identity]
        if _numero(node.get("lat")) != geo["lat"] or _numero(node.get("lon")) != geo["lon"]:
            raise ValueError("Coordenada incompatible con el catálogo compartido")
        for field in ("lluvia_prevista_24_mm", "lluvia_prevista_72_mm", "lluvia_prevista_7d_mm", "lluvia_prevista_15d_mm", "lluvia_previa_7d_mm", "escurrimiento_previsto_72_mm"):
            node[field] = _numero(node.get(field), 0, 100000)
        node["humedad_0_7_m3_m3"] = _numero(node.get("humedad_0_7_m3_m3"), 0, 1)
        node["indice_experimental"] = _numero(node.get("indice_experimental"), 0, 100)
        temp = _estado_fecha(node.get("fecha_calculo"), ref, 3)
        node.update(temp)
        node["vigente"] = temp["vigente"] and out["snapshot_vigente"]
        node["nivel_vigente"] = node.get("nivel_experimental", "SIN DATOS") if node["vigente"] else "SIN DATOS"
        integrada = _estado_fecha(node.get("fecha_evaluacion_integrada"), ref, 3)
        integrada_vigente = integrada["vigente"] and out["snapshot_vigente"]
        node["nivel_integrado_vigente"] = node.get("estado_integrado_ala", "SIN DATOS") if integrada_vigente else "SIN DATOS"
        # La hora del snapshot no renueva las evidencias. Los informes antiguos
        # conservan su color histórico, sin inventarles un desencadenante.
        for campo in ("desencadenantes", "evidencias_alerta"):
            evidencias = node.get(campo) or []
            if not isinstance(evidencias, list) or len(evidencias) > 500:
                raise ValueError("Evidencias del nodo no válidas")
            for e in evidencias:
                if not isinstance(e, dict):
                    raise ValueError("Evidencia no válida")
                limite = _fecha(e.get("valida_hasta") or e.get("hasta"))
                e["vigente"] = bool(e.get("vigente") is True and integrada_vigente
                    and (e.get("tipo") != "meteorologia" or node["vigente"]) and (limite is None or ref <= limite))
            node[campo] = evidencias
        node["explicacion_estado"] = ("Evaluación histórica; revisar vigencia de cada evidencia" if not integrada_vigente
            else "Trazabilidad disponible" if node.get("explicacion_version") == 1 else "Informe anterior sin desencadenante estructurado")
    if found != set(expected):
        raise ValueError("El informe no conserva los 58 puntos del catálogo")
    for rio in out["rios"]:
        if not isinstance(rio, dict):
            raise ValueError("Estación no válida")
        if rio.get("red") == "Litoral" and "/diario/reporte_diario" in _url(rio.get("fuente")):
            rio["fecha_observada_original"] = _texto(rio.get("fecha_observada_original") or rio.get("fecha_observada"))[:10]
            rio["fecha_observada"] = None
            rio["precision_observada"] = "hora_no_verificada_en_reporte_diario"
        for field in ("altura_m", "alerta_m", "evacuacion_m", "cero_ign_m"):
            rio[field] = _numero(rio.get(field), -10, 100)
        temporal = _estado_fecha(rio.get("fecha_observada"), ref, 6 if rio.get("red") == "Zárate–Campana" else 48)
        rio.update(edad_observada_horas=temporal["edad_horas"], observacion_estado=temporal["estado_tiempo"],
                   observacion_vigente=temporal["vigente"] and out["snapshot_vigente"] and rio["altura_m"] is not None)
        if rio.get("red") == "Zárate–Campana":
            identity = (rio.get("estacion"), rio.get("serie_observada"), rio.get("serie_pronostico"))
            if identity not in {("Zárate", 40, 29534), ("Campana", 41, 3405)}:
                raise ValueError("Identidad de estación local incompatible")
            rio["escala_equivalente"] = False
        emitted = _fecha(rio.get("fecha_emision"))
        curves = []
        seen = set()
        for point in rio.get("pronostico", []):
            if not isinstance(point, dict):
                continue
            dt = _fecha(point.get("fecha"))
            mid = _numero(point.get("central_m"), -10, 100)
            low, high = _numero(point.get("inferior_m"), -10, 100), _numero(point.get("superior_m"), -10, 100)
            if dt is None or mid is None or emitted is None or dt < emitted or dt < ref or dt in seen:
                continue
            if rio.get("red") == "Zárate–Campana" and dt > emitted + timedelta(hours=96):
                continue
            seen.add(dt)
            if (low is not None and low > mid) or (high is not None and high < mid):
                low = high = None
            curves.append({"fecha": dt.isoformat(), "central_m": mid, "inferior_m": low, "superior_m": high})
        rio["pronostico"] = sorted(curves, key=lambda p: p["fecha"])
        forecast = _estado_fecha(rio.get("fecha_emision"), ref, 6 if rio.get("red") == "Zárate–Campana" else 48)
        rio.update(pronostico_edad_horas=forecast["edad_horas"], pronostico_estado=forecast["estado_tiempo"],
                   pronostico_vigente=forecast["vigente"] and out["snapshot_vigente"] and bool(rio.get("pronostico")))
        # Never consume a remote rank, threshold crossing or evacuation command.
        for forbidden in ("prioridad", "orden_evacuacion", "rango_evacuacion"):
            rio.pop(forbidden, None)
    for item in out["represas"]:
        if isinstance(item, dict):
            if "/diario/reporte_diario" in _url(item.get("fuente")):
                item["fecha_original"] = _texto(item.get("fecha_original") or item.get("fecha"))[:10]
                item["fecha"] = None
            temporal = _estado_fecha(item.get("fecha"), ref, 48)
            item.update(temporal)
            item["vigente"] = temporal["vigente"] and out["snapshot_vigente"]
    for item in out["fuentes"]:
        if isinstance(item, dict):
            if "/diario/reporte_diario" in _url(item.get("url")):
                item["fecha_documento_original"] = _texto(item.get("fecha_documento_original") or item.get("fecha_documento"))[:10]
                item["fecha_documento"] = None
            temporal = _estado_fecha(item.get("fecha_documento"), ref, 48)
            item.update(temporal)
            item["vigente"] = temporal["vigente"] and out["snapshot_vigente"] and not item.get("error")
    return _json_finito(out)


def limpiar_cache_red_integrada():
    with _CACHE_LOCK:
        _CACHE_RED.clear()


def consultar_red_integrada(repository=REPOSITORY, session=None, referencia=None):
    if not isinstance(repository, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        return {"error": "Repositorio del informe no válido", "snapshot_vigente": False}
    url = f"https://raw.githubusercontent.com/{repository}/{STATE_BRANCH}/{SNAPSHOT_PATH}"
    try:
        payload = None
        if session is None:
            with _CACHE_LOCK:
                cached = _CACHE_RED.get(repository)
                if cached and time.monotonic() - cached[0] <= 300:
                    payload = deepcopy(cached[1])
        if payload is None:
            payload, _ = _request_json(session, url)
            # Cache raw evidence, never its recency; revalidate every call below.
            validar_red_integrada(payload, referencia)
            if session is None:
                with _CACHE_LOCK:
                    _CACHE_RED[repository] = (time.monotonic(), deepcopy(payload))
        out = validar_red_integrada(payload, referencia)
        out.update(error="", snapshot_url=url, actions_url=f"https://github.com/{repository}/actions")
        return out
    except Exception as exc:
        return {"error": "El informe común no está disponible o no es compatible: " + type(exc).__name__,
                "snapshot_vigente": False, "snapshot_url": url, "actions_url": f"https://github.com/{repository}/actions"}


def _mostrar_fecha(valor):
    date = _fecha(valor)
    return date.astimezone(TZ).strftime("%d/%m/%Y %H:%M ART") if date is not None else _texto(valor) or "Sin fecha/hora verificadas"


def mostrar_red_integrada(st, repository=REPOSITORY, app_actual="", datos=None):
    """Vista idéntica en ambos Streamlit, con estados del mismo informe horario."""
    st.subheader("🌐 Red integrada · Litoral y Zárate–Campana")
    st.write("Las dos páginas comparten este informe de meteorología, ríos y represas. Permite revisar la cuenca aguas arriba junto con la situación del Delta.")
    left, right = st.columns(2)
    left.link_button("Abrir Alerta Litoral Agro", "https://alerta-litoral-agro.streamlit.app/", key="red_integrada_ala")
    right.link_button("Abrir Alerta Zárate–Campana", "https://alerta-zarate-campana.streamlit.app/", key="red_integrada_zc")
    if datos is None and st.button("Actualizar informe compartido", key="red_integrada_actualizar"):
        limpiar_cache_red_integrada()
    datos = datos if isinstance(datos, dict) else consultar_red_integrada(repository)
    if datos.get("error"):
        st.warning(datos["error"] + ". No se confirma vigilancia conjunta hasta recuperar un informe válido.")
        st.link_button("Ver controles del informe compartido", datos.get("actions_url", f"https://github.com/{repository}/actions"), key="red_integrada_actions_error")
        return datos
    # Also revalidate cached values: opening a page cannot refresh a source timestamp.
    try:
        datos = {**validar_red_integrada(datos), "snapshot_url": datos.get("snapshot_url", "")}
    except ValueError as exc:
        st.warning("Informe común rechazado: " + str(exc))
        return {"error": str(exc), "snapshot_vigente": False}
    st.caption("Informe generado: " + _mostrar_fecha(datos["generated_at"]) + " · actualización prevista: cada hora · catálogo 1")
    monitor = datos.get("monitor") if isinstance(datos.get("monitor"), dict) else {}
    checked = _estado_fecha(monitor.get("checked_at"), _ahora(), 3)
    st.caption("Último control informado: " + _mostrar_fecha(monitor.get("checked_at")) + " · estado: " + _texto(monitor.get("health") or "sin informar"))
    if monitor.get("dry_run") is True:
        st.info("Este informe procede de una prueba de datos. El ensayo no acredita por sí solo que la actualización programada de ambas regiones esté activa.")
    elif not checked["vigente"]:
        st.warning("El informe no confirma un control automático reciente. Revisá la programación y la última ejecución del monitor.")
    st.link_button("Ver controles del informe compartido", f"https://github.com/{repository}/actions", key="red_integrada_actions")
    if not datos["snapshot_vigente"]:
        st.warning("El informe compartido tiene más de 3 horas. Se conserva como referencia fechada; la vigilancia conjunta actual no está confirmada.")
    a, b, c = st.columns(3)
    a.metric("Puntos del Litoral", sum(n["red"] == "Litoral" for n in datos["nodos"]))
    b.metric("Puntos Zárate–Campana", sum(n["red"] == "Zárate–Campana" for n in datos["nodos"]))
    c.metric("Lluvia 72 h con fecha vigente", sum(n.get("vigente") and n.get("lluvia_prevista_72_mm") is not None for n in datos["nodos"]))
    seleccion = st.selectbox("Área de la red integrada", ["Ambas regiones", "Litoral", "Zárate–Campana"], key="red_integrada_region")
    nodes = [n for n in datos["nodos"] if seleccion == "Ambas regiones" or n["red"] == seleccion]
    st.markdown("**Meteorología compartida**")
    rows = [{"Región": n["red"], "Localidad": n["localidad"], "Lluvia futura 24 h (mm)": n.get("lluvia_prevista_24_mm"),
             "Lluvia futura 72 h (mm)": n.get("lluvia_prevista_72_mm"), "Lluvia futura 7 días (mm)": n.get("lluvia_prevista_7d_mm"),
             "Lluvia disponible hasta 15 días (mm)": n.get("lluvia_prevista_15d_mm"),
             "Fin de cobertura extendida": _mostrar_fecha((n.get("cobertura_horaria") or {}).get("lluvia_prevista_15d", {}).get("hasta")),
             "Lluvia previa 7 días (mm)": n.get("lluvia_previa_7d_mm"),
             "Suelo superficial (m³/m³)": n.get("humedad_0_7_m3_m3"), "Escurrimiento futuro 72 h (mm)": n.get("escurrimiento_previsto_72_mm"),
             "Índice meteorológico /100": n.get("indice_experimental"),
             "Nivel meteorológico": n.get("nivel_meteorologico", "Sin discriminar en informe anterior"),
             "Riesgo integrado ALA al evaluar": n.get("estado_integrado_ala", "Variables compartidas · sin índice ALA"),
             "Desencadenante": n.get("motivo_principal") or "No documentado en este informe",
             "Trazabilidad": n.get("explicacion_estado"),
             "Estado temporal": n.get("estado_tiempo"), "Fecha de cálculo": _mostrar_fecha(n.get("fecha_calculo")), "Cobertura": n.get("estado_datos"), "Observación": n.get("error", "")} for n in nodes]
    st.dataframe(rows, hide_index=True, use_container_width=True)
    st.caption("Las ventanas comienzan en la hora de cálculo. El acumulado extendido suma sólo las horas hasta el fin de cobertura publicado, como máximo 15 días: puede terminar antes y no se prolonga. Las ventanas de 24/72 horas y 7 días necesitan todos sus datos. Suelo y escurrimiento son modelados. Un valor ausente permanece vacío.")
    st.map([{"lat": n["lat"], "lon": n["lon"]} for n in nodes])
    with st.expander("Evidencias del color integrado · estación, previsión y fuente"):
        st.dataframe([{"Localidad": n["localidad"], "Tipo": e.get("tipo"), "Estación": e.get("estacion"),
            "Altura observada (m)": e.get("altura_m"), "Umbral alerta (m)": e.get("alerta_m"),
            "Umbral evacuación (m)": e.get("evacuacion_m"), "Central prevista (m)": e.get("central_m"),
            "Desde": e.get("desde"), "Emisión": e.get("fecha_emision"), "Hora/fecha publicada": e.get("precision_desde"),
            "Límite de uso ALA": e.get("valida_hasta"), "Vigente al leer": e.get("vigente"),
            "Fuente": e.get("fuente"), "Fuente umbrales": e.get("fuente_umbrales")}
            for n in nodes for e in n.get("desencadenantes", [])], hide_index=True, use_container_width=True)
    st.markdown("**Ríos y pronósticos oficiales**")
    rivers = datos["rios"]
    st.dataframe([{"Región": r.get("red"), "Estación": r.get("estacion"), "Serie observada": r.get("serie_observada"),
                   "Altura observada (m)": r.get("altura_m"), "Fecha observada": _mostrar_fecha(r.get("fecha_observada") or r.get("fecha_observada_original")),
                   "Estado observado": r.get("observacion_estado"), "Umbral alerta observado (m)": r.get("alerta_m"),
                   "Umbral evacuación observado (m)": r.get("evacuacion_m"), "Serie pronóstico": r.get("serie_pronostico"),
                   "Emisión pronóstico": _mostrar_fecha(r.get("fecha_emision")), "Estado pronóstico": r.get("pronostico_estado"),
                   "Último punto publicado": _mostrar_fecha(max((p.get("fecha") for p in r.get("pronostico", []) if p.get("fecha")), default=None)),
                   "Fuente observada": r.get("fuente"), "Fuente pronóstico": r.get("fuente_pronostico"),
                   "Observaciones": "; ".join(x for x in (r.get("error"), r.get("error_pronostico")) if x)} for r in rivers], hide_index=True, use_container_width=True)
    with st.expander("Ver puntos numéricos de los pronósticos oficiales"):
        st.dataframe([{"Estación": r["estacion"], "Serie": r.get("serie_pronostico"), "Hora publicada": _mostrar_fecha(p.get("fecha")),
                       "Central (m)": p.get("central_m"), "Inferior (m)": p.get("inferior_m"), "Superior (m)": p.get("superior_m")} for r in rivers for p in r.get("pronostico", [])], hide_index=True, use_container_width=True)
    st.caption("Zárate y Campana: lecturas y corridas de hasta 6 horas. Las previsiones locales conservan su propia escala y llegan como máximo a 96 horas; no se comparan con umbrales de otra serie ni se extrapolan a semanas.")
    st.markdown("**Represas y cobertura de las fuentes**")
    st.dataframe(datos["represas"], hide_index=True, use_container_width=True)
    st.dataframe(datos["fuentes"], hide_index=True, use_container_width=True)
    with st.expander("Alcance de la integración y límites"):
        for limite in datos["limitaciones"]:
            st.write("• " + limite)
        st.write("El modelo local, los avisos oficiales y los accesos/refugios se revisan en las solapas de cada región. El informe compartido no confirma recursos operativos locales.")
    st.download_button("Descargar informe común JSON", json.dumps(datos, ensure_ascii=False, allow_nan=False, indent=2),
                       "red_litoral_zarate_campana.json", "application/json", key="red_integrada_descargar")
    return datos


NODOS_ALA = [{'localidad': 'Reconquista', 'provincia': 'Santa Fe', 'lat': -29.144, 'lon': -59.643},
 {'localidad': 'Avellaneda', 'provincia': 'Santa Fe', 'lat': -29.117, 'lon': -59.658},
 {'localidad': 'Vera', 'provincia': 'Santa Fe', 'lat': -29.46, 'lon': -60.213},
 {'localidad': 'Tostado', 'provincia': 'Santa Fe', 'lat': -29.233, 'lon': -61.769},
 {'localidad': 'Rafaela', 'provincia': 'Santa Fe', 'lat': -31.25, 'lon': -61.486},
 {'localidad': 'Santa Fe', 'provincia': 'Santa Fe', 'lat': -31.633, 'lon': -60.7},
 {'localidad': 'Esperanza', 'provincia': 'Santa Fe', 'lat': -31.448, 'lon': -60.932},
 {'localidad': 'Rosario', 'provincia': 'Santa Fe', 'lat': -32.946, 'lon': -60.639},
 {'localidad': 'Venado Tuerto', 'provincia': 'Santa Fe', 'lat': -33.745, 'lon': -61.968},
 {'localidad': 'Corrientes', 'provincia': 'Corrientes', 'lat': -27.469, 'lon': -58.83},
 {'localidad': 'Goya', 'provincia': 'Corrientes', 'lat': -29.14, 'lon': -59.263},
 {'localidad': 'Mercedes', 'provincia': 'Corrientes', 'lat': -29.182, 'lon': -58.075},
 {'localidad': 'Curuzú Cuatiá', 'provincia': 'Corrientes', 'lat': -29.791, 'lon': -58.054},
 {'localidad': 'Paso de los Libres', 'provincia': 'Corrientes', 'lat': -29.713, 'lon': -57.088},
 {'localidad': 'Santo Tomé', 'provincia': 'Corrientes', 'lat': -28.549, 'lon': -56.04},
 {'localidad': 'Paraná', 'provincia': 'Entre Ríos', 'lat': -31.741, 'lon': -60.511},
 {'localidad': 'La Paz', 'provincia': 'Entre Ríos', 'lat': -30.744, 'lon': -59.645},
 {'localidad': 'Concordia', 'provincia': 'Entre Ríos', 'lat': -31.393, 'lon': -58.02},
 {'localidad': 'Resistencia', 'provincia': 'Chaco', 'lat': -27.451075711, 'lon': -58.986483529},
 {'localidad': 'General José de San Martín',
  'provincia': 'Chaco',
  'lat': -26.53630236,
  'lon': -59.341321005},
 {'localidad': 'Presidencia Roque Sáenz Peña',
  'provincia': 'Chaco',
  'lat': -26.790971775,
  'lon': -60.441248325},
 {'localidad': 'Charata', 'provincia': 'Chaco', 'lat': -27.217922303, 'lon': -61.187347968},
 {'localidad': 'Villa Ángela', 'provincia': 'Chaco', 'lat': -27.576877556, 'lon': -60.711125666},
 {'localidad': 'Nueva Pompeya', 'provincia': 'Chaco', 'lat': -24.933074618, 'lon': -61.483145173},
 {'localidad': 'Formosa', 'provincia': 'Formosa', 'lat': -26.185008983, 'lon': -58.174896483},
 {'localidad': 'Clorinda', 'provincia': 'Formosa', 'lat': -25.286080881, 'lon': -57.7224082},
 {'localidad': 'Pirané', 'provincia': 'Formosa', 'lat': -25.732649819, 'lon': -59.109808063},
 {'localidad': 'El Colorado', 'provincia': 'Formosa', 'lat': -26.309061021, 'lon': -59.372308046},
 {'localidad': 'Las Lomitas', 'provincia': 'Formosa', 'lat': -24.708135606, 'lon': -60.591376883},
 {'localidad': 'Ingeniero Guillermo N. Juárez',
  'provincia': 'Formosa',
  'lat': -23.90373915,
  'lon': -61.842429269},
 {'localidad': 'Posadas', 'provincia': 'Misiones', 'lat': -27.36642598, 'lon': -55.893980275},
 {'localidad': 'Oberá', 'provincia': 'Misiones', 'lat': -27.486790956, 'lon': -55.120056983},
 {'localidad': 'Eldorado', 'provincia': 'Misiones', 'lat': -26.408876964, 'lon': -54.60705304},
 {'localidad': 'Puerto Iguazú',
  'provincia': 'Misiones',
  'lat': -25.597210098,
  'lon': -54.577223001},
 {'localidad': 'San Vicente', 'provincia': 'Misiones', 'lat': -26.993297387, 'lon': -54.486618972},
 {'localidad': 'El Soberbio', 'provincia': 'Misiones', 'lat': -27.297511036, 'lon': -54.197545618},
 {'localidad': 'Gualeguaychú',
  'provincia': 'Entre Ríos',
  'lat': -33.007797697,
  'lon': -58.510770086},
 {'localidad': 'Gualeguay', 'provincia': 'Entre Ríos', 'lat': -33.15043008, 'lon': -59.310575586},
 {'localidad': 'Victoria', 'provincia': 'Entre Ríos', 'lat': -32.621605823, 'lon': -60.157953952},
 {'localidad': 'Villaguay', 'provincia': 'Entre Ríos', 'lat': -31.867637571, 'lon': -59.026885213},
 {'localidad': 'Federal', 'provincia': 'Entre Ríos', 'lat': -30.956214281, 'lon': -58.779587505},
 {'localidad': 'Chajarí', 'provincia': 'Entre Ríos', 'lat': -30.754724203, 'lon': -57.981789368},
 {'localidad': 'Ituzaingó', 'provincia': 'Corrientes', 'lat': -27.5833333333333, 'lon': -56.7},
 {'localidad': 'Barranqueras',
  'provincia': 'Chaco',
  'lat': -27.4833333333333,
  'lon': -58.9333333333333},
 {'localidad': 'Andresito',
  'provincia': 'Misiones',
  'lat': -25.5833333333333,
  'lon': -53.9833333333333},
 {'localidad': 'Colón',
  'provincia': 'Entre Ríos',
  'lat': -32.2333333333333,
  'lon': -58.1166666666667},
 {'localidad': 'Concepción del Uruguay',
  'provincia': 'Entre Ríos',
  'lat': -32.4833333333333,
  'lon': -58.2333333333333},
 {'localidad': 'San Javier (Misiones)',
  'provincia': 'Misiones',
  'lat': -27.8833333333333,
  'lon': -55.1333333333333}]


NODOS_ZC = [{'localidad': 'Zárate centro',
  'partido': 'Zárate',
  'lat': -34.098,
  'lon': -59.028,
  'provincia': 'Buenos Aires',
  'referencia': 'Coordenada aproximada para una celda meteorológica; no es una estación ni un '
                'límite administrativo'},
 {'localidad': 'Zárate costanera',
  'partido': 'Zárate',
  'lat': -34.089,
  'lon': -59.011,
  'provincia': 'Buenos Aires',
  'referencia': 'Coordenada aproximada para una celda meteorológica; no es una estación ni un '
                'límite administrativo'},
 {'localidad': 'Lima',
  'partido': 'Zárate',
  'lat': -34.05,
  'lon': -59.197,
  'provincia': 'Buenos Aires',
  'referencia': 'Coordenada aproximada para una celda meteorológica; no es una estación ni un '
                'límite administrativo'},
 {'localidad': 'Escalada',
  'partido': 'Zárate',
  'lat': -34.164,
  'lon': -59.112,
  'provincia': 'Buenos Aires',
  'referencia': 'Coordenada aproximada para una celda meteorológica; no es una estación ni un '
                'límite administrativo'},
 {'localidad': 'Delta de Zárate · referencia',
  'partido': 'Zárate',
  'lat': -34.025,
  'lon': -58.935,
  'provincia': 'Buenos Aires',
  'referencia': 'Coordenada aproximada para una celda meteorológica; no es una estación ni un '
                'límite administrativo'},
 {'localidad': 'Campana centro',
  'partido': 'Campana',
  'lat': -34.168,
  'lon': -58.959,
  'provincia': 'Buenos Aires',
  'referencia': 'Coordenada aproximada para una celda meteorológica; no es una estación ni un '
                'límite administrativo'},
 {'localidad': 'Campana costanera',
  'partido': 'Campana',
  'lat': -34.155,
  'lon': -58.96,
  'provincia': 'Buenos Aires',
  'referencia': 'Coordenada aproximada para una celda meteorológica; no es una estación ni un '
                'límite administrativo'},
 {'localidad': 'Ingeniero Otamendi',
  'partido': 'Campana',
  'lat': -34.237,
  'lon': -58.893,
  'provincia': 'Buenos Aires',
  'referencia': 'Coordenada aproximada para una celda meteorológica; no es una estación ni un '
                'límite administrativo'},
 {'localidad': 'Río Luján',
  'partido': 'Campana',
  'lat': -34.28,
  'lon': -58.888,
  'provincia': 'Buenos Aires',
  'referencia': 'Coordenada aproximada para una celda meteorológica; no es una estación ni un '
                'límite administrativo'},
 {'localidad': 'Delta de Campana · referencia',
  'partido': 'Campana',
  'lat': -34.123,
  'lon': -58.842,
  'provincia': 'Buenos Aires',
  'referencia': 'Coordenada aproximada para una celda meteorológica; no es una estación ni un '
                'límite administrativo'}]
