import os
import math
import requests
import pandas as pd
import folium
import streamlit as st

from streamlit_folium import st_folium
from datetime import datetime, timezone
from zoneinfo import ZoneInfo


# ============================================================
# ALERTA LITORAL AGRO — V3.0
# ============================================================
#
# Sistema experimental de apoyo a la toma de decisiones para
# riesgo de anegamiento agropecuario.
#
# ÁREA:
#   Santa Fe
#   Corrientes
#   Entre Ríos
#
# COMPONENTES:
#   1. Amenaza meteorológica
#   2. Condición antecedente
#   3. Humedad superficial del suelo
#   4. Hidrología observada INA
#   5. Vulnerabilidad territorial
#   6. Tendencia
#
# RESULTADO:
#   Índice de Riesgo 0–100
#
# FUENTES:
#   - Open-Meteo
#   - INA / DSIyAH
#   - OpenStreetMap
#   - Esri
#   - Windy
#   - CIRA / GOES
#   - Telegram Bot API (opcional)
#
# IMPORTANTE:
# Este sistema NO reemplaza alertas oficiales.
#
# La humedad del suelo de Open-Meteo es MODELADA.
# La hidrología INA incorporada en esta versión corresponde a
# lecturas hidrométricas publicadas por el sistema consultado.
#
# El índice 0–100 es un modelo experimental que requiere
# calibración y validación histórica antes de ser utilizado
# para decisiones oficiales.
# ============================================================


# ============================================================
# CONFIGURACIÓN
# ============================================================

st.set_page_config(
    page_title="Alerta Litoral Agro V3",
    page_icon="🌧️",
    layout="wide",
    initial_sidebar_state="expanded",
)


TZ_ARG = ZoneInfo("America/Argentina/Buenos_Aires")

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"

# Servicio hidrológico INA / DSIyAH.
INA_WFS_URL = "https://alerta.ina.gob.ar/geoserver/public2/ows"

TELEGRAM_API = "https://api.telegram.org"


# ============================================================
# NODOS
# ============================================================

NODOS = [
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
# NIVELES
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
# FUNCIONES GENERALES
# ============================================================

def nivel_riesgo(score):
    score = max(0, min(100, float(score)))

    for nombre, datos in NIVELES.items():
        if datos["min"] <= score <= datos["max"]:
            return nombre

    return "CRÍTICO"


def distancia_km(lat1, lon1, lat2, lon2):
    """
    Distancia aproximada mediante Haversine.
    """

    r = 6371.0

    p1 = math.radians(lat1)
    p2 = math.radians(lat2)

    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)

    a = (
        math.sin(dp / 2) ** 2
        + math.cos(p1)
        * math.cos(p2)
        * math.sin(dl / 2) ** 2
    )

    return 2 * r * math.asin(math.sqrt(a))


def valor_seguro(valor, default=None):
    try:
        if valor is None:
            return default

        if isinstance(valor, str):
            valor = valor.replace(",", ".")

        return float(valor)
    except Exception:
        return default


# ============================================================
# CALIDAD DE DATOS
# ============================================================

def calidad_dato(valor, antiguedad_min=None):
    """
    Clasificación simple de calidad.
    """

    if valor is None:
        return {
            "estado": "SIN DATO",
            "emoji": "🔴",
            "score": 0,
        }

    if antiguedad_min is not None:

        if antiguedad_min <= 30:
            return {
                "estado": "BUENA",
                "emoji": "🟢",
                "score": 100,
            }

        if antiguedad_min <= 120:
            return {
                "estado": "ACEPTABLE",
                "emoji": "🟡",
                "score": 70,
            }

        if antiguedad_min <= 360:
            return {
                "estado": "RETRASADA",
                "emoji": "🟠",
                "score": 40,
            }

        return {
            "estado": "MUY ANTIGUA",
            "emoji": "🔴",
            "score": 15,
        }

    return {
        "estado": "DISPONIBLE",
        "emoji": "🟢",
        "score": 100,
    }


# ============================================================
# TELEGRAM
# ============================================================

