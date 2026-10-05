# ============================================================
# ALERTA LITORAL AGRO — V3.3
# ============================================================
#
# Sistema experimental de monitoreo y alerta temprana para
# riesgo de anegamiento agropecuario en:
#
#   - Santa Fe
#   - Corrientes
#   - Entre Ríos
#
# V3.3 incorpora:
#
#   1. Open-Meteo como fuente meteorológica.
#   2. INA / Sistema de Información Hidrológica de la
#      Cuenca del Plata como fuente hidrológica oficial.
#   3. Estaciones hidrométricas dinámicas del Litoral.
#   4. Altura hidrométrica actual.
#   5. Tendencia hidrométrica de las últimas 24 h.
#   6. Niveles de alerta y evacuación cuando están disponibles.
#   7. Integración meteorológica + hidrológica + territorial.
#   8. Índice integrado de riesgo 0–100.
#   9. Calidad de datos separada del nivel de riesgo.
#  10. Mapa sin OpenStreetMap / CartoDB / Mapbox.
#  11. Historial de ejecuciones.
#  12. Exportación CSV.
#  13. Telegram opcional.
#
# NOTA:
# La información hidrológica a tiempo útil puede ser cruda
# y no validada. Este sistema NO reemplaza una alerta oficial.
#
# ============================================================

import math
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from urllib.parse import urlencode

import folium
import pandas as pd
import requests
import streamlit as st

from folium.features import DivIcon
from streamlit_folium import st_folium


# ============================================================
# CONFIGURACIÓN GENERAL
# ============================================================

VERSION = "3.3"

TZ = ZoneInfo("America/Argentina/Buenos_Aires")

OPEN_METEO_ECMWF = "https://api.open-meteo.com/v1/ecmwf"
OPEN_METEO_GENERAL = "https://api.open-meteo.com/v1/forecast"

# API oficial INA / DSIyAH
INA_BASE = "https://alerta.ina.gob.ar/pub/datos"

# Variables INA conocidas:
# 2 = altura hidrométrica instantánea
# 4 = caudal instantáneo
INA_VAR_ALTURA = 2
INA_VAR_CAUDAL = 4


# ============================================================
# NODOS METEOROLÓGICOS
# ============================================================

NODOS = {
    # ----------------------------
    # SANTA FE
    # ----------------------------
    "Santa Fe": {
        "lat": -31.6333,
        "lon": -60.7000,
        "provincia": "Santa Fe",
    },
    "Reconquista": {
        "lat": -29.1500,
        "lon": -59.6500,
        "provincia": "Santa Fe",
    },
    "Rafaela": {
        "lat": -31.2500,
        "lon": -61.4900,
        "provincia": "Santa Fe",
    },
    "Rosario": {
        "lat": -32.9500,
        "lon": -60.6667,
        "provincia": "Santa Fe",
    },
    "San Javier": {
        "lat": -30.5833,
        "lon": -59.9333,
        "provincia": "Santa Fe",
    },
    "Tostado": {
        "lat": -29.2333,
        "lon": -61.7667,
        "provincia": "Santa Fe",
    },

    # ----------------------------
    # CORRIENTES
    # ----------------------------
    "Corrientes": {
        "lat": -27.4692,
        "lon": -58.8306,
        "provincia": "Corrientes",
    },
    "Goya": {
        "lat": -29.1399,
        "lon": -59.2626,
        "provincia": "Corrientes",
    },
    "Bella Vista": {
        "lat": -28.5100,
        "lon": -59.0400,
        "provincia": "Corrientes",
    },
    "Ituzaingó": {
        "lat": -27.5800,
        "lon": -56.6820,
        "provincia": "Corrientes",
    },
    "Mercedes": {
        "lat": -29.1818,
        "lon": -58.0750,
        "provincia": "Corrientes",
    },
    "Paso de los Libres": {
        "lat": -29.7131,
        "lon": -57.0877,
        "provincia": "Corrientes",
    },

    # ----------------------------
    # ENTRE RÍOS
    # ----------------------------
    "Paraná": {
        "lat": -31.7333,
        "lon": -60.5333,
        "provincia": "Entre Ríos",
    },
    "La Paz": {
        "lat": -30.7446,
        "lon": -59.6458,
        "provincia": "Entre Ríos",
    },
    "Concordia": {
        "lat": -31.3929,
        "lon": -58.0209,
        "provincia": "Entre Ríos",
    },
    "Gualeguaychú": {
        "lat": -33.0094,
        "lon": -58.5172,
        "provincia": "Entre Ríos",
    },
    "Concepción del Uruguay": {
        "lat": -32.4825,
        "lon": -58.2372,
        "provincia": "Entre Ríos",
    },
    "Villaguay": {
        "lat": -31.8500,
        "lon": -59.0260,
        "provincia": "Entre Ríos",
    },
}


# ============================================================
# VULNERABILIDAD TERRITORIAL EXPERIMENTAL
# ============================================================

VULNERABILIDAD = {
    "Santa Fe": {
        "Santa Fe": 0.90,
        "Reconquista": 0.90,
        "Rafaela": 0.65,
        "Rosario": 0.75,
        "San Javier": 0.95,
        "Tostado": 0.75,
    },
    "Corrientes": {
        "Corrientes": 0.85,
        "Goya": 0.90,
        "Bella Vista": 0.80,
        "Ituzaingó": 0.75,
        "Mercedes": 0.65,
        "Paso de los Libres": 0.75,
    },
    "Entre Ríos": {
        "Paraná": 0.75,
        "La Paz": 0.90,
        "Concordia": 0.80,
        "Gualeguaychú": 0.85,
        "Concepción del Uruguay": 0.80,
        "Villaguay": 0.70,
    },
}


# ============================================================
# NIVELES
# ============================================================

NIVELES = {
    "VERDE": {
        "min": 0,
        "max": 24,
        "emoji": "🟢",
        "color": "#2ca25f",
        "texto": "Bajo",
    },
    "AMARILLO": {
        "min": 25,
        "max": 49,
        "emoji": "🟡",
        "color": "#e6ab02",
        "texto": "Vigilancia",
    },
    "NARANJA": {
        "min": 50,
        "max": 74,
        "emoji": "🟠",
        "color": "#f16913",
        "texto": "Alto",
    },
    "ROJO": {
        "min": 75,
        "max": 100,
        "emoji": "🔴",
        "color": "#de2d26",
        "texto": "Muy alto",
    },
}


ACCIONES = {
    "VERDE": "Monitoreo rutinario.",
    "AMARILLO": "Revisar drenajes, bajos y sectores sensibles.",
    "NARANJA": "Preparar medidas preventivas y verificar sectores productivos vulnerables.",
    "ROJO": "Activar protocolo preventivo y evaluar evacuación de hacienda/equipos en zonas expuestas.",
}


# ============================================================
# UTILIDADES
# ============================================================

def ahora():
    return datetime.now(TZ)


def safe_float(value):
    try:
        if value is None:
            return None

        if isinstance(value, str):
            value = value.strip().replace(",", ".")

            if value.lower() in {
                "",
                "null",
                "none",
                "nan",
                "n/a",
                "s/d",
            }:
                return None

        return float(value)

    except Exception:
        return None


def clamp(valor, minimo=0, maximo=100):
    return max(minimo, min(maximo, valor))


def interpolar_score(valor, puntos):
    """
    puntos = [(umbral, score), ...]

    Interpolación lineal.
    """

    if valor is None:
        return 0

    if valor <= puntos[0][0]:
        return puntos[0][1]

    for i in range(1, len(puntos)):
        x1, y1 = puntos[i - 1]
        x2, y2 = puntos[i]

        if valor <= x2:
            if x2 == x1:
                return y2

            return y1 + ((valor - x1) / (x2 - x1)) * (y2 - y1)

    return puntos[-1][1]


def nivel_desde_riesgo(riesgo):
    riesgo = clamp(riesgo)

    if riesgo >= 75:
        return "ROJO"

    if riesgo >= 50:
        return "NARANJA"

    if riesgo >= 25:
        return "AMARILLO"

    return "VERDE"


def score_lluvia_24h(mm):
    return interpolar_score(
        mm,
        [
            (0, 0),
            (20, 5),
            (40, 10),
            (70, 17),
            (100, 22),
            (150, 25),
        ],
    )


def score_lluvia_72h(mm):
    return interpolar_score(
        mm,
        [
            (0, 0),
            (40, 5),
            (80, 10),
            (120, 17),
            (180, 22),
            (250, 25),
        ],
    )


def score_humedad(humedad_pct):
    return interpolar_score(
        humedad_pct,
        [
            (40, 0),
            (55, 5),
            (65, 10),
            (75, 15),
            (85, 20),
        ],
    )


