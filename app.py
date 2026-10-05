import os
import math
import requests
import pandas as pd
import folium
import streamlit as st

from streamlit_folium import st_folium
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


# ============================================================
# ALERTA LITORAL AGRO — V3.0
# ============================================================
#
# Sistema experimental de apoyo a la toma de decisiones para
# riesgo de anegamiento agropecuario.
#
# Área:
#   Santa Fe
#   Corrientes
#   Entre Ríos
#
# COMPONENTES DEL ÍNDICE 0–100:
#
#   Meteorología       35 %
#   Suelo              25 %
#   Hidrología         25 %
#   Vulnerabilidad     15 %
#
# FUENTES:
#   - Open-Meteo
#   - INA / DSIyAH
#   - OpenStreetMap
#   - Esri
#   - Windy
#   - CIRA / GOES
#   - Telegram Bot API
#
# IMPORTANTE:
#
# Open-Meteo aporta variables meteorológicas MODELADAS.
#
# La capa INA utiliza las últimas lecturas hidrométricas
# publicadas por el sistema DSIyAH.
#
# El índice 0–100 es EXPERIMENTAL.
# No representa una probabilidad estadística de inundación.
# No reemplaza alertas oficiales.
#
# ============================================================


# ============================================================
# CONFIGURACIÓN
# ============================================================

st.set_page_config(
    page_title="Alerta Litoral Agro",
    page_icon="🌧️",
    layout="wide",
    initial_sidebar_state="expanded",
)


TZ_ARG = ZoneInfo("America/Argentina/Buenos_Aires")

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"

INA_WFS_URL = "https://alerta.ina.gob.ar/geoserver/public2/ows"

TELEGRAM_API = "https://api.telegram.org"


# ============================================================
# NODOS
# ============================================================

NODOS = [

    # -------------------------
    # CORRIENTES
    # -------------------------

    {
        "nombre": "Goya",
        "provincia": "Corrientes",
        "lat": -29.14,
        "lon": -59.26,
        "rio": "Río Paraná",
        "zona_alta": "Loma Batelito",
        "vulnerabilidad": 78,
        "tipo_territorio": "Bajos fluviales y sectores productivos próximos al Paraná",
    },

    {
        "nombre": "Mercedes",
        "provincia": "Corrientes",
        "lat": -29.18,
        "lon": -58.07,
        "rio": "Sistema Iberá",
        "zona_alta": "Lomadas de Mercedes",
        "vulnerabilidad": 82,
        "tipo_territorio": "Entorno de humedales y bajos naturales",
    },

    {
        "nombre": "Curuzú Cuatiá",
        "provincia": "Corrientes",
        "lat": -29.79,
        "lon": -58.05,
        "rio": "Arroyo Sarandí",
        "zona_alta": "Sierras de Curuzú",
        "vulnerabilidad": 62,
        "tipo_territorio": "Cuencas interiores y sectores rurales",
    },

    {
        "nombre": "Paso de los Libres",
        "provincia": "Corrientes",
        "lat": -29.71,
        "lon": -57.08,
        "rio": "Río Uruguay",
        "zona_alta": "Zona alta de Paso de los Libres",
        "vulnerabilidad": 70,
        "tipo_territorio": "Área ribereña del Uruguay",
    },

    {
        "nombre": "Santo Tomé",
        "provincia": "Corrientes",
        "lat": -28.55,
        "lon": -56.04,
        "rio": "Río Uruguay",
        "zona_alta": "Loma Alta Santo Tomé",
        "vulnerabilidad": 68,
        "tipo_territorio": "Zona ribereña y sectores rurales bajos",
    },

    {
        "nombre": "Corrientes Capital",
        "provincia": "Corrientes",
        "lat": -27.46,
        "lon": -58.83,
        "rio": "Río Paraná",
        "zona_alta": "Sectores altos de Corrientes",
        "vulnerabilidad": 72,
        "tipo_territorio": "Área urbana y sectores bajos próximos al Paraná",
    },


    # -------------------------
    # SANTA FE
    # -------------------------

    {
        "nombre": "Reconquista",
        "provincia": "Santa Fe",
        "lat": -29.15,
        "lon": -59.65,
        "rio": "Río Paraná",
        "zona_alta": "Loma Alta Reconquista Oeste",
        "vulnerabilidad": 88,
        "tipo_territorio": "Bajos y áreas productivas vinculadas al valle de inundación",
    },

    {
        "nombre": "San Javier",
        "provincia": "Santa Fe",
        "lat": -30.58,
        "lon": -59.93,
        "rio": "Río San Javier",
        "zona_alta": "Sectores altos de la zona",
        "vulnerabilidad": 92,
        "tipo_territorio": "Valle de inundación y sectores bajos del Paraná",
    },

    {
        "nombre": "Vera",
        "provincia": "Santa Fe",
        "lat": -29.46,
        "lon": -60.21,
        "rio": "Cuenca Calchaquí",
        "zona_alta": "Cuchilla Fortín Olmos",
        "vulnerabilidad": 76,
        "tipo_territorio": "Cuencas interiores y áreas rurales",
    },

    {
        "nombre": "Santa Fe Capital",
        "provincia": "Santa Fe",
        "lat": -31.63,
        "lon": -60.70,
        "rio": "Río Salado / Paraná",
        "zona_alta": "Sectores altos del Litoral Centro",
        "vulnerabilidad": 95,
        "tipo_territorio": "Área urbana y confluencia de sistemas fluviales",
    },

    {
        "nombre": "Rosario",
        "provincia": "Santa Fe",
        "lat": -32.95,
        "lon": -60.66,
        "rio": "Río Paraná",
        "zona_alta": "Zonas altas del cordón industrial",
        "vulnerabilidad": 70,
        "tipo_territorio": "Área urbana e industrial próxima al Paraná",
    },

    {
        "nombre": "Tostado",
        "provincia": "Santa Fe",
        "lat": -29.23,
        "lon": -61.77,
        "rio": "Río Salado Norte",
        "zona_alta": "Lomadas de Tostado",
        "vulnerabilidad": 84,
        "tipo_territorio": "Cuenca interior con riesgo de anegamiento rural",
    },


    # -------------------------
    # ENTRE RÍOS
    # -------------------------

    {
        "nombre": "Concordia",
        "provincia": "Entre Ríos",
        "lat": -31.39,
        "lon": -58.02,
        "rio": "Río Uruguay",
        "zona_alta": "Lomas de Salto Grande",
        "vulnerabilidad": 88,
        "tipo_territorio": "Área ribereña del Uruguay",
    },

    {
        "nombre": "La Paz",
        "provincia": "Entre Ríos",
        "lat": -30.74,
        "lon": -59.64,
        "rio": "Río Paraná",
        "zona_alta": "Cuchilla Montiel",
        "vulnerabilidad": 82,
        "tipo_territorio": "Sectores bajos vinculados al Paraná",
    },

    {
        "nombre": "Victoria",
        "provincia": "Entre Ríos",
        "lat": -32.62,
        "lon": -60.15,
        "rio": "Delta del Paraná",
        "zona_alta": "Cuchilla Victoria",
        "vulnerabilidad": 96,
        "tipo_territorio": "Delta y humedales del Paraná",
    },

    {
        "nombre": "Gualeguay",
        "provincia": "Entre Ríos",
        "lat": -33.14,
        "lon": -59.31,
        "rio": "Río Gualeguay",
        "zona_alta": "Cuchilla de Gualeguay",
        "vulnerabilidad": 83,
        "tipo_territorio": "Cuenca rural y sectores bajos",
    },

    {
        "nombre": "Gualeguaychú",
        "provincia": "Entre Ríos",
        "lat": -33.01,
        "lon": -58.51,
        "rio": "Río Gualeguaychú",
        "zona_alta": "Lomas de Gualeguaychú",
        "vulnerabilidad": 81,
        "tipo_territorio": "Cuenca fluvial y sectores productivos",
    },

    {
        "nombre": "Paraná",
        "provincia": "Entre Ríos",
        "lat": -31.73,
        "lon": -60.52,
        "rio": "Río Paraná",
        "zona_alta": "Lomas de Paraná",
        "vulnerabilidad": 73,
        "tipo_territorio": "Barrancas y sectores bajos próximos al Paraná",
    },
]