def obtener_secret(nombre):
    """
    Permite usar Streamlit Secrets o variables de entorno.
    """

    try:
        valor = st.secrets.get(nombre)
        if valor:
            return valor
    except Exception:
        pass

    return os.getenv(nombre)


def telegram_configurado():
    return bool(
        obtener_secret("TELEGRAM_BOT_TOKEN")
        and obtener_secret("TELEGRAM_CHAT_ID")
    )


def enviar_telegram(mensaje):
    """
    Envía una alerta mediante Telegram.

    Requiere:

    TELEGRAM_BOT_TOKEN
    TELEGRAM_CHAT_ID
    """

    token = obtener_secret("TELEGRAM_BOT_TOKEN")
    chat_id = obtener_secret("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        return False, "Telegram no configurado."

    url = f"{TELEGRAM_API}/bot{token}/sendMessage"

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

        return True, "Mensaje enviado."

    except Exception as exc:
        return False, str(exc)


# ============================================================
# ALERTA TELEGRAM
# ============================================================

def construir_alerta_telegram(row):
    nivel = nivel_riesgo(row["riesgo"])

    return (
        "🚨 ALERTA LITORAL AGRO\n\n"
        f"📍 {row['nombre']} — {row['provincia']}\n"
        f"{NIVELES[nivel]['emoji']} "
        f"Riesgo: {nivel} — {row['riesgo']:.0f}/100\n\n"
        f"🌧️ Meteorología: {row['score_meteorologia']:.0f}/100\n"
        f"💧 Suelo: {row['score_suelo']:.0f}/100\n"
        f"🌊 Hidrología: {row['score_hidrologia']:.0f}/100\n"
        f"🗺️ Vulnerabilidad: {row['vulnerabilidad']:.0f}/100\n\n"
        f"🌧️ Lluvia reciente 72 h: "
        f"{row['lluvia_reciente_72h']:.1f} mm\n"
        f"🔮 Pronóstico 72 h: "
        f"{row['lluvia_pronostico_72h']:.1f} mm\n"
        f"🔮 Pronóstico 7 días: "
        f"{row['lluvia_pronostico_7d']:.1f} mm\n"
    )


def procesar_alertas_telegram(df):
    """
    Envía Telegram solamente cuando un nodo:

    - entra en MUY ALTO o CRÍTICO
    - o cambia significativamente de categoría.

    Se guarda el último nivel durante la sesión para evitar spam.
    """

    if not telegram_configurado():
        return []

    if "telegram_ultimo_nivel" not in st.session_state:
        st.session_state.telegram_ultimo_nivel = {}

    resultados = []

    for _, row in df.iterrows():

        nivel_actual = nivel_riesgo(row["riesgo"])
        nivel_anterior = st.session_state.telegram_ultimo_nivel.get(
            row["nombre"]
        )

        enviar = False

        if nivel_anterior is None:
            if nivel_actual in ["MUY ALTO", "CRÍTICO"]:
                enviar = True

        elif nivel_actual != nivel_anterior:

            if nivel_actual in [
                "ALTO",
                "MUY ALTO",
                "CRÍTICO",
            ]:
                enviar = True

        if enviar:

            mensaje = construir_alerta_telegram(row)

            ok, detalle = enviar_telegram(mensaje)

            resultados.append(
                {
                    "nodo": row["nombre"],
                    "nivel": nivel_actual,
                    "ok": ok,
                    "detalle": detalle,
                }
            )

        st.session_state.telegram_ultimo_nivel[
            row["nombre"]
        ] = nivel_actual

    return resultados


# ============================================================
# OPEN-METEO
# ============================================================

@st.cache_data(ttl=900, show_spinner=False)
def consultar_open_meteo():

    latitudes = ",".join(
        str(n["lat"])
        for n in NODOS
    )

    longitudes = ",".join(
        str(n["lon"])
        for n in NODOS
    )

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

        "timezone": "America/Argentina/Buenos_Aires",

        "past_days": 3,
        "forecast_days": 7,

        "forecast_hours": 168,
        "past_hours": 72,
    }

    response = requests.get(
        OPEN_METEO_URL,
        params=params,
        timeout=40,
        headers={
            "User-Agent": "Alerta-Litoral-Agro/3.0"
        },
    )

    response.raise_for_status()

    data = response.json()

    if isinstance(data, dict):
        data = [data]

    if len(data) != len(NODOS):
        raise RuntimeError(
            "Open-Meteo no devolvió la cantidad esperada "
            f"de ubicaciones: {len(data)} / {len(NODOS)}"
        )

    ahora = datetime.now(TZ_ARG)
    hoy = ahora.date()

    resultados = []

    for nodo, item in zip(NODOS, data):

        daily = item.get("daily", {})
        hourly = item.get("hourly", {})

        # ----------------------------------------------------
        # LLUVIA DIARIA
        # ----------------------------------------------------

        fechas = daily.get("time", [])
        precipitaciones = daily.get(
            "precipitation_sum",
            []
        )

        lluvia_reciente_24h = 0.0
        lluvia_reciente_72h = 0.0

        lluvia_pronostico_24h = 0.0
        lluvia_pronostico_72h = 0.0
        lluvia_pronostico_7d = 0.0

        pares = []

        for fecha, lluvia in zip(
            fechas,
            precipitaciones
        ):
            try:

                fecha_dt = datetime.fromisoformat(
                    fecha
                ).date()

                pares.append(
                    (
                        fecha_dt,
                        valor_seguro(
                            lluvia,
                            0.0
                        ),
                    )
                )

            except Exception:
                continue

        recientes = [
            lluvia
            for fecha, lluvia in pares
            if fecha < hoy
        ]

        futuros = [
            lluvia
            for fecha, lluvia in pares
            if fecha >= hoy
        ]

        if recientes:
            lluvia_reciente_24h = recientes[-1]

            lluvia_reciente_72h = sum(
                recientes[-3:]
            )

        if futuros:
            lluvia_pronostico_24h = futuros[0]

            lluvia_pronostico_72h = sum(
                futuros[:3]
            )

            lluvia_pronostico_7d = sum(
                futuros[:7]
            )

        # ----------------------------------------------------
        # HUMEDAD DE SUELO
        # ----------------------------------------------------

        horas = hourly.get("time", [])

        humedad_0_7 = hourly.get(
            "soil_moisture_0_to_7cm",
            []
        )

        humedad_7_28 = hourly.get(
            "soil_moisture_7_to_28cm",
            []
        )

        humedad_28_100 = hourly.get(
            "soil_moisture_28_to_100cm",
            []
        )

        runoff = hourly.get(
            "surface_runoff",
            []
        )

        viento = hourly.get(
            "wind_gusts_10m",
            []
        )

        # Último valor disponible.
        def ultimo_valor(lista):

            valores = [
                valor_seguro(v)
                for v in lista
                if valor_seguro(v) is not None
            ]

            return valores[-1] if valores else None

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

        # ----------------------------------------------------
        # ÍNDICE DE SUELO
        # ----------------------------------------------------

        if humedad_actual is None:

            score_suelo = 0.0
            humedad_indice = None

        else:

            humedad_indice = max(
                0.0,
                min(
                    100.0,
                    (
                        humedad_actual
                        / 0.45
                    ) * 100.0,
                ),
            )

            score_superficial = humedad_indice

            if humedad_7_28_actual is not None:

                score_7_28 = max(
                    0.0,
                    min(
                        100.0,
                        (
                            humedad_7_28_actual
                            / 0.45
                        ) * 100.0,
                    ),
                )

            else:
                score_7_28 = score_superficial

            if humedad_28_100_actual is not None:

                score_28_100 = max(
                    0.0,
                    min(
                        100.0,
                        (
                            humedad_28_100_actual
                            / 0.45
                        ) * 100.0,
                    ),
                )

            else:
                score_28_100 = score_7_28

            score_suelo = (
                score_superficial * 0.50
                + score_7_28 * 0.30
                + score_28_100 * 0.20
            )

        # ----------------------------------------------------
        # COMPONENTE METEOROLÓGICO
        # ----------------------------------------------------

        score_reciente = min(
            100.0,
            lluvia_reciente_72h
            / 120.0
            * 100.0,
        )

        score_futuro = min(
            100.0,
            lluvia_pronostico_72h
            / 120.0
            * 100.0,
        )

        score_7d = min(
            100.0,
            lluvia_pronostico_7d
            / 180.0
            * 100.0,
        )

        score_meteorologia = (
            score_reciente * 0.35
            + score_futuro * 0.45
            + score_7d * 0.20
        )

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
                    "🟢 Disponible",
            }
        )

    return resultados