def score_runoff(mm):
    return interpolar_score(
        mm,
        [
            (0, 0),
            (5, 2),
            (15, 4),
            (30, 6),
            (50, 8),
            (80, 10),
        ],
    )


def score_vulnerabilidad(v):
    return clamp(v * 10, 0, 10)


def score_hidrologico(altura, alerta, evacuacion, tendencia_24h):
    """
    Puntaje hidrológico 0-20.

    Prioridad:
      1. Nivel de alerta/evacuación si la estación los publica.
      2. Tendencia reciente.
      3. Nivel relativo si no existen umbrales.

    No se inventan umbrales.
    """

    if altura is None:
        return 0

    score = 0

    alerta = safe_float(alerta)
    evacuacion = safe_float(evacuacion)
    tendencia_24h = safe_float(tendencia_24h)

    # --------------------------------------------------------
    # Si existen umbrales oficiales en la estación
    # --------------------------------------------------------

    if alerta is not None and evacuacion is not None:

        if evacuacion > alerta:

            if altura >= evacuacion:
                score = 20

            elif altura >= alerta:
                fraccion = (altura - alerta) / (evacuacion - alerta)
                score = 12 + (fraccion * 8)

            else:
                # Hasta llegar al alerta, usamos aproximación
                # conservadora según proximidad.
                proporcion = altura / alerta if alerta > 0 else 0
                score = proporcion * 12

        elif alerta > 0:

            proporcion = altura / alerta
            score = clamp(proporcion * 15, 0, 15)

    # --------------------------------------------------------
    # Tendencia
    # --------------------------------------------------------

    if tendencia_24h is not None:

        if tendencia_24h >= 0.30:
            score += 5

        elif tendencia_24h >= 0.15:
            score += 3

        elif tendencia_24h >= 0.05:
            score += 1

    return clamp(score, 0, 20)


# ============================================================
# OPEN-METEO
# ============================================================

@st.cache_data(ttl=900, show_spinner=False)
def consultar_open_meteo():

    latitudes = ",".join(
        str(NODOS[n]["lat"])
        for n in NODOS
    )

    longitudes = ",".join(
        str(NODOS[n]["lon"])
        for n in NODOS
    )

    variables_ecmwf = (
        "precipitation,"
        "soil_moisture_0_to_7cm,"
        "soil_moisture_7_to_28cm,"
        "soil_moisture_28_to_100cm,"
        "runoff,"
        "wind_gusts_10m"
    )

    params_ecmwf = {
        "latitude": latitudes,
        "longitude": longitudes,
        "hourly": variables_ecmwf,
        "past_days": 3,
        "forecast_days": 7,
        "timezone": "America/Argentina/Buenos_Aires",
        "wind_speed_unit": "kmh",
    }

    headers = {
        "User-Agent": "Alerta-Litoral-Agro/3.3"
    }

    # ========================================================
    # INTENTO 1 — ECMWF
    # ========================================================

    try:

        r = requests.get(
            OPEN_METEO_ECMWF,
            params=params_ecmwf,
            headers=headers,
            timeout=45,
        )

        if r.ok:

            data = r.json()

            if isinstance(data, list) and len(data) == len(NODOS):

                return {
                    "modo": "ecmwf",
                    "fuente": "Open-Meteo / ECMWF",
                    "datos": data,
                }

    except Exception:
        pass

    # ========================================================
    # INTENTO 2 — API GENERAL
    # ========================================================

    variables_general = (
        "precipitation,"
        "soil_moisture_0_to_1cm,"
        "soil_moisture_1_to_3cm,"
        "soil_moisture_3_to_9cm,"
        "soil_moisture_9_to_27cm,"
        "soil_moisture_27_to_81cm,"
        "wind_gusts_10m"
    )

    params_general = {
        "latitude": latitudes,
        "longitude": longitudes,
        "hourly": variables_general,
        "past_days": 3,
        "forecast_days": 7,
        "timezone": "America/Argentina/Buenos_Aires",
        "wind_speed_unit": "kmh",
    }

    try:

        r = requests.get(
            OPEN_METEO_GENERAL,
            params=params_general,
            headers=headers,
            timeout=45,
        )

        if r.ok:

            data = r.json()

            if isinstance(data, list) and len(data) == len(NODOS):

                return {
                    "modo": "respaldo",
                    "fuente": "Open-Meteo / modelo general",
                    "datos": data,
                }

    except Exception:
        pass

    # ========================================================
    # INTENTO 3 — MÍNIMO
    # ========================================================

    params_minimo = {
        "latitude": latitudes,
        "longitude": longitudes,
        "hourly": "precipitation,wind_gusts_10m",
        "past_days": 3,
        "forecast_days": 7,
        "timezone": "America/Argentina/Buenos_Aires",
        "wind_speed_unit": "kmh",
    }

    try:

        r = requests.get(
            OPEN_METEO_GENERAL,
            params=params_minimo,
            headers=headers,
            timeout=45,
        )

        if r.ok:

            data = r.json()

            if isinstance(data, list) and len(data) == len(NODOS):

                return {
                    "modo": "minimo",
                    "fuente": "Open-Meteo / modo mínimo",
                    "datos": data,
                }

    except Exception:
        pass

    raise RuntimeError(
        "No fue posible obtener datos meteorológicos "
        "desde Open-Meteo."
    )


# ============================================================
# SERIES HORARIAS
# ============================================================

def serie_horaria(hourly, nombre_variable):

    if not hourly:
        return []

    tiempos = hourly.get("time", [])
    valores = hourly.get(nombre_variable, [])

    if not tiempos or not valores:
        return []

    resultado = []

    for t, v in zip(tiempos, valores):

        valor = safe_float(v)

        if valor is None:
            continue

        try:
            fecha = datetime.fromisoformat(
                str(t).replace("Z", "+00:00")
            )

            if fecha.tzinfo is None:
                fecha = fecha.replace(tzinfo=TZ)

        except Exception:
            continue

        resultado.append(
            (fecha, valor)
        )

    return resultado


def suma_periodo(serie, horas, referencia=None):

    if referencia is None:
        referencia = ahora()

    inicio = referencia - timedelta(hours=horas)

    return sum(
        valor
        for fecha, valor in serie
        if inicio <= fecha <= referencia
    )


def suma_futuro(serie, horas, referencia=None):

    if referencia is None:
        referencia = ahora()

    fin = referencia + timedelta(hours=horas)

    return sum(
        valor
        for fecha, valor in serie
        if referencia <= fecha <= fin
    )


def maximo_periodo(serie, horas, referencia=None):

    if referencia is None:
        referencia = ahora()

    inicio = referencia - timedelta(hours=horas)

    valores = [
        valor
        for fecha, valor in serie
        if inicio <= fecha <= referencia
    ]

    if not valores:
        return None

    return max(valores)


def maximo_futuro(serie, horas, referencia=None):

    if referencia is None:
        referencia = ahora()

    fin = referencia + timedelta(hours=horas)

    valores = [
        valor
        for fecha, valor in serie
        if referencia <= fecha <= fin
    ]

    if not valores:
        return None

    return max(valores)


def ultimo_dato(serie):

    if not serie:
        return None

    return max(
        fecha
        for fecha, _ in serie
    )


# ============================================================
# HUMEDAD DEL SUELO
# ============================================================

def calcular_humedad_ecmwf(hourly):

    capas = [
        (
            "soil_moisture_0_to_7cm",
            0.45,
        ),
        (
            "soil_moisture_7_to_28cm",
            0.35,
        ),
        (
            "soil_moisture_28_to_100cm",
            0.20,
        ),
    ]

    valores = []
    pesos = []

    for variable, peso in capas:

        serie = serie_horaria(
            hourly,
            variable,
        )

        if not serie:
            continue

        valor = serie[-1][1]

        valores.append(valor)
        pesos.append(peso)

    if not valores:
        return None

    humedad = (
        sum(
            v * p
            for v, p in zip(
                valores,
                pesos,
            )
        )
        / sum(pesos)
    )

    return humedad * 100


def calcular_humedad_respaldo(hourly):

    capas = [
        (
            "soil_moisture_0_to_1cm",
            0.15,
        ),
        (
            "soil_moisture_1_to_3cm",
            0.15,
        ),
        (
            "soil_moisture_3_to_9cm",
            0.20,
        ),
        (
            "soil_moisture_9_to_27cm",
            0.25,
        ),
        (
            "soil_moisture_27_to_81cm",
            0.25,
        ),
    ]

    valores = []
    pesos = []

    for variable, peso in capas:

        serie = serie_horaria(
            hourly,
            variable,
        )

        if not serie:
            continue

        valores.append(
            serie[-1][1]
        )

        pesos.append(peso)

    if not valores:
        return None

    humedad = (
        sum(
            v * p
            for v, p in zip(
                valores,
                pesos,
            )
        )
        / sum(pesos)
    )

    return humedad * 100