# ============================================================
# NIVELES DE RIESGO
# ============================================================

NIVELES = {

    "BAJO": {
        "emoji": "🟢",
        "color": "#28a745",
        "min": 0,
        "max": 20,
    },

    "MODERADO": {
        "emoji": "🟡",
        "color": "#d99b00",
        "min": 21,
        "max": 40,
    },

    "ALTO": {
        "emoji": "🟠",
        "color": "#fd7e14",
        "min": 41,
        "max": 60,
    },

    "MUY ALTO": {
        "emoji": "🔴",
        "color": "#dc3545",
        "min": 61,
        "max": 80,
    },

    "CRÍTICO": {
        "emoji": "🟣",
        "color": "#7b2cbf",
        "min": 81,
        "max": 100,
    },
}


# ============================================================
# FUNCIONES BÁSICAS
# ============================================================

def valor_seguro(valor, default=None):

    try:

        if valor is None:
            return default

        if isinstance(valor, str):
            valor = valor.replace(",", ".")

        return float(valor)

    except Exception:

        return default


def nivel_riesgo(score):

    score = max(
        0,
        min(
            100,
            float(score),
        ),
    )

    for nombre, datos in NIVELES.items():

        if (
            datos["min"]
            <= score
            <= datos["max"]
        ):
            return nombre

    return "CRÍTICO"


def distancia_km(
    lat1,
    lon1,
    lat2,
    lon2,
):

    radio = 6371.0

    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)

    dlat = math.radians(
        lat2 - lat1
    )

    dlon = math.radians(
        lon2 - lon1
    )

    a = (
        math.sin(dlat / 2) ** 2
        +
        math.cos(lat1_rad)
        * math.cos(lat2_rad)
        * math.sin(dlon / 2) ** 2
    )

    return (
        2
        * radio
        * math.asin(
            math.sqrt(a)
        )
    )


# ============================================================
# TELEGRAM
# ============================================================

def obtener_secret(nombre):

    try:

        valor = st.secrets.get(nombre)

        if valor:
            return valor

    except Exception:
        pass

    return os.getenv(nombre)


def telegram_configurado():

    return bool(
        obtener_secret(
            "TELEGRAM_BOT_TOKEN"
        )
        and
        obtener_secret(
            "TELEGRAM_CHAT_ID"
        )
    )


def enviar_telegram(mensaje):

    token = obtener_secret(
        "TELEGRAM_BOT_TOKEN"
    )

    chat_id = obtener_secret(
        "TELEGRAM_CHAT_ID"
    )

    if not token or not chat_id:

        return (
            False,
            "Telegram no configurado."
        )

    url = (
        f"{TELEGRAM_API}"
        f"/bot{token}"
        f"/sendMessage"
    )

    payload = {
        "chat_id": chat_id,
        "text": mensaje,
        "disable_web_page_preview": True,
    }

    try:

        response = requests.post(
            url,
            json=payload,
            timeout=15,
        )

        response.raise_for_status()

        return (
            True,
            "Mensaje enviado correctamente."
        )

    except Exception as exc:

        return (
            False,
            str(exc)
        )


# ============================================================
# OPEN-METEO
# ============================================================