# ============================================================
# INA — HIDROLOGÍA
# ============================================================

@st.cache_data(ttl=900, show_spinner=False)
def consultar_ina():

    params = {
        "service": "WFS",
        "version": "1.0.0",
        "request": "GetFeature",
        "typeName": "public2:ultimas_alturas",
        "outputFormat": "application/json",
        "srsName": "EPSG:4326",
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

    response.raise_for_status()

    data = response.json()

    features = data.get(
        "features",
        []
    )

    estaciones = []

    for feature in features:

        props = feature.get(
            "properties",
            {}
        )

        geometry = feature.get(
            "geometry",
            {}
        )

        coords = geometry.get(
            "coordinates",
            []
        )

        if len(coords) < 2:
            continue

        lon = valor_seguro(coords[0])
        lat = valor_seguro(coords[1])

        if lat is None or lon is None:
            continue

        estaciones.append(
            {
                "properties": props,
                "lat": lat,
                "lon": lon,
            }
        )

    return estaciones


def buscar_estacion_ina(nodo, estaciones):

    if not estaciones:
        return None

    mejor = None
    mejor_distancia = float("inf")

    for estacion in estaciones:

        distancia = distancia_km(
            nodo["lat"],
            nodo["lon"],
            estacion["lat"],
            estacion["lon"],
        )

        # Evitamos asociar estaciones demasiado lejanas.
        if distancia > 120:
            continue

        if distancia < mejor_distancia:

            mejor_distancia = distancia
            mejor = estacion

    if mejor is None:
        return None

    mejor["distancia_km"] = mejor_distancia

    return mejor


def extraer_propiedad(props, candidatos):

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


def procesar_hidrologia(nodo, estacion):

    if estacion is None:

        return {
            "nivel_hidrometrico":
                None,

            "nivel_24h":
                None,

            "tendencia_hidrologica":
                "SIN DATO",

            "score_hidrologia":
                0.0,

            "calidad_hidrologia":
                "🔴 Sin estación cercana",

            "estacion_ina":
                None,

            "distancia_ina":
                None,
        }

    props = estacion["properties"]

    nombre = extraer_propiedad(
        props,
        [
            "nombre",
            "name",
            "estacion",
            "site_name",
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
        ],
    )

    # --------------------------------------------------------
    # TENDENCIA
    # --------------------------------------------------------

    nivel_num = valor_seguro(nivel)
    nivel_24_num = valor_seguro(nivel_24)

    diferencia = None

    if (
        nivel_num is not None
        and nivel_24_num is not None
    ):
        diferencia = (
            nivel_num
            - nivel_24_num
        )

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

        "tendencia_hidrologica":
            tendencia,

        "score_hidrologia":
            score,

        "calidad_hidrologia":
            "🟢 INA disponible",

        "estacion_ina":
            nombre,

        "distancia_ina":
            estacion["distancia_km"],
    }