# ============================================================
# API HIDROLÓGICA INA
# ============================================================

def ina_url(resource, params=None):

    url = f"{INA_BASE}/{resource}"

    if params:
        query = urlencode(
            {
                k: v
                for k, v in params.items()
                if v is not None
            }
        )

        if query:
            url += "&" + query

    return url


def ina_get(resource, params=None, timeout=30):

    url = ina_url(
        resource,
        params,
    )

    headers = {
        "User-Agent": "Alerta-Litoral-Agro/3.3"
    }

    response = requests.get(
        url,
        headers=headers,
        timeout=timeout,
    )

    response.raise_for_status()

    payload = response.json()

    if isinstance(payload, dict):

        if (
            "mensaje" in payload
            and payload.get("data") in [None, []]
        ):
            raise RuntimeError(
                str(payload.get("mensaje"))
            )

    return payload


def normalizar_estaciones(payload):

    registros = []

    # --------------------------------------------------------
    # GeoJSON
    # --------------------------------------------------------

    if isinstance(payload, dict):

        if payload.get("type") == "FeatureCollection":

            for feature in payload.get(
                "features",
                [],
            ):

                props = feature.get(
                    "properties",
                    {},
                )

                geometry = feature.get(
                    "geometry",
                    {},
                )

                coords = geometry.get(
                    "coordinates",
                    [None, None],
                )

                lon = (
                    safe_float(coords[0])
                    if len(coords) >= 2
                    else None
                )

                lat = (
                    safe_float(coords[1])
                    if len(coords) >= 2
                    else None
                )

                registros.append(
                    {
                        **props,
                        "lon": lon,
                        "lat": lat,
                    }
                )

            return registros

        # ----------------------------------------------------
        # JSON con data
        # ----------------------------------------------------

        data = payload.get(
            "data"
        )

        if isinstance(data, list):

            return data

    # --------------------------------------------------------
    # Lista directa
    # --------------------------------------------------------

    if isinstance(payload, list):
        return payload

    return []


def texto_estacion(estacion):

    partes = []

    for clave in [
        "nombre",
        "name",
        "abrev",
        "rio",
        "river",
        "distrito",
        "provincia",
        "tipo",
        "tipo_nombre",
        "propietario",
        "nombre_red",
    ]:

        valor = estacion.get(clave)

        if valor is not None:
            partes.append(
                str(valor)
            )

    return " ".join(
        partes
    ).lower()


def obtener_coordenadas_estacion(estacion):

    lat = safe_float(
        estacion.get("lat")
    )

    lon = safe_float(
        estacion.get("lon")
    )

    if lat is not None and lon is not None:
        return lat, lon

    # Intento con nombres alternativos

    lat = safe_float(
        estacion.get("latitude")
    )

    lon = safe_float(
        estacion.get("longitude")
    )

    if lat is not None and lon is not None:
        return lat, lon

    geom = estacion.get(
        "geom"
    )

    if isinstance(
        geom,
        dict,
    ):

        coords = geom.get(
            "coordinates",
            [],
        )

        if (
            isinstance(coords, list)
            and len(coords) >= 2
        ):

            return (
                safe_float(coords[1]),
                safe_float(coords[0]),
            )

    return None, None


def obtener_sitecode(estacion):

    for clave in [
        "sitecode",
        "siteCode",
        "site_code",
        "codigo",
        "id",
    ]:

        valor = estacion.get(
            clave
        )

        if valor not in [
            None,
            "",
        ]:
            return str(valor)

    return None


def estacion_es_hidrologica(estacion):

    texto = texto_estacion(
        estacion
    )

    tipo = str(
        estacion.get(
            "tipo",
            ""
        )
    ).lower()

    tipo_nombre = str(
        estacion.get(
            "tipo_nombre",
            ""
        )
    ).lower()

    # Si la API la identifica explícitamente
    if (
        "hidro" in tipo
        or "hidro" in tipo_nombre
    ):
        return True

    # También aceptamos estaciones que
    # contienen variables/indicadores hidrométricos.
    palabras = [
        "hidrom",
        "río",
        "rio",
        "arroyo",
        "puerto",
        "hidrogr",
    ]

    return any(
        palabra in texto
        for palabra in palabras
    )


def dentro_del_litoral(lat, lon):

    if lat is None or lon is None:
        return False

    # Santa Fe
    santa_fe = (
        -35.0 <= lat <= -28.0
        and -63.5 <= lon <= -59.0
    )

    # Entre Ríos
    entre_rios = (
        -34.5 <= lat <= -30.0
        and -61.0 <= lon <= -57.5
    )

    # Corrientes
    corrientes = (
        -30.5 <= lat <= -26.5
        and -59.8 <= lon <= -55.5
    )

    return (
        santa_fe
        or entre_rios
        or corrientes
    )


def distancia_km(
    lat1,
    lon1,
    lat2,
    lon2,
):

    if None in [
        lat1,
        lon1,
        lat2,
        lon2,
    ]:
        return 99999

    r = 6371.0

    p1 = math.radians(lat1)
    p2 = math.radians(lat2)

    dlat = math.radians(
        lat2 - lat1
    )

    dlon = math.radians(
        lon2 - lon1
    )

    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(p1)
        * math.cos(p2)
        * math.sin(dlon / 2) ** 2
    )

    return (
        2
        * r
        * math.asin(
            math.sqrt(a)
        )
    )


@st.cache_data(ttl=1800, show_spinner=False)
def obtener_estaciones_ina():

    bbox = {
        "north": -26.0,
        "south": -35.0,
        "east": -53.0,
        "west": -64.0,
        "format": "json",
    }

    candidatos = []

    # --------------------------------------------------------
    # Intento con filtro hidrológico
    # --------------------------------------------------------

    for tipo in [
        "H",
        None,
    ]:

        try:

            params = bbox.copy()

            if tipo is not None:
                params["type"] = tipo

            payload = ina_get(
                "estaciones",
                params,
            )

            estaciones = normalizar_estaciones(
                payload
            )

            if estaciones:
                candidatos = estaciones
                break

        except Exception:
            continue

    if not candidatos:
        raise RuntimeError(
            "INA no devolvió estaciones."
        )

    resultado = []

    for estacion in candidatos:

        lat, lon = (
            obtener_coordenadas_estacion(
                estacion
            )
        )

        if not dentro_del_litoral(
            lat,
            lon,
        ):
            continue

        if not estacion_es_hidrologica(
            estacion
        ):
            continue

        sitecode = obtener_sitecode(
            estacion
        )

        if sitecode is None:
            continue

        nombre = (
            estacion.get(
                "nombre"
            )
            or estacion.get(
                "name"
            )
            or estacion.get(
                "abrev"
            )
            or f"Estación {sitecode}"
        )

        rio = (
            estacion.get(
                "rio"
            )
            or estacion.get(
                "river"
            )
            or ""
        )

        resultado.append(
            {
                "sitecode": sitecode,
                "nombre": str(nombre),
                "rio": str(rio),
                "lat": lat,
                "lon": lon,
                "alerta": safe_float(
                    estacion.get(
                        "nivel_de_alerta"
                    )
                ),
                "evacuacion": safe_float(
                    estacion.get(
                        "nivel_de_evacuacion"
                    )
                ),
                "aguas_bajas": safe_float(
                    estacion.get(
                        "nivel_de_aguas_bajas"
                    )
                ),
                "propietario": str(
                    estacion.get(
                        "propietario",
                        "",
                    )
                ),
                "red": str(
                    estacion.get(
                        "nombre_red",
                        "",
                    )
                ),
            }
        )

    # Eliminar duplicados
    unicas = {}

    for estacion in resultado:

        clave = (
            estacion["sitecode"],
            estacion["nombre"],
        )

        unicas[clave] = estacion

    return list(
        unicas.values()
    )


def estaciones_cercanas(
    estaciones,
    limite=14,
):

    evaluadas = []

    for estacion in estaciones:

        distancias = []

        for nodo in NODOS.values():

            d = distancia_km(
                estacion["lat"],
                estacion["lon"],
                nodo["lat"],
                nodo["lon"],
            )

            distancias.append(d)

        distancia_minima = min(
            distancias
        )

        texto = (
            estacion["nombre"]
            + " "
            + estacion["rio"]
        ).lower()

        prioridad = 0

        nombres_prioritarios = [
            "corrientes",
            "goya",
            "reconquista",
            "la paz",
            "paraná",
            "parana",
            "santa fe",
            "esquina",
            "bella vista",
            "empedrado",
            "itati",
            "ita ibate",
            "ita ibaté",
            "santa elena",
            "hernandarias",
            "rosario",
        ]

        if any(
            nombre in texto
            for nombre in nombres_prioritarios
        ):
            prioridad = 1

        evaluadas.append(
            (
                -prioridad,
                distancia_minima,
                estacion,
            )
        )

    evaluadas.sort(
        key=lambda x: (
            x[0],
            x[1],
        )
    )

    return [
        item[2]
        for item in evaluadas[
            :limite
        ]
    ]


