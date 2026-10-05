# ============================================================
# 🌧️ ALERTA LITORAL AGRO
# ============================================================
# Sistema experimental de alerta temprana para riesgo de
# anegamiento agropecuario.
#
# Regiones:
#   Santa Fe
#   Corrientes
#   Entre Ríos
#
# Fuentes:
#   Open-Meteo
#   ECMWF
#
# NO UTILIZA DATOS DE VIALIDAD.
#
# Versión: V3.3.2
# ============================================================

import streamlit as st
import requests
import pandas as pd
import numpy as np
import plotly.express as px

from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
import json
import hashlib


# ============================================================
# CONFIGURACIÓN
# ============================================================

st.set_page_config(
    page_title="Alerta Litoral Agro",
    page_icon="🌧️",
    layout="wide",
    initial_sidebar_state="expanded",
)

APP_NAME = "Alerta Litoral Agro"
VERSION = "V3.3.2"

TZ = ZoneInfo("America/Argentina/Buenos_Aires")

HISTORY_FILE = Path("historial_alerta_litoral.csv")
STATE_FILE = Path("telegram_alert_state.json")


# ============================================================
# NODOS DE MONITOREO
# ============================================================

NODOS = [

    # --------------------------------------------------------
    # SANTA FE
    # --------------------------------------------------------

    {
        "localidad": "Reconquista",
        "provincia": "Santa Fe",
        "lat": -29.144,
        "lon": -59.643,
    },

    {
        "localidad": "Avellaneda",
        "provincia": "Santa Fe",
        "lat": -29.117,
        "lon": -59.658,
    },

    {
        "localidad": "Vera",
        "provincia": "Santa Fe",
        "lat": -29.460,
        "lon": -60.213,
    },

    {
        "localidad": "Tostado",
        "provincia": "Santa Fe",
        "lat": -29.233,
        "lon": -61.769,
    },

    {
        "localidad": "Rafaela",
        "provincia": "Santa Fe",
        "lat": -31.250,
        "lon": -61.486,
    },

    {
        "localidad": "Santa Fe",
        "provincia": "Santa Fe",
        "lat": -31.633,
        "lon": -60.700,
    },

    {
        "localidad": "Esperanza",
        "provincia": "Santa Fe",
        "lat": -31.448,
        "lon": -60.932,
    },

    {
        "localidad": "Rosario",
        "provincia": "Santa Fe",
        "lat": -32.946,
        "lon": -60.639,
    },

    {
        "localidad": "Venado Tuerto",
        "provincia": "Santa Fe",
        "lat": -33.745,
        "lon": -61.968,
    },

    # --------------------------------------------------------
    # CORRIENTES
    # --------------------------------------------------------

    {
        "localidad": "Corrientes",
        "provincia": "Corrientes",
        "lat": -27.469,
        "lon": -58.830,
    },

    {
        "localidad": "Goya",
        "provincia": "Corrientes",
        "lat": -29.140,
        "lon": -59.263,
    },

    {
        "localidad": "Mercedes",
        "provincia": "Corrientes",
        "lat": -29.182,
        "lon": -58.075,
    },

    {
        "localidad": "Curuzú Cuatiá",
        "provincia": "Corrientes",
        "lat": -29.791,
        "lon": -58.054,
    },

    {
        "localidad": "Paso de los Libres",
        "provincia": "Corrientes",
        "lat": -29.713,
        "lon": -57.088,
    },

    {
        "localidad": "Santo Tomé",
        "provincia": "Corrientes",
        "lat": -28.549,
        "lon": -56.040,
    },

    # --------------------------------------------------------
    # ENTRE RÍOS
    # --------------------------------------------------------

    {
        "localidad": "Paraná",
        "provincia": "Entre Ríos",
        "lat": -31.741,
        "lon": -60.511,
    },

    {
        "localidad": "La Paz",
        "provincia": "Entre Ríos",
        "lat": -30.744,
        "lon": -59.645,
    },

    {
        "localidad": "Concordia",
        "provincia": "Entre Ríos",
        "lat": -31.393,
        "lon": -58.020,
    },
]


# ============================================================
# UTILIDADES
# ============================================================

def ahora():
    return datetime.now(TZ)


def clamp(valor, minimo=0, maximo=100):

    try:
        valor = float(valor)
    except Exception:
        return minimo

    return max(minimo, min(maximo, valor))


