# ============================================================
# 🌧️ ALERTA LITORAL AGRO
# Sistema experimental de alerta temprana para riesgo de
# anegamiento agropecuario
#
# Versión: V3.6.2
# Cambio principal:
#   - Pronóstico extendido 15 días → tarjetas visuales
#   - Humedad de suelo → porcentaje en interfaz
#
# Regiones:
#   Santa Fe
#   Corrientes
#   Entre Ríos
# ============================================================

import streamlit as st
import requests
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from html import escape


# ============================================================
# CONFIGURACIÓN
# ============================================================

st.set_page_config(
    page_title="Alerta Litoral Agro",
    page_icon="🌧️",
    layout="wide",
    initial_sidebar_state="expanded"
)

APP_NAME = "Alerta Litoral Agro"
VERSION = "V3.6.2"
MODELO = "ECMWF IFS HRES 9 km"

API_URL = "https://api.open-meteo.com/v1/ecmwf"

TZ = "America/Argentina/Buenos_Aires"

HEADERS = {
    "User-Agent": "Alerta-Litoral-Agro/3.6"
}


# ============================================================
# ESTILOS
# ============================================================

st.markdown(
    """
    <style>

    .main-title {
        font-size: 2.3rem;
        font-weight: 800;
        margin-bottom: 0.1rem;
    }

    .subtitle {
        color: #6b7280;
        font-size: 1rem;
        margin-bottom: 1.5rem;
    }

    .forecast-container {
        display: flex;
        gap: 14px;
        overflow-x: auto;
        padding: 10px 4px 18px 4px;
        scroll-behavior: smooth;
    }

    .forecast-container::-webkit-scrollbar {
        height: 8px;
    }

    .forecast-container::-webkit-scrollbar-track {
        background: #e5e7eb;
        border-radius: 10px;
    }

    .forecast-container::-webkit-scrollbar-thumb {
        background: #9ca3af;
        border-radius: 10px;
    }

    .forecast-card {
        min-width: 205px;
        max-width: 205px;
        flex: 0 0 205px;
        border: 1px solid #dbe3ea;
        border-radius: 18px;
        padding: 16px;
        background: linear-gradient(
            180deg,
            #ffffff 0%,
            #f7fafc 100%
        );
        box-shadow: 0 4px 12px rgba(0,0,0,0.07);
    }

    .forecast-card:hover {
        transform: translateY(-2px);
        box-shadow: 0 7px 18px rgba(0,0,0,0.10);
    }

    .forecast-date {
        font-size: 0.95rem;
        font-weight: 800;
        color: #111827;
        margin-bottom: 6px;
    }

    .forecast-icon {
        font-size: 3.4rem;
        text-align: center;
        line-height: 1.1;
        margin: 7px 0;
    }

    .forecast-condition {
        text-align: center;
        font-weight: 700;
        color: #374151;
        min-height: 42px;
    }

    .forecast-temp {
        text-align: center;
        margin: 8px 0 12px 0;
    }

    .forecast-temp-max {
        font-size: 1.45rem;
        font-weight: 800;
        color: #dc2626;
    }

    .forecast-temp-min {
        font-size: 1.05rem;
        font-weight: 700;
        color: #2563eb;
        margin-left: 6px;
    }

    .forecast-item {
        display: flex;
        justify-content: space-between;
        border-top: 1px solid #e5e7eb;
        padding: 7px 0;
        font-size: 0.84rem;
    }

    .forecast-item-label {
        color: #6b7280;
    }

    .forecast-item-value {
        font-weight: 700;
        color: #111827;
    }

    .forecast-model {
        color: #6b7280;
        font-size: 0.85rem;
        margin-bottom: 10px;
    }

    .risk-badge {
        display: inline-block;
        padding: 5px 11px;
        border-radius: 999px;
        font-size: 0.78rem;
        font-weight: 800;
    }

    .risk-bajo {
        background: #dcfce7;
        color: #166534;
    }

    .risk-medio {
        background: #fef3c7;
        color: #92400e;
    }

    .risk-alto {
        background: #fed7aa;
        color: #9a3412;
    }

    .risk-muy-alto {
        background: #fee2e2;
        color: #991b1b;
    }

    .info-box {
        border-radius: 12px;
        padding: 12px 15px;
        background: #f3f4f6;
        border: 1px solid #e5e7eb;
        margin-bottom: 12px;
    }

    </style>
    """,
    unsafe_allow_html=True
)


# ============================================================
# TÍTULO
# ============================================================

st.markdown(
    '<div class="main-title">🌧️ Alerta Litoral Agro</div>',
    unsafe_allow_html=True
)

st.markdown(
    f"""
    <div class="subtitle">
        Sistema experimental de alerta temprana para riesgo de
        anegamiento agropecuario · {VERSION}
    </div>
    """,
    unsafe_allow_html=True
)


# ============================================================
# NODOS
# ============================================================