@st.cache_data(ttl=900, show_spinner=False)
def obtener_datos_hidrologicos():

    estaciones = obtener_estaciones_ina()

    seleccionadas = estaciones_cercanas(
        estaciones,
        limite=18,
    )

    fecha_fin = ahora()
    fecha_inicio = (
        fecha_fin
        - timedelta(days=7)
    )

    resultados = []

    for estacion in seleccionadas:

        try:

            payload = ina_get(
                "datos",
                {
                    "timeStart": fecha_inicio.strftime(
                        "%Y-%m-%d"
                    ),
                    "timeEnd": fecha_fin.strftime(
                        "%Y-%m-%d"
                    ),
                    "siteCode": estacion[
                        "sitecode"
                    ],
                    "varId": INA_VAR_ALTURA,
                    "format": "json",
                },
                timeout=20,
            )

            datos = payload.get(
                "data",
                []
            )

            if not datos:
                continue

            registros = []

            for dato in datos:

                fecha = dato.get(
                    "timestart"
                )

                valor = safe_float(
                    dato.get(
                        "valor"
                    )
                )

                if not fecha or valor is None:
                    continue

                try:

                    dt = datetime.fromisoformat(
                        str(fecha).replace(
                            "Z",
                            "+00:00",
                        )
                    )

                    if dt.tzinfo is None:
                        dt = dt.replace(
                            tzinfo=TZ
                        )

                except Exception:
                    continue

                registros.append(
                    (
                        dt,
                        valor,
                    )
                )

            if not registros:
                continue

            registros.sort(
                key=lambda x: x[0]
            )

            ultimo_dt, altura = registros[-1]

            # ------------------------------------------------
            # Tendencia 24h
            # ------------------------------------------------

            objetivo = (
                ultimo_dt
                - timedelta(hours=24)
            )

            anterior = min(
                registros,
                key=lambda x: abs(
                    (
                        x[0]
                        - objetivo
                    ).total_seconds()
                ),
            )

            diferencia_24h = (
                altura
                - anterior[1]
            )

            # ------------------------------------------------
            # Tendencia 72h
            # ------------------------------------------------

            objetivo_72 = (
                ultimo_dt
                - timedelta(hours=72)
            )

            anterior_72 = min(
                registros,
                key=lambda x: abs(
                    (
                        x[0]
                        - objetivo_72
                    ).total_seconds()
                ),
            )

            diferencia_72h = (
                altura
                - anterior_72[1]
            )

            resultados.append(
                {
                    **estacion,
                    "altura": altura,
                    "fecha_dato": ultimo_dt,
                    "tendencia_24h": diferencia_24h,
                    "tendencia_72h": diferencia_72h,
                    "min_7d": min(
                        v
                        for _, v in registros
                    ),
                    "max_7d": max(
                        v
                        for _, v in registros
                    ),
                    "n_datos": len(
                        registros
                    ),
                    "serie": registros,
                }
            )

        except Exception:
            continue

    return resultados


# ============================================================
# ASIGNACIÓN DE ESTACIÓN HIDROLÓGICA A CADA NODO
# ============================================================

def estacion_hidrologica_mas_cercana(
    nodo_nombre,
    estaciones,
):

    nodo = NODOS[
        nodo_nombre
    ]

    mejor = None
    mejor_distancia = 99999

    for estacion in estaciones:

        d = distancia_km(
            nodo["lat"],
            nodo["lon"],
            estacion["lat"],
            estacion["lon"],
        )

        if d < mejor_distancia:

            mejor = estacion
            mejor_distancia = d

    if mejor is None:
        return None

    resultado = dict(
        mejor
    )

    resultado[
        "distancia_nodo_km"
    ] = mejor_distancia

    return resultado


# ============================================================
# EVALUACIÓN METEOROLÓGICA
# ============================================================