def normalizar_0_100(valor, minimo, maximo):

    try:
        valor = float(valor)
    except Exception:
        return 0

    if maximo <= minimo:
        return 0

    resultado = (
        (valor - minimo)
        / (maximo - minimo)
        * 100
    )

    return clamp(resultado)


def nivel_desde_indice(indice):

    if indice >= 75:
        return "MUY ALTO"

    if indice >= 55:
        return "ALTO"

    if indice >= 35:
        return "MEDIO"

    return "BAJO"


def emoji_nivel(nivel):

    return {
        "MUY ALTO": "🔴",
        "ALTO": "🟠",
        "MEDIO": "🟡",
        "BAJO": "🟢",
    }.get(nivel, "⚪")


def accion_desde_nivel(nivel):

    acciones = {

        "MUY ALTO":
            "Priorizar medidas preventivas. "
            "Evitar ingreso de maquinaria a sectores "
            "comprometidos y evaluar medidas preventivas "
            "sobre animales, cultivos e insumos.",

        "ALTO":
            "Reforzar el monitoreo. Evitar tareas que "
            "puedan compactar el suelo y revisar sectores "
            "bajos o con antecedentes de anegamiento.",

        "MEDIO":
            "Mantener vigilancia sobre lluvias, humedad "
            "del suelo y evolución del riesgo antes de "
            "realizar tareas sensibles.",

        "BAJO":
            "Condiciones actualmente favorables. "
            "Mantener el monitoreo meteorológico.",
    }

    return acciones.get(nivel, "")


# ============================================================
# OPEN-METEO
# ============================================================

@st.cache_data(ttl=900, show_spinner=False)
def obtener_datos_open_meteo(lat, lon):

    url = "https://api.open-meteo.com/v1/forecast"

    params = {

        "latitude": lat,
        "longitude": lon,

        "hourly": ",".join([
            "precipitation",
            "rain",
            "showers",
            "soil_moisture_0_to_1cm",
            "soil_moisture_1_to_3cm",
            "soil_moisture_3_to_9cm",
            "runoff",
        ]),

        "daily": ",".join([
            "precipitation_sum",
            "rain_sum",
            "showers_sum",
            "precipitation_probability_max",
        ]),

        "forecast_days": 7,

        "models": "ecmwf_ifs025",

        "timezone":
            "America/Argentina/Buenos_Aires",

        "temperature_unit": "celsius",

        "wind_speed_unit": "kmh",

        "precipitation_unit": "mm",
    }

    try:

        response = requests.get(
            url,
            params=params,
            timeout=30,
        )

        response.raise_for_status()

        return response.json()

    except Exception as error:

        return {
            "error": str(error)
        }


# ============================================================
# PROCESAMIENTO DEL NODO
# ============================================================