NODOS = [
    {
        "localidad": "Reconquista",
        "provincia": "Santa Fe",
        "lat": -29.15,
        "lon": -59.65
    },
    {
        "localidad": "Avellaneda",
        "provincia": "Santa Fe",
        "lat": -29.12,
        "lon": -59.66
    },
    {
        "localidad": "Vera",
        "provincia": "Santa Fe",
        "lat": -29.46,
        "lon": -60.21
    },
    {
        "localidad": "Tostado",
        "provincia": "Santa Fe",
        "lat": -29.23,
        "lon": -61.77
    },
    {
        "localidad": "Rafaela",
        "provincia": "Santa Fe",
        "lat": -31.25,
        "lon": -61.49
    },
    {
        "localidad": "Santa Fe",
        "provincia": "Santa Fe",
        "lat": -31.63,
        "lon": -60.70
    },
    {
        "localidad": "Esperanza",
        "provincia": "Santa Fe",
        "lat": -31.45,
        "lon": -60.93
    },
    {
        "localidad": "Rosario",
        "provincia": "Santa Fe",
        "lat": -32.95,
        "lon": -60.66
    },
    {
        "localidad": "Venado Tuerto",
        "provincia": "Santa Fe",
        "lat": -33.75,
        "lon": -61.97
    },
    {
        "localidad": "Corrientes",
        "provincia": "Corrientes",
        "lat": -27.47,
        "lon": -58.83
    },
    {
        "localidad": "Goya",
        "provincia": "Corrientes",
        "lat": -29.14,
        "lon": -59.26
    },
    {
        "localidad": "Mercedes",
        "provincia": "Corrientes",
        "lat": -29.18,
        "lon": -58.08
    },
    {
        "localidad": "Curuzú Cuatiá",
        "provincia": "Corrientes",
        "lat": -29.79,
        "lon": -58.05
    },
    {
        "localidad": "Paso de los Libres",
        "provincia": "Corrientes",
        "lat": -29.71,
        "lon": -57.09
    },
    {
        "localidad": "Santo Tomé",
        "provincia": "Corrientes",
        "lat": -28.55,
        "lon": -56.04
    },
    {
        "localidad": "Paraná",
        "provincia": "Entre Ríos",
        "lat": -31.73,
        "lon": -60.53
    },
    {
        "localidad": "La Paz",
        "provincia": "Entre Ríos",
        "lat": -30.74,
        "lon": -59.65
    },
    {
        "localidad": "Concordia",
        "provincia": "Entre Ríos",
        "lat": -31.40,
        "lon": -58.02
    }
]


# ============================================================
# VARIABLES OPEN-METEO
# ============================================================

HOURLY_VARIABLES = [
    "temperature_2m",
    "relative_humidity_2m",
    "dew_point_2m",
    "apparent_temperature",
    "pressure_msl",
    "cloud_cover",
    "cloud_cover_low",
    "cloud_cover_mid",
    "cloud_cover_high",
    "wind_speed_10m",
    "wind_gusts_10m",
    "wind_direction_10m",
    "shortwave_radiation",
    "direct_radiation",
    "diffuse_radiation",
    "cape",
    "vapor_pressure_deficit",
    "et0_fao_evapotranspiration",
    "evapotranspiration",
    "precipitation",
    "rain",
    "showers",
    "precipitation_probability",
    "runoff",
    "soil_temperature_0_to_7cm",
    "soil_temperature_7_to_28cm",
    "soil_temperature_28_to_100cm",
    "soil_temperature_100_to_255cm",
    "soil_moisture_0_to_7cm",
    "soil_moisture_7_to_28cm",
    "soil_moisture_28_to_100cm",
    "soil_moisture_100_to_255cm",
    "weather_code"
]

DAILY_VARIABLES = [
    "weather_code",
    "temperature_2m_max",
    "temperature_2m_min",
    "apparent_temperature_max",
    "apparent_temperature_min",
    "precipitation_sum",
    "rain_sum",
    "showers_sum",
    "precipitation_probability_max",
    "precipitation_hours",
    "wind_speed_10m_max",
    "wind_gusts_10m_max",
    "wind_direction_10m_dominant",
    "cape_max",
    "shortwave_radiation_sum",
    "sunshine_duration",
    "et0_fao_evapotranspiration"
]


# ============================================================
# FUNCIONES AUXILIARES
# ============================================================

def normalizar_0_100(valor, minimo, maximo):
    if pd.isna(valor):
        return 0.0

    if maximo == minimo:
        return 0.0

    resultado = ((valor - minimo) / (maximo - minimo)) * 100

    return float(np.clip(resultado, 0, 100))


def direccion_viento(grados):
    if pd.isna(grados):
        return "—"

    direcciones = [
        "N", "NNE", "NE", "ENE",
        "E", "ESE", "SE", "SSE",
        "S", "SSO", "SO", "OSO",
        "O", "ONO", "NO", "NNO"
    ]

    indice = int((grados + 11.25) / 22.5) % 16

    return direcciones[indice]


def nivel_riesgo(indice):
    if indice >= 75:
        return "MUY ALTO"
    elif indice >= 55:
        return "ALTO"
    elif indice >= 35:
        return "MEDIO"
    else:
        return "BAJO"


def emoji_riesgo(nivel):
    return {
        "BAJO": "🟢",
        "MEDIO": "🟡",
        "ALTO": "🟠",
        "MUY ALTO": "🔴"
    }.get(nivel, "⚪")