def evaluar_nodo(
    nombre,
    datos_meteo,
    modo,
    estacion_hidro=None,
):

    nodo = NODOS[
        nombre
    ]

    hourly = datos_meteo.get(
        "hourly",
        {}
    )

    precipitacion = serie_horaria(
        hourly,
        "precipitation",
    )

    viento = serie_horaria(
        hourly,
        "wind_gusts_10m",
    )

    lluvia_24 = suma_periodo(
        precipitacion,
        24,
    )

    lluvia_72 = suma_periodo(
        precipitacion,
        72,
    )

    lluvia_futura_24 = suma_futuro(
        precipitacion,
        24,
    )

    lluvia_futura_72 = suma_futuro(
        precipitacion,
        72,
    )

    lluvia_futura_7d = suma_futuro(
        precipitacion,
        168,
    )

    rafaga_72 = maximo_periodo(
        viento,
        72,
    )

    rafaga_futura_72 = maximo_futuro(
        viento,
        72,
    )

    # --------------------------------------------------------
    # Humedad
    # --------------------------------------------------------

    humedad = None

    if modo == "ecmwf":
        humedad = calcular_humedad_ecmwf(
            hourly
        )

    elif modo == "respaldo":
        humedad = calcular_humedad_respaldo(
            hourly
        )

    # --------------------------------------------------------
    # Runoff
    # --------------------------------------------------------

    runoff = serie_horaria(
        hourly,
        "runoff",
    )

    runoff_72 = suma_periodo(
        runoff,
        72,
    )

    runoff_futuro_72 = suma_futuro(
        runoff,
        72,
    )

    runoff_disponible = (
        modo == "ecmwf"
        and bool(runoff)
    )

    # --------------------------------------------------------
    # Vulnerabilidad
    # --------------------------------------------------------

    vulnerabilidad = (
        VULNERABILIDAD.get(
            nodo["provincia"],
            {}
        ).get(
            nombre,
            0.70,
        )
    )

    # --------------------------------------------------------
    # COMPONENTES METEOROLÓGICOS
    # --------------------------------------------------------

    score_24 = score_lluvia_24h(
        lluvia_24
    )

    score_72 = score_lluvia_72h(
        lluvia_72
    )

    score_hum = score_humedad(
        humedad
    )

    score_vuln = score_vulnerabilidad(
        vulnerabilidad
    )

    # --------------------------------------------------------
    # RUNOFF
    # --------------------------------------------------------

    runoff_indicador = None
    score_run = None

    if runoff_disponible:

        runoff_indicador = (
            0.60 * runoff_72
            + 0.40 * runoff_futuro_72
        )

        score_run = score_runoff(
            runoff_indicador
        )

    # --------------------------------------------------------
    # HIDROLOGÍA
    # --------------------------------------------------------

    score_hidro = 0

    altura = None
    alerta_hidro = None
    evacuacion_hidro = None
    tendencia_hidro_24 = None
    tendencia_hidro_72 = None
    fecha_hidro = None
    estacion_hidro_nombre = None
    rio_hidro = None
    distancia_hidro = None

    if estacion_hidro:

        altura = estacion_hidro.get(
            "altura"
        )

        alerta_hidro = estacion_hidro.get(
            "alerta"
        )

        evacuacion_hidro = estacion_hidro.get(
            "evacuacion"
        )

        tendencia_hidro_24 = estacion_hidro.get(
            "tendencia_24h"
        )

        tendencia_hidro_72 = estacion_hidro.get(
            "tendencia_72h"
        )

        fecha_hidro = estacion_hidro.get(
            "fecha_dato"
        )

        estacion_hidro_nombre = (
            estacion_hidro.get(
                "nombre"
            )
        )

        rio_hidro = (
            estacion_hidro.get(
                "rio"
            )
        )

        distancia_hidro = (
            estacion_hidro.get(
                "distancia_nodo_km"
            )
        )

        score_hidro = score_hidrologico(
            altura,
            alerta_hidro,
            evacuacion_hidro,
            tendencia_hidro_24,
        )

    # ========================================================
    # ÍNDICE INTEGRADO
    # ========================================================
    #
    # Máximo:
    #
    # Meteorología
    #   Lluvia antecedente     20
    #   Lluvia pronosticada    20
    #   Humedad                15
    #   Runoff                  5
    #
    # Hidrología               20
    #
    # Vulnerabilidad            10
    #
    # Total                    90
    #
    # El 10 restante corresponde a una
    # reserva de integración/confiabilidad.
    #
    # Para no inflar artificialmente el riesgo,
    # los componentes ausentes NO se redistribuyen
    # automáticamente salvo runoff cuando el resto
    # meteorológico está completo.
    # ========================================================

    pesos = {
        "lluvia_24": 20,
        "lluvia_72": 20,
        "humedad": 15,
        "runoff": 5,
        "hidrologia": 20,
        "vulnerabilidad": 10,
    }

    scores = {
        "lluvia_24": score_24,
        "lluvia_72": score_72,
        "humedad": score_hum,
        "runoff": score_run,
        "hidrologia": score_hidro,
        "vulnerabilidad": score_vuln,
    }

    # --------------------------------------------------------
    # Convertimos los scores históricos:
    #
    # lluvia_24: score max 25 -> peso 20
    # lluvia_72: score max 25 -> peso 20
    # humedad:   score max 20 -> peso 15
    # runoff:    score max 10 -> peso 5
    # hidro:     score max 20 -> peso 20
    # vulnerab.: score max 10 -> peso 10
    # --------------------------------------------------------

    max_scores = {
        "lluvia_24": 25,
        "lluvia_72": 25,
        "humedad": 20,
        "runoff": 10,
        "hidrologia": 20,
        "vulnerabilidad": 10,
    }

    riesgo_bruto = 0
    peso_disponible = 0

    for componente, score in scores.items():

        if score is None:
            continue

        max_score = max_scores[
            componente
        ]

        peso = pesos[
            componente
        ]

        proporcion = (
            score / max_score
            if max_score > 0
            else 0
        )

        riesgo_bruto += (
            proporcion * peso
        )

        peso_disponible += peso

    # --------------------------------------------------------
    # Reserva de 10 puntos.
    #
    # Solo se normaliza a 100 cuando existe
    # una base suficientemente completa.
    # --------------------------------------------------------

    componentes_criticos = [
        score_24,
        score_72,
        score_hum,
        score_hidro,
    ]

    completos = all(
        x is not None
        for x in componentes_criticos
    )

    if completos and peso_disponible > 0:

        riesgo = (
            riesgo_bruto
            / peso_disponible
            * 100
        )

    else:

        # Si faltan datos críticos,
        # no se infla el riesgo.
        riesgo = (
            riesgo_bruto
            / 90
            * 100
        )

    riesgo = round(
        clamp(riesgo),
        1,
    )

    nivel = nivel_desde_riesgo(
        riesgo
    )

    # --------------------------------------------------------
    # Calidad de datos
    # --------------------------------------------------------

    calidad = 0

    if len(precipitacion) >= 120:
        calidad += 25
    elif len(precipitacion) >= 48:
        calidad += 20
    elif precipitacion:
        calidad += 12

    if humedad is not None:
        calidad += 25

    if viento:
        calidad += 10

    if runoff_disponible:
        calidad += 10

    if estacion_hidro is not None:
        calidad += 20

    # Cobertura temporal
    if (
        precipitacion
        and len(precipitacion) >= 72
    ):
        calidad += 10

    calidad = int(
        clamp(
            calidad,
            0,
            100,
        )
    )

    if calidad >= 85:
        calidad_categoria = "Alta"

    elif calidad >= 65:
        calidad_categoria = "Media"

    else:
        calidad_categoria = "Baja"

    # --------------------------------------------------------
    # Último dato meteorológico
    # --------------------------------------------------------

    ultimos = []

    for serie in [
        precipitacion,
        viento,
    ]:

        ultimo = ultimo_dato(
            serie
        )

        if ultimo:
            ultimos.append(
                ultimo
            )

    ultimo_meteo = (
        max(ultimos)
        if ultimos
        else None
    )

    return {
        "nodo": nombre,
        "provincia": nodo[
            "provincia"
        ],
        "lat": nodo["lat"],
        "lon": nodo["lon"],

        # Meteo
        "lluvia_24h": lluvia_24,
        "lluvia_72h": lluvia_72,
        "lluvia_futura_24h": lluvia_futura_24,
        "lluvia_futura_72h": lluvia_futura_72,
        "lluvia_futura_7d": lluvia_futura_7d,

        "humedad_suelo": humedad,

        "runoff_72h": runoff_72,
        "runoff_futuro_72h": runoff_futuro_72,
        "runoff_indicador": runoff_indicador,

        "rafaga_72h": rafaga_72,
        "rafaga_futura_72h": rafaga_futura_72,

        # Hidrología
        "estacion_hidrologica": estacion_hidro_nombre,
        "rio": rio_hidro,
        "distancia_hidrologica_km": distancia_hidro,
        "altura_hidrometrica": altura,
        "nivel_alerta_hidrologica": alerta_hidro,
        "nivel_evacuacion_hidrologica": evacuacion_hidro,
        "tendencia_hidrologica_24h": tendencia_hidro_24,
        "tendencia_hidrologica_72h": tendencia_hidro_72,
        "fecha_dato_hidrologico": fecha_hidro,

        # Scores
        "score_lluvia_24": score_24,
        "score_lluvia_72": score_72,
        "score_humedad": score_hum,
        "score_runoff": score_run,
        "score_hidrologia": score_hidro,
        "score_vulnerabilidad": score_vuln,

        # Resultado
        "riesgo": riesgo,
        "nivel": nivel,
        "calidad": calidad,
        "calidad_categoria": calidad_categoria,

        "ultimo_dato_meteo": ultimo_meteo,
    }


# ============================================================
# TELEGRAM
# ============================================================

def telegram_configurado():

    try:

        token = st.secrets.get(
            "TELEGRAM_BOT_TOKEN"
        )

        chat_id = st.secrets.get(
            "TELEGRAM_CHAT_ID"
        )

        return bool(
            token
            and chat_id
        )

    except Exception:
        return False


def enviar_telegram(resultados):

    try:

        token = st.secrets.get(
            "TELEGRAM_BOT_TOKEN"
        )

        chat_id = st.secrets.get(
            "TELEGRAM_CHAT_ID"
        )

        if not token or not chat_id:
            return False, (
                "Telegram no configurado."
            )

        altos = [
            r
            for r in resultados
            if r["riesgo"] >= 50
        ]

        if not altos:

            mensaje = (
                "🟢 ALERTA LITORAL AGRO V3.3\n\n"
                "No se detectan nodos con riesgo "
                "integrado alto o muy alto.\n\n"
                f"Actualización: "
                f"{ahora().strftime('%d/%m/%Y %H:%M')}"
            )

        else:

            lineas = [
                "⚠️ ALERTA LITORAL AGRO V3.3",
                "",
            ]

            for r in sorted(
                altos,
                key=lambda x: x["riesgo"],
                reverse=True,
            )[:8]:

                nivel = NIVELES[
                    r["nivel"]
                ]

                lineas.append(
                    f"{nivel['emoji']} "
                    f"{r['nodo']}: "
                    f"{r['riesgo']:.0f}/100"
                )

                if r[
                    "altura_hidrometrica"
                ] is not None:

                    lineas.append(
                        "   🌊 "
                        f"{r['estacion_hidrologica']}: "
                        f"{r['altura_hidrometrica']:.2f} m"
                    )

            lineas.append("")
            lineas.append(
                "Actualización: "
                + ahora().strftime(
                    "%d/%m/%Y %H:%M"
                )
            )

            mensaje = "\n".join(
                lineas
            )

        url = (
            f"https://api.telegram.org/bot"
            f"{token}/sendMessage"
        )

        response = requests.post(
            url,
            json={
                "chat_id": chat_id,
                "text": mensaje,
            },
            timeout=20,
        )

        if response.ok:
            return True, (
                "Mensaje enviado correctamente."
            )

        return False, (
            f"Telegram respondió "
            f"{response.status_code}."
        )

    except Exception as exc:

        return False, (
            f"Error Telegram: {exc}"
        )


# ============================================================
# MAPA
# ============================================================
#
# IMPORTANTE:
#
# NO usamos:
#   - OpenStreetMap
#   - CartoDB
#   - Mapbox
#   - Google Maps
#
# Esto evita problemas de tiles/API key.
#
# Se utiliza una base territorial esquemática
# completamente embebida.
# ============================================================