@st.cache_data(
    ttl=900,
    show_spinner=False,
)
def consultar_open_meteo():

    latitudes = ",".join(
        str(n["lat"])
        for n in NODOS
    )

    longitudes = ",".join(
        str(n["lon"])
        for n in NODOS
    )

    # ========================================================
    # IMPORTANTE:
    #
    # NO usamos simultáneamente:
    #
    # forecast_days + forecast_hours
    # past_days + past_hours
    #
    # Usamos past_days + forecast_days.
    #
    # Esto evita el error 400 de la versión anterior.
    # ========================================================

    params = {

        "latitude": latitudes,

        "longitude": longitudes,

        "hourly": (
            "precipitation,"
            "soil_moisture_0_to_7cm,"
            "soil_moisture_7_to_28cm,"
            "soil_moisture_28_to_100cm,"
            "surface_runoff,"
            "wind_gusts_10m"
        ),

        "daily": (
            "precipitation_sum,"
            "precipitation_probability_max"
        ),

        "timezone":
            "America/Argentina/Buenos_Aires",

        "past_days": 3,

        "forecast_days": 7,

        "cell_selection": "land",
    }

    response = requests.get(
        OPEN_METEO_URL,
        params=params,
        timeout=45,
        headers={
            "User-Agent":
                "Alerta-Litoral-Agro/3.0"
        },
    )

    # --------------------------------------------------------
    # Mostrar detalle real si Open-Meteo devuelve 400.
    # --------------------------------------------------------

    if response.status_code != 200:

        try:
            detalle = response.json()
        except Exception:
            detalle = response.text

        raise RuntimeError(
            "Open-Meteo respondió "
            f"{response.status_code}.\n\n"
            f"Detalle:\n{detalle}"
        )

    data = response.json()

    if isinstance(data, dict):

        data = [data]

    if len(data) != len(NODOS):

        raise RuntimeError(
            "Open-Meteo devolvió "
            f"{len(data)} ubicaciones, "
            f"pero se esperaban "
            f"{len(NODOS)}."
        )

    ahora = datetime.now(TZ_ARG)

    resultados = []


    # ========================================================
    # PROCESAR CADA NODO
    # ========================================================

    for nodo, item in zip(
        NODOS,
        data,
    ):

        hourly = item.get(
            "hourly",
            {},
        )

        daily = item.get(
            "daily",
            {},
        )

        tiempos = hourly.get(
            "time",
            [],
        )

        precipitacion = hourly.get(
            "precipitation",
            [],
        )

        humedad_0_7 = hourly.get(
            "soil_moisture_0_to_7cm",
            [],
        )

        humedad_7_28 = hourly.get(
            "soil_moisture_7_to_28cm",
            [],
        )

        humedad_28_100 = hourly.get(
            "soil_moisture_28_to_100cm",
            [],
        )

        runoff = hourly.get(
            "surface_runoff",
            [],
        )

        viento = hourly.get(
            "wind_gusts_10m",
            [],
        )


        # ====================================================
        # CREAR SERIE HORARIA
        # ====================================================

        serie = []

        cantidad = min(
            len(tiempos),
            len(precipitacion),
        )

        for i in range(cantidad):

            try:

                tiempo = datetime.fromisoformat(
                    tiempos[i]
                )

                if tiempo.tzinfo is None:

                    tiempo = tiempo.replace(
                        tzinfo=TZ_ARG
                    )

                lluvia = valor_seguro(
                    precipitacion[i],
                    0.0,
                )

                serie.append(
                    {
                        "time": tiempo,
                        "precip": lluvia,
                    }
                )

            except Exception:

                continue


        # ====================================================
        # SEPARAR PASADO / FUTURO
        # ====================================================

        pasadas = [
            x
            for x in serie
            if x["time"] <= ahora
        ]

        futuras = [
            x
            for x in serie
            if x["time"] > ahora
        ]


        # ====================================================
        # LLUVIA ÚLTIMAS 24 H
        # ====================================================

        inicio_24 = ahora - timedelta(
            hours=24
        )

        lluvia_reciente_24h = sum(
            x["precip"]
            for x in pasadas
            if x["time"] >= inicio_24
        )


        # ====================================================
        # LLUVIA ÚLTIMAS 72 H
        # ====================================================

        inicio_72 = ahora - timedelta(
            hours=72
        )

        lluvia_reciente_72h = sum(
            x["precip"]
            for x in pasadas
            if x["time"] >= inicio_72
        )


        # ====================================================
        # PRÓXIMAS 24 H
        # ====================================================

        fin_24 = ahora + timedelta(
            hours=24
        )

        lluvia_pronostico_24h = sum(
            x["precip"]
            for x in futuras
            if x["time"] <= fin_24
        )


        # ====================================================
        # PRÓXIMAS 72 H
        # ====================================================

        fin_72 = ahora + timedelta(
            hours=72
        )

        lluvia_pronostico_72h = sum(
            x["precip"]
            for x in futuras
            if x["time"] <= fin_72
        )


        # ====================================================
        # PRÓXIMOS 7 DÍAS
        # ====================================================

        fin_7d = ahora + timedelta(
            days=7
        )

        lluvia_pronostico_7d = sum(
            x["precip"]
            for x in futuras
            if x["time"] <= fin_7d
        )


        # ====================================================
        # HUMEDAD ACTUAL
        # ====================================================

        def ultimo_valor(lista):

            valores = [
                valor_seguro(v)
                for v in lista
                if valor_seguro(v) is not None
            ]

            if not valores:
                return None

            return valores[-1]


        humedad_actual = ultimo_valor(
            humedad_0_7
        )

        humedad_7_28_actual = ultimo_valor(
            humedad_7_28
        )

        humedad_28_100_actual = ultimo_valor(
            humedad_28_100
        )

        runoff_actual = ultimo_valor(
            runoff
        )

        viento_actual = ultimo_valor(
            viento
        )


        # ====================================================
        # ÍNDICE DE HUMEDAD DEL SUELO
        # ====================================================

        def humedad_a_indice(valor):

            if valor is None:
                return None

            return max(
                0,
                min(
                    100,
                    (
                        valor
                        / 0.45
                    ) * 100,
                ),
            )


        indice_0_7 = humedad_a_indice(
            humedad_actual
        )

        indice_7_28 = humedad_a_indice(
            humedad_7_28_actual
        )

        indice_28_100 = humedad_a_indice(
            humedad_28_100_actual
        )


        if indice_0_7 is None:

            score_suelo = 0.0

            humedad_indice = None

        else:

            if indice_7_28 is None:
                indice_7_28 = indice_0_7

            if indice_28_100 is None:
                indice_28_100 = indice_7_28

            humedad_indice = indice_0_7

            score_suelo = (
                indice_0_7 * 0.50
                +
                indice_7_28 * 0.30
                +
                indice_28_100 * 0.20
            )


        # ====================================================
        # SCORE METEOROLÓGICO
        # ====================================================

        score_reciente = min(
            100,
            (
                lluvia_reciente_72h
                / 120
            ) * 100,
        )

        score_futuro = min(
            100,
            (
                lluvia_pronostico_72h
                / 120
            ) * 100,
        )

        score_7d = min(
            100,
            (
                lluvia_pronostico_7d
                / 180
            ) * 100,
        )


        score_meteorologia = (
            score_reciente * 0.35
            +
            score_futuro * 0.45
            +
            score_7d * 0.20
        )


        # ====================================================
        # CALIDAD
        # ====================================================

        calidad_meteo = "🟢 DISPONIBLE"

        if not serie:

            calidad_meteo = "🔴 SIN DATOS"

        elif len(serie) < 100:

            calidad_meteo = "🟡 DATOS PARCIALES"


        # ====================================================
        # RESULTADO
        # ====================================================

        resultados.append(
            {

                **nodo,

                "lluvia_reciente_24h":
                    lluvia_reciente_24h,

                "lluvia_reciente_72h":
                    lluvia_reciente_72h,

                "lluvia_pronostico_24h":
                    lluvia_pronostico_24h,

                "lluvia_pronostico_72h":
                    lluvia_pronostico_72h,

                "lluvia_pronostico_7d":
                    lluvia_pronostico_7d,

                "humedad_volumetrica":
                    humedad_actual,

                "humedad_7_28":
                    humedad_7_28_actual,

                "humedad_28_100":
                    humedad_28_100_actual,

                "humedad_indice":
                    humedad_indice,

                "runoff":
                    runoff_actual,

                "viento_gustas":
                    viento_actual,

                "score_suelo":
                    score_suelo,

                "score_meteorologia":
                    score_meteorologia,

                "calidad_meteorologia":
                    calidad_meteo,
            }
        )


    return resultados


# ============================================================
# INA — HIDROLOGÍA
# ============================================================

@st.cache_data(
    ttl=900,
    show_spinner=False,
)
def consultar_ina():

    params = {

        "service": "WFS",

        "version": "1.0.0",

        "request": "GetFeature",

        "typeName":
            "public2:ultimas_alturas",

        "outputFormat":
            "application/json",

        "srsName":
            "EPSG:4326",
    }


    response = requests.get(
        INA_WFS_URL,
        params=params,
        timeout=40,
        headers={
            "User-Agent":
                "Alerta-Litoral-Agro/3.0"
        },
    )


    if response.status_code != 200:

        raise RuntimeError(
            "INA respondió "
            f"{response.status_code}: "
            f"{response.text[:500]}"
        )


    data = response.json()


    features = data.get(
        "features",
        [],
    )


    estaciones = []


    for feature in features:

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
            [],
        )


        if len(coords) < 2:
            continue


        lon = valor_seguro(
            coords[0]
        )

        lat = valor_seguro(
            coords[1]
        )


        if (
            lat is None
            or lon is None
        ):
            continue


        estaciones.append(
            {
                "properties":
                    props,

                "lat":
                    lat,

                "lon":
                    lon,
            }
        )


    return estaciones