def procesar_nodo(nodo, datos):

    if not datos or "error" in datos:

        return {
            **nodo,

            "estado_datos": "ERROR",

            "indice": np.nan,

            "nivel": "SIN DATOS",

            "humedad": np.nan,

            "lluvia_24": np.nan,

            "lluvia_72": np.nan,

            "lluvia_7d": np.nan,

            "lluvia_futura_24": np.nan,

            "lluvia_futura_72": np.nan,

            "lluvia_futura_7d": np.nan,

            "runoff_24": np.nan,

            "runoff_72": np.nan,

            "runoff_7d": np.nan,

            "prob_lluvia": np.nan,

            "tendencia": "SIN DATOS",

            "direccion_hidrologica": "SIN DATOS",

            "velocidad_hidrologica": "SIN DATOS",

            "accion": "Sin datos suficientes.",
        }

    hourly = datos.get(
        "hourly",
        {}
    )

    daily = datos.get(
        "daily",
        {}
    )

    precipitacion = np.array(
        hourly.get(
            "precipitation",
            []
        ),
        dtype=float,
    )

    runoff = np.array(
        hourly.get(
            "runoff",
            []
        ),
        dtype=float,
    )

    soil_0_1 = np.array(
        hourly.get(
            "soil_moisture_0_to_1cm",
            []
        ),
        dtype=float,
    )

    soil_1_3 = np.array(
        hourly.get(
            "soil_moisture_1_to_3cm",
            []
        ),
        dtype=float,
    )

    soil_3_9 = np.array(
        hourly.get(
            "soil_moisture_3_to_9cm",
            []
        ),
        dtype=float,
    )


    # ========================================================
    # HUMEDAD
    # ========================================================

    humedad_componentes = []

    for array in [
        soil_0_1,
        soil_1_3,
        soil_3_9,
    ]:

        if len(array) > 0:

            validos = array[
                np.isfinite(array)
            ]

            if len(validos) > 0:

                humedad_componentes.append(
                    float(validos[0])
                )

    if humedad_componentes:

        humedad = float(
            np.mean(
                humedad_componentes
            )
        )

    else:

        humedad = 0.0


    # ========================================================
    # LLUVIA RECIENTE
    # ========================================================

    lluvia_24 = float(
        np.nansum(
            precipitacion[:24]
        )
    )

    lluvia_72 = float(
        np.nansum(
            precipitacion[:72]
        )
    )

    lluvia_7d = float(
        np.nansum(
            precipitacion[:168]
        )
    )


    # ========================================================
    # RUNOFF
    # ========================================================

    runoff_24 = float(
        np.nansum(
            runoff[:24]
        )
    )

    runoff_72 = float(
        np.nansum(
            runoff[:72]
        )
    )

    runoff_7d = float(
        np.nansum(
            runoff[:168]
        )
    )


    # ========================================================
    # PRONÓSTICO
    # ========================================================

    daily_prec = np.array(
        daily.get(
            "precipitation_sum",
            []
        ),
        dtype=float,
    )

    daily_probability = np.array(
        daily.get(
            "precipitation_probability_max",
            []
        ),
        dtype=float,
    )

    lluvia_futura_24 = (
        float(daily_prec[0])
        if len(daily_prec) >= 1
        else 0
    )

    lluvia_futura_72 = (
        float(
            np.nansum(
                daily_prec[:3]
            )
        )
        if len(daily_prec) > 0
        else 0
    )

    lluvia_futura_7d = (
        float(
            np.nansum(
                daily_prec[:7]
            )
        )
        if len(daily_prec) > 0
        else 0
    )

    prob_lluvia = (

        float(
            np.nanmax(
                daily_probability[:3]
            )
        )

        if len(daily_probability) > 0

        else 0
    )


    # ========================================================
    # COMPONENTE HUMEDAD
    # ========================================================

    humedad_score = normalizar_0_100(
        humedad,
        0.15,
        0.45,
    )


    # ========================================================
    # COMPONENTE LLUVIA
    # ========================================================

    lluvia_reciente_score = normalizar_0_100(
        lluvia_72,
        10,
        100,
    )

    lluvia_futura_score = normalizar_0_100(
        lluvia_futura_72,
        10,
        100,
    )


    # ========================================================
    # RUNOFF
    # ========================================================

    runoff_score = normalizar_0_100(
        runoff_72,
        1,
        30,
    )


    # ========================================================
    # ACUMULACIÓN
    # ========================================================

    acumulacion_score = normalizar_0_100(
        lluvia_7d,
        30,
        180,
    )


    # ========================================================
    # REFERENCIA INA
    # ========================================================

    ina_score = 0

    if lluvia_24 >= 50:

        ina_score += 40

    elif lluvia_24 >= 30:

        ina_score += 25

    elif lluvia_24 >= 20:

        ina_score += 15


    if lluvia_72 >= 100:

        ina_score += 40

    elif lluvia_72 >= 70:

        ina_score += 30

    elif lluvia_72 >= 50:

        ina_score += 20


    ina_score = clamp(
        ina_score
    )


    # ========================================================
    # TENDENCIA HIDROLÓGICA
    # ========================================================
    #
    # MUY IMPORTANTE:
    #
    # SUBIDA = aumenta riesgo
    # ESTABLE = neutra
    # BAJADA = disminuye riesgo
    #
    # NO utilizar abs().
    # ========================================================

    if len(runoff) >= 72:

        runoff_reciente = float(
            np.nanmean(
                runoff[:12]
            )
        )

        runoff_anterior = float(
            np.nanmean(
                runoff[36:72]
            )
        )

        diferencia = (
            runoff_reciente
            - runoff_anterior
        )

        if diferencia > 0.5:

            direccion_hidrologica = "SUBIDA"

            if diferencia >= 2:

                velocidad_hidrologica = "RÁPIDA"

                tendencia = "SUBIDA RÁPIDA"

            else:

                velocidad_hidrologica = "LENTA"

                tendencia = "SUBIDA LENTA"

        elif diferencia < -0.5:

            direccion_hidrologica = "BAJADA"

            if diferencia <= -2:

                velocidad_hidrologica = "RÁPIDA"

                tendencia = "BAJADA RÁPIDA"

            else:

                velocidad_hidrologica = "LENTA"

                tendencia = "BAJADA LENTA"

        else:

            direccion_hidrologica = "ESTABLE"

            velocidad_hidrologica = "NEUTRA"

            tendencia = "ESTABLE"

    else:

        direccion_hidrologica = "SIN DATOS"

        velocidad_hidrologica = "SIN DATOS"

        tendencia = "SIN DATOS"


    # ========================================================
    # EFECTO TENDENCIA
    # ========================================================

    tendencia_factor = 0

    if direccion_hidrologica == "SUBIDA":

        if velocidad_hidrologica == "RÁPIDA":

            tendencia_factor = 10

        else:

            tendencia_factor = 5

    elif direccion_hidrologica == "BAJADA":

        if velocidad_hidrologica == "RÁPIDA":

            tendencia_factor = -8

        else:

            tendencia_factor = -4


    # ========================================================
    # ÍNDICE FINAL
    # ========================================================

    indice = (

        humedad_score * 0.25

        + lluvia_reciente_score * 0.20

        + lluvia_futura_score * 0.20

        + runoff_score * 0.10

        + acumulacion_score * 0.10

        + ina_score * 0.15

    )

    indice += tendencia_factor

    indice = clamp(
        indice
    )


    # ========================================================
    # CONDICIÓN CRÍTICA
    # ========================================================

    if (

        lluvia_72 >= 120

        or lluvia_7d >= 220

        or runoff_72 >= 50

    ):

        indice = max(
            indice,
            75
        )


    nivel = nivel_desde_indice(
        indice
    )

    accion = accion_desde_nivel(
        nivel
    )


    # ========================================================
    # RESULTADO
    # ========================================================

    return {

        **nodo,

        "estado_datos": "OK",

        "indice": round(
            indice,
            1
        ),

        "nivel": nivel,

        "humedad": round(
            humedad,
            3
        ),

        "lluvia_24": round(
            lluvia_24,
            1
        ),

        "lluvia_72": round(
            lluvia_72,
            1
        ),

        "lluvia_7d": round(
            lluvia_7d,
            1
        ),

        "lluvia_futura_24": round(
            lluvia_futura_24,
            1
        ),

        "lluvia_futura_72": round(
            lluvia_futura_72,
            1
        ),

        "lluvia_futura_7d": round(
            lluvia_futura_7d,
            1
        ),

        "runoff_24": round(
            runoff_24,
            2
        ),

        "runoff_72": round(
            runoff_72,
            2
        ),

        "runoff_7d": round(
            runoff_7d,
            2
        ),

        "prob_lluvia": round(
            prob_lluvia,
            1
        ),

        "tendencia": tendencia,

        "direccion_hidrologica":
            direccion_hidrologica,

        "velocidad_hidrologica":
            velocidad_hidrologica,

        "accion": accion,

        "actualizado":
            ahora().strftime(
                "%d/%m/%Y %H:%M"
            ),
    }