def crear_mapa(resultados):

    mapa = folium.Map(
        location=[
            -31.0,
            -59.5,
        ],
        zoom_start=6,
        tiles=None,
        control_scale=True,
        zoom_control=True,
        prefer_canvas=True,
    )

    # --------------------------------------------------------
    # Área de monitoreo
    # --------------------------------------------------------

    folium.Rectangle(
        bounds=[
            [-34.6, -63.5],
            [-26.3, -55.0],
        ],
        color="#777777",
        weight=2,
        fill=True,
        fill_opacity=0.04,
        tooltip="Área general de monitoreo",
    ).add_to(
        mapa
    )

    # --------------------------------------------------------
    # Grilla geográfica
    # --------------------------------------------------------

    for lat in range(
        -34,
        -26,
        1,
    ):

        folium.PolyLine(
            [
                (lat, -63.5),
                (lat, -55.0),
            ],
            color="#d0d0d0",
            weight=1,
            opacity=0.45,
        ).add_to(
            mapa
        )

    for lon in range(
        -63,
        -54,
        1,
    ):

        folium.PolyLine(
            [
                (-34.6, lon),
                (-26.3, lon),
            ],
            color="#d0d0d0",
            weight=1,
            opacity=0.45,
        ).add_to(
            mapa
        )

    # --------------------------------------------------------
    # Etiquetas territoriales
    # --------------------------------------------------------

    etiquetas = [
        (
            -31.3,
            -61.2,
            "SANTA FE",
        ),
        (
            -32.1,
            -59.3,
            "ENTRE RÍOS",
        ),
        (
            -28.7,
            -57.5,
            "CORRIENTES",
        ),
    ]

    for lat, lon, texto in etiquetas:

        folium.Marker(
            [lat, lon],
            icon=DivIcon(
                html=(
                    '<div style="'
                    'font-size:15px;'
                    'font-weight:700;'
                    'color:#555;'
                    'text-shadow:1px 1px 2px white;'
                    'white-space:nowrap;'
                    '">'
                    f"{texto}"
                    "</div>"
                )
            ),
        ).add_to(
            mapa
        )

    # --------------------------------------------------------
    # Encabezado
    # --------------------------------------------------------

    titulo = """
    <div style="
        position: fixed;
        top: 10px;
        left: 55px;
        z-index: 9999;
        background: rgba(255,255,255,0.94);
        padding: 9px 13px;
        border-radius: 7px;
        box-shadow: 0 1px 6px rgba(0,0,0,.25);
        font-family: Arial;
        font-size: 13px;
    ">
        <b>Alerta Litoral Agro V3.3</b><br>
        Mapa territorial sin tiles externos
    </div>
    """

    mapa.get_root().html.add_child(
        folium.Element(titulo)
    )

    # --------------------------------------------------------
    # Estaciones meteorológicas / nodos
    # --------------------------------------------------------

    for r in resultados:

        nivel = NIVELES[
            r["nivel"]
        ]

        popup_html = f"""
        <div style="font-family:Arial; min-width:250px;">
            <h4 style="margin-bottom:8px;">
                {nivel['emoji']} {r['nodo']}
            </h4>

            <b>Riesgo:</b>
            {r['riesgo']:.0f}/100
            ({nivel['texto']})<br>

            <b>Calidad:</b>
            {r['calidad']}/100
            ({r['calidad_categoria']})<br>

            <hr>

            <b>🌧️ Lluvia 24h:</b>
            {r['lluvia_24h']:.1f} mm<br>

            <b>🌧️ Lluvia 72h:</b>
            {r['lluvia_72h']:.1f} mm<br>

            <b>💧 Humedad:</b>
            {
                f"{r['humedad_suelo']:.0f}%"
                if r['humedad_suelo'] is not None
                else "N/D"
            }<br>

            <hr>

            <b>🌊 Estación:</b>
            {
                r['estacion_hidrologica']
                if r['estacion_hidrologica']
                else "N/D"
            }<br>

            <b>Río:</b>
            {
                r['rio']
                if r['rio']
                else "N/D"
            }<br>

            <b>Altura:</b>
            {
                f"{r['altura_hidrometrica']:.2f} m"
                if r['altura_hidrometrica'] is not None
                else "N/D"
            }<br>

            <b>Tendencia 24h:</b>
            {
                f"{r['tendencia_hidrologica_24h']:+.2f} m"
                if r['tendencia_hidrologica_24h'] is not None
                else "N/D"
            }<br>

            <hr>

            <b>Fuente:</b><br>
            INA / DSIyAH
        </div>
        """

        folium.CircleMarker(
            location=[
                r["lat"],
                r["lon"],
            ],
            radius=9,
            color=nivel["color"],
            fill=True,
            fill_color=nivel["color"],
            fill_opacity=0.85,
            weight=2,
            popup=folium.Popup(
                popup_html,
                max_width=350,
            ),
            tooltip=(
                f"{nivel['emoji']} "
                f"{r['nodo']} — "
                f"{r['riesgo']:.0f}/100"
            ),
        ).add_to(
            mapa
        )

    # --------------------------------------------------------
    # Estaciones hidrométricas
    # --------------------------------------------------------

    estaciones_mostradas = set()

    for r in resultados:

        nombre = r[
            "estacion_hidrologica"
        ]

        if not nombre:
            continue

        if nombre in estaciones_mostradas:
            continue

        estaciones_mostradas.add(
            nombre
        )

        lat = None
        lon = None

        # Buscamos la estación mediante
        # los resultados ya vinculados.

        for rr in resultados:

            if (
                rr[
                    "estacion_hidrologica"
                ]
                == nombre
            ):

                # La estación no necesariamente
                # coincide exactamente con el nodo.
                # Usamos el nodo como representación
                # secundaria si no tenemos coordenadas.
                lat = rr["lat"]
                lon = rr["lon"]
                break

        if lat is None:
            continue

        folium.CircleMarker(
            location=[
                lat,
                lon,
            ],
            radius=5,
            color="#1f78b4",
            fill=True,
            fill_color="#1f78b4",
            fill_opacity=0.8,
            weight=1,
            tooltip=(
                "🌊 "
                + nombre
            ),
        ).add_to(
            mapa
        )

    return mapa


# ============================================================
# HISTORIAL
# ============================================================

def guardar_historial(resultados):

    if "historial" not in st.session_state:
        st.session_state.historial = []

    for r in resultados:

        st.session_state.historial.append(
            {
                "fecha": ahora().strftime(
                    "%Y-%m-%d %H:%M"
                ),
                "nodo": r["nodo"],
                "provincia": r["provincia"],
                "riesgo": r["riesgo"],
                "nivel": r["nivel"],
                "calidad": r["calidad"],
                "lluvia_24h": r["lluvia_24h"],
                "lluvia_72h": r["lluvia_72h"],
                "humedad": r["humedad_suelo"],
                "altura_hidrometrica": r[
                    "altura_hidrometrica"
                ],
                "tendencia_hidrologica_24h": r[
                    "tendencia_hidrologica_24h"
                ],
            }
        )


# ============================================================
# DATAFRAME
# ============================================================

def resultados_dataframe(resultados):

    filas = []

    for r in resultados:

        filas.append(
            {
                "Nodo": r["nodo"],
                "Provincia": r["provincia"],
                "Riesgo": round(
                    r["riesgo"],
                    1,
                ),
                "Nivel": r["nivel"],
                "Calidad": r["calidad"],
                "Calidad categoría": r[
                    "calidad_categoria"
                ],
                "Lluvia 24h mm": round(
                    r["lluvia_24h"],
                    1,
                ),
                "Lluvia 72h mm": round(
                    r["lluvia_72h"],
                    1,
                ),
                "Pronóstico 24h mm": round(
                    r["lluvia_futura_24h"],
                    1,
                ),
                "Pronóstico 72h mm": round(
                    r["lluvia_futura_72h"],
                    1,
                ),
                "Humedad suelo %": (
                    round(
                        r["humedad_suelo"],
                        1,
                    )
                    if r["humedad_suelo"]
                    is not None
                    else None
                ),
                "Runoff 72h mm": round(
                    r["runoff_72h"],
                    1,
                ),
                "Estación hidrológica": r[
                    "estacion_hidrologica"
                ],
                "Río": r["rio"],
                "Altura río m": (
                    round(
                        r[
                            "altura_hidrometrica"
                        ],
                        2,
                    )
                    if r[
                        "altura_hidrometrica"
                    ]
                    is not None
                    else None
                ),
                "Tendencia río 24h m": (
                    round(
                        r[
                            "tendencia_hidrologica_24h"
                        ],
                        2,
                    )
                    if r[
                        "tendencia_hidrologica_24h"
                    ]
                    is not None
                    else None
                ),
                "Distancia estación km": (
                    round(
                        r[
                            "distancia_hidrologica_km"
                        ],
                        1,
                    )
                    if r[
                        "distancia_hidrologica_km"
                    ]
                    is not None
                    else None
                ),
            }
        )

    return pd.DataFrame(
        filas
    )


# ============================================================
# CONFIGURACIÓN STREAMLIT
# ============================================================

st.set_page_config(
    page_title="Alerta Litoral Agro",
    page_icon="🌧️",
    layout="wide",
)


# ============================================================
# ENCABEZADO
# ============================================================

st.title(
    "🌧️ Alerta Litoral Agro"
)

st.caption(
    f"V{VERSION} — "
    "Monitoreo meteorológico, hidrológico "
    "y territorial para riesgo de anegamiento"
)


# ============================================================
# SIDEBAR
# ============================================================