# ============================================================
# CÓDIGOS METEOROLÓGICOS WMO
# ============================================================

def interpretar_weather_code(codigo):

    if pd.isna(codigo):
        return "Sin datos", "🌡️"

    codigo = int(codigo)

    mapa = {

        0: ("Cielo despejado", "☀️"),

        1: ("Mayormente despejado", "🌤️"),
        2: ("Parcialmente nublado", "⛅"),
        3: ("Nublado", "☁️"),

        45: ("Niebla", "🌫️"),
        48: ("Niebla con escarcha", "🌫️"),

        51: ("Llovizna débil", "🌦️"),
        53: ("Llovizna moderada", "🌦️"),
        55: ("Llovizna intensa", "🌧️"),

        56: ("Llovizna helada débil", "🌧️"),
        57: ("Llovizna helada intensa", "🌧️"),

        61: ("Lluvia débil", "🌦️"),
        63: ("Lluvia moderada", "🌧️"),
        65: ("Lluvia intensa", "🌧️"),

        66: ("Lluvia helada débil", "🌧️"),
        67: ("Lluvia helada intensa", "🌧️"),

        71: ("Nieve débil", "🌨️"),
        73: ("Nieve moderada", "🌨️"),
        75: ("Nieve intensa", "❄️"),

        77: ("Granizo/nieve", "❄️"),

        80: ("Chaparrones débiles", "🌦️"),
        81: ("Chaparrones moderados", "🌧️"),
        82: ("Chaparrones fuertes", "⛈️"),

        85: ("Nevadas débiles", "🌨️"),
        86: ("Nevadas fuertes", "❄️"),

        95: ("Tormenta", "⛈️"),
        96: ("Tormenta con granizo", "⛈️"),
        99: ("Tormenta fuerte con granizo", "⛈️")
    }

    return mapa.get(
        codigo,
        ("Condición meteorológica", "🌦️")
    )


# ============================================================
# OBTENER DATOS ECMWF
# ============================================================

@st.cache_data(ttl=900)
def obtener_datos_ecmwf():

    latitudes = ",".join(
        str(n["lat"]) for n in NODOS
    )

    longitudes = ",".join(
        str(n["lon"]) for n in NODOS
    )

    parametros = {

        "latitude": latitudes,
        "longitude": longitudes,

        "hourly": ",".join(HOURLY_VARIABLES),

        "daily": ",".join(DAILY_VARIABLES),

        "past_days": 8,

        "forecast_days": 15,

        "timezone": TZ,

        "wind_speed_unit": "kmh",

        "temperature_unit": "celsius",

        "precipitation_unit": "mm"
    }

    respuesta = requests.get(
        API_URL,
        params=parametros,
        headers=HEADERS,
        timeout=60
    )

    respuesta.raise_for_status()

    return respuesta.json()


# ============================================================
# PROCESAMIENTO DE NODO
# ============================================================