# ============================================================
# OBTENER TODOS LOS NODOS
# ============================================================

def obtener_todos_los_nodos():

    resultados = []

    for nodo in NODOS:

        datos = obtener_datos_open_meteo(
            nodo["lat"],
            nodo["lon"]
        )

        resultado = procesar_nodo(
            nodo,
            datos
        )

        resultados.append(
            resultado
        )

    return pd.DataFrame(
        resultados
    )


# ============================================================
# HISTORIAL
# ============================================================

def guardar_historial(df):

    if df.empty:
        return

    columnas = [

        "actualizado",

        "localidad",

        "provincia",

        "indice",

        "nivel",

        "humedad",

        "lluvia_24",

        "lluvia_72",

        "lluvia_7d",

        "lluvia_futura_24",

        "lluvia_futura_72",

        "lluvia_futura_7d",

        "runoff_24",

        "runoff_72",

        "runoff_7d",

        "tendencia",
    ]

    columnas_validas = [
        columna
        for columna in columnas
        if columna in df.columns
    ]

    nuevo = df[
        columnas_validas
    ].copy()

    try:

        if HISTORY_FILE.exists():

            anterior = pd.read_csv(
                HISTORY_FILE
            )

            combinado = pd.concat(
                [
                    anterior,
                    nuevo
                ],
                ignore_index=True
            )

        else:

            combinado = nuevo

        combinado.to_csv(
            HISTORY_FILE,
            index=False
        )

    except Exception:

        pass


# ============================================================
# TELEGRAM
# ============================================================