with st.sidebar:

    st.header(
        "⚙️ Sistema"
    )

    st.write(
        f"Versión: **V{VERSION}**"
    )

    st.write(
        "Zona: **Santa Fe · Corrientes · Entre Ríos**"
    )

    st.divider()

    st.subheader(
        "Fuentes"
    )

    st.write(
        "🌧️ Open-Meteo"
    )

    st.write(
        "🌊 INA / DSIyAH"
    )

    st.write(
        "🗺️ Base territorial embebida"
    )

    st.caption(
        "El mapa no utiliza OpenStreetMap, "
        "CartoDB, Mapbox ni API keys de mapas."
    )

    st.divider()

    if st.button(
        "🔄 Actualizar datos",
        use_container_width=True,
    ):

        consultar_open_meteo.clear()
        obtener_estaciones_ina.clear()
        obtener_datos_hidrologicos.clear()

        st.rerun()

    st.divider()

    st.subheader(
        "Telegram"
    )

    if telegram_configurado():

        st.success(
            "Telegram configurado"
        )

    else:

        st.info(
            "Telegram opcional"
        )

        st.caption(
            "Configurar TELEGRAM_BOT_TOKEN "
            "y TELEGRAM_CHAT_ID en Secrets."
        )


# ============================================================
# OBTENER DATOS
# ============================================================

try:

    with st.spinner(
        "Consultando datos meteorológicos..."
    ):

        meteorologia = (
            consultar_open_meteo()
        )

except Exception as exc:

    st.error(
        "No fue posible obtener "
        "datos meteorológicos."
    )

    st.exception(exc)

    st.stop()


modo = meteorologia[
    "modo"
]

datos_meteo = meteorologia[
    "datos"
]

fuente_meteo = meteorologia[
    "fuente"
]


# ============================================================
# DATOS HIDROLÓGICOS
# ============================================================

try:

    with st.spinner(
        "Consultando estaciones hidrométricas oficiales..."
    ):

        estaciones_hidro = (
            obtener_datos_hidrologicos()
        )

    hidrologia_ok = bool(
        estaciones_hidro
    )

except Exception as exc:

    estaciones_hidro = []

    hidrologia_ok = False

    st.warning(
        "La fuente hidrológica oficial "
        "no respondió en esta actualización."
    )

    st.caption(
        f"Detalle: {exc}"
    )


# ============================================================
# EVALUACIÓN
# ============================================================

resultados = []

for i, nombre in enumerate(
    NODOS.keys()
):

    estacion = (
        estacion_hidrologica_mas_cercana(
            nombre,
            estaciones_hidro,
        )
        if estaciones_hidro
        else None
    )

    resultado = evaluar_nodo(
        nombre,
        datos_meteo[i],
        modo,
        estacion,
    )

    resultados.append(
        resultado
    )


# ============================================================
# HISTORIAL
# ============================================================

guardar_historial(
    resultados
)


# ============================================================
# INDICADORES GENERALES
# ============================================================

riesgo_promedio = (
    sum(
        r["riesgo"]
        for r in resultados
    )
    / len(resultados)
)

calidad_promedio = (
    sum(
        r["calidad"]
        for r in resultados
    )
    / len(resultados)
)

riesgo_maximo = max(
    r["riesgo"]
    for r in resultados
)

nodos_altos = sum(
    1
    for r in resultados
    if r["riesgo"] >= 50
)

nodos_rojos = sum(
    1
    for r in resultados
    if r["riesgo"] >= 75
)


# ============================================================
# ESTADO DE FUENTES
# ============================================================

col1, col2, col3, col4 = st.columns(4)

with col1:

    st.metric(
        "Riesgo promedio",
        f"{riesgo_promedio:.0f}/100",
    )

with col2:

    st.metric(
        "Riesgo máximo",
        f"{riesgo_maximo:.0f}/100",
    )

with col3:

    st.metric(
        "Calidad promedio",
        f"{calidad_promedio:.0f}/100",
    )

with col4:

    st.metric(
        "Estaciones hidrológicas",
        len(estaciones_hidro),
    )


st.divider()


# ============================================================
# FUENTES
# ============================================================

col1, col2 = st.columns(2)

with col1:

    st.success(
        f"🌧️ Meteorología: {fuente_meteo}"
    )

with col2:

    if hidrologia_ok:

        st.success(
            "🌊 Hidrología: INA / DSIyAH"
        )

    else:

        st.warning(
            "🌊 Hidrología: sin datos en esta actualización"
        )


st.caption(
    "La calidad de datos indica la disponibilidad "
    "y completitud de las fuentes utilizadas. "
    "No representa la intensidad del riesgo."
)


# ============================================================
# ALERTA GENERAL
# ============================================================

if nodos_rojos > 0:

    st.error(
        f"🔴 {nodos_rojos} nodo(s) "
        "presentan riesgo muy alto."
    )

elif nodos_altos > 0:

    st.warning(
        f"🟠 {nodos_altos} nodo(s) "
        "presentan riesgo alto o muy alto."
    )

else:

    st.success(
        "🟢 No se detectan nodos "
        "con riesgo integrado alto."
    )


# ============================================================
# TABLA PRINCIPAL
# ============================================================

st.subheader(
    "📊 Situación por nodo"
)

df = resultados_dataframe(
    resultados
)

st.dataframe(
    df,
    use_container_width=True,
    hide_index=True,
)


# ============================================================
# DETALLE POR NODO
# ============================================================

st.subheader(
    "🔎 Análisis detallado"
)

for r in sorted(
    resultados,
    key=lambda x: x["riesgo"],
    reverse=True,
):

    nivel = NIVELES[
        r["nivel"]
    ]

    with st.expander(
        f"{nivel['emoji']} "
        f"{r['nodo']} — "
        f"{r['riesgo']:.0f}/100 "
        f"({nivel['texto']})"
    ):

        c1, c2, c3, c4 = st.columns(4)

        with c1:

            st.metric(
                "Riesgo",
                f"{r['riesgo']:.0f}/100",
            )

        with c2:

            st.metric(
                "Calidad",
                f"{r['calidad']}/100",
            )

        with c3:

            st.metric(
                "Lluvia 72h",
                f"{r['lluvia_72h']:.1f} mm",
            )

        with c4:

            if r[
                "altura_hidrometrica"
            ] is not None:

                st.metric(
                    "Altura río",
                    f"{r['altura_hidrometrica']:.2f} m",
                )

            else:

                st.metric(
                    "Altura río",
                    "N/D",
                )

        st.write(
            f"**Acción sugerida:** "
            f"{ACCIONES[r['nivel']]}"
        )

        st.write(
            "### 🌧️ Meteorología"
        )

        meteo_cols = st.columns(5)

        with meteo_cols[0]:
            st.metric(
                "Lluvia 24h",
                f"{r['lluvia_24h']:.1f} mm",
            )

        with meteo_cols[1]:
            st.metric(
                "Lluvia 72h",
                f"{r['lluvia_72h']:.1f} mm",
            )

        with meteo_cols[2]:
            st.metric(
                "Pronóstico 72h",
                f"{r['lluvia_futura_72h']:.1f} mm",
            )

        with meteo_cols[3]:

            if r[
                "humedad_suelo"
            ] is not None:

                st.metric(
                    "Humedad suelo",
                    f"{r['humedad_suelo']:.0f}%",
                )

            else:

                st.metric(
                    "Humedad suelo",
                    "N/D",
                )

        with meteo_cols[4]:

            if r[
                "runoff_indicador"
            ] is not None:

                st.metric(
                    "Escorrentía estimada",
                    f"{r['runoff_indicador']:.1f} mm",
                )

            else:

                st.metric(
                    "Escorrentía estimada",
                    "N/D",
                )

        st.write(
            "### 🌊 Hidrología oficial"
        )

        if r[
            "estacion_hidrologica"
        ]:

            st.write(
                f"**Estación:** "
                f"{r['estacion_hidrologica']}"
            )

            st.write(
                f"**Río:** "
                f"{r['rio'] or 'N/D'}"
            )

            st.write(
                f"**Distancia al nodo:** "
                f"{r['distancia_hidrologica_km']:.1f} km"
            )

            if r[
                "altura_hidrometrica"
            ] is not None:

                st.write(
                    f"**Altura hidrométrica:** "
                    f"{r['altura_hidrometrica']:.2f} m"
                )

            if r[
                "tendencia_hidrologica_24h"
            ] is not None:

                tendencia = r[
                    "tendencia_hidrologica_24h"
                ]

                if tendencia > 0:
                    texto_tendencia = (
                        f"ascenso de "
                        f"{tendencia:+.2f} m"
                    )

                elif tendencia < 0:
                    texto_tendencia = (
                        f"descenso de "
                        f"{tendencia:+.2f} m"
                    )

                else:
                    texto_tendencia = (
                        "estable"
                    )

                st.write(
                    f"**Tendencia 24h:** "
                    f"{texto_tendencia}"
                )

            if r[
                "nivel_alerta_hidrologica"
            ] is not None:

                st.write(
                    f"**Nivel de alerta publicado "
                    f"por la estación:** "
                    f"{r['nivel_alerta_hidrologica']:.2f} m"
                )

            if r[
                "nivel_evacuacion_hidrologica"
            ] is not None:

                st.write(
                    f"**Nivel de evacuación publicado "
                    f"por la estación:** "
                    f"{r['nivel_evacuacion_hidrologica']:.2f} m"
                )

            if r[
                "fecha_dato_hidrologico"
            ]:

                st.caption(
                    "Último dato hidrológico: "
                    + r[
                        "fecha_dato_hidrologico"
                    ].strftime(
                        "%d/%m/%Y %H:%M"
                    )
                )

        else:

            st.info(
                "No se encontró una estación "
                "hidrométrica cercana con datos "
                "disponibles en esta actualización."
            )

        st.write(
            "### 🧮 Componentes del índice"
        )

        componentes = pd.DataFrame(
            [
                {
                    "Componente": "Lluvia antecedente 24h",
                    "Puntaje": round(
                        r["score_lluvia_24"],
                        1,
                    ),
                },
                {
                    "Componente": "Lluvia antecedente 72h",
                    "Puntaje": round(
                        r["score_lluvia_72"],
                        1,
                    ),
                },
                {
                    "Componente": "Humedad del suelo",
                    "Puntaje": round(
                        r["score_humedad"],
                        1,
                    ),
                },
                {
                    "Componente": "Escorrentía modelada",
                    "Puntaje": (
                        round(
                            r["score_runoff"],
                            1,
                        )
                        if r[
                            "score_runoff"
                        ]
                        is not None
                        else None
                    ),
                },
                {
                    "Componente": "Hidrología",
                    "Puntaje": round(
                        r["score_hidrologia"],
                        1,
                    ),
                },
                {
                    "Componente": "Vulnerabilidad territorial",
                    "Puntaje": round(
                        r["score_vulnerabilidad"],
                        1,
                    ),
                },
            ]
        )

        st.dataframe(
            componentes,
            use_container_width=True,
            hide_index=True,
        )