def procesar_nodo(nodo, datos):

    hourly = datos.get("hourly", {})
    daily = datos.get("daily", {})

    tiempos = pd.to_datetime(
        hourly.get("time", []),
        errors="coerce"
    )

    ahora = datetime.now(
        ZoneInfo(TZ)
    ).replace(tzinfo=None)

    df_horario = pd.DataFrame({
        "time": tiempos
    })

    for variable in HOURLY_VARIABLES:

        valores = hourly.get(
            variable,
            []
        )

        if len(valores) == len(df_horario):

            df_horario[variable] = valores

    if len(df_horario) == 0:

        raise ValueError(
            f"No hay datos horarios para {nodo['localidad']}"
        )

    df_horario["time"] = pd.to_datetime(
        df_horario["time"]
    )

    # --------------------------------------------------------
    # DATOS ACTUALES
    # --------------------------------------------------------

    df_pasado = df_horario[
        df_horario["time"] <= ahora
    ]

    if len(df_pasado) == 0:

        actual = df_horario.iloc[0]

    else:

        actual = df_pasado.iloc[-1]

    # --------------------------------------------------------
    # HUMEDAD DEL SUELO
    # --------------------------------------------------------

    humedad_0_7 = actual.get(
        "soil_moisture_0_to_7cm",
        np.nan
    )

    humedad_7_28 = actual.get(
        "soil_moisture_7_to_28cm",
        np.nan
    )

    humedad_28_100 = actual.get(
        "soil_moisture_28_to_100cm",
        np.nan
    )

    humedad_100_255 = actual.get(
        "soil_moisture_100_to_255cm",
        np.nan
    )

    humedades = [
        humedad_0_7,
        humedad_7_28,
        humedad_28_100,
        humedad_100_255
    ]

    humedades_validas = [
        x for x in humedades
        if pd.notna(x)
    ]

    if humedades_validas:

        humedad_suelo_promedio = float(
            np.mean(humedades_validas)
        )

    else:

        humedad_suelo_promedio = np.nan

    # --------------------------------------------------------
    # PRECIPITACIÓN
    # --------------------------------------------------------

    df_pasado_72 = df_horario[
        (df_horario["time"] <= ahora)
        &
        (df_horario["time"] >= ahora - timedelta(hours=72))
    ]

    lluvia_72 = float(
        df_pasado_72["precipitation"].fillna(0).sum()
    )

    runoff_72 = float(
        df_pasado_72["runoff"].fillna(0).sum()
    )

    # --------------------------------------------------------
    # PRECIPITACIÓN FUTURA
    # --------------------------------------------------------

    df_futuro = df_horario[
        df_horario["time"] > ahora
    ]

    df_futuro_72 = df_futuro[
        df_futuro["time"] <= ahora + timedelta(hours=72)
    ]

    lluvia_futura_72 = float(
        df_futuro_72["precipitation"].fillna(0).sum()
    )

    # --------------------------------------------------------
    # ACUMULACIÓN 7 DÍAS ANTERIORES
    # --------------------------------------------------------

    df_pasado_7d = df_horario[
        (df_horario["time"] <= ahora)
        &
        (df_horario["time"] >= ahora - timedelta(days=7))
    ]

    lluvia_7d = float(
        df_pasado_7d["precipitation"].fillna(0).sum()
    )

    # --------------------------------------------------------
    # COMPONENTES DEL RIESGO
    # --------------------------------------------------------

    humedad_score = normalizar_0_100(
        humedad_suelo_promedio,
        0.15,
        0.45
    )

    lluvia_reciente_score = normalizar_0_100(
        lluvia_72,
        10,
        100
    )

    lluvia_futura_score = normalizar_0_100(
        lluvia_futura_72,
        10,
        100
    )

    runoff_score = normalizar_0_100(
        runoff_72,
        1,
        30
    )

    acumulacion_score = normalizar_0_100(
        lluvia_7d,
        30,
        180
    )

    lluvia_critica_score = 0

    lluvia_24h = float(
        df_pasado[
            df_pasado["time"] >= ahora - timedelta(hours=24)
        ]["precipitation"].fillna(0).sum()
    )

    if lluvia_24h >= 50:
        lluvia_critica_score += 40

    elif lluvia_24h >= 30:
        lluvia_critica_score += 25

    elif lluvia_24h >= 20:
        lluvia_critica_score += 15

    if lluvia_72 >= 100:
        lluvia_critica_score += 40

    elif lluvia_72 >= 70:
        lluvia_critica_score += 30

    elif lluvia_72 >= 50:
        lluvia_critica_score += 20

    lluvia_critica_score = min(
        lluvia_critica_score,
        100
    )

    # --------------------------------------------------------
    # ÍNDICE
    # --------------------------------------------------------

    indice = (
        humedad_score * 0.25
        + lluvia_reciente_score * 0.20
        + lluvia_futura_score * 0.20
        + runoff_score * 0.10
        + acumulacion_score * 0.10
        + lluvia_critica_score * 0.15
    )

    # --------------------------------------------------------
    # TENDENCIA DEL ESCURRIMIENTO
    # --------------------------------------------------------

    reciente = df_horario[
        (df_horario["time"] <= ahora)
        &
        (df_horario["time"] >= ahora - timedelta(hours=12))
    ]

    anterior = df_horario[
        (df_horario["time"] < ahora - timedelta(hours=12))
        &
        (df_horario["time"] >= ahora - timedelta(hours=36))
    ]

    runoff_reciente = (
        reciente["runoff"].fillna(0).sum()
        if len(reciente)
        else 0
    )

    runoff_anterior = (
        anterior["runoff"].fillna(0).sum()
        if len(anterior)
        else 0
    )

    if runoff_anterior > 0:

        diferencia = (
            runoff_reciente - runoff_anterior
        ) / runoff_anterior

    else:

        diferencia = 0

    if diferencia >= 0.50:
        tendencia_runoff = "Subida rápida"
        indice += 10

    elif diferencia >= 0.20:
        tendencia_runoff = "Subida lenta"
        indice += 5

    elif diferencia <= -0.50:
        tendencia_runoff = "Bajada rápida"
        indice -= 8

    elif diferencia <= -0.20:
        tendencia_runoff = "Bajada lenta"
        indice -= 4

    else:
        tendencia_runoff = "Estable"

    # --------------------------------------------------------
    # CONDICIONES CRÍTICAS
    # --------------------------------------------------------

    if (
        lluvia_72 >= 120
        or lluvia_7d >= 220
        or runoff_72 >= 50
    ):
        indice = max(indice, 75)

    indice = float(
        np.clip(indice, 0, 100)
    )

    nivel = nivel_riesgo(indice)

    # --------------------------------------------------------
    # PRONÓSTICO DIARIO
    # --------------------------------------------------------

    fechas = pd.to_datetime(
        daily.get("time", [])
    )

    df_daily = pd.DataFrame({
        "fecha": fechas
    })

    for variable in DAILY_VARIABLES:

        valores = daily.get(
            variable,
            []
        )

        if len(valores) == len(df_daily):

            df_daily[variable] = valores

    return {

        "localidad": nodo["localidad"],
        "provincia": nodo["provincia"],
        "lat": nodo["lat"],
        "lon": nodo["lon"],

        "actual": actual,

        "indice": indice,
        "nivel": nivel,

        "humedad_0_7": humedad_0_7,
        "humedad_7_28": humedad_7_28,
        "humedad_28_100": humedad_28_100,
        "humedad_100_255": humedad_100_255,

        "humedad_suelo_promedio":
            humedad_suelo_promedio,

        "lluvia_24h": lluvia_24h,
        "lluvia_72": lluvia_72,
        "lluvia_futura_72":
            lluvia_futura_72,
        "lluvia_7d": lluvia_7d,

        "runoff_72": runoff_72,

        "tendencia_runoff":
            tendencia_runoff,

        "humedad_score":
            humedad_score,

        "lluvia_reciente_score":
            lluvia_reciente_score,

        "lluvia_futura_score":
            lluvia_futura_score,

        "runoff_score":
            runoff_score,

        "acumulacion_score":
            acumulacion_score,

        "lluvia_critica_score":
            lluvia_critica_score,

        "daily":
            df_daily,

        "horario":
            df_horario
    }