# ============================================================
# BÚSQUEDA ESTACIÓN INA
# ============================================================

def buscar_estacion_ina(
    nodo,
    estaciones,
):

    if not estaciones:
        return None


    mejor = None

    mejor_distancia = float(
        "inf"
    )


    for estacion in estaciones:

        distancia = distancia_km(

            nodo["lat"],

            nodo["lon"],

            estacion["lat"],

            estacion["lon"],
        )


        # Máximo 120 km.
        if distancia > 120:
            continue


        if distancia < mejor_distancia:

            mejor_distancia = distancia

            mejor = estacion


    if mejor is None:
        return None


    mejor["distancia_km"] = (
        mejor_distancia
    )


    return mejor


# ============================================================
# EXTRAER PROPIEDADES INA
# ============================================================

def extraer_propiedad(
    props,
    candidatos,
):

    normalizadas = {}


    for key, value in props.items():

        clave = (
            str(key)
            .lower()
            .replace("_", "")
            .replace("-", "")
            .replace(" ", "")
        )

        normalizadas[clave] = value


    for candidato in candidatos:

        clave = (
            candidato
            .lower()
            .replace("_", "")
            .replace("-", "")
            .replace(" ", "")
        )


        if clave in normalizadas:

            return normalizadas[clave]


    return None


# ============================================================
# PROCESAR HIDROLOGÍA
# ============================================================

def procesar_hidrologia(
    nodo,
    estacion,
):

    if estacion is None:

        return {

            "nivel_hidrometrico":
                None,

            "nivel_24h":
                None,

            "diferencia_24h":
                None,

            "tendencia_hidrologica":
                "SIN DATO",

            "score_hidrologia":
                0.0,

            "calidad_hidrologia":
                "🔴 SIN ESTACIÓN CERCANA",

            "estacion_ina":
                None,

            "distancia_ina":
                None,
        }


    props = estacion[
        "properties"
    ]


    nombre = extraer_propiedad(
        props,
        [
            "nombre",
            "name",
            "estacion",
            "site_name",
            "nombre_estacion",
        ],
    )


    nivel = extraer_propiedad(
        props,
        [
            "altura",
            "nivel",
            "valor",
            "valor_actual",
            "h",
            "real",
            "alturareal",
        ],
    )


    nivel_24 = extraer_propiedad(
        props,
        [
            "altura24",
            "nivel24",
            "valor24",
            "valor_24h",
            "h24",
            "alturareal24",
        ],
    )


    nivel_num = valor_seguro(
        nivel
    )

    nivel_24_num = valor_seguro(
        nivel_24
    )


    diferencia = None


    if (
        nivel_num is not None
        and
        nivel_24_num is not None
    ):

        diferencia = (
            nivel_num
            -
            nivel_24_num
        )


    # ========================================================
    # TENDENCIA
    # ========================================================

    if diferencia is None:

        tendencia = "SIN DATO"

        score = 35.0

    elif diferencia >= 0.30:

        tendencia = "ASCENDENTE FUERTE"

        score = 90.0

    elif diferencia >= 0.10:

        tendencia = "ASCENDENTE"

        score = 75.0

    elif diferencia <= -0.30:

        tendencia = "DESCENDENTE FUERTE"

        score = 15.0

    elif diferencia <= -0.10:

        tendencia = "DESCENDENTE"

        score = 25.0

    else:

        tendencia = "ESTABLE"

        score = 40.0


    return {

        "nivel_hidrometrico":
            nivel_num,

        "nivel_24h":
            nivel_24_num,

        "diferencia_24h":
            diferencia,

        "tendencia_hidrologica":
            tendencia,

        "score_hidrologia":
            score,

        "calidad_hidrologia":
            "🟢 INA DISPONIBLE",

        "estacion_ina":
            nombre,

        "distancia_ina":
            estacion[
                "distancia_km"
            ],
    }


# ============================================================
# RIESGO 0–100
# ============================================================

def calcular_riesgo(
    score_meteorologia,
    score_suelo,
    score_hidrologia,
    vulnerabilidad,
):

    # --------------------------------------------------------
    # PESOS
    # --------------------------------------------------------
    #
    # Meteorología      35 %
    # Suelo             25 %
    # Hidrología        25 %
    # Vulnerabilidad    15 %
    #
    # --------------------------------------------------------

    riesgo = (

        score_meteorologia
        * 0.35

        +

        score_suelo
        * 0.25

        +

        score_hidrologia
        * 0.25

        +

        vulnerabilidad
        * 0.15
    )


    return round(
        max(
            0,
            min(
                100,
                riesgo,
            ),
        ),
        1,
    )


# ============================================================
# TENDENCIA DEL RIESGO
# ============================================================

def calcular_tendencia(
    nombre,
    riesgo_actual,
):

    if (
        "historial"
        not in st.session_state
    ):

        st.session_state.historial = []


    anteriores = [

        item["riesgo"]

        for item
        in st.session_state.historial

        if item["nombre"] == nombre
    ]


    if not anteriores:

        return "🆕 NUEVO"


    anterior = anteriores[-1]

    diferencia = (
        riesgo_actual
        -
        anterior
    )


    if diferencia >= 10:

        return "⬆️ ASCENDENTE FUERTE"


    if diferencia >= 3:

        return "↗️ ASCENDENTE"


    if diferencia <= -10:

        return "⬇️ DESCENDENTE FUERTE"


    if diferencia <= -3:

        return "↘️ DESCENDENTE"


    return "➡️ ESTABLE"


# ============================================================
# ACCIONES OPERATIVAS
# ============================================================

def accion_operativa(
    nivel,
):

    acciones = {

        "BAJO":
            "Monitoreo rutinario.",

        "MODERADO":
            "Mantener seguimiento de lluvia, suelo y niveles hidrológicos.",

        "ALTO":
            "Revisar sectores bajos, drenajes y accesos rurales. Preparar medidas preventivas.",

        "MUY ALTO":
            "Preparar medidas preventivas para hacienda, maquinaria, drenajes y accesos.",

        "CRÍTICO":
            "Evaluar activación de protocolos locales y medidas preventivas de protección y traslado hacia sectores seguros según la situación local.",
    }


    return acciones.get(
        nivel,
        "Mantener monitoreo.",
    )


# ============================================================
# GENERAR DATOS
# ============================================================