def obtener_secrets_telegram():

    try:

        token = st.secrets.get(
            "TELEGRAM_BOT_TOKEN",
            ""
        )

        chat_id = st.secrets.get(
            "TELEGRAM_CHAT_ID",
            ""
        )

        return token, chat_id

    except Exception:

        return "", ""


def cargar_estado_telegram():

    if not STATE_FILE.exists():

        return {}

    try:

        with open(
            STATE_FILE,
            "r",
            encoding="utf-8"
        ) as archivo:

            return json.load(
                archivo
            )

    except Exception:

        return {}


def guardar_estado_telegram(
    estado
):

    try:

        with open(
            STATE_FILE,
            "w",
            encoding="utf-8"
        ) as archivo:

            json.dump(
                estado,
                archivo,
                ensure_ascii=False,
                indent=2
            )

    except Exception:

        pass


def enviar_telegram(
    mensaje
):

    token, chat_id = (
        obtener_secrets_telegram()
    )

    if not token or not chat_id:

        return False

    url = (
        "https://api.telegram.org/bot"
        f"{token}/sendMessage"
    )

    payload = {

        "chat_id": chat_id,

        "text": mensaje,

        "parse_mode": "HTML",

        "disable_web_page_preview": True,
    }

    try:

        response = requests.post(
            url,
            json=payload,
            timeout=15
        )

        return response.ok

    except Exception:

        return False


def generar_mensaje_alerta(
    row
):

    emoji = emoji_nivel(
        row["nivel"]
    )

    return f"""
{emoji} <b>ALERTA LITORAL AGRO</b>

<b>Nivel:</b> {row["nivel"]}
<b>Localidad:</b> {row["localidad"]}
<b>Provincia:</b> {row["provincia"]}

<b>Índice de riesgo:</b> {row["indice"]}/100

<b>Humedad superficial:</b>
{row["humedad"]:.3f}

<b>Lluvia reciente:</b>
24 h: {row["lluvia_24"]:.1f} mm
72 h: {row["lluvia_72"]:.1f} mm
7 días: {row["lluvia_7d"]:.1f} mm

<b>Pronóstico:</b>
24 h: {row["lluvia_futura_24"]:.1f} mm
72 h: {row["lluvia_futura_72"]:.1f} mm
7 días: {row["lluvia_futura_7d"]:.1f} mm

<b>Runoff 72 h:</b>
{row["runoff_72"]:.2f}

<b>Tendencia:</b>
{row["tendencia"]}

<b>Acción recomendada:</b>
{row["accion"]}

<i>Actualizado: {row["actualizado"]}</i>

⚠️ Sistema experimental.
No reemplaza avisos oficiales.
""".strip()


# ============================================================
# ANTI-SPAM TELEGRAM
# ============================================================

def clave_alerta(row):

    contenido = (
        f'{row["localidad"]}|'
        f'{row["nivel"]}|'
        f'{round(row["indice"] / 5)}'
    )

    return hashlib.md5(
        contenido.encode(
            "utf-8"
        )
    ).hexdigest()


def procesar_alertas_telegram(
    df
):

    token, chat_id = (
        obtener_secrets_telegram()
    )

    if not token or not chat_id:

        return

    estado = cargar_estado_telegram()

    for _, row in df.iterrows():

        if row["nivel"] not in [
            "ALTO",
            "MUY ALTO",
        ]:

            continue

        clave = clave_alerta(
            row
        )

        clave_localidad = (
            f'{row["provincia"]}_'
            f'{row["localidad"]}'
        )

        ultima = estado.get(
            clave_localidad
        )

        if ultima == clave:

            continue

        mensaje = (
            generar_mensaje_alerta(
                row
            )
        )

        enviado = enviar_telegram(
            mensaje
        )

        if enviado:

            estado[
                clave_localidad
            ] = clave

    guardar_estado_telegram(
        estado
    )


# ============================================================
# MAPA
# ============================================================

