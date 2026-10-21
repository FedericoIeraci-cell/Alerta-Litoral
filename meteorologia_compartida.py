"""Captura pública de las consultas ECMWF del monitor, sin credenciales.

El monitor publica los valores originales, incluidos los ceros y los nulos.
La captura evita repetir la misma consulta para cada visitante de Streamlit;
su fecha de consulta nunca se sustituye por la fecha de lectura.
"""
from __future__ import annotations

import base64
import binascii
from contextlib import suppress
from datetime import datetime, timedelta, timezone
import gzip
import json
import math
import re
import time
import zlib

SCHEMA_VERSION = 1
FUENTE = "https://api.open-meteo.com/v1/ecmwf"
SNAPSHOT_PATH = "meteorologia_ecmwf.json"
SNAPSHOT_URL = (
    "https://raw.githubusercontent.com/FedericoIeraci-cell/Alerta-Litoral/"
    "ala-monitor-state/" + SNAPSHOT_PATH
)
SNAPSHOT_ZC_PATH = "meteorologia_zc_ecmwf.json"
SNAPSHOT_ZC_URL = (
    "https://raw.githubusercontent.com/FedericoIeraci-cell/Alerta-Litoral/"
    "ala-monitor-state/" + SNAPSHOT_ZC_PATH
)
MAX_BYTES = 2_000_000
MAX_UNCOMPRESSED_BYTES = 20_000_000
MAX_AGE_HOURS = 3
ERROR_PUBLICO = "La fuente meteorológica no devolvió datos utilizables para este nodo."

_ORDEN_HORARIAS = (
    "temperature_2m", "apparent_temperature", "dew_point_2m", "relative_humidity_2m",
    "pressure_msl", "surface_pressure", "cloud_cover", "cloud_cover_low", "cloud_cover_mid",
    "cloud_cover_high", "wind_speed_10m", "wind_gusts_10m", "wind_direction_10m",
    "shortwave_radiation", "cape", "vapour_pressure_deficit", "evapotranspiration",
    "et0_fao_evapotranspiration", "precipitation", "rain", "showers",
    "precipitation_probability", "runoff", "soil_temperature_0_to_7cm",
    "soil_temperature_7_to_28cm", "soil_temperature_28_to_100cm", "soil_temperature_100_to_255cm",
    "soil_moisture_0_to_7cm", "soil_moisture_7_to_28cm", "soil_moisture_28_to_100cm",
    "soil_moisture_100_to_255cm", "weather_code",
)
VARIABLES_HORARIAS = frozenset(_ORDEN_HORARIAS)
_ORDEN_DIARIAS = (
    "weather_code", "temperature_2m_min", "temperature_2m_max", "apparent_temperature_min",
    "apparent_temperature_max", "precipitation_sum", "rain_sum", "showers_sum",
    "precipitation_probability_max", "precipitation_hours", "wind_speed_10m_max",
    "wind_gusts_10m_max", "wind_direction_10m_dominant", "cape_max",
    "shortwave_radiation_sum", "sunshine_duration", "et0_fao_evapotranspiration",
)
VARIABLES_DIARIAS = frozenset(_ORDEN_DIARIAS)
_CAMPOS = frozenset({"hourly", "daily", "hourly_units", "daily_units", "latitude", "longitude",
                     "elevation", "timezone", "timezone_abbreviation", "utc_offset_seconds",
                     "generationtime_ms", "error"})
_UNIDADES = frozenset({"iso8601", "°C", "%", "hPa", "km/h", "°", "W/m²", "J/kg", "kPa",
                       "mm", "m³/m³", "m3/m3", "wmo code", "h", "MJ/m²", "s"})
_ZONAS = frozenset({"America/Argentina/Buenos_Aires", "America/Buenos_Aires"})


class CapturaMeteorologicaError(ValueError):
    """Error público: nunca contiene cuerpos HTTP, URLs privadas ni excepciones."""


def _error(mensaje="La captura meteorológica tiene un formato no válido."):
    raise CapturaMeteorologicaError(mensaje)


def _fecha(value):
    if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}T.+", value):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            _error("La captura requiere fecha, hora y zona horaria verificables.")
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        _error("La captura requiere fecha, hora y zona horaria verificables.")
    return value.astimezone(timezone.utc)