# ============================================================
# ÍNDICE DE RIESGO
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
    # Meteorología       35 %
    # Suelo              25 %
    # Hidrología         25 %
    # Vulnerabilidad     15 %
    #
    # Estos pesos son iniciales y deben calibrarse.
    # --------------------------------------------------------

    score = (
        score_meteorologia * 0.35
        + score_suelo * 0.25
        + score_hidrologia * 0.25
        + vulnerabilidad * 0.15
    )

    return round(
        max(
            0.0,
            min(100.0, score)
        ),
        1,
    )


# ============================================================
# TENDENCIA DEL RIESGO
# ============================================================

def calcular_tendencia(nombre, riesgo_actual):

    if "historial" not in st.session_state:
        st.session_state.historial = []

    anteriores = [
        item["riesgo"]
        for item in st.session_state.historial
        if item["nombre"] == nombre
    ]

    if not anteriores:
        return "NUEVO"

    anterior = anteriores[-1]

    diferencia = riesgo_actual - anterior

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
# ACCIONES
# ============================================================

def accion_operativa(nivel):

    acciones = {

        "BAJO":
            "Monitoreo rutinario.",

        "MODERADO":
            "Mantener seguimiento de lluvia, suelo y niveles hidrológicos.",

        "ALTO":
            "Revisar bajos, drenajes y accesos rurales. Preparar medidas preventivas.",

        "MUY ALTO":
            "Preparar medidas preventivas para hacienda, maquinaria, drenajes y accesos.",

        "CRÍTICO":
            "Evaluar activación de protocolos locales y traslado preventivo hacia sectores seguros, según situación local.",
    }

    return acciones.get(
        nivel,
        "Mantener monitoreo.",
    )