def crear_mapa(df):

    mapa_df = df.copy()

    # --------------------------------------------------------
    # Colores de riesgo
    # --------------------------------------------------------

    colores = {

        "BAJO": "green",

        "MEDIO": "yellow",

        "ALTO": "orange",

        "MUY ALTO": "red",
    }

    # --------------------------------------------------------
    # Mapa moderno de Plotly
    # --------------------------------------------------------

    fig = px.scatter_map(

        mapa_df,

        lat="lat",

        lon="lon",

        color="nivel",

        size="indice",

        size_max=22,

        hover_name="localidad",

        hover_data={

            "provincia": True,

            "indice": True,

            "nivel": True,

            "humedad": True,

            "lluvia_24": True,

            "lluvia_72": True,

            "lluvia_7d": True,

            "lluvia_futura_24": True,

            "lluvia_futura_72": True,

            "runoff_72": True,

            "tendencia": True,

            "lat": False,

            "lon": False,
        },

        color_discrete_map=colores,

        zoom=5,

        height=650,
    )

    # --------------------------------------------------------
    # Estilo del mapa
    # --------------------------------------------------------

    fig.update_layout(

        map_style="open-street-map",

        margin={

            "r": 0,

            "t": 0,

            "l": 0,

            "b": 0,
        },

        legend={

            "title":
                "Nivel de riesgo",
        },
    )

    return fig


# ============================================================
# INTERFAZ
# ============================================================

st.title(
    "🌧️ Alerta Litoral Agro"
)

st.subheader(
    "Sistema experimental de alerta temprana "
    "para riesgo de anegamiento agropecuario"
)

st.caption(
    f"Versión {VERSION} · "
    "Santa Fe · Corrientes · Entre Ríos"
)


# ============================================================
# SIDEBAR
# ============================================================

with st.sidebar:

    st.header(
        "⚙️ Control"
    )

    actualizar = st.button(
        "🔄 Actualizar datos",
        use_container_width=True,
    )

    st.divider()

    st.markdown(
        """
### Fuentes

- Open-Meteo
- ECMWF
- Precipitación
- Humedad del suelo
- Runoff
- Umbrales de referencia

**No se utilizan datos de Vialidad.**
"""
    )

    st.divider()

    st.markdown(
        """
### Niveles

🟢 **BAJO**

🟡 **MEDIO**

🟠 **ALTO**

🔴 **MUY ALTO**
"""
    )


# ============================================================
# DATOS
# ============================================================

if (

    actualizar

    or "df_alerta"
    not in st.session_state

):

    with st.spinner(
        "Consultando datos meteorológicos..."
    ):

        df_alerta = (
            obtener_todos_los_nodos()
        )

        st.session_state[
            "df_alerta"
        ] = df_alerta

        guardar_historial(
            df_alerta
        )

else:

    df_alerta = (
        st.session_state[
            "df_alerta"
        ]
    )


# ============================================================
# TELEGRAM
# ============================================================

procesar_alertas_telegram(
    df_alerta
)


# ============================================================
# DATOS VÁLIDOS
# ============================================================

df_validos = df_alerta[
    df_alerta[
        "estado_datos"
    ] == "OK"
].copy()


if df_validos.empty:

    st.error(
        "No se pudieron obtener datos válidos."
    )

    st.stop()


# ============================================================
# MÉTRICAS
# ============================================================

maximo = (
    df_validos[
        "indice"
    ].max()
)

promedio = (
    df_validos[
        "indice"
    ].mean()
)

cantidad_alto = len(
    df_validos[
        df_validos[
            "nivel"
        ] == "ALTO"
    ]
)

cantidad_muy_alto = len(
    df_validos[
        df_validos[
            "nivel"
        ] == "MUY ALTO"
    ]
)


# ============================================================
# MÉTRICAS SUPERIORES
# ============================================================

col1, col2, col3, col4 = (
    st.columns(4)
)

with col1:

    st.metric(
        "🔴 Muy alto",
        cantidad_muy_alto,
    )

with col2:

    st.metric(
        "🟠 Alto",
        cantidad_alto,
    )

with col3:

    st.metric(
        "📊 Riesgo máximo",
        f"{maximo:.1f}/100",
    )

with col4:

    st.metric(
        "📈 Riesgo promedio",
        f"{promedio:.1f}/100",
    )


# ============================================================
# ESTADO GENERAL
# ============================================================

if cantidad_muy_alto > 0:

    st.error(
        f"🔴 Se detectan "
        f"{cantidad_muy_alto} nodos "
        "en nivel MUY ALTO."
    )

elif cantidad_alto > 0:

    st.warning(
        f"🟠 Se detectan "
        f"{cantidad_alto} nodos "
        "en nivel ALTO."
    )

else:

    st.success(
        "🟢 No se detectan nodos "
        "en nivel ALTO o MUY ALTO "
        "según el índice actual."
    )


# ============================================================
# MAPA
# ============================================================

st.header(
    "🗺️ Mapa de riesgo"
)

fig_mapa = crear_mapa(
    df_validos
)

st.plotly_chart(
    fig_mapa,
    use_container_width=True
)