def generar_datos():

    meteo = consultar_open_meteo()


    try:

        estaciones_ina = (
            consultar_ina()
        )

        ina_ok = True

        ina_error = None

    except Exception as exc:

        estaciones_ina = []

        ina_ok = False

        ina_error = str(exc)


    resultados = []


    for nodo in meteo:

        estacion = (
            buscar_estacion_ina(
                nodo,
                estaciones_ina,
            )
        )


        hidro = (
            procesar_hidrologia(
                nodo,
                estacion,
            )
        )


        riesgo = calcular_riesgo(

            nodo[
                "score_meteorologia"
            ],

            nodo[
                "score_suelo"
            ],

            hidro[
                "score_hidrologia"
            ],

            nodo[
                "vulnerabilidad"
            ],
        )


        nivel = nivel_riesgo(
            riesgo
        )


        tendencia = (
            calcular_tendencia(
                nodo["nombre"],
                riesgo,
            )
        )


        resultados.append(
            {

                **nodo,

                **hidro,

                "riesgo":
                    riesgo,

                "nivel":
                    nivel,

                "tendencia":
                    tendencia,

                "accion":
                    accion_operativa(
                        nivel
                    ),
            }
        )


    return (
        resultados,
        ina_ok,
        ina_error,
    )


# ============================================================
# TELEGRAM — MENSAJE
# ============================================================

def construir_alerta_telegram(
    row,
):

    nivel = row["nivel"]


    return (
        "🚨 ALERTA LITORAL AGRO\n\n"

        f"📍 {row['nombre']} — "
        f"{row['provincia']}\n"

        f"{NIVELES[nivel]['emoji']} "
        f"RIESGO: {nivel} — "
        f"{row['riesgo']:.0f}/100\n\n"

        f"🌧️ Meteorología: "
        f"{row['score_meteorologia']:.0f}/100\n"

        f"💧 Suelo: "
        f"{row['score_suelo']:.0f}/100\n"

        f"🌊 Hidrología: "
        f"{row['score_hidrologia']:.0f}/100\n"

        f"🗺️ Vulnerabilidad: "
        f"{row['vulnerabilidad']:.0f}/100\n\n"

        f"🌧️ Lluvia 72 h previas: "
        f"{row['lluvia_reciente_72h']:.1f} mm\n"

        f"🔮 Pronóstico 72 h: "
        f"{row['lluvia_pronostico_72h']:.1f} mm\n"

        f"🔮 Pronóstico 7 días: "
        f"{row['lluvia_pronostico_7d']:.1f} mm\n\n"

        f"📈 Tendencia: "
        f"{row['tendencia']}\n\n"

        f"🎯 Acción:\n"
        f"{row['accion']}"
    )


# ============================================================
# TELEGRAM — CONTROL DE CAMBIOS
# ============================================================

def procesar_alertas_telegram(
    df,
):

    if not telegram_configurado():

        return []


    if (
        "telegram_ultimo_nivel"
        not in st.session_state
    ):

        st.session_state.telegram_ultimo_nivel = {}


    resultados = []


    for _, row in df.iterrows():

        nombre = row["nombre"]

        nivel_actual = row["nivel"]


        nivel_anterior = (
            st.session_state
            .telegram_ultimo_nivel
            .get(nombre)
        )


        enviar = False


        # Primera ejecución:
        # solamente alertar si ya es MUY ALTO o CRÍTICO.
        if nivel_anterior is None:

            if nivel_actual in [
                "MUY ALTO",
                "CRÍTICO",
            ]:

                enviar = True


        # Cambio posterior.
        elif (
            nivel_actual
            != nivel_anterior
        ):

            if nivel_actual in [
                "ALTO",
                "MUY ALTO",
                "CRÍTICO",
            ]:

                enviar = True


        if enviar:

            mensaje = (
                construir_alerta_telegram(
                    row
                )
            )


            ok, detalle = (
                enviar_telegram(
                    mensaje
                )
            )


            resultados.append(
                {
                    "nodo":
                        nombre,

                    "nivel":
                        nivel_actual,

                    "ok":
                        ok,

                    "detalle":
                        detalle,
                }
            )


        st.session_state[
            "telegram_ultimo_nivel"
        ][nombre] = nivel_actual


    return resultados


# ============================================================
# INTERFAZ
# ============================================================

ahora = datetime.now(
    TZ_ARG
)


st.title(
    "🌧️ Alerta Litoral Agro"
)


st.subheader(
    "Sistema experimental de alerta temprana para riesgo de anegamiento agropecuario"
)


st.info(
    "📍 Santa Fe · Corrientes · Entre Ríos  |  "
    "Índice integrado de riesgo 0–100"
)


# ============================================================
# SIDEBAR
# ============================================================

with st.sidebar:

    st.header(
        "⚙️ Control"
    )


    if st.button(
        "🔄 Actualizar datos ahora",
        use_container_width=True,
    ):

        consultar_open_meteo.clear()

        consultar_ina.clear()

        st.rerun()


    st.markdown("---")


    st.markdown(
        "### 📡 Fuentes"
    )


    st.markdown(
        """
**Meteorología**
- Open-Meteo

**Hidrología**
- INA / DSIyAH

**Cartografía**
- OpenStreetMap
- Esri

**Monitoreo**
- Windy
- CIRA / GOES

**Notificaciones**
- Telegram
"""
    )


    st.markdown("---")


    st.markdown(
        "### 🚨 Telegram"
    )


    if telegram_configurado():

        st.success(
            "🟢 Telegram configurado"
        )

    else:

        st.warning(
            "🟡 Telegram no configurado"
        )

        st.caption(
            "Agregar TELEGRAM_BOT_TOKEN "
            "y TELEGRAM_CHAT_ID en "
            "Streamlit Secrets."
        )


    st.markdown("---")


    st.markdown(
        "### 🧮 Modelo"
    )


    st.caption(
        "Meteorología 35% · "
        "Suelo 25% · "
        "Hidrología 25% · "
        "Vulnerabilidad 15%"
    )


    st.markdown("---")


    st.caption(
        "Modelo experimental. "
        "Requiere validación histórica."
    )


# ============================================================
# CARGAR DATOS
# ============================================================

try:

    (
        datos,
        ina_ok,
        ina_error,
    ) = generar_datos()


    sistema_ok = True

    error_datos = None


except Exception as exc:

    datos = []

    ina_ok = False

    ina_error = None

    sistema_ok = False

    error_datos = str(exc)


# ============================================================
# ERROR
# ============================================================

if not sistema_ok:

    st.error(
        "🔴 NO FUE POSIBLE ACTUALIZAR "
        "LOS DATOS METEOROLÓGICOS."
    )


    st.code(
        error_datos
    )


    st.info(
        "La aplicación detuvo el cálculo "
        "para evitar mostrar un índice "
        "que no tenga datos meteorológicos válidos."
    )


    st.stop()


df = pd.DataFrame(
    datos
)


# ============================================================
# ESTADO DE FUENTES
# ============================================================

st.markdown(
    "## 📡 Estado de las fuentes"
)


f1, f2, f3 = st.columns(3)