# ============================================================
# PROCESAMIENTO PRINCIPAL
# ============================================================

def generar_datos():

    meteo = consultar_open_meteo()

    try:
        hidrologia = consultar_ina()
        ina_ok = True
        ina_error = None

    except Exception as exc:

        hidrologia = []
        ina_ok = False
        ina_error = str(exc)

    resultados = []

    for nodo in meteo:

        estacion = buscar_estacion_ina(
            nodo,
            hidrologia,
        )

        hidro = procesar_hidrologia(
            nodo,
            estacion,
        )

        riesgo = calcular_riesgo(
            nodo["score_meteorologia"],
            nodo["score_suelo"],
            hidro["score_hidrologia"],
            nodo["vulnerabilidad"],
        )

        nivel = nivel_riesgo(riesgo)

        tendencia = calcular_tendencia(
            nodo["nombre"],
            riesgo,
        )

        resultado = {
            **nodo,
            **hidro,

            "riesgo": riesgo,

            "nivel": nivel,

            "tendencia": tendencia,

            "accion":
                accion_operativa(nivel),

            "calidad_general":
                "🟢 Buena"
                if nodo["score_suelo"] > 0
                else "🟡 Parcial",
        }

        resultados.append(resultado)

    return resultados, ina_ok, ina_error


# ============================================================
# INTERFAZ
# ============================================================

ahora = datetime.now(TZ_ARG)