# ============================================================
# CARGA DE DATOS
# ============================================================

with st.spinner(
    "Actualizando datos meteorológicos ECMWF..."
):

    try:

        datos_api = obtener_datos_ecmwf()

        resultados = []

        if isinstance(datos_api, list):

            datos_por_nodo = datos_api

        else:

            datos_por_nodo = [
                datos_api
            ]

        for i, nodo in enumerate(NODOS):

            try:

                datos = datos_por_nodo[i]

                resultado = procesar_nodo(
                    nodo,
                    datos
                )

                resultados.append(resultado)

            except Exception as e:

                st.warning(
                    f"No se pudo procesar "
                    f"{nodo['localidad']}: {e}"
                )

    except Exception as e:

        st.error(
            f"No fue posible obtener los datos ECMWF: {e}"
        )

        st.stop()


# ============================================================
# DATAFRAME GENERAL
# ============================================================

df_resultados = pd.DataFrame(
    [
        {
            "Localidad":
                r["localidad"],

            "Provincia":
                r["provincia"],

            "Riesgo":
                r["nivel"],

            "Índice":
                round(r["indice"], 1),

            "Humedad suelo (%)":
                round(
                    r["humedad_suelo_promedio"] * 100,
                    1
                )
                if pd.notna(
                    r["humedad_suelo_promedio"]
                )
                else np.nan,

            "Lluvia 24h (mm)":
                round(r["lluvia_24h"], 1),

            "Lluvia 72h (mm)":
                round(r["lluvia_72"], 1),

            "Lluvia futura 72h (mm)":
                round(
                    r["lluvia_futura_72"],
                    1
                ),

            "Runoff 72h (mm)":
                round(
                    r["runoff_72"],
                    1
                )
        }

        for r in resultados
    ]
)


# ============================================================
# MÉTRICAS PRINCIPALES
# ============================================================

cantidad_muy_alto = sum(
    r["nivel"] == "MUY ALTO"
    for r in resultados
)

cantidad_alto = sum(
    r["nivel"] == "ALTO"
    for r in resultados
)

riesgo_maximo = (
    max(
        [r["indice"] for r in resultados]
    )
    if resultados
    else 0
)

riesgo_promedio = (
    np.mean(
        [r["indice"] for r in resultados]
    )
    if resultados
    else 0
)

c1, c2, c3, c4 = st.columns(4)

with c1:
    st.metric(
        "🔴 Muy alto",
        cantidad_muy_alto
    )

with c2:
    st.metric(
        "🟠 Alto",
        cantidad_alto
    )

with c3:
    st.metric(
        "📊 Riesgo máximo",
        f"{riesgo_maximo:.1f}/100"
    )

with c4:
    st.metric(
        "📈 Riesgo promedio",
        f"{riesgo_promedio:.1f}/100"
    )


# ============================================================
# ESTADO GENERAL
# ============================================================

if cantidad_muy_alto > 0:

    st.error(
        "🔴 Se detectan nodos con nivel MUY ALTO."
    )

elif cantidad_alto > 0:

    st.warning(
        "🟠 Se detectan nodos con nivel ALTO."
    )

else:

    st.success(
        "🟢 No se detectan nodos en nivel ALTO "
        "o MUY ALTO según el índice experimental."
    )


# ============================================================
# MAPA
# ============================================================

st.subheader("🗺️ Estado de los nodos")

try:

    import folium
    from streamlit_folium import st_folium

    mapa = folium.Map(
        location=[-31.0, -60.0],
        zoom_start=6,
        tiles="CartoDB positron"
    )

    colores = {
        "BAJO": "green",
        "MEDIO": "orange",
        "ALTO": "red",
        "MUY ALTO": "darkred"
    }

    for r in resultados:

        popup = f"""
        <b>{escape(r['localidad'])}</b><br>
        {escape(r['provincia'])}<br>
        Riesgo: {r['nivel']}<br>
        Índice: {r['indice']:.1f}/100<br>
        Humedad suelo:
        {r['humedad_suelo_promedio'] * 100:.1f}%
        """

        folium.CircleMarker(
            location=[
                r["lat"],
                r["lon"]
            ],
            radius=9,
            color=colores.get(
                r["nivel"],
                "gray"
            ),
            fill=True,
            fill_opacity=0.8,
            popup=folium.Popup(
                popup,
                max_width=280
            )
        ).add_to(mapa)

    st_folium(
        mapa,
        width=None,
        height=600
    )