def _numero(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _error()
    if abs(value) > 1e12 or not math.isfinite(value):
        _error()
    return value


def _catalogo(nodos):
    if not isinstance(nodos, (list, tuple)) or not 0 < len(nodos) <= 256:
        _error("El catálogo meteorológico no es válido.")
    salida = []
    for nodo in nodos:
        if not isinstance(nodo, dict):
            _error()
        item = {}
        for campo in ("localidad", "provincia"):
            value = nodo.get(campo, "")
            if (not isinstance(value, str) or len(value) > 150 or
                    any(ord(c) < 32 for c in value) or "://" in value):
                _error()
            if campo == "localidad" and not value.strip():
                _error()
            item[campo] = value
        item["lat"], item["lon"] = _numero(nodo.get("lat")), _numero(nodo.get("lon"))
        if not -90 <= item["lat"] <= 90 or not -180 <= item["lon"] <= 180:
            _error()
        salida.append(item)
    return salida


def _tiempo_modelo(value, diario=False):
    patron = r"\d{4}-\d{2}-\d{2}" if diario else r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?"
    if not isinstance(value, str) or not re.fullmatch(patron, value):
        _error()
    try:
        datetime.fromisoformat(value)
    except ValueError:
        _error()
    return value


def _dato(raw, estricto):
    if not isinstance(raw, dict) or (estricto and set(raw) - _CAMPOS):
        _error()
    if raw.get("error"):
        if estricto and (set(raw) != {"error"} or raw["error"] != ERROR_PUBLICO):
            _error()
        return {"error": ERROR_PUBLICO}
    salida = {}
    for campo in ("latitude", "longitude", "elevation", "utc_offset_seconds", "generationtime_ms"):
        if campo in raw:
            salida[campo] = _numero(raw[campo])
    if "latitude" in salida and not -90 <= salida["latitude"] <= 90:
        _error()
    if "longitude" in salida and not -180 <= salida["longitude"] <= 180:
        _error()
    if "utc_offset_seconds" in salida and salida["utc_offset_seconds"] != -10800:
        _error()
    if "timezone" in raw:
        if not isinstance(raw["timezone"], str) or raw["timezone"] not in _ZONAS:
            _error()
        salida["timezone"] = raw["timezone"]
    if "timezone_abbreviation" in raw:
        if (not isinstance(raw["timezone_abbreviation"], str) or
                raw["timezone_abbreviation"] not in {"GMT-3", "GMT-03", "-03", "ART"}):
            _error()
        salida["timezone_abbreviation"] = raw["timezone_abbreviation"]
    for campo, variables, limite in (("hourly", VARIABLES_HORARIAS, 2000),
                                      ("daily", VARIABLES_DIARIAS, 100)):
        permitidas = variables | {"time"}
        if campo in raw:
            bloque = raw[campo]
            if not isinstance(bloque, dict) or (estricto and set(bloque) - permitidas):
                _error()
            tiempos = bloque.get("time", [])
            if not isinstance(tiempos, list) or len(tiempos) > limite:
                _error()
            limpio = {"time": [_tiempo_modelo(t, campo == "daily") for t in tiempos]}
            if limpio["time"] != sorted(set(limpio["time"])):
                _error()
            for clave in bloque.keys() & variables:
                valores = bloque[clave]
                if not isinstance(valores, list) or len(valores) != len(tiempos):
                    _error()
                limpio[clave] = [None if v is None else _numero(v) for v in valores]
            salida[campo] = limpio
        unidades = campo + "_units"
        if unidades in raw:
            bloque = raw[unidades]
            if not isinstance(bloque, dict) or (estricto and set(bloque) - permitidas):
                _error()
            limpio = {}
            for clave in bloque.keys() & permitidas:
                unidad = bloque[clave]
                if unidad == "undefined":
                    # Open-Meteo publica esta unidad para variables no disponibles
                    # en un modelo, acompañada por una serie completamente nula.
                    # Conservarla evita perder el resto del pronóstico válido;
                    # nunca se interpreta una magnitud numérica sin unidad.
                    valores = salida.get(campo, {}).get(clave)
                    if (clave == "time" or not isinstance(valores, list) or
                            any(valor is not None for valor in valores)):
                        _error(f"La unidad no disponible de {campo}.{clave} no corresponde a una serie nula.")
                elif not isinstance(unidad, str) or unidad not in _UNIDADES:
                    _error(f"La unidad de {campo}.{clave} no está admitida en la captura meteorológica.")
                limpio[clave] = unidad
            salida[unidades] = limpio
    return salida


def _con_datos(dato):
    return not dato.get("error") and any(
        any(value is not None for value in values)
        for campo in ("hourly", "daily") for clave, values in dato.get(campo, {}).items()
        if clave != "time"
    )


def crear_captura(nodos, datos, consultado):
    """Proyecta sólo datos meteorológicos públicos; rechaza el fallo total."""
    catalogo = _catalogo(nodos)
    if not isinstance(datos, list) or len(datos) != len(catalogo):
        _error("La cantidad de respuestas meteorológicas no coincide con los nodos.")
    captura = {"schema_version": SCHEMA_VERSION, "fuente": FUENTE,
               "consultado": _fecha(consultado).isoformat(), "nodos": catalogo,
               "datos": [_dato(d, False) for d in datos]}
    return validar_captura(captura, catalogo, referencia=consultado)


def validar_captura(captura, nodos, referencia=None):
    """Valida estructura, identidad/orden y vigencia; devuelve una copia pública."""
    esperados = _catalogo(nodos)
    if (not isinstance(captura, dict) or
            set(captura) != {"schema_version", "fuente", "consultado", "nodos", "datos"} or
            type(captura["schema_version"]) is not int or captura["schema_version"] != SCHEMA_VERSION or
            captura["fuente"] != FUENTE):
        _error()
    recibidos = captura["nodos"]
    if (not isinstance(recibidos, list) or len(recibidos) != len(esperados) or
            any(not isinstance(n, dict) or set(n) != {"localidad", "provincia", "lat", "lon"}
                for n in recibidos) or _catalogo(recibidos) != esperados):
        _error("La captura corresponde a un catálogo u orden de nodos diferente.")
    fecha = _fecha(captura["consultado"])
    ahora = _fecha(referencia) if referencia is not None else datetime.now(timezone.utc)
    if fecha > ahora + timedelta(minutes=5):
        _error("La captura meteorológica tiene una fecha futura no verificable.")
    if ahora - fecha > timedelta(hours=MAX_AGE_HOURS):
        _error("La captura meteorológica está vencida: supera tres horas.")
    datos = captura["datos"]
    if not isinstance(datos, list) or len(datos) != len(esperados):
        _error("La cantidad de respuestas meteorológicas no coincide con los nodos.")
    limpios = [_dato(d, True) for d in datos]
    if not any(_con_datos(d) for d in limpios):
        _error("La captura no contiene ningún nodo con datos meteorológicos utilizables.")
    return {"schema_version": SCHEMA_VERSION, "fuente": FUENTE,
            "consultado": fecha.isoformat(), "nodos": esperados, "datos": limpios}


def _json_bytes(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")


def serializar_captura(captura):
    """JSON gzip/base64 determinista; la vigencia se verifica al leer/publicar."""
    if not isinstance(captura, dict):
        _error()
    limpia = validar_captura(captura, captura.get("nodos"), captura.get("consultado"))
    contenido = _json_bytes(limpia)
    if len(contenido) > MAX_UNCOMPRESSED_BYTES:
        _error("La captura meteorológica supera el tamaño admitido.")
    payload = base64.b64encode(gzip.compress(contenido, compresslevel=6, mtime=0)).decode("ascii")
    resultado = _json_bytes({"schema_version": SCHEMA_VERSION, "encoding": "gzip+base64", "payload": payload})
    if len(resultado) > MAX_BYTES:
        _error("La captura meteorológica comprimida supera el tamaño admitido.")
    return resultado


def _leer_json(contenido):
    def objeto(pares):
        result = {}
        for k, v in pares:
            if k in result:
                _error()
            result[k] = v
        return result
    def no_finito(_):
        _error()
    try:
        return json.loads(contenido, object_pairs_hook=objeto, parse_constant=no_finito)
    except (ValueError, TypeError, UnicodeError, RecursionError):
        _error("La captura meteorológica contiene JSON no válido.")


def deserializar_captura(contenido, nodos, referencia=None):
    """Descomprime con límites antes de validar; no confía en metadatos externos."""
    if not isinstance(contenido, bytes) or not 0 < len(contenido) <= MAX_BYTES:
        _error("La captura meteorológica supera el tamaño admitido o está vacía.")
    sobre = _leer_json(contenido)
    if (not isinstance(sobre, dict) or set(sobre) != {"schema_version", "encoding", "payload"} or
            type(sobre["schema_version"]) is not int or sobre["schema_version"] != SCHEMA_VERSION or
            sobre["encoding"] != "gzip+base64" or not isinstance(sobre["payload"], str)):
        _error()
    try:
        comprimido = base64.b64decode(sobre["payload"], validate=True)
        descompresor = zlib.decompressobj(16 + zlib.MAX_WBITS)
        raw = descompresor.decompress(comprimido, MAX_UNCOMPRESSED_BYTES + 1)
        if (len(raw) > MAX_UNCOMPRESSED_BYTES or not descompresor.eof or
                descompresor.unused_data or descompresor.unconsumed_tail):
            _error("La captura meteorológica comprimida está dañada o supera el límite.")
    except (ValueError, binascii.Error, zlib.error):
        _error("La captura meteorológica comprimida está dañada o supera el límite.")
    return validar_captura(_leer_json(raw), nodos, referencia)


def consultar_captura(nodos, referencia=None, session=None):
    return _consultar_captura_url(SNAPSHOT_URL, nodos, referencia, session)


def consultar_captura_zc(nodos, referencia=None, session=None):
    """La captura ZC completa tiene un archivo propio y el mismo contrato."""
    return _consultar_captura_url(SNAPSHOT_ZC_URL, nodos, referencia, session)


def _consultar_captura_url(url, nodos, referencia=None, session=None):
    """Descarga pública acotada, sin redirecciones, credenciales ni reintentos."""
    propia = session is None
    if propia:
        import requests
        session = requests.Session()
        session.trust_env = False
    respuesta = None
    try:
        inicio = time.monotonic()
        respuesta = session.get(url, timeout=(5, 15), stream=True, allow_redirects=False,
                                headers={"Accept": "application/json", "Cache-Control": "no-cache",
                                         "User-Agent": "ALA-MeteorologiaCompartida/1.0"})
        if respuesta.status_code != 200 or getattr(respuesta, "url", url) != url:
            _error("No se pudo consultar la captura meteorológica pública.")
        largo = respuesta.headers.get("Content-Length")
        if largo is not None and (not str(largo).isdigit() or int(largo) > MAX_BYTES):
            _error("La captura meteorológica supera el tamaño admitido.")
        partes, total = [], 0
        for parte in respuesta.iter_content(chunk_size=65536):
            total += len(parte)
            if total > MAX_BYTES or time.monotonic() - inicio > 25:
                _error("La captura meteorológica excedió el tamaño o tiempo admitido.")
            partes.append(parte)
        return deserializar_captura(b"".join(partes), nodos, referencia)
    except CapturaMeteorologicaError:
        raise
    except Exception:
        _error("No se pudo consultar la captura meteorológica pública.")
    finally:
        if respuesta is not None:
            with suppress(Exception):
                respuesta.close()
        if propia:
            with suppress(Exception):
                session.close()


def crear_captura_zc(referencia=None, session=None):
    """Consulta las variables completas fuera de Streamlit, sin enviar avisos.

    El reloj se fija antes de comenzar las descargas: publicar o leer el
    archivo después no renueva la fecha de sus datos. Una consulta incompleta no
    reemplaza la captura anterior; las grillas reducidas son independientes.
    """
    from concurrent.futures import ThreadPoolExecutor
    import time
    from red_integrada import NODOS_ZC
    import requests
    if session is None:
        session = requests
    consulta = _fecha(referencia) if referencia is not None else datetime.now(timezone.utc)
    # Misma petición del getter ALA: orden de claves, variables y User-Agent.
    base = {"hourly": ",".join(_ORDEN_HORARIAS),
            "daily": ",".join(_ORDEN_DIARIAS),
            "past_days": 8, "forecast_days": 15,
            "timezone": "America/Argentina/Buenos_Aires", "temperature_unit": "celsius",
            "wind_speed_unit": "kmh", "precipitation_unit": "mm", "cell_selection": "land"}

    def consultar(lote, params):
        respuesta = None
        try:
            respuesta = session.get(FUENTE, params=params, timeout=(5, 30),
                                    headers={"User-Agent": "Alerta-Litoral-Agro/3.10.2"})
            respuesta.raise_for_status()
            datos = respuesta.json()
            datos = datos if isinstance(datos, list) else [datos]
            if len(datos) != len(lote):
                raise ValueError("Cantidad de coordenadas incompatible")
            salida = []
            for nodo, dato in zip(lote, datos):
                limpio = _dato(dato, False)
                lat, lon = limpio.get("latitude"), limpio.get("longitude")
                if lat is None or lon is None:
                    raise ValueError("Celda sin coordenadas")
                p1, p2 = math.radians(nodo["lat"]), math.radians(lat)
                dp, dl = math.radians(lat - nodo["lat"]), math.radians(lon - nodo["lon"])
                h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
                if 6371 * 2 * math.asin(math.sqrt(min(1, max(0, h)))) > 40:
                    raise ValueError("Celda incompatible")
                for campo in ("hourly", "daily"):
                    bloque = limpio.get(campo, {})
                    unidades = limpio.get(campo + "_units", {})
                    solicitadas = set(params[campo].split(","))
                    if (not bloque.get("time") or not solicitadas <= bloque.keys()
                            or not solicitadas <= unidades.keys()):
                        raise ValueError("Respuesta sin todas las variables solicitadas")
                salida.append(limpio)
            return salida
        finally:
            if respuesta is not None:
                with suppress(Exception):
                    respuesta.close()

    def consultar_por_partes(lote, params):
        # Tras dos fallos transitorios: reduce el trabajo por petición, no
        # el conjunto de variables ni el horizonte. No reintenta HTTP 403/429.
        grupos = [dict(params, hourly=",".join(_ORDEN_HORARIAS[i::2]),
                       daily=",".join(_ORDEN_DIARIAS[i::2])) for i in range(2)]
        primera, segunda = (consultar(lote, grupo) for grupo in grupos)
        for destino, parte in zip(primera, segunda):
            for campo in ("latitude", "longitude", "timezone", "utc_offset_seconds"):
                if campo not in destino or destino[campo] != parte.get(campo):
                    raise ValueError("Partes de celdas o zonas horarias diferentes")
            for campo in ("hourly", "daily"):
                if destino[campo]["time"] != parte[campo]["time"]:
                    raise ValueError("Partes con ejes temporales diferentes")
                destino[campo].update(parte[campo])
                destino[campo + "_units"].update(parte[campo + "_units"])
        return primera

    def descargar(lote):
        params = dict(latitude=",".join(f'{n["lat"]:.6f}' for n in lote),
                      longitude=",".join(f'{n["lon"]:.6f}' for n in lote), **base)
        contexto = f"Consulta meteorológica ZC: {lote[0]['localidad']}"
        try:
            for intento in range(2):
                try:
                    return consultar(lote, params)
                except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
                    if intento:
                        if isinstance(exc, requests.exceptions.ReadTimeout):
                            return consultar_por_partes(lote, params)
                        raise
                except requests.exceptions.HTTPError as exc:
                    respuesta = exc.response
                    if getattr(respuesta, "status_code", None) not in {502, 503, 504}:
                        raise
                    # Una espera explícita del proveedor no se ignora: si
                    # supera el presupuesto, este control conserva el respaldo.
                    espera = getattr(respuesta, "headers", {}).get("Retry-After")
                    if espera is not None and (not str(espera).isdigit() or int(espera) > 30):
                        raise
                    time.sleep(max(1, int(espera or 1)))
                    if intento:
                        return consultar_por_partes(lote, params)
        except requests.exceptions.Timeout as exc:
            clase = ("timeout_conexion" if isinstance(exc, requests.exceptions.ConnectTimeout) else
                     "timeout_lectura" if isinstance(exc, requests.exceptions.ReadTimeout) else "timeout")
            _error(f"{contexto}, {clase}.")
        except requests.exceptions.ConnectionError:
            _error(f"{contexto}, conexion.")
        except requests.exceptions.HTTPError as exc:
            estado = getattr(getattr(exc, "response", None), "status_code", None)
            codigo = f"http_{estado}" if isinstance(estado, int) and 100 <= estado <= 599 else "http"
            _error(f"{contexto}, {codigo}.")
        except (ValueError, TypeError):
            _error(f"{contexto}, respuesta_invalida.")
        except Exception:
            _error(f"{contexto}, error_interno.")

    # La respuesta completa por coordenada es grande: agrupar localidades
    # multiplica el trabajo del proveedor antes de devolver el primer byte.
    # Aislarlas también limita cada reintento a la localidad que falló.
    lotes = [[nodo] for nodo in NODOS_ZC]
    with ThreadPoolExecutor(max_workers=2) as pool:
        datos = [dato for lote in pool.map(descargar, lotes) for dato in lote]
    if any(not _con_datos(dato) for dato in datos):
        _error("La captura meteorológica completa de Zárate–Campana no cubre todos los nodos.")
    return crear_captura(NODOS_ZC, datos, consulta)