st.title("🌧️ Alerta Litoral Agro")

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

    st.header("⚙️ Control")

    if st.button(
        "🔄 Actualizar datos ahora",
        use_container_width=True,
    ):

        consultar_open_meteo.clear()
        consultar_ina.clear()

        st.rerun()

    st.markdown("---")

    st.markdown("### 📡 Fuentes")

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

        **Alertas**
        - Telegram
        """
    )

    st.markdown("---")

    st.markdown("### 🚨 Telegram")

    if telegram_configurado():

        st.success(
            "🟢 Telegram configurado"
        )

    else:

        st.warning(
            "🟡 Telegram no configurado"
        )

        st.caption(
            "Configurar TELEGRAM_BOT_TOKEN y "
            "TELEGRAM_CHAT_ID en Streamlit Secrets."
        )

    st.markdown("---")

    st.markdown("### 🧮 Modelo")

    st.caption(
        "Meteorología 35% · "
        "Suelo 25% · "
        "Hidrología 25% · "
        "Vulnerabilidad 15%"
    )

    st.markdown("---")

    st.caption(
        "El índice 0–100 es experimental y "
        "requiere validación histórica."
    )


# ============================================================
# CARGA
# ============================================================

try:

    datos, ina_ok, ina_error = generar_datos()

    sistema_ok = True
    error_datos = None

except Exception as exc:

    datos = []
    ina_ok = False
    sistema_ok = False
    error_datos = str(exc)


if not sistema_ok:

    st.error(
        "🔴 No fue posible obtener los datos meteorológicos."
    )

    st.code(error_datos)

    st.stop()


df = pd.DataFrame(datos)


# ============================================================
# ESTADO DE FUENTES
# ============================================================

st.markdown("## 📡 Estado de las fuentes")

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
            "Sistema de notificaciones configurado"
        )

    else:

        st.info(
            "⚪ Telegram\n\n"
            "No configurado"
        )


if not ina_ok:

    st.warning(
        "⚠️ La capa hidrológica no pudo actualizarse. "
        "El índice continúa funcionando, pero el componente "
        "hidrológico queda sin aporte observado."
    )

    if ina_error:
        with st.expander("Detalle técnico INA"):
            st.code(ina_error)


st.caption(
    f"Consulta del sistema: "
    f"{ahora.strftime('%d/%m/%Y %H:%M:%S')} ART"
)


# ============================================================
# HISTORIAL
# ============================================================

if "historial" not in st.session_state:
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
        procesar_alertas_telegram(df)
    )

    if resultados_telegram:

        for resultado in resultados_telegram:

            if resultado["ok"]:

                st.toast(
                    f"Telegram enviado: "
                    f"{resultado['nodo']}",
                    icon="🚨",
                )


# ============================================================
# RESUMEN
# ============================================================

st.markdown("---")
st.markdown("## 🚨 Situación actual")


conteo = (
    df["nivel"]
    .value_counts()
    .to_dict()
)


c1, c2, c3, c4, c5 = st.columns(5)

c1.metric(
    "🟢 Bajo",
    conteo.get("BAJO", 0),
)

c2.metric(
    "🟡 Moderado",
    conteo.get("MODERADO", 0),
)

c3.metric(
    "🟠 Alto",
    conteo.get("ALTO", 0),
)

c4.metric(
    "🔴 Muy alto",
    conteo.get("MUY ALTO", 0),
)

c5.metric(
    "🟣 Crítico",
    conteo.get("CRÍTICO", 0),
)


# ============================================================
# NODO DE MAYOR RIESGO
# ============================================================

mayor = df.loc[
    df["riesgo"].idxmax()
]

nivel_mayor = nivel_riesgo(
    mayor["riesgo"]
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
st.markdown("## 🗺️ Mapa operativo de riesgo")


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

    nivel = NIVELES[row["nivel"]]

    humedad_texto = (
        f"{row['humedad_indice']:.1f}%"
        if row["humedad_indice"]
        is not None
        else "Sin dato"
    )

    nivel_hidro = (
        f"{row['nivel_hidrometrico']:.2f} m"
        if row["nivel_hidrometrico"]
        is not None
        else "Sin dato"
    )

    nivel_hidro_24 = (
        f"{row['nivel_24h']:.2f} m"
        if row["nivel_24h"]
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
        width:350px;
        font-family:Arial,sans-serif;
        line-height:1.45;
    ">

        <h3 style="margin-bottom:4px;">
            {nivel["emoji"]}
            {row["nombre"]}
        </h3>

        <b>{row["provincia"]}</b>

        <hr>

        <h3>
            Riesgo:
            {row["riesgo"]:.0f}/100
            {nivel["emoji"]}
        </h3>

        <b>Nivel:</b>
        {row["nivel"]}<br>

        <b>Tendencia:</b>
        {row["tendencia"]}

        <hr>

        <b>Componentes del índice</b><br>

        🌧️ Meteorología:
        {row["score_meteorologia"]:.0f}/100<br>

        💧 Suelo:
        {row["score_suelo"]:.0f}/100<br>

        🌊 Hidrología:
        {row["score_hidrologia"]:.0f}/100<br>

        🗺️ Vulnerabilidad:
        {row["vulnerabilidad"]:.0f}/100

        <hr>

        <b>Precipitación</b><br>

        🌧️ 24 h recientes:
        {row["lluvia_reciente_24h"]:.1f} mm<br>

        🌧️ 72 h recientes:
        {row["lluvia_reciente_72h"]:.1f} mm<br>

        🔮 24 h:
        {row["lluvia_pronostico_24h"]:.1f} mm<br>

        🔮 72 h:
        {row["lluvia_pronostico_72h"]:.1f} mm<br>

        🔮 7 días:
        {row["lluvia_pronostico_7d"]:.1f} mm

        <hr>

        <b>Suelo</b><br>

        💧 Índice superficial:
        {humedad_texto}<br>

        <small>
        Humedad 0–7 cm modelada:
        {row["humedad_volumetrica"] or "Sin dato"}
        </small>

        <hr>

        <b>Hidrología INA</b><br>

        🌊 Estación:
        {estacion}<br>

        📍 Distancia:
        {distancia}<br>

        📏 Nivel actual:
        {nivel_hidro}<br>

        📏 Nivel 24 h:
        {nivel_hidro_24}<br>

        📈 Tendencia:
        {row["tendencia_hidrologica"]}

        <hr>

        <b>Vulnerabilidad territorial</b><br>

        🗺️ Índice:
        {row["vulnerabilidad"]}/100<br>

        {row["tipo_territorio"]}

        <hr>

        <b>🎯 Acción orientativa</b><br>

        {row["accion"]}

        <hr>

        <small>
        ⚠️ Modelo experimental.
        No constituye alerta oficial.
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
            max_width=400,
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
# TABLA PRINCIPAL
# ============================================================

st.markdown("---")
st.markdown("## 📊 Estado operativo por nodo")


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
        "lluvia_reciente_72h",
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
    "Lluvia 72 h previas (mm)",
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


for columna in [
    "Riesgo",
    "Meteorología",
    "Suelo",
    "Hidrología",
    "Vulnerabilidad",
    "Lluvia 72 h previas (mm)",
    "Pronóstico 72 h (mm)",
    "Pronóstico 7 días (mm)",
    "Nivel río (m)",
]:

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
# DESGLOSE DEL MODELO
# ============================================================

st.markdown("---")
st.markdown("## 🧮 Desglose del índice de riesgo")


st.info(
    "El índice no es una medición directa de inundación. "
    "Es una combinación ponderada de variables meteorológicas, "
    "condición del suelo, hidrología y vulnerabilidad territorial."
)


st.markdown(
    """
