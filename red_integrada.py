"""Contrato público compartido ALA / Zárate–Campana, versión 1.

Sólo transporta evidencia y su cobertura. No envía mensajes, no emite órdenes,
no suma caudales de estaciones sucesivas ni convierte compuertas en alturas.
Cada página publica su región en su propio repositorio y proceso.
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
VERSION_RED = "2026-10-22.regional"
REPOSITORY = "FedericoIeraci-cell/Alerta-Litoral"
STATE_BRANCH = "ala-monitor-state"
SNAPSHOT_PATH = "red_integrada.json"
SNAPSHOT_MAX_AGE_HOURS = 3
MONITOR_STATUS_PATH = "monitor_status.json"
MONITOR_MAX_BYTES = 500_000
# Se conserva el límite de tres horas que ya usaba el lector para el control.
MONITOR_MAX_AGE_HOURS = 3
API_METEO = "https://api.open-meteo.com/v1/ecmwf"
INA_WFS = "https://alerta.ina.gob.ar/geoserver/ows"
MAX_BYTES = 2_000_000
PRIVATE_KEYS = {"token", "chat_id", "destino", "mensaje", "telegram_bot_token", "telegram_chat_id", "github_token", "authorization", "password", "secret"}
_CACHE_RED = {}
_CACHE_MONITOR = {}
_CACHE_LOCK = threading.Lock()
LIMITES = [
    "La vista común es evidencia de vigilancia; no es un modelo hidráulico calibrado ni una orden de evacuación.",
    "Una señal del Litoral no se transforma automáticamente en una alerta local de Zárate–Campana.",
    "Los aportes de represas y estaciones sucesivas no se suman; no se calcula una velocidad fija de llegada.",
    "Paraná y Uruguay mantienen ramas distintas antes del estuario; marea y viento también condicionan el Delta.",
    "Las escalas de puerto 40/41 y las series de pronóstico 29534/3405 no tienen equivalencia documentada: sus umbrales no se cruzan.",
    "La automática de Zárate (29437) es una referencia RHN distinta del puerto. Se muestra con su propia fecha y escala, sin trasladar los umbrales del puerto ni extenderla a Campana.",
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
            "fecha_consulta": None, "fecha_emision_modelo": None, "referencia_pronostico": None,
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
    salida["referencia_pronostico"] = ref.astimezone(TZ).isoformat()
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



def _rio_automatico_zarate(observacion, referencia=None):
    """Referencia RHN independiente: nunca hereda alturas o umbrales del puerto."""
    from hidrometria_local import revalidar_altura_zarate
    dato = revalidar_altura_zarate(observacion, referencia)
    rio = _rio_vacio("Paraná Las Palmas - Zárate", 29437, None)
    rio.update(altura_m=dato.get("altura_m"), fecha_observada=dato.get("fecha_observada"),
               fecha_consulta=dato.get("consulta"), fuente=dato.get("fuente") or "https://alerta.ina.gob.ar/",
               cero_ign_m=dato.get("cero_ign_m"), error=dato.get("error") or "",
               error_pronostico="La previsión local se muestra por separado; esta fila identifica la medición automática.",
               observacion_automatica=dato, zona_horaria_verificada=bool(dato.get("fecha_observada")),
               tipo_observacion="Estación automática RHN / INA")
    return rio


def altura_automatica_zarate_de_red(datos, referencia=None):
    """Extrae sólo la referencia verificada del informe común, sin una consulta paralela."""
    try:
        informe = validar_red_integrada(datos, referencia)
    except (ValueError, TypeError):
        return None
    if not informe.get("snapshot_vigente"):
        return None
    filas = [r for r in informe["rios"] if r.get("red") == "Zárate–Campana"
             and r.get("serie_observada") == 29437]
    return deepcopy(filas[0]["observacion_automatica"]) if len(filas) == 1 else None


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
                       fuente=fuente, error="", zona_horaria_verificada=True,
                       precision_observada="fecha_registro_fuente", fecha_registro=dt.isoformat(),
                       fecha_medicion_confirmada=False)
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


def _meteo_transitorio(exc):
    import requests
    if isinstance(exc, (requests.Timeout, requests.ConnectionError)):
        return True
    response = getattr(exc, "response", None)
    return getattr(response, "status_code", None) in {429, 500, 502, 503, 504}


def _guardar_grilla_zc(nodo, payload):
    # Sólo variables públicas acotadas; no se guarda la respuesta HTTP completa.
    if nodo.get("lluvia_prevista_72_mm") is not None:
        keys = ("latitude", "longitude", "timezone", "utc_offset_seconds", "hourly_units", "hourly")
        nodo["grilla_verificada"] = {k: deepcopy(payload.get(k)) for k in keys[:4]}
        variables = ("time", "precipitation", "runoff", "soil_moisture_0_to_7cm")
        for campo in ("hourly", "hourly_units"):
            nodo["grilla_verificada"][campo] = {k: deepcopy(v) for k, v in payload[campo].items() if k in variables}
        nodo["grilla_version"] = 1
    return nodo


def _recuperar_meteo_zc(nodo, anterior, referencia, error):
    if not isinstance(anterior, dict) or anterior.get("grilla_version") != 1:
        return None
    if any(anterior.get(k) != nodo.get(k) for k in ("localidad", "lat", "lon")) or anterior.get("fuente") != API_METEO:
        return None
    fecha = _fecha(anterior.get("fecha_consulta"))
    # No se renueva esta fecha en fallos sucesivos ni se aceptan fechas futuras.
    if fecha is None or fecha > referencia:
        return None
    payload = anterior.get("grilla_verificada")
    try:
        vigente = (referencia - fecha).total_seconds() <= 3 * 3600
        recuperado = parsear_meteorologia_zc(nodo, payload, fecha, referencia if vigente else fecha)
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
        return None
    if recuperado.get("lluvia_prevista_72_mm") is None:
        return None
    if not vigente:
        recuperado["estado_datos"] = "VENCIDO"
    recuperado.update(recuperacion="GRILLA_PREVIA_VIGENTE" if vigente else "GRILLA_PREVIA_VENCIDA", fecha_ultimo_intento=referencia.isoformat(),
                      error_actualizacion=error)
    return _guardar_grilla_zc(recuperado, payload)


def consultar_meteorologia_zc(referencia=None, session=None, anterior=None):
    """Diez consultas aisladas; tres intentos como máximo por punto.

    Dos intentos completos y, ante fallos transitorios, uno reducido de lluvia.
    Como máximo tres solicitudes simultáneas, sin reintentos de HTTP 4xx salvo 429.
    Una grilla anterior sólo cuenta como actual hasta tres horas desde su consulta real.
    Después se conserva exclusivamente como referencia vencida.
    """
    ref = _ahora(referencia)
    previos = {}
    if isinstance(anterior, dict):
        try:
            validado = validar_red_integrada(anterior, ref)
            previos = {n["localidad"]: n for n in validado["nodos"] if n["red"] == "Zárate–Campana"}
        except (ValueError, TypeError, KeyError):
            pass

    def consultar(nodo):
        params = {"latitude": str(nodo["lat"]), "longitude": str(nodo["lon"]),
                  "hourly": "precipitation,runoff,soil_moisture_0_to_7cm", "past_days": 8, "forecast_days": 15,
                  "timezone": "America/Argentina/Buenos_Aires", "precipitation_unit": "mm", "cell_selection": "land"}
        intentos = []
        for intento in range(3):
            if intento:
                time.sleep(1)
            if intento == 2:
                params.update(hourly="precipitation", past_days=0, forecast_days=4)
            inicio = time.monotonic()
            try:
                payload, _ = _request_json(session, API_METEO, params, timeout=(5, 20))
                records = payload if isinstance(payload, list) else [payload]
                if len(records) != 1:
                    raise ValueError("Cantidad de coordenadas incompatible")
                resultado = parsear_meteorologia_zc(nodo, records[0], ref, ref)
                if resultado["lluvia_prevista_72_mm"] is None:
                    raise ValueError("Lluvia de 72 horas no verificable")
                intentos.append({"modo": "lluvia_4d" if intento == 2 else "completo",
                                 "resultado": "verificado", "segundos": round(time.monotonic() - inicio, 2)})
                resultado.update(recuperacion="CONSULTA_REDUCIDA" if intento == 2 else "CONSULTA_DIRECTA",
                                 fecha_ultimo_intento=ref.isoformat(), intentos_meteorologia=intentos)
                return _guardar_grilla_zc(resultado, records[0])
            except Exception as exc:
                status = getattr(getattr(exc, "response", None), "status_code", None)
                codigo = type(exc).__name__ + (f" HTTP {status}" if status is not None else "")
                intentos.append({"modo": "lluvia_4d" if intento == 2 else "completo",
                                 "resultado": codigo, "segundos": round(time.monotonic() - inicio, 2)})
                if not _meteo_transitorio(exc):
                    break
        error = "Consulta meteorológica no verificada: " + intentos[-1]["resultado"]
        resultado = _recuperar_meteo_zc(nodo, previos.get(nodo["localidad"]), ref, error)
        if resultado is None:
            resultado = _nodo_vacio(nodo, error=error)
            resultado["fecha_ultimo_intento"] = ref.isoformat()
        resultado["intentos_meteorologia"] = intentos
        return resultado

    with ThreadPoolExecutor(max_workers=3) as pool:
        return list(pool.map(consultar, NODOS_ZC))


def consultar_zc_red(referencia=None, session=None, anterior=None, meteorologia=None):
    """Consulta diez coordenadas y fuentes INA locales; toda ausencia queda explícita."""
    ref = _ahora(referencia)
    consulta = ref.isoformat()
    nodos = consultar_meteorologia_zc(ref, session, anterior) if meteorologia is None else meteorologia
    fuentes = []
    actuales = sum(n.get("lluvia_prevista_72_mm") is not None and
                   _estado_fecha(n.get("fecha_calculo"), ref, 3)["vigente"] for n in nodos)
    fallos = sum(bool(n.get("error_actualizacion")) or n.get("lluvia_prevista_72_mm") is None for n in nodos)
    fuentes.append({"red": "Zárate–Campana", "organismo": "Open-Meteo / ECMWF", "url": API_METEO,
                    "consultado": consulta, "fecha_documento": None, "estado_publicado": "CONSULTA",
                    "error": "" if actuales == len(NODOS_ZC) and not fallos else
                    f"{actuales}/{len(NODOS_ZC)} referencias con lluvia de 72 h dentro del plazo; {fallos} sin actualización directa"})
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
    # La automática es otra estación: se agrega después de las dos escalas de puerto.
    # Su lectura no cambia el índice, los candidatos ni los destinatarios de ALA.
    from hidrometria_local import consultar_altura_zarate
    try:
        automatica = consultar_altura_zarate(referencia=ref, session=session)
    except Exception:
        automatica = {}
    rio_automatico = _rio_automatico_zarate(automatica, ref)
    rios.append(rio_automatico)
    fuentes.append({"red": "Zárate–Campana", "organismo": "INA / RHN · estación automática de Zárate",
                    "url": rio_automatico["fuente"], "consultado": rio_automatico["fecha_consulta"],
                    "fecha_documento": rio_automatico["fecha_observada"], "error": rio_automatico["error"]})
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
                  referencia_pronostico=_iso(fila.get("referencia_pronostico")),
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
    # El horizonte extendido conserva su fin real; no se completa hasta 15 días.
    horas = _numero(fila.get("pronostico_extendido_horas"), 1, 360)
    hasta = _iso(fila.get("pronostico_extendido_hasta"))
    extendida = _numero(fila.get("lluvia_futura_extendida"), 0, 100000)
    if fila.get("cobertura_pronostico_extendido") is True and horas is not None and horas.is_integer() and hasta and extendida is not None:
        salida["lluvia_prevista_15d_mm"] = extendida
        salida["cobertura_horaria"]["lluvia_prevista_15d"] = {
            "validas": int(horas), "esperadas": int(horas), "hasta": hasta,
            "maximo_solicitado_horas": 360,
        }
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
    precision = item.get("precision_fecha") or "fecha_hora_publicada"
    if "/diario/reporte_diario" in fuente:
        precision = "hora_no_verificada_en_reporte_diario"
    return {"red": "Litoral", "estacion": _texto(item.get("nombre") or key), "serie_observada": item.get("series_id"),
            "serie_pronostico": item.get("series_id"), "altura_m": _numero(item.get("altura_m")),
            "alerta_m": _numero(item.get("alerta_m")), "evacuacion_m": _numero(item.get("evacuacion_m")),
            "fecha_observada": observada if precision in ("fecha_hora_publicada", "fecha_registro_fuente") else None,
            "fecha_observada_original": _texto(item.get("fecha")) if precision in ("fecha_hora_publicada", "fecha_registro_fuente") else _texto(item.get("fecha"))[:10],
            "fecha_registro": _iso(item.get("fecha_registro") or item.get("fecha")),
            "fecha_medicion_confirmada": precision == "fecha_hora_publicada",
            "precision_observada": precision, "fecha_documento_original": _texto(item.get("fecha_documento")),
            "fecha_emision": max(emisiones) if emisiones else None, "pronostico": curves,
            "fuente": fuente, "fuente_pronostico": _url(item.get("fuente_pronostico")) or fuente,
            "fuente_umbrales": _url(item.get("fuente_umbrales")) or fuente,
            "representatividad": _texto(item.get("representatividad")), "escala_equivalente": True,
            "error": "", "error_pronostico": "" if curves else "Sin puntos numéricos publicados"}


def _normalizar_represa(key, item):
    doc_date = _iso(item.get("fecha"))
    original = _texto(item.get("fecha"))
    if "/diario/reporte_diario" in _url(item.get("fuente")):
        doc_date, original = None, original[:10]
    campos = {"red": "Cuenca compartida", "nombre": _texto(item.get("nombre") or key),
              "fecha": doc_date, "fecha_original": original,
              "precision_fecha": _texto(item.get("precision_fecha")), "precision_hasta": _texto(item.get("precision_hasta")),
              "hasta": _iso(item.get("hasta")), "fuente": _url(item.get("fuente")),
              "tipo": _texto(item.get("tipo")), "texto": _texto(item.get("texto"), 5000),
              "consulta": _iso(item.get("consulta"))}
    for field in ("afluente_tipo", "afluente_previsto_interpretacion"):
        campos[field] = _texto(item.get(field), 1200)
    extremos = item.get("afluente_previsto_extremos_publicados_m3s")
    if isinstance(extremos, (tuple, list)):
        campos["afluente_previsto_extremos_publicados_m3s"] = [_numero(v, 0) for v in extremos[:2]]
    for k in ("afluente_m3s", "erogado_m3s", "afluente_previsto_min_m3s", "afluente_previsto_max_m3s", "cota_embalse_m", "turbinas_m3s", "vertedero_m3s", "cota_restitucion_m"):
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
            "precision_fecha_documento": _texto(item.get("precision_fecha_documento")),
            "precision_valido_hasta": _texto(item.get("precision_valido_hasta")),
            "consultado": _iso(item.get("consultado")), "valido_hasta": _iso(item.get("valido_hasta")),
            "estado_publicado": _texto(item.get("estado_publicado") or item.get("estado")),
            "error": _texto(item.get("error"))}


def construir_red_integrada(frame_ala, panorama_ala, zc=None, monitor=None, referencia=None, version_ala="", region=None):
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
    fuentes = list(panorama_ala.get("fuentes", [])) + list(zc.get("fuentes", []))
    smn = panorama_ala.get("smn")
    if isinstance(smn, dict):
        incompleto = bool(smn.get("error") or smn.get("fallos") or smn.get("truncado"))
        fuentes.append({
            "red": "Litoral", "organismo": "SMN · canal de avisos consultado por ALA",
            "url": "https://ssl.smn.gob.ar/CAP/AR.php",
            "fecha_documento": smn.get("fecha_feed"), "consultado": smn.get("consultado"),
            "estado_publicado": "CONSULTA_INCOMPLETA" if incompleto else "CONSULTA_COMPLETA",
            "error": "La consulta de avisos está incompleta; revisar los documentos oficiales." if incompleto else "",
        })
    payload = {"schema_version": SCHEMA_VERSION, "catalogo_version": CATALOGO_VERSION,
               "generated_at": ref.isoformat(), "producer": {"app": "Alerta Litoral Agro · monitor", "version": _texto(version_ala)},
               "monitor": safe_monitor, "nodos": ala + zrows,
               "rios": [_normalizar_rio_ala(k, v) for k, v in panorama_ala.get("estaciones", {}).items() if isinstance(v, dict)] + list(zc.get("rios", [])),
               "represas": [_normalizar_represa(k, v) for k, v in panorama_ala.get("represas", {}).items() if isinstance(v, dict)],
               "fuentes": [_normalizar_fuente(f) for f in fuentes if isinstance(f, dict)],
               "limitaciones": list(LIMITES), "permite_orden_evacuacion": False, "envia_telegram": False}
    if region is not None:
        if region not in {"Litoral", "Zárate–Campana"}:
            raise ValueError("Región no compatible")
        payload["regiones"] = [region]
        payload["producer"]["app"] = "Alerta Litoral Agro" if region == "Litoral" else "Alerta Zárate–Campana"
        for campo in ("nodos", "rios"):
            payload[campo] = [item for item in payload[campo] if item.get("red") == region]
        fuentes_regionales = {region, "Cuenca compartida"} if region == "Litoral" else {region}
        payload["fuentes"] = [item for item in payload["fuentes"] if item.get("red") in fuentes_regionales]
        if region == "Zárate–Campana":
            payload["represas"] = []
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
    if payload.get("monitor") is not None and not isinstance(payload["monitor"], dict):
        raise ValueError("Estado del control automático no válido")
    regiones = payload.get("regiones", ["Litoral", "Zárate–Campana"])
    if (not isinstance(regiones, list) or not regiones or len(set(regiones)) != len(regiones)
            or any(region not in {"Litoral", "Zárate–Campana"} for region in regiones)):
        raise ValueError("Alcance regional no compatible")
    expected = {(red, n["localidad"]): n for red, nodes in (("Litoral", NODOS_ALA), ("Zárate–Campana", NODOS_ZC)) if red in regiones for n in nodes}
    found = set()
    out = {k: deepcopy(payload.get(k)) for k in ("schema_version", "catalogo_version", "generated_at", "producer", "monitor", "nodos", "rios", "represas", "fuentes")}
    if "regiones" in payload:
        out["regiones"] = list(regiones)
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
        raise ValueError("El informe no conserva todos los puntos de su catálogo regional")
    automatica_vista = False
    for rio in out["rios"]:
        if not isinstance(rio, dict):
            raise ValueError("Estación no válida")
        es_automatica = rio.get("red") == "Zárate–Campana" and rio.get("serie_observada") == 29437
        if es_automatica:
            if automatica_vista or rio.get("estacion") != "Paraná Las Palmas - Zárate" or rio.get("serie_pronostico") is not None:
                raise ValueError("Identidad de estación automática incompatible o repetida")
            automatica_vista = True
            # Reconstruye los campos visibles desde el contrato validado, sin
            # aceptar umbrales ni una curva remota agregados a esta medición.
            dato = deepcopy(rio.get("observacion_automatica"))
            rio.clear()
            rio.update(_rio_automatico_zarate(dato, ref))
        if rio.get("red") == "Litoral" and "/diario/reporte_diario" in _url(rio.get("fuente")):
            rio["fecha_observada_original"] = _texto(rio.get("fecha_observada_original") or rio.get("fecha_observada"))[:10]
            rio["fecha_observada"] = None
            rio["precision_observada"] = "hora_no_verificada_en_reporte_diario"
        if rio.get("red") == "Zárate–Campana" and rio.get("serie_observada") in (40, 41):
            # La hora de la serie portuaria identifica el registro, no acredita
            # una medición a esa hora. La automática RHN se valida por separado.
            rio["precision_observada"] = "fecha_registro_fuente"
            rio["fecha_registro"] = _iso(rio.get("fecha_observada"))
            rio["fecha_medicion_confirmada"] = False
        for field in ("altura_m", "alerta_m", "evacuacion_m", "cero_ign_m"):
            rio[field] = _numero(rio.get(field), -10, 100)
        temporal = _estado_fecha(rio.get("fecha_observada"), ref, 6 if rio.get("red") == "Zárate–Campana" else 48)
        rio.update(edad_observada_horas=temporal["edad_horas"], observacion_estado=temporal["estado_tiempo"],
                   observacion_vigente=temporal["vigente"] and out["snapshot_vigente"] and rio["altura_m"] is not None)
        if es_automatica:
            rio["observacion_vigente"] = bool(rio["observacion_vigente"] and rio["observacion_automatica"].get("vigente"))
        if rio.get("red") == "Zárate–Campana":
            identity = (rio.get("estacion"), rio.get("serie_observada"), rio.get("serie_pronostico"))
            if identity not in {("Zárate", 40, 29534), ("Campana", 41, 3405), ("Paraná Las Palmas - Zárate", 29437, None)}:
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
            temporal = _estado_fecha(item.get("fecha"), ref, 2 if item.get("tipo") == "observacion_horaria_ctm" else 48)
            hasta = _fecha(item.get("hasta"))
            if hasta is not None and ref > hasta:
                temporal.update(vigente=False, estado_tiempo="VENCIDO")
            item.update(temporal)
            item["vigente"] = temporal["vigente"] and out["snapshot_vigente"]
    # El mismo parte puede llegar con dos claves internas. No duplica una represa
    # ni su caudal; se conservan informes que difieran en fecha, fuente o contenido.
    reportes, vistos = [], set()
    for item in out["represas"]:
        firma = json.dumps(item, sort_keys=True, ensure_ascii=False, allow_nan=False)
        if firma not in vistos:
            vistos.add(firma)
            reportes.append(item)
    out["represas"] = reportes
    for item in out["fuentes"]:
        if isinstance(item, dict):
            if "/diario/reporte_diario" in _url(item.get("url")):
                item["fecha_documento_original"] = _texto(item.get("fecha_documento_original") or item.get("fecha_documento"))[:10]
                item["fecha_documento"] = None
            # La tabla de fuentes aplica la misma ventana que la tabla de ríos.
            # Una observación local antigua no se vuelve vigente por releerla.
            es_ina_local = item.get("red") == "Zárate–Campana" and (
                _normalizar(item.get("organismo")).startswith("ina ")
                or "alerta.ina.gob.ar" in _url(item.get("url")))
            es_smn = "smn" in _normalizar(item.get("organismo"))
            max_horas = 6 if es_ina_local else 24 if es_smn else 48
            temporal = _estado_fecha(item.get("fecha_documento"), ref, max_horas)
            hasta = _fecha(item.get("valido_hasta"))
            if hasta is not None and ref > hasta:
                temporal.update(vigente=False, estado_tiempo="VENCIDO")
            item.update(temporal)
            item["limite_antiguedad_horas"] = max_horas
            item["vigente"] = temporal["vigente"] and out["snapshot_vigente"] and not item.get("error")
    return _json_finito(out)


def limpiar_cache_red_integrada():
    with _CACHE_LOCK:
        _CACHE_RED.clear()
        _CACHE_MONITOR.clear()


def consultar_red_integrada(repository=REPOSITORY, session=None, referencia=None, actualizar=False):
    if not isinstance(repository, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        return {"error": "Repositorio del informe no válido", "snapshot_vigente": False}
    url = f"https://raw.githubusercontent.com/{repository}/{STATE_BRANCH}/{SNAPSHOT_PATH}"
    try:
        payload = None
        if session is None and not actualizar:
            with _CACHE_LOCK:
                cached = _CACHE_RED.get(repository)
                if cached and time.monotonic() - cached[0] <= 300:
                    payload = deepcopy(cached[1])
        if payload is None:
            # El botón solicita una nueva versión también al caché HTTP de GitHub.
            params = {"revision": str(time.time_ns())} if actualizar else None
            payload, _ = _request_json(session, url, params=params)
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


_ALCANCE_MONITOR = "Meteorología + hidrología + represas"
_SALUD_MONITOR = frozenset({"ok", "datos_parciales", "dry_run", "sin_datos", "error_registro",
                          "envio_sin_confirmacion", "envio_rechazado", "configuracion_invalida", "error_red_integrada",
                          "error_meteorologia", "error_meteorologia_zc"})
_COBERTURAS_MONITOR = {
    "meteorologia": "Meteorología", "hidrologia_observada": "Observaciones de ríos",
    "hidrologia_prevista": "Previsión de ríos", "represas": "Información de represas",
    "avisos_smn": "Consulta de avisos oficiales",
}


def _registro_publico_monitor(payload):
    """Contrato mínimo del registro horario; excluye historial, envíos y texto libre."""
    if not isinstance(payload, dict) or type(payload.get("schema_version")) is not int or payload["schema_version"] != 1:
        raise ValueError("Registro de control incompatible")
    if _fecha(payload.get("checked_at")) is None:
        raise ValueError("Control sin fecha y zona horaria verificadas")
    frequency = payload.get("frequency_minutes")
    if type(frequency) is not int or not 5 <= frequency <= 1440:
        raise ValueError("Frecuencia del control incompatible")
    if payload.get("scope") not in {_ALCANCE_MONITOR, "ECMWF / índice meteorológico"}:
        raise ValueError("Alcance del control incompatible")
    if not isinstance(payload.get("health"), str) or payload["health"] not in _SALUD_MONITOR:
        raise ValueError("Resultado del control no reconocido")
    for flag in ("dry_run", "publication_only", "red_integrada_publicada"):
        if type(payload.get(flag)) is not bool:
            raise ValueError("Modo del control sin confirmar")
    for key in ("nodes_total", "nodes_valid", "nodes_red"):
        if type(payload.get(key)) is not int or payload[key] < 0:
            raise ValueError("Cobertura del control incompatible")
    if not payload["nodes_red"] <= payload["nodes_valid"] <= payload["nodes_total"]:
        raise ValueError("Cobertura del control inconsistente")
    salida = {key: payload[key] for key in ("schema_version", "checked_at", "frequency_minutes", "scope", "health",
              "dry_run", "publication_only", "red_integrada_publicada", "nodes_total", "nodes_valid", "nodes_red")}
    publication = payload.get("red_integrada_estado")
    if publication not in {"pendiente", "verificada", "error", "no_solicitada"}:
        raise ValueError("Publicación del control sin confirmar")
    salida["red_integrada_estado"] = publication
    coverage = payload.get("coverage", {})
    if not isinstance(coverage, dict):
        raise ValueError("Cobertura del control incompatible")
    salida["coverage"] = {}
    for key in _COBERTURAS_MONITOR:
        if key not in coverage:
            continue
        record = coverage[key]
        if (not isinstance(record, dict) or type(record.get("vigentes")) is not int
                or type(record.get("aplicables")) is not int
                or not 0 <= record["vigentes"] <= record["aplicables"] <= payload["nodes_total"]):
            raise ValueError("Cobertura aplicable inconsistente")
        salida["coverage"][key] = {field: record[field] for field in ("vigentes", "aplicables")}
    receipt = payload.get("red_integrada_confirmacion")
    if receipt is not None:
        if not isinstance(receipt, dict):
            raise ValueError("Confirmación de publicación incompatible")
        fields = ("generated_at", "verified_at")
        if any(_fecha(receipt.get(key)) is None for key in fields):
            raise ValueError("Publicación sin fechas verificadas")
        if (not isinstance(receipt.get("sha"), str) or not re.fullmatch(r"[0-9a-fA-F]{40,64}", receipt["sha"])
                or type(receipt.get("bytes")) is not int or not 0 < receipt["bytes"] <= MAX_BYTES):
            raise ValueError("Confirmación de publicación incompatible")
        generated, verified = (_fecha(receipt[key]) for key in fields)
        checked = _fecha(payload["checked_at"])
        if generated < checked - timedelta(minutes=5) or verified < generated or verified < checked:
            raise ValueError("Fechas de publicación inconsistentes")
        salida["red_integrada_confirmacion"] = {key: receipt[key] for key in (*fields, "sha", "bytes")}
    return salida


def consultar_estado_monitor(repository=REPOSITORY, session=None, referencia=None, actualizar=False):
    """Lee sólo monitor_status.json; nunca ejecuta controles ni consulta el registro de envíos."""
    if not isinstance(repository, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        return {"error": "Repositorio del control no válido"}
    url = f"https://raw.githubusercontent.com/{repository}/{STATE_BRANCH}/{MONITOR_STATUS_PATH}"
    try:
        payload = None
        if session is None and not actualizar:
            with _CACHE_LOCK:
                cached = _CACHE_MONITOR.get(repository)
                if cached and time.monotonic() - cached[0] <= 300:
                    payload = deepcopy(cached[1])
        if payload is None:
            params = {"revision": str(time.time_ns())} if actualizar else None
            raw, _ = _request_json(session, url, params=params, max_bytes=MONITOR_MAX_BYTES)
            payload = _registro_publico_monitor(raw)
            # Una lectura intermedia puede preceder por segundos a la confirmación.
            if session is None and payload["red_integrada_estado"] != "pendiente":
                with _CACHE_LOCK:
                    _CACHE_MONITOR[repository] = (time.monotonic(), deepcopy(payload))
        return {**deepcopy(payload), "error": "", "monitor_url": url,
                "actions_url": f"https://github.com/{repository}/actions"}
    except Exception:
        return {"error": "No se pudo verificar el registro del control automático.", "monitor_url": url,
                "actions_url": f"https://github.com/{repository}/actions"}


def resumen_publico_monitor(estado_monitor, referencia=None):
    """Describe el último registro normal; una publicación de prueba nunca lo acredita."""
    fuente = f"https://raw.githubusercontent.com/{REPOSITORY}/{STATE_BRANCH}/{MONITOR_STATUS_PATH}"
    resumen = {"estado": "sin_verificacion", "fecha": None, "alcance": _ALCANCE_MONITOR, "faltantes": [],
               "fuente": fuente, "control_reciente": False, "publicacion_confirmada": False,
               "motivo": "No se pudo verificar el último control automático.", "etiqueta": "Control sin verificar",
               "frequency_minutes": None, "edad_horas": None, "limite_antiguedad_horas": MONITOR_MAX_AGE_HOURS}
    if not isinstance(estado_monitor, dict) or estado_monitor.get("error"):
        return resumen
    try:
        registro = _registro_publico_monitor(estado_monitor)
    except (ValueError, TypeError):
        return resumen
    ref = _ahora(referencia)
    temporal = _estado_fecha(registro["checked_at"], ref, MONITOR_MAX_AGE_HOURS)
    faltantes = []
    if registro["nodes_valid"] < registro["nodes_total"]:
        faltantes.append(f"Clasificación integrada: {registro['nodes_valid']} de {registro['nodes_total']} localidades interpretables.")
    for key, record in registro["coverage"].items():
        if record["vigentes"] < record["aplicables"]:
            faltantes.append(f"{_COBERTURAS_MONITOR[key]}: {record['vigentes']} de {record['aplicables']} localidades aplicables con información vigente.")
    resumen.update(fecha=registro["checked_at"], alcance=registro["scope"], faltantes=faltantes,
                   fuente=_url(estado_monitor.get("monitor_url")) or fuente, frequency_minutes=registro["frequency_minutes"],
                   edad_horas=temporal["edad_horas"], health=registro["health"], nodes_total=registro["nodes_total"],
                   nodes_valid=registro["nodes_valid"], nodes_invalid=registro["nodes_total"] - registro["nodes_valid"])
    if temporal["estado_tiempo"] == "FECHA_FUTURA":
        resumen["motivo"] = "El registro tiene una fecha futura; el último control no se puede verificar."
        return resumen
    if not temporal["vigente"]:
        resumen.update(estado="vencido", etiqueta="Control anterior", motivo="El último registro tiene más de tres horas; falta confirmar un control reciente.")
        return resumen
    if registro["dry_run"] or registro["publication_only"] or registro["health"] == "dry_run":
        resumen.update(estado="comprobacion", etiqueta="Comprobación de datos", motivo="El registro corresponde a una comprobación, no a un control normal del canal.")
        return resumen
    if registro["scope"] != _ALCANCE_MONITOR:
        resumen["motivo"] = "El registro tiene un alcance anterior; no confirma el control conjunto de lluvia, ríos y represas."
        return resumen
    problemas = {
        "sin_datos": "El control no pudo interpretar ninguna localidad.",
        "error_registro": "El control requiere revisar su registro de avisos.",
        "envio_sin_confirmacion": "El control registró avisos cuya entrega no quedó confirmada.",
        "envio_rechazado": "El control registró un problema al entregar avisos.",
        "configuracion_invalida": "El control requiere revisar la configuración del canal.",
        "error_red_integrada": "El control guardó la evaluación, pero no confirmó el informe compartido.",
        "error_meteorologia": "El control no confirmó la publicación de la captura meteorológica de ALA.",
        "error_meteorologia_zc": "El control no confirmó la captura meteorológica completa de Zárate–Campana; el respaldo conserva su fecha original.",
    }
    if registro["health"] in problemas or registro["red_integrada_estado"] == "error":
        resumen.update(estado="con_error", etiqueta="Control con un problema", motivo=problemas.get(registro["health"], problemas["error_red_integrada"]))
        return resumen
    receipt = registro.get("red_integrada_confirmacion")
    if (registro["red_integrada_publicada"] is not True or registro["red_integrada_estado"] != "verificada"
            or receipt is None or _fecha(receipt["verified_at"]) > ref + timedelta(minutes=5)):
        resumen["motivo"] = "El registro aún no permite confirmar que el control terminó y publicó sus datos."
        return resumen
    parcial = registro["health"] == "datos_parciales" or bool(faltantes)
    resumen.update(estado="datos_parciales" if parcial else "normal_reciente", control_reciente=True,
                   etiqueta="Control normal con datos parciales" if parcial else "Control normal confirmado",
                   publicacion_confirmada=True, publicacion_fecha=receipt["generated_at"], confirmacion_fecha=receipt["verified_at"],
                   motivo=f"{registro['nodes_valid']} de {registro['nodes_total']} localidades con clasificación integrada.")
    return resumen


def resumen_estado_operativo(datos, estado_monitor, referencia=None):
    """Interfaz de portada: fechas, alcance, faltantes y fuentes separados de datos/control."""
    red = resumen_publico_red(datos, referencia)
    fuente = _url(datos.get("snapshot_url")) if isinstance(datos, dict) else ""
    informe = {"estado": red["estado"], "fecha": red.get("generated_at"),
               "fuente": fuente or f"https://raw.githubusercontent.com/{REPOSITORY}/{STATE_BRANCH}/{SNAPSHOT_PATH}",
               "alcance": " / ".join(datos.get("regiones", ["Red Litoral–Zárate–Campana"])) if isinstance(datos, dict) else "Datos regionales", "faltantes": [], "comprobacion": red.get("prueba", False)}
    if red["estado"] == "sin_confirmar":
        informe["motivo"] = "No se pudo verificar el informe compartido."
    else:
        informe.update(nodos=red["nodos"], localidades_sin_clasificacion=red["sin_clasificacion_litoral"],
                       motivo=f"{red['nodos']} referencias comparten el mismo informe; cada fuente conserva su fecha.")
        for campo, etiqueta in (("alturas_recientes", "Alturas vigentes"), ("pronosticos_rio_recientes", "Pronósticos fluviales vigentes")):
            if red[campo] < red["estaciones"]:
                informe["faltantes"].append(f"{etiqueta}: {red[campo]} de {red['estaciones']} estaciones.")
    return {"datos": informe, "control": resumen_publico_monitor(estado_monitor, referencia)}


def _mostrar_fecha(valor):
    date = _fecha(valor)
    return date.astimezone(TZ).strftime("%d/%m/%Y %H:%M ART") if date is not None else _texto(valor) or "Sin fecha/hora verificadas"


def fecha_publica(valor, precision="fecha_hora_publicada"):
    """Fecha legible sin agregar una hora que la fuente no publicó."""
    texto = _texto(valor, 100)
    if precision == "fecha_registro_fuente" and _fecha(valor) is not None:
        return _fecha(valor).astimezone(TZ).strftime("%d/%m/%Y") + " · hora de medición no confirmada"
    if precision in {"fecha_sin_hora", "hora_no_verificada_en_reporte_diario"}:
        fecha = _fecha(valor)
        if fecha is not None:
            return fecha.astimezone(TZ).strftime("%d/%m/%Y") + " · hora no publicada"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", texto):
        try:
            return datetime.strptime(texto, "%Y-%m-%d").strftime("%d/%m/%Y") + " · hora no publicada"
        except ValueError:
            return "Fecha no verificada"
    return _mostrar_fecha(valor)


def valor_publico(valor, unidad=""):
    numero = _numero(valor)
    if numero is None:
        return "Sin dato"
    texto = f"{numero:,.2f}".rstrip("0").rstrip(".")
    texto = texto.replace(",", "_").replace(".", ",").replace("_", ".")
    return texto + (" " + unidad if unidad else "")


def estado_publico(estado, vigente, informe_vigente=True, error=""):
    if not informe_vigente:
        return "Informe antiguo: revisar actualización"
    if estado == "VENCIDO":
        return "Dato antiguo: no confirma la situación actual"
    if error:
        return "Datos incompletos: revisar fuente"
    if vigente:
        return "Dentro del plazo de uso"
    return {"VENCIDO": "Dato antiguo: no confirma la situación actual",
            "FECHA_FUTURA": "Fecha inconsistente: no usar como dato actual",
            "SIN_FECHA_HORA_VERIFICADA": "Fecha u hora sin verificar"}.get(estado, "Situación actual sin confirmar")


def resumen_publico_red(datos, referencia=None):
    """Hechos de conexión y cobertura. No deduce una alerta local nueva."""
    if not isinstance(datos, dict) or datos.get("error"):
        return {"estado": "sin_confirmar", "error": "No se pudo comprobar el informe compartido."}
    try:
        verificado = validar_red_integrada(datos, referencia)
    except (ValueError, TypeError):
        return {"estado": "sin_confirmar", "error": "El informe compartido no pasó la comprobación de datos."}
    nodos, rios = verificado["nodos"], verificado["rios"]
    monitor = verificado.get("monitor") or {}
    control = _estado_fecha(monitor.get("checked_at"), _ahora(referencia), 3)
    return {"estado": "reciente" if verificado["snapshot_vigente"] else "antiguo",
            "generated_at": verificado["generated_at"], "edad_horas": verificado["snapshot_edad_horas"],
            "prueba": monitor.get("dry_run") is True,
            "control_reciente": control["vigente"] and monitor.get("dry_run") is False and monitor.get("health") in {"ok", "datos_parciales"},
            "control_parcial": monitor.get("health") == "datos_parciales",
            "nodos": len(nodos), "litoral": sum(n["red"] == "Litoral" for n in nodos),
            "zarate_campana": sum(n["red"] == "Zárate–Campana" for n in nodos),
            "lluvia_72_reciente": sum(n.get("vigente") is True and n.get("lluvia_prevista_72_mm") is not None for n in nodos),
            "alturas_recientes": sum(r.get("observacion_vigente") is True for r in rios),
            "pronosticos_rio_recientes": sum(r.get("pronostico_vigente") is True for r in rios),
            "estaciones": len(rios),
            "rojos": [n["localidad"] for n in nodos if n.get("nivel_integrado_vigente") == "ROJO"],
            "sin_clasificacion_litoral": [n["localidad"] for n in nodos if n["red"] == "Litoral" and n.get("nivel_integrado_vigente") == "SIN DATOS"],
            "datos": verificado}


def _mostrar_control_publico(st, control, *, comprobacion_datos=False):
    fecha = " · " + fecha_publica(control["fecha"]) if control["fecha"] else ""
    texto = control["etiqueta"] + fecha + ". " + control["motivo"]
    if control["control_reciente"]:
        st.caption(texto)
    elif control["estado"] in {"con_error", "vencido"}:
        st.warning(texto)
    else:
        st.info(texto)
    with st.expander("Detalles del control y datos pendientes"):
        st.write("El informe compartido y el registro del control normal se consultan por separado. "
                 "Una comprobación de datos no acredita vigilancia ni entrega de avisos.")
        if comprobacion_datos:
            st.write("El informe de datos mostrado se publicó en modo de comprobación. El control indicado arriba procede de su propio registro.")
        st.write("Alcance del control: " + control["alcance"] + ".")
        if control["frequency_minutes"] is not None:
            st.write(f"Frecuencia configurada: cada {control['frequency_minutes']} minutos. "
                     f"El lector considera reciente el último registro durante {MONITOR_MAX_AGE_HOURS} horas; su fecha no garantiza una ejecución posterior.")
        for faltante in control["faltantes"]:
            st.write(faltante)
        if control.get("confirmacion_fecha"):
            st.write("Publicación confirmada por ese control: " + fecha_publica(control["confirmacion_fecha"]) + ".")
        st.markdown("[Ver el registro público del control](" + control["fuente"] + ")")


def mostrar_estado_conexion(st, app_actual="ala", datos=None, *, estado_monitor=None, repository=REPOSITORY, referencia=None):
    if datos is None and referencia is None:
        from panel_hidro_actual import mostrar_panel_hidrologia_actual
        mostrar_panel_hidrologia_actual(st)
    consultar = datos is None
    datos = consultar_red_integrada(repository, referencia=referencia) if consultar else datos
    if estado_monitor is None and consultar:
        estado_monitor = consultar_estado_monitor(repository, referencia=referencia)
    resumen = resumen_publico_red(datos, referencia)
    operativo = resumen_estado_operativo(datos, estado_monitor, referencia)
    resumen["estado_operativo"] = operativo
    if resumen["estado"] == "sin_confirmar":
        st.warning("No se pudo verificar el informe de esta página. Revisá la fecha de cada dato antes de usarlo.")
        _mostrar_control_publico(st, operativo["control"])
        return resumen
    edad = resumen["edad_horas"]
    minutos = max(0, round(edad * 60)) if edad is not None else None
    antiguedad = f" · hace {minutos} min" if minutos is not None else ""
    regional = bool(datos.get("regiones"))
    titulo = "Datos de " + " / ".join(datos["regiones"]) if regional else "Red Litoral–Zárate–Campana · informe común"
    st.caption(titulo + ": " + fecha_publica(resumen["generated_at"]) + antiguedad)
    if resumen["estado"] == "antiguo":
        st.warning("El informe tiene más de 3 horas. La actualización de esta página necesita revisión.")
    _mostrar_control_publico(st, operativo["control"], comprobacion_datos=resumen["prueba"])
    otra = "Zárate–Campana" if str(app_actual).lower() == "ala" else "Litoral"
    url = "https://alerta-zarate-campana.streamlit.app/" if otra == "Zárate–Campana" else "https://alerta-litoral-agro.streamlit.app/"
    if regional:
        st.caption(f"{resumen['nodos']} referencias de esta página. Actualización y estado independientes. [Abrir {otra}]({url}). Cada fuente conserva su fecha.")
    else:
        st.caption(f"48 localidades del Litoral + 10 referencias de Zárate–Campana. [Ver {otra}]({url}). "
                   "El informe común y la consulta local pueden tener horas distintas; compará también el período de cada pronóstico.")
    return resumen


def filas_publicas_nodos(datos, region="Ambas regiones"):
    filas = []
    for n in datos["nodos"]:
        if region != "Ambas regiones" and n["red"] != region:
            continue
        litoral = n["red"] == "Litoral"
        nivel = n.get("nivel_integrado_vigente", "SIN DATOS") if litoral else "Ver evaluación local"
        motivo = n.get("motivo_principal") or "No hay una explicación verificable para esta evaluación."
        if litoral and nivel == "SIN DATOS":
            motivo = "Faltan datos para una clasificación integrada actual. La lluvia se muestra por separado."
        if not litoral:
            motivo = "Variables compartidas; la evaluación de inundación local se consulta en Zárate–Campana."
        filas.append({"Localidad": n["localidad"], "Región": n["red"], "Evaluación integrada": nivel,
                      "Por qué / alcance": motivo,
                      "Lluvia prevista 24 h": valor_publico(n.get("lluvia_prevista_24_mm"), "mm"),
                      "Lluvia prevista 72 h": valor_publico(n.get("lluvia_prevista_72_mm"), "mm"),
                      "Consulta / cálculo de lluvia": fecha_publica(n.get("fecha_calculo")),
                      "Origen de la lluvia": {"GRILLA_PREVIA_VIGENTE": "Grilla anterior aún vigente; falló la actualización",
                          "GRILLA_PREVIA_VENCIDA": "Grilla anterior vencida; sólo referencia histórica",
                          "CONSULTA_REDUCIDA": "Consulta reducida: lluvia hasta 4 días",
                          "CONSULTA_DIRECTA": "Consulta completa verificada"}.get(n.get("recuperacion"), "Consulta original"),
                      "Vigencia de la lluvia": estado_publico(n.get("estado_tiempo"), n.get("vigente"), datos["snapshot_vigente"], n.get("error"))})
    return filas


def mostrar_red_integrada(st, repository=REPOSITORY, app_actual="", datos=None, *, estado_monitor=None, referencia=None):
    """La misma evidencia, las mismas edades y el mismo lenguaje en ambas páginas."""
    st.subheader("🌐 Datos regionales" if app_actual in {"ala", "zc"} else "🌐 Red integrada · Litoral y Zárate–Campana")
    st.write("Seguimiento de lluvia, ríos y represas. Cada página tiene su propio proceso de actualización; cada dato conserva su lugar, fecha y fuente.")
    left, right = st.columns(2)
    left.link_button("Abrir Alerta Litoral Agro", "https://alerta-litoral-agro.streamlit.app/", key="red_integrada_ala")
    right.link_button("Abrir Alerta Zárate–Campana", "https://alerta-zarate-campana.streamlit.app/", key="red_integrada_zc")
    consultar = datos is None
    actualizar = consultar and st.button("Actualizar datos de esta página", key="red_integrada_actualizar")
    datos = consultar_red_integrada(repository, actualizar=actualizar, referencia=referencia) if consultar else datos
    if estado_monitor is None and consultar:
        estado_monitor = consultar_estado_monitor(repository, actualizar=actualizar, referencia=referencia)
    resumen = resumen_publico_red(datos, referencia)
    control = resumen_publico_monitor(estado_monitor, referencia)
    if resumen["estado"] == "sin_confirmar":
        st.warning(resumen["error"] + " Revisá el actualizador de esta página.")
        _mostrar_control_publico(st, control)
        st.link_button("Ver controles del informe compartido", f"https://github.com/{repository}/actions", key="red_integrada_actions_error")
        return datos if isinstance(datos, dict) else {"error": resumen["error"], "snapshot_vigente": False}
    snapshot_url = datos.get("snapshot_url", "")
    datos = {**resumen["datos"], "snapshot_url": snapshot_url}
    st.caption("Informe generado: " + fecha_publica(datos["generated_at"]) + " · publicación programada aproximadamente cada 10 minutos")
    if resumen["estado"] == "antiguo":
        st.warning("Informe antiguo. Se conserva como referencia; sus colores no describen la situación actual.")
    _mostrar_control_publico(st, control, comprobacion_datos=resumen["prueba"])
    a, b, c = st.columns(3)
    a.metric("Localidades con lluvia reciente", f"{resumen['lluvia_72_reciente']}/{resumen['nodos']}")
    b.metric("Estaciones con altura reciente", f"{resumen['alturas_recientes']}/{resumen['estaciones']}")
    c.metric("Estaciones con pronóstico vigente", f"{resumen['pronosticos_rio_recientes']}/{resumen['estaciones']}")
    if resumen["rojos"]:
        st.error("Atención prioritaria en el Litoral: " + ", ".join(resumen["rojos"]) + ". Revisá el motivo y las acciones de cada localidad.")
    st.caption("El color integrado de ALA combina sus evidencias disponibles. No es un nivel oficial del SMN ni una orden de evacuación. "
               "Una crecida aguas arriba refuerza la preparación; no permite fijar la llegada del agua a Zárate o Campana.")
    if resumen["sin_clasificacion_litoral"] and resumen["estado"] == "reciente":
        st.warning("Clasificación integrada sin datos suficientes: " + ", ".join(resumen["sin_clasificacion_litoral"]) + ". La falta de información no significa riesgo bajo.")
    regiones = datos.get("regiones")
    seleccion = regiones[0] if regiones and len(regiones) == 1 else st.selectbox("Área de la red integrada", ["Ambas regiones", "Litoral", "Zárate–Campana"], key="red_integrada_region")
    nodes = [n for n in datos["nodos"] if seleccion == "Ambas regiones" or n["red"] == seleccion]
    st.markdown("### Situación y lluvia por localidad")
    st.dataframe(filas_publicas_nodos(datos, seleccion), hide_index=True, use_container_width=True)
    st.caption("0 mm significa que no se prevé lluvia en esa ventana. Sin dato significa información ausente. "
               "Una grilla previa vigente conserva su consulta original y recalcula las ventanas; una consulta reducida sólo aporta lluvia hasta cuatro días. La hora de consulta no es la hora de emisión del modelo. Los avisos oficiales y las acciones locales se muestran en la portada de cada región.")
    with st.expander("Ubicación de las localidades de la red"):
        st.map([{"lat": n["lat"], "lon": n["lon"]} for n in nodes])
        st.caption("Este mapa ubica los puntos de consulta; sus marcadores no representan niveles de alerta.")
    with st.expander("Más datos de lluvia, suelo y períodos disponibles"):
        st.dataframe([{"Localidad": n["localidad"], "Lluvia prevista 7 días": valor_publico(n.get("lluvia_prevista_7d_mm"), "mm"),
            "Períodos contados desde": fecha_publica(n.get("referencia_pronostico")),
            "Qué representa la fecha": n.get("precision_calculo") or "Revisar fuente",
            "Lluvia en el horizonte disponible": valor_publico(n.get("lluvia_prevista_15d_mm"), "mm"),
            "Fin de ese horizonte": fecha_publica((n.get("cobertura_horaria") or {}).get("lluvia_prevista_15d", {}).get("hasta")),
            "Lluvia de los 7 días anteriores": valor_publico(n.get("lluvia_previa_7d_mm"), "mm"),
            "Humedad estimada de 0 a 7 cm": valor_publico(n.get("humedad_0_7_m3_m3"), "m³/m³"),
            "Agua que podría escurrir en 72 h": valor_publico(n.get("escurrimiento_previsto_72_mm"), "mm"),
            "Índice meteorológico /100": valor_publico(n.get("indice_experimental")),
            "Nivel sólo meteorológico": n.get("nivel_meteorologico") if n.get("vigente") else "Sin evaluación vigente",
            "Recuperación meteorológica": n.get("recuperacion") or "Consulta original",
            "Último intento": fecha_publica(n.get("fecha_ultimo_intento")),
            "Fallo de actualización": n.get("error_actualizacion") or "",
            "Información pendiente": n.get("error") or "Sin faltantes registrados en estas variables"} for n in nodes], hide_index=True, use_container_width=True)
        st.caption("El horizonte puede terminar antes de 15 días. La humedad y el escurrimiento son estimaciones del modelo; "
                   "no acreditan suelo transitable ni profundidad de inundación. El índice no es un porcentaje de probabilidad.")
    st.markdown("### Ríos · última lectura y pronóstico por separado")
    rivers = datos["rios"]
    st.dataframe([{"Estación": r.get("estacion"), "Región": r.get("red"),
        "Última altura publicada": valor_publico(r.get("altura_m"), "m"),
        "Fecha de esa lectura": fecha_publica(r.get("fecha_observada") or r.get("fecha_observada_original"), r.get("precision_observada")),
        "¿Sirve como lectura actual?": estado_publico(r.get("observacion_estado"), r.get("observacion_vigente"), datos["snapshot_vigente"], r.get("error")),
        "Emisión del pronóstico": fecha_publica(r.get("fecha_emision")),
        "¿Pronóstico vigente?": "Sí" if r.get("pronostico_vigente") else "No confirmado",
        "Hasta cuándo hay previsión": fecha_publica(max((p.get("fecha") for p in r.get("pronostico", []) if p.get("fecha")), default=None))
        } for r in rivers], hide_index=True, use_container_width=True)
    st.caption("Si la fuente sólo permite confirmar el día del registro, se indica que la hora de medición no está confirmada. "
               "Dentro del plazo de uso no significa medición en tiempo real. "
               "El informe admite hasta 48 horas para las fuentes fluviales del Litoral. En Zárate y Campana, una lectura o emisión de más de 6 horas deja de considerarse actual. "
               "Las escalas de los puertos (40/41) no se comparan con los pronósticos (29534/3405) sin equivalencia documentada. "
               "La automática de Zárate (29437) conserva su propia referencia y no usa los umbrales del puerto. "
               "Las alturas de estaciones diferentes tampoco se suman ni indican profundidad en un barrio.")
    with st.expander("Fuentes, referencias de alerta y puntos del pronóstico de los ríos"):
        st.dataframe([{"Estación": r.get("estacion"), "Serie observada": r.get("serie_observada"),
            "Referencia de alerta de esa estación": valor_publico(r.get("alerta_m"), "m"),
            "Referencia de evacuación de esa estación": valor_publico(r.get("evacuacion_m"), "m"),
            "Serie de pronóstico": r.get("serie_pronostico"), "Fuente de la lectura": r.get("fuente"),
            "Fuente del pronóstico": r.get("fuente_pronostico"),
            "Limitaciones": "; ".join(x for x in (r.get("error"), r.get("error_pronostico")) if x)} for r in rivers], hide_index=True, use_container_width=True)
        st.dataframe([{"Estación": r["estacion"], "Fecha prevista": fecha_publica(p.get("fecha")),
            "Escenario central": valor_publico(p.get("central_m"), "m"), "Extremo inferior publicado": valor_publico(p.get("inferior_m"), "m"),
            "Extremo superior publicado": valor_publico(p.get("superior_m"), "m"),
            "Vigencia de la emisión": "Vigente" if r.get("pronostico_vigente") else "No vigente"}
            for r in rivers for p in r.get("pronostico", [])], hide_index=True, use_container_width=True)
        st.caption("La referencia de evacuación pertenece a la estación y requiere corroboración local. No equivale a una orden de evacuación. "
                   "Los extremos publicados no se convierten en una probabilidad que la fuente no informó.")
    st.markdown("### Represas · qué se informó y de cuándo es")
    st.dataframe([{"Represa": d.get("nombre"), "Fecha del parte": fecha_publica(d.get("fecha") or d.get("fecha_original"), d.get("precision_fecha")),
        "Agua entrante informada": valor_publico(d.get("afluente_m3s"), "m³/s"),
        "Tipo del dato entrante": "Previsión para el día del parte" if d.get("afluente_tipo") == "prevision_del_dia" else "Ver el parte original",
        "Descarga informada": valor_publico(d.get("erogado_m3s"), "m³/s"),
        "Frecuencia del dato": "Horario CTM" if d.get("tipo") == "observacion_horaria_ctm" else "Parte / boletín",
        "Cota del embalse": valor_publico(d.get("cota_embalse_m"), "m"),
        "Evolución de aportes publicada": " → ".join(valor_publico(v, "m³/s") for v in d.get("afluente_previsto_extremos_publicados_m3s", [])) or "Sin secuencia publicada",
        "Hasta": fecha_publica(d.get("hasta"), d.get("precision_hasta")),
        "Vigencia": estado_publico(d.get("estado_tiempo"), d.get("vigente"), datos["snapshot_vigente"]),
        "Fuente": d.get("fuente")} for d in datos["represas"]], hide_index=True, use_container_width=True)
    st.caption("m³/s expresa cuánta agua pasa por segundo, no la altura del río. Un caudal entrante previsto no es una descarga medida. "
               "No se suman represas de una misma cadena. El mismo parte repetido se muestra una sola vez.")
    with st.expander("Leer el detalle de los partes de represas"):
        for d in datos["represas"]:
            st.markdown("**" + _texto(d.get("nombre")) + " · " + fecha_publica(d.get("fecha") or d.get("fecha_original"), d.get("precision_fecha")) + "**")
            st.write(d.get("texto") or "Sin detalle textual publicado en este informe.")
            if d.get("afluente_previsto_interpretacion"):
                st.caption(d["afluente_previsto_interpretacion"])
    st.markdown("### Estado de las fuentes")
    st.dataframe([{"Fuente": f.get("organismo"), "Región / alcance": f.get("red"),
        "Fecha del dato o documento": fecha_publica(f.get("fecha_documento") or f.get("fecha_documento_original"), f.get("precision_fecha_documento")),
        "Última consulta": fecha_publica(f.get("consultado")),
        "Vigencia": estado_publico(f.get("estado_tiempo"), f.get("vigente"), datos["snapshot_vigente"], f.get("error")),
        "Qué falta confirmar": f.get("error") or "", "Enlace original": f.get("url")
        } for f in datos["fuentes"]], hide_index=True, use_container_width=True)
    with st.expander("Evidencias que explican el color integrado"):
        st.dataframe([{"Localidad": n["localidad"], "Tipo de evidencia": e.get("tipo"), "Estación": e.get("estacion"),
            "Fecha publicada": fecha_publica(e.get("fecha_observada") or e.get("desde"), e.get("precision_fecha") or e.get("precision_desde")),
            "Emisión": fecha_publica(e.get("fecha_emision"), e.get("precision_fecha_emision")),
            "Precisión de la fecha": e.get("precision_fecha") or e.get("precision_desde"), "Vigente al consultar": "Sí" if e.get("vigente") else "No",
            "Fuente": e.get("fuente"), "Fuente de los umbrales": e.get("fuente_umbrales")}
            for n in nodes for e in n.get("desencadenantes", [])], hide_index=True, use_container_width=True)
    with st.expander("Cómo funcionan juntas las dos páginas y qué falta completar"):
        st.write("El monitor de ALA consulta las fuentes y publica un informe horario común. Las dos páginas leen ese mismo archivo. "
                 "Cada portada conserva su evaluación local y puede consultar datos más recientes; eso no cambia la fecha del informe común.")
        for limite in datos["limitaciones"]:
            st.write("• " + limite)
        st.write("Los accesos, refugios y recursos sanitarios se confirman con la autoridad local. Un punto en el mapa no acredita que estén disponibles.")
    st.link_button("Ver controles del informe compartido", f"https://github.com/{repository}/actions", key="red_integrada_actions")
    st.download_button("Descargar los datos y sus fuentes", json.dumps(datos, ensure_ascii=False, allow_nan=False, indent=2),
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