with f1:

    st.success(
        "🟢 Open-Meteo\n\n"
        "Datos meteorológicos disponibles"
    )


with f2:

    if ina_ok:

        st.success(
            "🟢 INA / DSIyAH\n\n"
            "Servicio hidrológico disponible"
        )

    else:

        st.warning(
            "🟡 INA / DSIyAH\n\n"
            "Sin datos hidrológicos disponibles"
        )


with f3:

    if telegram_configurado():

        st.success(
            "🟢 Telegram\n\n"
            "Notificaciones configuradas"
        )

    else:

        st.info(
            "⚪ Telegram\n\n"
            "No configurado"
        )


if not ina_ok:

    st.warning(
        "⚠️ La fuente hidrológica INA no "
        "pudo actualizarse. El componente "
        "hidrológico se mantiene sin "
        "aporte observado."
    )


    if ina_error:

        with st.expander(
            "Detalle técnico INA"
        ):

            st.code(
                ina_error
            )


st.caption(
    "Consulta del sistema: "
    f"{ahora.strftime('%d/%m/%Y %H:%M:%S')} ART"
)


# ============================================================
# HISTORIAL DE SESIÓN
# ============================================================

if (
    "historial"
    not in st.session_state
):

    st.session_state.historial = []


for row in datos:

    st.session_state.historial.append(
        {

            "fecha":
                ahora.strftime(
                    "%d/%m/%Y %H:%M"
                ),

            "nombre":
                row["nombre"],

            "riesgo":
                row["riesgo"],

            "nivel":
                row["nivel"],
        }
    )


# Limitar tamaño.

st.session_state.historial = (
    st.session_state.historial[-5000:]
)


# ============================================================
# TELEGRAM
# ============================================================

if telegram_configurado():

    resultados_telegram = (
        procesar_alertas_telegram(
            df
        )
    )

    for resultado in resultados_telegram:

        if resultado["ok"]:

            st.toast(
                "Telegram enviado: "
                f"{resultado['nodo']}",
                icon="🚨",
            )

        else:

            st.warning(
                "Telegram no pudo enviar "
                f"la alerta de "
                f"{resultado['nodo']}: "
                f"{resultado['detalle']}"
            )


# ============================================================
# RESUMEN
# ============================================================

st.markdown("---")


st.markdown(
    "## 🚨 Situación actual"
)


conteo = (
    df["nivel"]
    .value_counts()
    .to_dict()
)


c1, c2, c3, c4, c5 = st.columns(5)


c1.metric(
    "🟢 Bajo",
    conteo.get(
        "BAJO",
        0,
    ),
)


c2.metric(
    "🟡 Moderado",
    conteo.get(
        "MODERADO",
        0,
    ),
)


c3.metric(
    "🟠 Alto",
    conteo.get(
        "ALTO",
        0,
    ),
)


c4.metric(
    "🔴 Muy alto",
    conteo.get(
        "MUY ALTO",
        0,
    ),
)


c5.metric(
    "🟣 Crítico",
    conteo.get(
        "CRÍTICO",
        0,
    ),
)


# ============================================================
# MAYOR RIESGO
# ============================================================

mayor = df.loc[
    df["riesgo"].idxmax()
]


nivel_mayor = (
    mayor["nivel"]
)


st.warning(
    f"{NIVELES[nivel_mayor]['emoji']} "
    f"**Mayor riesgo actual: "
    f"{mayor['nombre']} — "
    f"{mayor['riesgo']:.0f}/100 "
    f"({nivel_mayor})**"
)


# ============================================================
# MAPA
# ============================================================

st.markdown("---")


st.markdown(
    "## 🗺️ Mapa operativo de riesgo"
)


m = folium.Map(
    location=[
        -30.5,
        -59.5,
    ],

    zoom_start=6,

    tiles="OpenStreetMap",

    control_scale=True,
)


folium.TileLayer(
    "CartoDB positron",
    name="Cartografía clara",
    control=True,
).add_to(m)


folium.TileLayer(
    "Esri WorldImagery",
    name="🛰️ Satelital",
    attr="Esri",
    control=True,
).add_to(m)


for row in datos:

    nivel = NIVELES[
        row["nivel"]
    ]


    humedad_texto = (

        f"{row['humedad_indice']:.1f}%"

        if row["humedad_indice"]
        is not None

        else "Sin dato"
    )


    nivel_hidro = (

        f"{row['nivel_hidrometrico']:.2f}"

        if row[
            "nivel_hidrometrico"
        ] is not None

        else "Sin dato"
    )


    nivel_hidro_24 = (

        f"{row['nivel_24h']:.2f}"

        if row["nivel_24h"]
        is not None

        else "Sin dato"
    )


    diferencia_hidro = (

        f"{row['diferencia_24h']:+.2f}"

        if row["diferencia_24h"]
        is not None

        else "Sin dato"
    )


    estacion = (

        row["estacion_ina"]

        if row["estacion_ina"]

        else "No disponible"
    )


    distancia = (

        f"{row['distancia_ina']:.1f} km"

        if row["distancia_ina"]
        is not None

        else "-"
    )


    popup_html = f"""

    <div style="
        width:360px;
        font-family:Arial,sans-serif;
        line-height:1.45;
    ">

        <h2 style="margin-bottom:3px;">
            {nivel["emoji"]}
            {row["nombre"]}
        </h2>

        <b>{row["provincia"]}</b>

        <hr>

        <h2>
            {nivel["emoji"]}
            {row["riesgo"]:.0f}/100
        </h2>

        <b>Nivel:</b>
        {row["nivel"]}<br>

        <b>Tendencia:</b>
        {row["tendencia"]}

        <hr>

        <b>🧮 Componentes del índice</b><br><br>

        🌧️ Meteorología:
        {row["score_meteorologia"]:.0f}/100
        <br>

        💧 Suelo:
        {row["score_suelo"]:.0f}/100
        <br>

        🌊 Hidrología:
        {row["score_hidrologia"]:.0f}/100
        <br>

        🗺️ Vulnerabilidad:
        {row["vulnerabilidad"]:.0f}/100

        <hr>

        <b>🌧️ Precipitación</b><br>

        Últimas 24 h:
        {row["lluvia_reciente_24h"]:.1f} mm
        <br>

        Últimas 72 h:
        {row["lluvia_reciente_72h"]:.1f} mm
        <br>

        Próximas 24 h:
        {row["lluvia_pronostico_24h"]:.1f} mm
        <br>

        Próximas 72 h:
        {row["lluvia_pronostico_72h"]:.1f} mm
        <br>

        Próximos 7 días:
        {row["lluvia_pronostico_7d"]:.1f} mm

        <hr>

        <b>💧 Suelo</b><br>

        Índice superficial:
        {humedad_texto}
        <br>

        Humedad 0–7 cm:
        {row["humedad_volumetrica"] or "Sin dato"}
        <br>

        Humedad 7–28 cm:
        {row["humedad_7_28"] or "Sin dato"}
        <br>

        Humedad 28–100 cm:
        {row["humedad_28_100"] or "Sin dato"}

        <hr>

        <b>🌊 Hidrología INA</b><br>

        Estación:
        {estacion}
        <br>

        Distancia:
        {distancia}
        <br>

        Nivel actual:
        {nivel_hidro}
        <br>

        Nivel 24 h:
        {nivel_hidro_24}
        <br>

        Variación 24 h:
        {diferencia_hidro}
        <br>

        Tendencia:
        {row["tendencia_hidrologica"]}

        <hr>

        <b>🗺️ Vulnerabilidad</b><br>

        Índice:
        {row["vulnerabilidad"]}/100
        <br>

        {row["tipo_territorio"]}

        <hr>

        <b>🎯 Acción orientativa</b><br>

        {row["accion"]}

        <hr>

        <small>
        ⚠️ Modelo experimental.
        No constituye una alerta oficial.
        </small>

    </div>

    """


    folium.CircleMarker(

        location=[
            row["lat"],
            row["lon"],
        ],

        radius=11,

        color=nivel["color"],

        fill=True,

        fill_color=nivel["color"],

        fill_opacity=0.82,

        weight=3,

        popup=folium.Popup(
            popup_html,
            max_width=410,
        ),

        tooltip=(
            f"{nivel['emoji']} "
            f"{row['nombre']} — "
            f"{row['riesgo']:.0f}/100"
        ),
    ).add_to(m)