### Fórmula V3.0

**Riesgo =**

- 🌧️ Meteorología × **35%**
- 💧 Condición del suelo × **25%**
- 🌊 Hidrología × **25%**
- 🗺️ Vulnerabilidad territorial × **15%**

**Resultado final: 0–100**
"""
)


# ============================================================
# NODO SELECCIONADO
# ============================================================

st.markdown("### 🔎 Analizar nodo")


nombres = [
    row["nombre"]
    for row in datos
]


seleccion = st.selectbox(
    "Seleccioná una localidad",
    nombres,
)


row = next(
    r for r in datos
    if r["nombre"] == seleccion
)


nivel = NIVELES[row["nivel"]]


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
    f"### 🎯 Acción orientativa"

)

st.warning(
    row["accion"]
)


# ============================================================
# HISTORIAL
# ============================================================

st.markdown("---")
st.markdown("## 📈 Historial de evolución del riesgo")


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
        historial_df["nombre"] == seleccion
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
            "El historial de esta versión se conserva "
            "durante la sesión activa de Streamlit."
        )

    else:

        st.info(
            "Todavía no hay suficientes registros "
            "para este nodo."
        )

else:

    st.info(
        "Todavía no hay historial disponible."
    )


# ============================================================
# ACCIONES
# ============================================================

st.markdown("---")
st.markdown("## 🎯 Matriz de decisión")


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
st.markdown("## 🛰️ Monitoreo meteorológico complementario")


r1, r2 = st.columns(2)


with r1:

    st.markdown(
        "### 🌀 Radar y modelos"
    )

    st.caption(
        "Herramienta complementaria. "
        "No forma parte directamente del cálculo 0–100."
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
        "Seguimiento complementario de "
        "nubosidad y actividad eléctrica."
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
st.markdown("## 🧪 Control de calidad de datos")


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
            "Lecturas hidrométricas",
        ],
        [
            "OpenStreetMap / Esri",
            "Cartografía",
            "🟢 Disponible",
            "Información cartográfica",
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


st.caption(
    "La aplicación diferencia entre información "
    "modelada y datos hidrométricos publicados."
)


# ============================================================
# METODOLOGÍA
# ============================================================

st.markdown("---")
st.markdown("## 🧭 Metodología V3.0")


with st.expander(
    "Ver metodología completa",
    expanded=False,
):

    st.markdown(
        """