except Exception as e:

    st.warning(
        f"No se pudo cargar el mapa: {e}"
    )


# ============================================================
# TABLA GENERAL DE NODOS
# ============================================================

st.subheader("📋 Estado de los nodos")

df_mostrar = df_resultados.copy()

st.dataframe(
    df_mostrar,
    use_container_width=True,
    hide_index=True
)


# ============================================================
# SELECCIÓN DE LOCALIDAD
# ============================================================

localidades = [
    r["localidad"]
    for r in resultados
]

localidad_seleccionada = st.selectbox(
    "📍 Seleccioná una localidad",
    localidades
)

row = next(
    r for r in resultados
    if r["localidad"] == localidad_seleccionada
)


# ============================================================
# DETALLE
# ============================================================

st.markdown("---")

st.subheader(
    f"📍 Análisis de {row['localidad']}"
)

d1, d2, d3, d4 = st.columns(4)

with d1:

    st.metric(
        "Nivel de riesgo",
        f"{emoji_riesgo(row['nivel'])} "
        f"{row['nivel']}"
    )

with d2:

    st.metric(
        "Índice",
        f"{row['indice']:.1f}/100"
    )

with d3:

    st.metric(
        "Lluvia 72h",
        f"{row['lluvia_72']:.1f} mm"
    )

with d4:

    st.metric(
        "Runoff 72h",
        f"{row['runoff_72']:.1f} mm"
    )


# ============================================================
# HUMEDAD DEL SUELO
# ============================================================

st.subheader("🌱 Humedad del suelo")

st.caption(
    "Contenido volumétrico de agua del suelo. "
    "Los valores se muestran como porcentaje; "
    "el modelo conserva internamente el valor decimal m³/m³."
)

s1, s2, s3, s4, s5 = st.columns(5)

with s1:

    valor = row["humedad_0_7"]

    st.metric(
        "0–7 cm",
        f"{valor * 100:.1f}%"
        if pd.notna(valor)
        else "—"
    )

with s2:

    valor = row["humedad_7_28"]

    st.metric(
        "7–28 cm",
        f"{valor * 100:.1f}%"
        if pd.notna(valor)
        else "—"
    )

with s3:

    valor = row["humedad_28_100"]

    st.metric(
        "28–100 cm",
        f"{valor * 100:.1f}%"
        if pd.notna(valor)
        else "—"
    )

with s4:

    valor = row["humedad_100_255"]

    st.metric(
        "100–255 cm",
        f"{valor * 100:.1f}%"
        if pd.notna(valor)
        else "—"
    )

with s5:

    valor = row["humedad_suelo_promedio"]

    st.metric(
        "Promedio",
        f"{valor * 100:.1f}%"
        if pd.notna(valor)
        else "—"
    )


# ============================================================
# 🌦️ PRONÓSTICO EXTENDIDO 15 DÍAS
# ============================================================

st.markdown("---")

st.subheader(
    "🌦️ Pronóstico extendido — 15 días"
)

st.markdown(
    f"""
    <div class="forecast-model">
        Modelo: <b>{MODELO}</b> ·
        Pronóstico diario para <b>{row['localidad']}</b>.
        Desplazá horizontalmente para ver los días siguientes.
    </div>
    """,
    unsafe_allow_html=True
)


# ============================================================
# FUNCIÓN PARA CONSTRUIR TARJETAS
# ============================================================