folium.LayerControl(
    collapsed=False
).add_to(m)


st_folium(
    m,
    width=None,
    height=700,
    returned_objects=[],
)


# ============================================================
# TABLA OPERATIVA
# ============================================================

st.markdown("---")


st.markdown(
    "## 📊 Estado operativo por nodo"
)


tabla = df[
    [
        "nombre",
        "provincia",
        "riesgo",
        "nivel",
        "tendencia",
        "score_meteorologia",
        "score_suelo",
        "score_hidrologia",
        "vulnerabilidad",
        "lluvia_reciente_24h",
        "lluvia_reciente_72h",
        "lluvia_pronostico_24h",
        "lluvia_pronostico_72h",
        "lluvia_pronostico_7d",
        "nivel_hidrometrico",
    ]
].copy()


tabla.columns = [

    "Localidad",

    "Provincia",

    "Riesgo",

    "Nivel",

    "Tendencia",

    "Meteorología",

    "Suelo",

    "Hidrología",

    "Vulnerabilidad",

    "Lluvia 24 h previas (mm)",

    "Lluvia 72 h previas (mm)",

    "Pronóstico 24 h (mm)",

    "Pronóstico 72 h (mm)",

    "Pronóstico 7 días (mm)",

    "Nivel río (m)",
]


tabla["Nivel"] = tabla[
    "Nivel"
].map(

    lambda x:
        f"{NIVELES[x]['emoji']} {x}"
)


columnas_numericas = [

    "Riesgo",

    "Meteorología",

    "Suelo",

    "Hidrología",

    "Vulnerabilidad",

    "Lluvia 24 h previas (mm)",

    "Lluvia 72 h previas (mm)",

    "Pronóstico 24 h (mm)",

    "Pronóstico 72 h (mm)",

    "Pronóstico 7 días (mm)",

    "Nivel río (m)",
]


for columna in columnas_numericas:

    tabla[columna] = pd.to_numeric(
        tabla[columna],
        errors="coerce",
    ).round(1)


tabla = tabla.sort_values(
    "Riesgo",
    ascending=False,
)


st.dataframe(
    tabla,
    use_container_width=True,
    hide_index=True,
)


# ============================================================
# ANALIZAR NODO
# ============================================================

st.markdown("---")


st.markdown(
    "## 🔎 Analizar nodo"
)


nombres = [
    row["nombre"]
    for row in datos
]


seleccion = st.selectbox(
    "Seleccioná una localidad",
    nombres,
)


row = next(
    r
    for r in datos
    if r["nombre"] == seleccion
)


nivel = NIVELES[
    row["nivel"]
]


a1, a2, a3, a4 = st.columns(4)


with a1:

    st.metric(
        "Índice de riesgo",
        f"{row['riesgo']:.0f}/100",
    )


with a2:

    st.metric(
        "Nivel",
        f"{nivel['emoji']} {row['nivel']}",
    )


with a3:

    st.metric(
        "Tendencia",
        row["tendencia"],
    )


with a4:

    st.metric(
        "Vulnerabilidad",
        f"{row['vulnerabilidad']:.0f}/100",
    )


st.markdown(
    "### 🧮 Desglose"
)


desglose = pd.DataFrame(
    [
        [
            "🌧️ Meteorología",
            row["score_meteorologia"],
            "35%",
        ],

        [
            "💧 Suelo",
            row["score_suelo"],
            "25%",
        ],

        [
            "🌊 Hidrología",
            row["score_hidrologia"],
            "25%",
        ],

        [
            "🗺️ Vulnerabilidad",
            row["vulnerabilidad"],
            "15%",
        ],
    ],
    columns=[
        "Componente",
        "Valor",
        "Peso",
    ],
)


st.dataframe(
    desglose,
    use_container_width=True,
    hide_index=True,
)


st.markdown(
    "### 🎯 Acción orientativa"
)


st.warning(
    row["accion"]
)


# ============================================================
# HISTORIAL
# ============================================================

st.markdown("---")


st.markdown(
    "## 📈 Historial de evolución del riesgo"
)


historial_df = pd.DataFrame(
    st.session_state.historial
)


if not historial_df.empty:

    historial_df["fecha"] = pd.to_datetime(
        historial_df["fecha"],
        dayfirst=True,
        errors="coerce",
    )


    historial_nodo = historial_df[
        historial_df["nombre"]
        == seleccion
    ].copy()


    if not historial_nodo.empty:

        historial_nodo = (
            historial_nodo
            .sort_values("fecha")
        )


        st.line_chart(
            historial_nodo.set_index(
                "fecha"
            )["riesgo"]
        )


        st.caption(
            "El historial de esta V3.0 "
            "se conserva durante la sesión "
            "activa de Streamlit."
        )

    else:

        st.info(
            "Todavía no hay registros "
            "para este nodo."
        )

else:

    st.info(
        "Todavía no hay historial disponible."
    )


# ============================================================
# MATRIZ DE DECISIÓN
# ============================================================

st.markdown("---")


st.markdown(
    "## 🎯 Matriz de decisión"
)


acciones_df = pd.DataFrame(
    [

        [
            "🟢 BAJO",
            "0–20",
            "Monitoreo rutinario.",
        ],

        [
            "🟡 MODERADO",
            "21–40",
            "Seguimiento de lluvia, suelo y niveles.",
        ],

        [
            "🟠 ALTO",
            "41–60",
            "Revisar bajos, drenajes y accesos rurales.",
        ],

        [
            "🔴 MUY ALTO",
            "61–80",
            "Preparar medidas preventivas.",
        ],

        [
            "🟣 CRÍTICO",
            "81–100",
            "Evaluar activación de protocolos locales.",
        ],
    ],

    columns=[
        "Nivel",
        "Índice",
        "Interpretación",
    ],
)