# ============================================================
# TABLA
# ============================================================

st.header(
    "📋 Estado de los nodos"
)

tabla = df_validos[
    [
        "localidad",
        "provincia",
        "indice",
        "nivel",
        "humedad",
        "lluvia_24",
        "lluvia_72",
        "lluvia_7d",
        "lluvia_futura_24",
        "lluvia_futura_72",
        "lluvia_futura_7d",
        "runoff_72",
        "tendencia",
    ]
].copy()


tabla.columns = [

    "Localidad",

    "Provincia",

    "Riesgo",

    "Nivel",

    "Humedad",

    "Lluvia 24h",

    "Lluvia 72h",

    "Lluvia 7d",

    "Pronóstico 24h",

    "Pronóstico 72h",

    "Pronóstico 7d",

    "Runoff 72h",

    "Tendencia",
]


st.dataframe(

    tabla,

    use_container_width=True,

    hide_index=True,
)


# ============================================================
# DETALLE
# ============================================================

st.header(
    "🔎 Análisis por localidad"
)

localidades = sorted(
    df_validos[
        "localidad"
    ].tolist()
)

localidad_seleccionada = (
    st.selectbox(
        "Seleccionar localidad",
        localidades,
    )
)

row = df_validos[
    df_validos[
        "localidad"
    ]
    == localidad_seleccionada
].iloc[0]


st.markdown(
    f"""
## {emoji_nivel(row["nivel"])} {row["nivel"]}

### {row["localidad"]}, {row["provincia"]}

### Índice: {row["indice"]:.1f}/100

**Tendencia:** {row["tendencia"]}

**Dirección:** {row["direccion_hidrologica"]}

**Velocidad:** {row["velocidad_hidrologica"]}
"""
)


d1, d2, d3, d4 = (
    st.columns(4)
)

with d1:

    st.metric(
        "Humedad",
        f'{row["humedad"]:.3f}',
    )

with d2:

    st.metric(
        "Lluvia 24h",
        f'{row["lluvia_24"]:.1f} mm',
    )

with d3:

    st.metric(
        "Lluvia 72h",
        f'{row["lluvia_72"]:.1f} mm',
    )

with d4:

    st.metric(
        "Runoff 72h",
        f'{row["runoff_72"]:.2f}',
    )


st.info(
    f'💡 **Acción recomendada:** '
    f'{row["accion"]}'
)


# ============================================================
# PRECIPITACIÓN
# ============================================================

st.header(
    "🌧️ Precipitación"
)

precipitacion_df = pd.DataFrame({

    "Periodo": [

        "Últimas 24 h",

        "Últimas 72 h",

        "Últimos 7 días",

        "Pronóstico 24 h",

        "Pronóstico 72 h",

        "Pronóstico 7 días",
    ],

    "mm": [

        row["lluvia_24"],

        row["lluvia_72"],

        row["lluvia_7d"],

        row["lluvia_futura_24"],

        row["lluvia_futura_72"],

        row["lluvia_futura_7d"],
    ],
})


fig_prec = px.bar(

    precipitacion_df,

    x="Periodo",

    y="mm",

    title=(
        "Precipitación reciente "
        "y prevista"
    ),
)

st.plotly_chart(
    fig_prec,
    use_container_width=True,
)


# ============================================================
# COMPONENTES
# ============================================================

st.header(
    "📊 Componentes del riesgo"
)

componentes = pd.DataFrame({

    "Componente": [

        "Humedad del suelo",

        "Lluvia reciente",

        "Lluvia futura",

        "Runoff",

        "Acumulación",
    ],

    "Valor": [

        normalizar_0_100(
            row["humedad"],
            0.15,
            0.45,
        ),

        normalizar_0_100(
            row["lluvia_72"],
            10,
            100,
        ),

        normalizar_0_100(
            row["lluvia_futura_72"],
            10,
            100,
        ),

        normalizar_0_100(
            row["runoff_72"],
            1,
            30,
        ),

        normalizar_0_100(
            row["lluvia_7d"],
            30,
            180,
        ),
    ],
})


fig_componentes = px.bar(

    componentes,

    x="Componente",

    y="Valor",

    range_y=[
        0,
        100
    ],

    title=(
        "Componentes "
        "normalizados del riesgo"
    ),
)

st.plotly_chart(
    fig_componentes,
    use_container_width=True,
)


# ============================================================
# HISTORIAL
# ============================================================

st.header(
    "📈 Historial"
)