def crear_tarjeta_pronostico(dia):

    codigo = dia.get(
        "weather_code",
        np.nan
    )

    condicion, icono = interpretar_weather_code(
        codigo
    )

    fecha = pd.to_datetime(
        dia["fecha"]
    )

    dias_semana = [
        "Lunes",
        "Martes",
        "Miércoles",
        "Jueves",
        "Viernes",
        "Sábado",
        "Domingo"
    ]

    meses = [
        "enero",
        "febrero",
        "marzo",
        "abril",
        "mayo",
        "junio",
        "julio",
        "agosto",
        "septiembre",
        "octubre",
        "noviembre",
        "diciembre"
    ]

    nombre_dia = dias_semana[
        fecha.weekday()
    ]

    fecha_texto = (
        f"{nombre_dia}<br>"
        f"{fecha.day} de "
        f"{meses[fecha.month - 1]}"
    )

    tmax = dia.get(
        "temperature_2m_max",
        np.nan
    )

    tmin = dia.get(
        "temperature_2m_min",
        np.nan
    )

    lluvia = dia.get(
        "precipitation_sum",
        np.nan
    )

    probabilidad = dia.get(
        "precipitation_probability_max",
        np.nan
    )

    viento = dia.get(
        "wind_speed_10m_max",
        np.nan
    )

    rafaga = dia.get(
        "wind_gusts_10m_max",
        np.nan
    )

    direccion = direccion_viento(
        dia.get(
            "wind_direction_10m_dominant",
            np.nan
        )
    )

    horas_lluvia = dia.get(
        "precipitation_hours",
        np.nan
    )

    def numero(valor, decimales=1):

        if pd.isna(valor):
            return "—"

        return f"{float(valor):.{decimales}f}"

    tarjeta = f"""
    <div class="forecast-card">

        <div class="forecast-date">
            {fecha_texto}
        </div>

        <div class="forecast-icon">
            {icono}
        </div>

        <div class="forecast-condition">
            {escape(condicion)}
        </div>

        <div class="forecast-temp">

            <span class="forecast-temp-max">
                {numero(tmax, 0)}°
            </span>

            <span class="forecast-temp-min">
                {numero(tmin, 0)}°
            </span>

        </div>

        <div class="forecast-item">

            <span class="forecast-item-label">
                🌧️ Lluvia
            </span>

            <span class="forecast-item-value">
                {numero(lluvia)} mm
            </span>

        </div>

        <div class="forecast-item">

            <span class="forecast-item-label">
                💧 Probabilidad
            </span>

            <span class="forecast-item-value">
                {numero(probabilidad, 0)}%
            </span>

        </div>

        <div class="forecast-item">

            <span class="forecast-item-label">
                🌬️ Viento
            </span>

            <span class="forecast-item-value">
                {numero(viento, 0)} km/h
            </span>

        </div>

        <div class="forecast-item">

            <span class="forecast-item-label">
                💨 Ráfagas
            </span>

            <span class="forecast-item-value">
                {numero(rafaga, 0)} km/h
            </span>

        </div>

        <div class="forecast-item">

            <span class="forecast-item-label">
                🧭 Dirección
            </span>

            <span class="forecast-item-value">
                {direccion}
            </span>

        </div>

        <div class="forecast-item">

            <span class="forecast-item-label">
                ⏱️ Horas lluvia
            </span>

            <span class="forecast-item-value">
                {numero(horas_lluvia, 1)}
            </span>

        </div>

    </div>
    """

    return tarjeta


# ============================================================
# PREPARAR PRONÓSTICO
# ============================================================

df_forecast = row["daily"].copy()

hoy = pd.Timestamp(
    datetime.now(
        ZoneInfo(TZ)
    ).date()
)

df_forecast = df_forecast[
    df_forecast["fecha"] >= hoy
].copy()

df_forecast = df_forecast.head(15)


# ============================================================
# MOSTRAR TARJETAS
# ============================================================

if len(df_forecast) > 0:

    tarjetas = []

    for _, dia in df_forecast.iterrows():

        tarjetas.append(
            crear_tarjeta_pronostico(dia)
        )

    html_pronostico = (
        '<div class="forecast-container">'
        + "".join(tarjetas)
        + "</div>"
    )

    st.markdown(
        html_pronostico,
        unsafe_allow_html=True
    )

else:

    st.info(
        "No hay datos disponibles para el "
        "pronóstico extendido."
    )


# ============================================================
# LLUVIA Y ESCURRIMIENTO
# ============================================================

st.markdown("---")

st.subheader(
    "🌧️ Evolución de precipitación y escurrimiento"
)

df_h = row["horario"].copy()

df_h["precipitación"] = (
    df_h["precipitation"]
    .fillna(0)
)

df_h["runoff"] = (
    df_h["runoff"]
    .fillna(0)
)

fig_lluvia = go.Figure()

fig_lluvia.add_trace(
    go.Bar(
        x=df_h["time"],
        y=df_h["precipitación"],
        name="Precipitación"
    )
)

fig_lluvia.add_trace(
    go.Scatter(
        x=df_h["time"],
        y=df_h["runoff"],
        name="Runoff",
        mode="lines"
    )
)

fig_lluvia.update_layout(
    xaxis_title="Fecha",
    yaxis_title="mm",
    hovermode="x unified",
    height=400
)

st.plotly_chart(
    fig_lluvia,
    use_container_width=True
)


# ============================================================
# VARIABLES ATMOSFÉRICAS
# ============================================================

st.subheader(
    "🌤️ Variables atmosféricas"
)

actual = row["actual"]

a1, a2, a3, a4 = st.columns(4)

with a1:

    st.metric(
        "Temperatura",
        f"{actual.get('temperature_2m', np.nan):.1f} °C"
        if pd.notna(actual.get("temperature_2m", np.nan))
        else "—"
    )

with a2:

    st.metric(
        "Humedad relativa",
        f"{actual.get('relative_humidity_2m', np.nan):.0f}%"
        if pd.notna(actual.get("relative_humidity_2m", np.nan))
        else "—"
    )

with a3:

    st.metric(
        "Presión",
        f"{actual.get('pressure_msl', np.nan):.0f} hPa"
        if pd.notna(actual.get("pressure_msl", np.nan))
        else "—"
    )

with a4:

    st.metric(
        "Viento",
        f"{actual.get('wind_speed_10m', np.nan):.0f} km/h"
        if pd.notna(actual.get("wind_speed_10m", np.nan))
        else "—"
    )


# ============================================================
# VARIABLES CONVECTIVAS
# ============================================================

st.subheader(
    "⛈️ Variables atmosféricas y convectivas"
)

c1, c2, c3, c4 = st.columns(4)

with c1:

    cape = actual.get(
        "cape",
        np.nan
    )

    st.metric(
        "CAPE",
        f"{cape:.0f} J/kg"
        if pd.notna(cape)
        else "—"
    )