st.dataframe(
    acciones_df,
    use_container_width=True,
    hide_index=True,
)


# ============================================================
# MONITOREO COMPLEMENTARIO
# ============================================================

st.markdown("---")


st.markdown(
    "## 🛰️ Monitoreo meteorológico complementario"
)


r1, r2 = st.columns(2)


with r1:

    st.markdown(
        "### 🌀 Radar y modelos"
    )

    st.caption(
        "Herramienta complementaria. "
        "No forma parte directamente "
        "del cálculo 0–100."
    )


    st.components.v1.iframe(

        "https://embed.windy.com/embed2.html"
        "?lat=-30.5"
        "&lon=-59.5"
        "&detailLat=-30.5"
        "&detailLon=-59.5"
        "&width=700"
        "&height=460"
        "&zoom=6"
        "&level=surface"
        "&overlay=rain"
        "&product=ecmwf"
        "&menu="
        "&message=true"
        "&marker="
        "&calendar=now"
        "&pressure=true"
        "&type=map"
        "&location=coordinates"
        "&detail="
        "&metricWind=km%2Fh"
        "&metricTemp=%C2%B0C"
        "&radarRange=-1",

        height=500,

        scrolling=False,
    )


with r2:

    st.markdown(
        "### ⚡ GOES-19 / GLM"
    )


    st.caption(
        "Seguimiento complementario "
        "de nubosidad y actividad eléctrica."
    )


    st.link_button(
        "⚡ Abrir visor CIRA GOES-19 / GLM",

        "https://slider.cira.colostate.edu/",

        use_container_width=True,
    )


# ============================================================
# CONTROL DE CALIDAD
# ============================================================

st.markdown("---")


st.markdown(
    "## 🧪 Control de calidad de datos"
)


calidad_df = pd.DataFrame(
    [

        [
            "Open-Meteo",
            "Meteorología",
            "🟢 Disponible",
            "Datos modelados",
        ],

        [
            "INA / DSIyAH",
            "Hidrología",
            (
                "🟢 Disponible"
                if ina_ok
                else "🔴 No disponible"
            ),
            "Nivel hidrométrico observado publicado",
        ],

        [
            "OpenStreetMap / Esri",
            "Cartografía",
            "🟢 Disponible",
            "Cartografía",
        ],

        [
            "Telegram",
            "Notificaciones",
            (
                "🟢 Configurado"
                if telegram_configurado()
                else "⚪ No configurado"
            ),
            "Alertas automáticas",
        ],
    ],

    columns=[
        "Fuente",
        "Componente",
        "Estado",
        "Tipo",
    ],
)


st.dataframe(
    calidad_df,
    use_container_width=True,
    hide_index=True,
)


# ============================================================
# METODOLOGÍA
# ============================================================

st.markdown("---")


st.markdown(
    "## 🧭 Metodología V3.0"
)


with st.expander(
    "Ver metodología completa",
    expanded=False,
):

    st.markdown(
        """
### 1. Amenaza meteorológica

Se utilizan acumulados horarios para estimar:

- precipitación de las últimas 24 horas;
- precipitación de las últimas 72 horas;
- precipitación prevista para las próximas 24 horas;
- precipitación prevista para las próximas 72 horas;
- precipitación prevista para los próximos 7 días.

Esto permite diferenciar condición antecedente y amenaza futura.

### 2. Condición del suelo

El modelo utiliza humedad volumétrica modelada en:

- 0–7 cm;
- 7–28 cm;
- 28–100 cm.

El componente de suelo representa el 25% del índice.

La humedad modelada NO equivale a una medición directa de
saturación hidrológica de todo el perfil.

### 3. Hidrología

Se busca la estación hidrométrica INA más cercana al nodo,
con un radio máximo de 120 km.

Se intenta incorporar:

- nivel actual;
- nivel 24 horas antes;
- diferencia de nivel;
- tendencia.

### 4. Vulnerabilidad territorial

Cada nodo posee actualmente un valor inicial de vulnerabilidad
entre 0 y 100.

Esta variable representa una parametrización inicial.

Debe ser posteriormente calibrada mediante:

- mapas de inundabilidad;
- uso del suelo;
- tipo de suelo;
- antecedentes de anegamiento;
- exposición agropecuaria;
- infraestructura;
- drenaje;
- proximidad a cursos de agua.

### 5. Índice de riesgo

La fórmula inicial es:

Riesgo =
Meteorología × 35%
+
Suelo × 25%
+
Hidrología × 25%
+
Vulnerabilidad × 15%

El resultado se normaliza entre 0 y 100.

### 6. Interpretación

0–20:
BAJO

21–40:
MODERADO

41–60:
ALTO

61–80:
MUY ALTO

81–100:
CRÍTICO

Los umbrales y pesos son experimentales.
Deben validarse contra eventos históricos.
"""
    )


# ============================================================
# LIMITACIONES
# ============================================================

st.markdown("---")


st.markdown(
    "## ⚠️ Limitaciones actuales"
)


st.markdown(
    """
- El índice 0–100 es experimental.
- No representa una probabilidad estadística de inundación.
- Open-Meteo aporta información meteorológica modelada.
- La humedad del suelo es modelada.
- La estación hidrométrica más cercana puede estar a distancia significativa.
- La vulnerabilidad territorial actual es una parametrización inicial.
- El historial se conserva durante la sesión activa.
- Telegram requiere configuración mediante Secrets.
- Los datos hidrológicos INA deben interpretarse según las advertencias del organismo.
- El sistema no reemplaza alertas oficiales.
"""
)


# ============================================================
# EVOLUCIÓN
# ============================================================

st.markdown("---")


st.markdown(
    "## 🚀 Evolución prevista"
)


st.markdown(
    """
### V3.1
- Base de datos histórica persistente.
- Exportación CSV.
- Historial de eventos.
- Registro de alertas Telegram.

### V3.2
- Incorporación de precipitaciones observadas.
- Más estaciones hidrométricas.
- Análisis de anomalías.
- Validación automática de fuentes.

### V4.0
- Calibración histórica del índice.
- Mapas de inundabilidad.
- Uso real del suelo.
- Tipo de suelo.
- Exposición agropecuaria.

### V4.x
- Validación estadística.
- POD.
- FAR.
- CSI.
- BIAS.
- Modelos probabilísticos.
- Machine Learning.
"""
)


# ============================================================
# PIE
# ============================================================

st.markdown("---")


st.caption(
    "Alerta Litoral Agro V3.0 — "
    "Sistema experimental de apoyo a la toma "
    "de decisiones frente al riesgo de "
    "anegamiento agropecuario."
)


st.caption(
    "Última ejecución: "
    f"{ahora.strftime('%d/%m/%Y %H:%M:%S')} ART"
)