if HISTORY_FILE.exists():

    try:

        historial = pd.read_csv(
            HISTORY_FILE
        )

        if not historial.empty:

            localidades_historial = sorted(
                historial[
                    "localidad"
                ]
                .dropna()
                .unique()
                .tolist()
            )

            localidad_hist = (
                st.selectbox(
                    "Localidad para historial",
                    localidades_historial,
                    key="hist_localidad",
                )
            )

            hist_local = historial[
                historial[
                    "localidad"
                ]
                == localidad_hist
            ].copy()

            if not hist_local.empty:

                hist_local[
                    "actualizado"
                ] = pd.to_datetime(
                    hist_local[
                        "actualizado"
                    ],
                    dayfirst=True,
                    errors="coerce",
                )

                hist_local = (
                    hist_local
                    .sort_values(
                        "actualizado"
                    )
                )

                fig_hist = px.line(

                    hist_local,

                    x="actualizado",

                    y="indice",

                    markers=True,

                    title=(
                        "Evolución del riesgo — "
                        f"{localidad_hist}"
                    ),
                )

                fig_hist.update_yaxes(
                    range=[
                        0,
                        100
                    ]
                )

                st.plotly_chart(
                    fig_hist,
                    use_container_width=True,
                )

                csv_hist = (
                    hist_local
                    .to_csv(
                        index=False
                    )
                    .encode(
                        "utf-8"
                    )
                )

                st.download_button(

                    "⬇️ Descargar historial CSV",

                    csv_hist,

                    file_name=(
                        "historial_"
                        "alerta_litoral.csv"
                    ),

                    mime="text/csv",
                )

    except Exception as error:

        st.warning(
            "No se pudo cargar el historial: "
            f"{error}"
        )

else:

    st.info(
        "El historial comenzará a generarse "
        "cuando la aplicación procese datos."
    )


# ============================================================
# EXPORTAR
# ============================================================

st.header(
    "⬇️ Exportar estado actual"
)

csv_actual = (
    df_validos
    .to_csv(
        index=False
    )
    .encode(
        "utf-8"
    )
)

st.download_button(

    "Descargar estado actual CSV",

    csv_actual,

    file_name=(
        "alerta_litoral_agro_actual.csv"
    ),

    mime="text/csv",
)


# ============================================================
# METODOLOGÍA
# ============================================================

with st.expander(
    "📚 Metodología y limitaciones"
):

    st.markdown(
        """
### ¿Qué evalúa el sistema?

El índice integra:

- humedad del suelo;
- precipitación reciente;
- precipitación acumulada;
- precipitación prevista;
- runoff;
- intensidad de precipitación;
- tendencia hidrológica.

### Tendencia hidrológica

El sistema diferencia:

**SUBIDA**

Aumenta el riesgo.

**ESTABLE**

Efecto neutro.

**BAJADA**

Reduce el riesgo.

También diferencia entre:

- subida rápida;
- subida lenta;
- bajada rápida;
- bajada lenta;
- estado estable.

### Índice de riesgo

El resultado se expresa entre:

**0 y 100**

| Índice | Nivel |
|---:|---|
| 0–34.9 | 🟢 BAJO |
| 35–54.9 | 🟡 MEDIO |
| 55–74.9 | 🟠 ALTO |
| 75–100 | 🔴 MUY ALTO |

### Uso

El sistema tiene como objetivo ayudar a
identificar anticipadamente condiciones
potencialmente favorables al anegamiento
agropecuario.

### Limitaciones

Es un sistema experimental.

No constituye una alerta oficial,
pronóstico hidrológico oficial ni reemplaza
la información de organismos competentes.
"""
    )


# ============================================================
# ESTADO DEL SISTEMA
# ============================================================

st.header(
    "🟢 Estado del sistema"
)

st.write(
    "Última actualización: "
    f"**{ahora().strftime('%d/%m/%Y %H:%M:%S')}**"
)

st.write(
    "Nodos procesados: "
    f"**{len(df_validos)} / {len(NODOS)}**"
)

st.write(
    "Fuente meteorológica principal: "
    "**Open-Meteo / ECMWF**"
)

telegram_token, telegram_chat = (
    obtener_secrets_telegram()
)

if telegram_token and telegram_chat:

    st.write(
        "Alertas Telegram: **🟢 configuradas**"
    )

else:

    st.write(
        "Alertas Telegram: "
        "**⚪ no configuradas**"
    )

st.caption(
    f"{APP_NAME} · {VERSION} · "
    "Sistema experimental"
)