# ============================================================
# MAPA
# ============================================================

st.subheader(
    "🗺️ Mapa territorial de riesgo"
)

st.caption(
    "Base territorial embebida. "
    "Los colores representan el riesgo integrado "
    "de cada nodo. Los datos hidrológicos provienen "
    "del INA cuando están disponibles."
)

mapa = crear_mapa(
    resultados
)

st_folium(
    mapa,
    width=None,
    height=650,
    returned_objects=[],
)


# ============================================================
# ESTACIONES HIDROMÉTRICAS
# ============================================================

st.subheader(
    "🌊 Estaciones hidrométricas oficiales detectadas"
)

if estaciones_hidro:

    filas_hidro = []

    for e in estaciones_hidro:

        filas_hidro.append(
            {
                "Estación": e[
                    "nombre"
                ],
                "Río": e[
                    "rio"
                ],
                "Latitud": round(
                    e["lat"],
                    4,
                ),
                "Longitud": round(
                    e["lon"],
                    4,
                ),
                "Altura actual (m)": round(
                    e["altura"],
                    2,
                )
                if e[
                    "altura"
                ]
                is not None
                else None,
                "Tendencia 24h (m)": round(
                    e[
                        "tendencia_24h"
                    ],
                    2,
                )
                if e[
                    "tendencia_24h"
                ]
                is not None
                else None,
                "Nivel alerta (m)": (
                    round(
                        e["alerta"],
                        2,
                    )
                    if e[
                        "alerta"
                    ]
                    is not None
                    else None
                ),
                "Nivel evacuación (m)": (
                    round(
                        e[
                            "evacuacion"
                        ],
                        2,
                    )
                    if e[
                        "evacuacion"
                    ]
                    is not None
                    else None
                ),
                "Datos 7d": e[
                    "n_datos"
                ],
            }
        )

    df_hidro = pd.DataFrame(
        filas_hidro
    )

    st.dataframe(
        df_hidro,
        use_container_width=True,
        hide_index=True,
    )

else:

    st.warning(
        "No hay estaciones hidrométricas "
        "con datos disponibles en esta actualización."
    )


# ============================================================
# TELEGRAM
# ============================================================

st.subheader(
    "📲 Telegram"
)

if telegram_configurado():

    if st.button(
        "📤 Enviar estado actual a Telegram",
        use_container_width=True,
    ):

        ok, mensaje = enviar_telegram(
            resultados
        )

        if ok:
            st.success(
                mensaje
            )
        else:
            st.error(
                mensaje
            )

else:

    st.info(
        "Telegram está disponible de forma opcional. "
        "No es necesario para que funcione el sistema."
    )


# ============================================================
# HISTORIAL
# ============================================================

st.subheader(
    "📚 Historial de ejecuciones"
)

if (
    "historial"
    in st.session_state
    and st.session_state.historial
):

    df_historial = pd.DataFrame(
        st.session_state.historial
    )

    st.dataframe(
        df_historial.tail(100),
        use_container_width=True,
        hide_index=True,
    )

    csv_historial = (
        df_historial
        .to_csv(
            index=False
        )
        .encode(
            "utf-8-sig"
        )
    )

    st.download_button(
        "⬇️ Descargar historial CSV",
        data=csv_historial,
        file_name=(
            "alerta_litoral_agro_historial.csv"
        ),
        mime="text/csv",
        use_container_width=True,
    )

else:

    st.info(
        "El historial se generará durante las "
        "actualizaciones de esta sesión."
    )


# ============================================================
# DESCARGA DEL ESTADO ACTUAL
# ============================================================

st.subheader(
    "⬇️ Exportación"
)

csv_actual = (
    df.to_csv(
        index=False
    )
    .encode(
        "utf-8-sig"
    )
)

st.download_button(
    "Descargar estado actual CSV",
    data=csv_actual,
    file_name=(
        "alerta_litoral_agro_v3_3.csv"
    ),
    mime="text/csv",
    use_container_width=True,
)


# ============================================================
# METODOLOGÍA
# ============================================================

with st.expander(
    "📘 Metodología V3.3"
):

    st.markdown(
        """
### Objetivo

Alerta Litoral Agro es un prototipo de monitoreo
orientado a anticipar condiciones favorables para
anegamientos agropecuarios en Santa Fe, Corrientes
y Entre Ríos.

### 1. Meteorología

Se utilizan datos horarios de Open-Meteo:

- precipitación;
- humedad del suelo;
- ráfagas de viento;
- escorrentía superficial modelada cuando está disponible.

La escorrentía modelada no se considera una observación
hidrológica.

### 2. Hidrología

V3.3 incorpora datos oficiales del sistema hidrológico
del Instituto Nacional del Agua (INA), perteneciente al
Sistema de Información Hidrológica de la Cuenca del Plata.

Se utilizan principalmente:

- altura hidrométrica;
- tendencia de las últimas 24 horas;
- tendencia de las últimas 72 horas;
- nivel de alerta cuando la estación lo publica;
- nivel de evacuación cuando la estación lo publica.

La estación hidrométrica se selecciona automáticamente
en función de su disponibilidad y proximidad territorial
a cada nodo meteorológico.

### 3. Riesgo integrado

El índice combina:

- precipitación antecedente;
- precipitación prevista;
- humedad del suelo;
- escorrentía modelada;
- condición hidrológica;
- vulnerabilidad territorial.

El resultado final se expresa entre 0 y 100.

### 4. Calidad de datos

La calidad es independiente del riesgo.

Una situación puede tener:

Riesgo alto + calidad alta

o:

Riesgo alto + calidad baja.

En el segundo caso el sistema detecta señales
importantes, pero recomienda mayor cautela en
la interpretación.

### 5. Hidrología y alerta

Los datos hidrológicos oficiales pueden contener
registros a tiempo útil sin validar.

Por lo tanto:

**Alerta Litoral Agro NO reemplaza una alerta oficial
de inundación.**

El sistema funciona como herramienta experimental
de integración y monitoreo.

### 6. Mapa

El mapa de V3.3 no depende de:

- OpenStreetMap;
- CartoDB;
- Mapbox;
- Google Maps;
- API keys cartográficas.

La base territorial es esquemática y se utiliza
principalmente para representar los nodos y su
distribución espacial.

### 7. Interpretación

🟢 0–24: Bajo

🟡 25–49: Vigilancia

🟠 50–74: Alto

🔴 75–100: Muy alto
"""
    )


# ============================================================
# FUENTES
# ============================================================

st.divider()

st.caption(
    "Fuentes principales: "
    "Open-Meteo para variables meteorológicas; "
    "INA / DSIyAH para información hidrológica oficial. "
    f"Alerta Litoral Agro V{VERSION}."
)

st.caption(
    "Última actualización de la aplicación: "
    + ahora().strftime(
        "%d/%m/%Y %H:%M:%S"
    )
)