### 1. Amenaza meteorológica

Se considera:

- precipitación reciente de 24 h;
- precipitación acumulada reciente de 72 h;
- precipitación prevista para 24 h;
- precipitación prevista para 72 h;
- acumulado previsto de 7 días.

La información meteorológica de Open-Meteo utilizada
por el modelo debe interpretarse como **información modelada**,
no como medición directa de un pluviómetro local.

### 2. Condición del suelo

La V3.0 utiliza:

- humedad 0–7 cm;
- humedad 7–28 cm;
- humedad 28–100 cm.

La condición del suelo aporta el 25% del índice.

La humedad del suelo es una variable modelada y no equivale
por sí misma a una medición de saturación hidrológica.

### 3. Hidrología

La V3.0 busca la estación hidrométrica disponible
más cercana al nodo.

Se incorpora:

- nivel hidrométrico;
- nivel de referencia de 24 h cuando está disponible;
- tendencia;
- distancia entre estación y nodo.

### 4. Vulnerabilidad territorial

Cada nodo posee un índice inicial de vulnerabilidad
entre 0 y 100.

Este componente representa una aproximación territorial
y debe ser calibrado posteriormente mediante:

- mapas de inundabilidad;
- usos del suelo;
- tipo de suelo;
- antecedentes de anegamiento;
- exposición de producción agropecuaria;
- infraestructura;
- drenaje;
- proximidad a cursos de agua.

### 5. Índice final

El resultado se normaliza entre 0 y 100.

Los pesos actuales son:

| Componente | Peso |
|---|---:|
| Meteorología | 35% |
| Suelo | 25% |
| Hidrología | 25% |
| Vulnerabilidad | 15% |

Estos pesos constituyen una **configuración inicial experimental**.

Para una futura versión profesional deberán calibrarse
contra eventos históricos conocidos.
"""
    )


# ============================================================
# LIMITACIONES
# ============================================================

st.markdown("---")
st.markdown("## ⚠️ Limitaciones")


st.markdown(
    """
- El índice 0–100 es experimental.
- No representa una probabilidad estadística de inundación.
- Open-Meteo aporta información meteorológica modelada.
- La humedad del suelo no equivale a saturación completa del perfil.
- La estación hidrométrica más cercana puede encontrarse a una distancia significativa del nodo.
- La vulnerabilidad territorial actual es una parametrización inicial.
- El historial de esta V3.0 se conserva durante la sesión de Streamlit.
- Telegram depende de la configuración de un Bot.
- La herramienta no reemplaza alertas oficiales ni organismos de emergencia.
"""
)


# ============================================================
# PRÓXIMA EVOLUCIÓN
# ============================================================

st.markdown("---")
st.markdown("## 🚀 Evolución prevista")


st.markdown(
    """
### V3.1
- Persistencia histórica externa.
- Base de datos de eventos.
- Exportación CSV.
- Gráficos históricos.

### V3.2
- Pluviómetros observados.
- Mayor cantidad de estaciones hidrométricas.
- Análisis de anomalías.

### V4.0
- Calibración histórica del índice.
- Mapas de vulnerabilidad reales.
- Modelos de suelo.
- Uso del suelo agropecuario.
- Validación retrospectiva.

### V4.x
- Sistema de puntuación probabilística.
- Machine Learning.
- Detección automática de eventos.
- Evaluación del desempeño:
  **POD / FAR / CSI / BIAS**.
"""
)


# ============================================================
# PIE
# ============================================================

st.markdown("---")

st.caption(
    "Alerta Litoral Agro V3.0 — "
    "Sistema experimental de apoyo a la toma de decisiones "
    "frente al riesgo de anegamiento agropecuario."
)

st.caption(
    f"Última ejecución: "
    f"{ahora.strftime('%d/%m/%Y %H:%M:%S')} ART"
)