with c2:

    vpd = actual.get(
        "vapor_pressure_deficit",
        np.nan
    )

    st.metric(
        "VPD",
        f"{vpd:.2f} kPa"
        if pd.notna(vpd)
        else "—"
    )

with c3:

    dew = actual.get(
        "dew_point_2m",
        np.nan
    )

    st.metric(
        "Punto de rocío",
        f"{dew:.1f} °C"
        if pd.notna(dew)
        else "—"
    )

with c4:

    gust = actual.get(
        "wind_gusts_10m",
        np.nan
    )

    st.metric(
        "Ráfaga",
        f"{gust:.0f} km/h"
        if pd.notna(gust)
        else "—"
    )


# ============================================================
# COMPONENTES DEL ÍNDICE
# ============================================================

st.subheader(
    "📊 Componentes del índice de riesgo"
)

componentes = pd.DataFrame({
    "Componente": [
        "Humedad del suelo",
        "Lluvia reciente 72h",
        "Lluvia futura 72h",
        "Runoff 72h",
        "Acumulación 7 días",
        "Lluvia crítica"
    ],

    "Puntaje": [
        row["humedad_score"],
        row["lluvia_reciente_score"],
        row["lluvia_futura_score"],
        row["runoff_score"],
        row["acumulacion_score"],
        row["lluvia_critica_score"]
    ]
})

fig_componentes = px.bar(
    componentes,
    x="Componente",
    y="Puntaje",
    range_y=[0, 100],
    text_auto=".1f"
)

fig_componentes.update_layout(
    height=400,
    yaxis_title="Puntaje 0–100",
    xaxis_title=""
)

st.plotly_chart(
    fig_componentes,
    use_container_width=True
)


# ============================================================
# TENDENCIA HIDROLÓGICA
# ============================================================

st.subheader(
    "💧 Tendencia hidrológica"
)

st.info(
    f"Runoff reciente: **{row['tendencia_runoff']}**"
)


# ============================================================
# EXPORTACIÓN CSV
# ============================================================

st.markdown("---")

st.subheader(
    "📥 Exportación de datos"
)

csv_general = df_resultados.to_csv(
    index=False
).encode("utf-8-sig")

st.download_button(
    label="⬇️ Descargar estado de nodos CSV",
    data=csv_general,
    file_name="alerta_litoral_agro_nodos.csv",
    mime="text/csv"
)


# ============================================================
# METODOLOGÍA
# ============================================================

st.markdown("---")

with st.expander(
    "ℹ️ Metodología y limitaciones"
):

    st.markdown(
        f"""
### Alerta Litoral Agro {VERSION}

Sistema experimental de alerta temprana orientado
a evaluar el riesgo de anegamiento agropecuario.

**Modelo meteorológico**

{MODELO}

**Fuentes principales**

- Open-Meteo
- ECMWF IFS HRES
- NOAA GOES-19 ABI
- Windy como visualización complementaria

### Componentes principales del índice

El índice combina:

1. Humedad del suelo.
2. Precipitación antecedente de 72 horas.
3. Precipitación prevista para las próximas 72 horas.
4. Runoff antecedente de 72 horas.
5. Acumulación de precipitación de 7 días.
6. Componente de lluvia crítica.

### Niveles

- 🟢 **BAJO:** 0–34.9
- 🟡 **MEDIO:** 35–54.9
- 🟠 **ALTO:** 55–74.9
- 🔴 **MUY ALTO:** 75–100

### Humedad del suelo

Los datos de humedad de Open-Meteo representan
contenido volumétrico de agua del suelo en m³/m³.

Para facilitar la interpretación al usuario,
la interfaz los presenta como porcentaje.

Ejemplo:

`0.341 m³/m³ = 34.1%`

### Pronóstico extendido

El pronóstico de 15 días se utiliza principalmente
para planificación y análisis de tendencia.

La incertidumbre aumenta progresivamente con el
horizonte temporal.

### Limitaciones

Este sistema es experimental y no constituye
un sistema oficial de alerta meteorológica,
hidrológica o de protección civil.

Los pesos y umbrales utilizados deben ser
validados mediante series históricas,
eventos reales y métricas de desempeño.

Las variables meteorológicas y de suelo
proporcionadas por el modelo no sustituyen
mediciones locales.

El índice no debe utilizarse como único criterio
para decisiones críticas.

GOES se utiliza como fuente independiente de
visualización y no forma parte actualmente del
cálculo numérico del índice.
        """
    )


# ============================================================
# ESTADO DEL SISTEMA
# ============================================================

st.markdown("---")

st.subheader(
    "🟢 Estado del sistema"
)

st.write(
    f"""
    **Versión:** {VERSION}

    **Modelo:** {MODELO}

    **Nodos procesados:** {len(resultados)}/{len(NODOS)}

    **Actualización de datos:** cada 15 minutos

    **Última ejecución:**
    {datetime.now(ZoneInfo(TZ)).strftime('%d/%m/%Y %H:%M:%S')}
    """
)

st.caption(
    "Alerta Litoral Agro — prototipo experimental "
    "de monitoreo y alerta temprana."
)
