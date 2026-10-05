import math
import requests
import pandas as pd
import streamlit as st
import plotly.graph_objects as go
from datetime import datetime
from zoneinfo import ZoneInfo
import streamlit.components.v1 as components
from urllib.parse import urlencode

# ============================================================
# ALERTA LITORAL AGRO — V3.3.3
# Prototipo de alerta temprana para riesgo de anegamiento
# agropecuario en Santa Fe, Corrientes y Entre Ríos.
#
# Fuentes:
# - Open-Meteo / ECMWF
# - INA / DSIyAH
# - Windy
# - NOAA GOES-19
#
# Telegram:
# - Configuración mediante Streamlit Secrets
#
# IMPORTANTE:
# El índice de riesgo y los umbrales son EXPERIMENTALES.
# ============================================================

st.set_page_config(
    page_title="Alerta Litoral Agro",
    page_icon="🌧️",
    layout="wide",
)

APP_VERSION = "3.3.3"
TZ = ZoneInfo("America/Argentina/Buenos_Aires")
REQUEST_TIMEOUT = 25

# ============================================================
# FUENTES
# ============================================================

OPEN_METEO_ECMWF = "https://api.open-meteo.com/v1/ecmwf"
OPEN_METEO_FORECAST = "https://api.open-meteo.com/v1/forecast"

INA_BASE = "https://alerta.ina.gob.ar/pub/datos"
INA_ESTACIONES = f"{INA_BASE}/estaciones"
INA_SERIES = f"{INA_BASE}/series"
INA_DATOS = f"{INA_BASE}/datos"

INA_VAR_ALTURA = 2
INA_VAR_CAUDAL = 4

WINDY_EMBED_BASE = "https://embed.windy.com/embed2.html"

GOES19_GEOCOLOR_URL = (
    "https://www.goes.noaa.gov/sector_band.php"
    "?band=GEOCOLOR&length=24&sat=G19&sector=ssa"
)

GOES19_GLM_URL = (
    "https://www.goes.noaa.gov/sector_band.php"
    "?band=EXTENT3&length=12&sat=G19&sector=ssa"
)

GOES19_SECTOR_URL = (
    "https://www.goes.noaa.gov/sector.php"
    "?sat=G19&sector=ssa"
)

# ============================================================
# NODOS DE MONITOREO
# ============================================================

NODOS = [
    {
        "nombre": "Santa Fe",
        "provincia": "Santa Fe",
        "lat": -31.6333,
        "lon": -60.7000,
        "vulnerabilidad": 82,
    },
    {
        "nombre": "Reconquista",
        "provincia": "Santa Fe",
        "lat": -29.1500,
        "lon": -59.6500,
        "vulnerabilidad": 70,
    },
    {
        "nombre": "Rafaela",
        "provincia": "Santa Fe",
        "lat": -31.2500,
        "lon": -61.4833,
        "vulnerabilidad": 65,
    },
    {
        "nombre": "Rosario",
        "provincia": "Santa Fe",
        "lat": -32.9500,
        "lon": -60.6667,
        "vulnerabilidad": 78,
    },
    {
        "nombre": "San Javier",
        "provincia": "Santa Fe",
        "lat": -30.5833,
        "lon": -59.9333,
        "vulnerabilidad": 80,
    },
    {
        "nombre": "Tostado",
        "provincia": "Santa Fe",
        "lat": -29.2333,
        "lon": -61.7667,
        "vulnerabilidad": 55,
    },
    {
        "nombre": "Corrientes",
        "provincia": "Corrientes",
        "lat": -27.4833,
        "lon": -58.8167,
        "vulnerabilidad": 82,
    },
    {
        "nombre": "Goya",
        "provincia": "Corrientes",
        "lat": -29.1500,
        "lon": -59.2667,
        "vulnerabilidad": 78,
    },
    {
        "nombre": "Bella Vista",
        "provincia": "Corrientes",
        "lat": -28.5000,
        "lon": -59.0500,
        "vulnerabilidad": 72,
    },
    {
        "nombre": "Ituzaingó",
        "provincia": "Corrientes",
        "lat": -27.5833,
        "lon": -56.6833,
        "vulnerabilidad": 68,
    },
    {
        "nombre": "Mercedes",
        "provincia": "Corrientes",
        "lat": -29.1833,
        "lon": -58.0833,
        "vulnerabilidad": 58,
    },
    {
        "nombre": "Paso de los Libres",
        "provincia": "Corrientes",
        "lat": -29.7167,
        "lon": -57.0833,
        "vulnerabilidad": 65,
    },
    {
        "nombre": "Paraná",
        "provincia": "Entre Ríos",
        "lat": -31.7333,
        "lon": -60.5333,
        "vulnerabilidad": 75,
    },
    {
        "nombre": "La Paz",
        "provincia": "Entre Ríos",
        "lat": -30.7333,
        "lon": -59.6500,
        "vulnerabilidad": 72,
    },
    {
        "nombre": "Concordia",
        "provincia": "Entre Ríos",
        "lat": -31.4000,
        "lon": -58.0167,
        "vulnerabilidad": 84,
    },
    {
        "nombre": "Gualeguaychú",
        "provincia": "Entre Ríos",
        "lat": -33.0167,
        "lon": -58.5167,
        "vulnerabilidad": 80,
    },
    {
        "nombre": "Concepción del Uruguay",
        "provincia": "Entre Ríos",
        "lat": -32.4833,
        "lon": -58.2333,
        "vulnerabilidad": 76,
    },
    {
        "nombre": "Villaguay",
        "provincia": "Entre Ríos",
        "lat": -31.8500,
        "lon": -59.0167,
        "vulnerabilidad": 63,
    },
]

# ============================================================
# NIVELES DE RIESGO
# ============================================================

RIESGOS = [
    ("BAJO", 0, 24, "🟢"),
    ("MODERADO", 25, 49, "🟡"),
    ("ALTO", 50, 74, "🟠"),
    ("MUY ALTO", 75, 100, "🔴"),
]


# ============================================================
# FUNCIONES GENERALES
# ============================================================

def ahora():
    return datetime.now(TZ)


def emoji_riesgo(nivel):
    for nombre, _, _, emoji in RIESGOS:
        if nivel == nombre:
            return emoji

    return "⚪"


def nivel_riesgo(score):
    score = max(0, min(100, float(score)))

    for nombre, minimo, maximo, _ in RIESGOS:
        if minimo <= score <= maximo:
            return nombre

    return "MUY ALTO"


def safe_float(value):
    try:
        if value is None:
            return None

        if isinstance(value, str) and not value.strip():
            return None

        return float(value)

    except Exception:
        return None


def distancia_km(lat1, lon1, lat2, lon2):
    radio = 6371.0

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

    return 2 * radio * math.asin(math.sqrt(a))


# ============================================================
# SCORE METEOROLÓGICO
# ============================================================

def calcular_score_lluvia_24h(mm):

    if mm is None:
        return None

    if mm >= 100:
        return 100

    if mm >= 75:
        return 85

    if mm >= 50:
        return 65

    if mm >= 30:
        return 40

    if mm >= 15:
        return 20

    return 0


def calcular_score_lluvia_72h(mm):

    if mm is None:
        return None

    if mm >= 200:
        return 100

    if mm >= 150:
        return 85

    if mm >= 100:
        return 65

    if mm >= 60:
        return 40

    if mm >= 30:
        return 20

    return 0


def calcular_score_humedad(humedad):

    if humedad is None:
        return None

    if humedad >= 0.45:
        return 100

    if humedad >= 0.40:
        return 85

    if humedad >= 0.35:
        return 70

    if humedad >= 0.30:
        return 50

    if humedad >= 0.25:
        return 30

    return 10


def calcular_score_escorrentia(valor):

    if valor is None:
        return None

    if valor >= 50:
        return 100

    if valor >= 30:
        return 80

    if valor >= 20:
        return 60

    if valor >= 10:
        return 40

    if valor >= 5:
        return 20

    return 0


# ============================================================
# TELEGRAM
# ============================================================

def obtener_config_telegram():

    try:
        token = st.secrets.get(
            "TELEGRAM_BOT_TOKEN",
            "",
        )

        chat_id = st.secrets.get(
            "TELEGRAM_CHAT_ID",
            "",
        )

        return (
            str(token).strip(),
            str(chat_id).strip(),
        )

    except Exception:
        return "", ""


def telegram_configurado():

    token, chat_id = obtener_config_telegram()

    return bool(
        token
        and chat_id
        and token != "TU_TOKEN_DEL_BOT"
        and chat_id != "TU_CHAT_ID"
    )


def enviar_telegram(texto):

    token, chat_id = obtener_config_telegram()

    if not token or not chat_id:
        return (
            False,
            "Telegram no configurado en Streamlit Secrets.",
        )

    url = (
        f"https://api.telegram.org/"
        f"bot{token}/sendMessage"
    )

    payload = {
        "chat_id": chat_id,
        "text": texto,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    try:

        response = requests.post(
            url,
            data=payload,
            timeout=REQUEST_TIMEOUT,
        )

        if response.ok:
            return (
                True,
                "Mensaje enviado correctamente.",
            )

        return (
            False,
            (
                f"Telegram respondió HTTP "
                f"{response.status_code}: "
                f"{response.text[:300]}"
            ),
        )

    except Exception as exc:

        return (
            False,
            f"Error de conexión con Telegram: {exc}",
        )


def formatear_mensaje_alerta(resultado):

    humedad = (
        f"{resultado['humedad_suelo']:.3f}"
        if resultado["humedad_suelo"] is not None
        else "s/d"
    )

    lluvia_24 = (
        f"{resultado['lluvia_24h']:.1f}"
        if resultado["lluvia_24h"] is not None
        else "s/d"
    )

    lluvia_72 = (
        f"{resultado['lluvia_72h']:.1f}"
        if resultado["lluvia_72h"] is not None
        else "s/d"
    )

    escorrentia = (
        f"{resultado['escorrentia_72h']:.1f}"
        if resultado["escorrentia_72h"] is not None
        else "s/d"
    )

    hidro = (
        f"{resultado['hidro_score']:.0f}"
        if resultado["hidro_score"] is not None
        else "s/d"
    )

    nivel = resultado["nivel"]

    return (
        f"{emoji_riesgo(nivel)} "
        f"<b>ALERTA LITORAL AGRO</b>\n\n"
        f"<b>{resultado['nombre']}</b> — "
        f"{resultado['provincia']}\n"
        f"Riesgo: <b>{resultado['score']:.1f}/100</b>\n"
        f"Nivel: <b>{nivel}</b>\n\n"
        f"🌧️ Lluvia 24h: {lluvia_24} mm\n"
        f"🌧️ Lluvia 72h: {lluvia_72} mm\n"
        f"💧 Humedad suelo: {humedad}\n"
        f"🌊 Escorrentía 72h: {escorrentia}\n"
        f"📈 Hidrología: {hidro}/100\n\n"
        f"Actualizado: "
        f"{resultado['actualizado'].strftime('%d/%m/%Y %H:%M')}"
    )


def enviar_alertas_automaticas(resultados):

    if not telegram_configurado():
        return []

    enviados = []

    ultimo = st.session_state.setdefault(
        "telegram_alertas_enviadas",
        set(),
    )

    for resultado in resultados:

        if resultado["nivel"] not in (
            "ALTO",
            "MUY ALTO",
        ):
            continue

        clave = (
            f"{resultado['nombre']}|"
            f"{resultado['nivel']}|"
            f"{resultado['score']:.0f}|"
            f"{resultado['actualizado'].strftime('%Y%m%d%H')}"
        )

        if clave in ultimo:
            continue

        ok, _ = enviar_telegram(
            formatear_mensaje_alerta(resultado)
        )

        if ok:
            ultimo.add(clave)
            enviados.append(resultado["nombre"])

    return enviados


def enviar_resumen_telegram(resultados):

    if not resultados:
        return False, "No hay resultados."

    df = pd.DataFrame(resultados)

    df = df.sort_values(
        "score",
        ascending=False,
    )

    top = df.head(8)

    lineas = [
        "🌧️ <b>ALERTA LITORAL AGRO</b>",
        (
            "Actualización: "
            f"{ahora().strftime('%d/%m/%Y %H:%M')}"
        ),
        "",
    ]

    for _, row in top.iterrows():

        lineas.append(
            f"{emoji_riesgo(row['nivel'])} "
            f"<b>{row['nombre']}</b> — "
            f"{row['score']:.1f}/100 "
            f"({row['nivel']})"
        )

    return enviar_telegram(
        "\n".join(lineas)
    )


# ============================================================
# INA
# ============================================================

def normalizar_estaciones(data):

    if isinstance(data, dict):

        for key in (
            "estaciones",
            "stations",
            "data",
            "results",
        ):

            if isinstance(data.get(key), list):

                data = data[key]
                break

    if not isinstance(data, list):
        return pd.DataFrame()

    df = pd.json_normalize(data)

    rename = {}

    for column in df.columns:

        cl = column.lower()

        if cl in (
            "lat",
            "latitude",
            "latitud",
            "ubicacion.lat",
        ):
            rename[column] = "lat"

        elif cl in (
            "lon",
            "lng",
            "longitude",
            "longitud",
            "ubicacion.lon",
        ):
            rename[column] = "lon"

        elif (
            "sitecode" in cl
            or cl in (
                "codigo",
                "code",
                "id",
            )
        ):
            rename[column] = "siteCode"

        elif (
            "nombre" in cl
            or cl in (
                "name",
                "station_name",
            )
        ):
            rename[column] = "nombre"

    df = df.rename(
        columns=rename
    )

    if (
        "lat" not in df.columns
        or "lon" not in df.columns
    ):
        return pd.DataFrame()

    df["lat"] = pd.to_numeric(
        df["lat"],
        errors="coerce",
    )

    df["lon"] = pd.to_numeric(
        df["lon"],
        errors="coerce",
    )

    return df.dropna(
        subset=["lat", "lon"]
    ).copy()


@st.cache_data(
    ttl=900,
    show_spinner=False,
)
def obtener_estaciones_ina():

    try:

        response = requests.get(
            INA_ESTACIONES,
            timeout=REQUEST_TIMEOUT,
        )

        response.raise_for_status()

        return normalizar_estaciones(
            response.json()
        )

    except Exception:

        return pd.DataFrame()


def extraer_registros_ina(data):

    if isinstance(data, dict):

        for key in (
            "datos",
            "data",
            "results",
            "series",
            "observaciones",
        ):

            if isinstance(data.get(key), list):
                return data[key]

    if isinstance(data, list):
        return data

    return []


def normalizar_datos_ina(data):

    registros = extraer_registros_ina(data)

    if not registros:
        return pd.DataFrame()

    df = pd.json_normalize(
        registros
    )

    fecha_col = None
    valor_col = None

    for column in df.columns:

        cl = column.lower()

        if (
            fecha_col is None
            and any(
                x in cl
                for x in (
                    "fecha",
                    "date",
                    "time",
                    "timestamp",
                )
            )
        ):
            fecha_col = column

        if (
            valor_col is None
            and any(
                x in cl
                for x in (
                    "valor",
                    "value",
                    "dato",
                    "nivel",
                    "caudal",
                )
            )
        ):
            valor_col = column

    if (
        fecha_col is None
        or valor_col is None
    ):
        return pd.DataFrame()

    df["fecha"] = pd.to_datetime(
        df[fecha_col],
        errors="coerce",
        utc=True,
    )

    df["valor"] = pd.to_numeric(
        df[valor_col],
        errors="coerce",
    )

    return (
        df.dropna(
            subset=[
                "fecha",
                "valor",
            ]
        )
        .sort_values("fecha")
    )


def obtener_datos_ina(
    site_code,
    var_id,
):

    if site_code is None:
        return pd.DataFrame()

    params = {
        "siteCode": site_code,
        "varId": var_id,
    }

    try:

        response = requests.get(
            INA_DATOS,
            params=params,
            timeout=REQUEST_TIMEOUT,
        )

        response.raise_for_status()

        return normalizar_datos_ina(
            response.json()
        )

    except Exception:

        return pd.DataFrame()


def calcular_hidrologia(estacion):

    if estacion is None:

        return {
            "score": None,
            "nivel": None,
            "evacuacion": None,
            "tendencia": None,
            "serie": pd.DataFrame(),
        }

    site = estacion.get(
        "siteCode"
    )

    altura = obtener_datos_ina(
        site,
        INA_VAR_ALTURA,
    )

    caudal = obtener_datos_ina(
        site,
        INA_VAR_CAUDAL,
    )

    df = (
        altura
        if not altura.empty
        else caudal
    )

    if df.empty:

        return {
            "score": None,
            "nivel": estacion.get(
                "nivel_de_alerta"
            ),
            "evacuacion": estacion.get(
                "nivel_de_evacuacion"
            ),
            "tendencia": None,
            "serie": df,
        }

    ultimo = float(
        df.iloc[-1]["valor"]
    )

    corte = (
        df["fecha"].max()
        - pd.Timedelta(hours=24)
    )

    prev = df[
        df["fecha"] <= corte
    ]

    tendencia = None

    if not prev.empty:

        tendencia = (
            ultimo
            - float(
                prev.iloc[-1]["valor"]
            )
        )

    score = 20.0

    if tendencia is not None:

        if tendencia > 1.0:
            score = 100.0

        elif tendencia > 0.5:
            score = 80.0

        elif tendencia > 0.2:
            score = 60.0

        elif tendencia > 0.0:
            score = 40.0

        else:
            score = 20.0

    alerta = safe_float(
        estacion.get(
            "nivel_de_alerta"
        )
    )

    evacuacion = safe_float(
        estacion.get(
            "nivel_de_evacuacion"
        )
    )

    if alerta is not None:

        if ultimo >= alerta:
            score = max(
                score,
                85.0,
            )

    if evacuacion is not None:

        if ultimo >= evacuacion:
            score = 100.0

    return {
        "score": score,
        "nivel": ultimo,
        "evacuacion": evacuacion,
        "tendencia": tendencia,
        "serie": df,
    }


def obtener_estacion_cercana(
    nodo,
    estaciones,
    max_km=150,
):

    if (
        estaciones is None
        or estaciones.empty
    ):
        return None

    mejor = None
    mejor_dist = None

    for _, row in estaciones.iterrows():

        d = distancia_km(
            nodo["lat"],
            nodo["lon"],
            float(row["lat"]),
            float(row["lon"]),
        )

        if (
            mejor_dist is None
            or d < mejor_dist
        ):
            mejor = row.to_dict()
            mejor_dist = d

    if (
        mejor is not None
        and mejor_dist <= max_km
    ):

        mejor["_distancia_km"] = (
            mejor_dist
        )

        return mejor

    return None


# ============================================================
# OPEN-METEO
# ============================================================

def preparar_meteo(data):

    if (
        not isinstance(data, dict)
        or "hourly" not in data
    ):
        return pd.DataFrame()

    hourly = data["hourly"]

    df = pd.DataFrame(
        hourly
    )

    if "time" not in df.columns:
        return pd.DataFrame()

    df["time"] = pd.to_datetime(
        df["time"],
        errors="coerce",
    )

    return (
        df.dropna(
            subset=["time"]
        )
        .sort_values("time")
    )


@st.cache_data(
    ttl=600,
    show_spinner=False,
)
def obtener_meteo(
    lat,
    lon,
):

    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": ",".join(
            [
                "precipitation",
                "soil_moisture_0_to_7cm",
                "soil_moisture_7_to_28cm",
                "soil_moisture_28_to_100cm",
                "soil_moisture_100_to_255cm",
                "runoff",
                "wind_gusts_10m",
            ]
        ),
        "past_days": 3,
        "forecast_days": 7,
        "timezone": (
            "America/Argentina/"
            "Buenos_Aires"
        ),
        "cell_selection": "land",
    }

    try:

        response = requests.get(
            OPEN_METEO_ECMWF,
            params=params,
            timeout=REQUEST_TIMEOUT,
        )

        response.raise_for_status()

        df = preparar_meteo(
            response.json()
        )

        if df.empty:

            response = requests.get(
                OPEN_METEO_FORECAST,
                params=params,
                timeout=REQUEST_TIMEOUT,
            )

            response.raise_for_status()

            df = preparar_meteo(
                response.json()
            )

        return df

    except Exception:

        return pd.DataFrame()


def calcular_meteo(df):

    if df.empty:

        return {
            "lluvia_24h": None,
            "lluvia_72h": None,
            "humedad_suelo": None,
            "escorrentia_72h": None,
            "max_gust": None,
            "cobertura": 0,
        }

    ahora_local = (
        pd.Timestamp.now(
            tz=TZ
        ).tz_localize(None)
    )

    df = df.copy()

    if getattr(
        df["time"].dt,
        "tz",
        None,
    ) is not None:

        df["time"] = (
            df["time"]
            .dt
            .tz_localize(None)
        )

    pasado = df[
        df["time"] <= ahora_local
    ]

    if pasado.empty:
        pasado = df

    ult24 = pasado[
        pasado["time"]
        >= ahora_local
        - pd.Timedelta(hours=24)
    ]

    ult72 = pasado[
        pasado["time"]
        >= ahora_local
        - pd.Timedelta(hours=72)
    ]

    def suma(frame, columna):

        if columna not in frame.columns:
            return None

        serie = pd.to_numeric(
            frame[columna],
            errors="coerce",
        ).dropna()

        if serie.empty:
            return None

        return float(
            serie.sum()
        )

    def media_humedad(frame):

        columnas = [
            columna
            for columna in [
                "soil_moisture_0_to_7cm",
                "soil_moisture_7_to_28cm",
                "soil_moisture_28_to_100cm",
                "soil_moisture_100_to_255cm",
            ]
            if columna in frame.columns
        ]

        if not columnas:
            return None

        valores = (
            frame[columnas]
            .apply(
                pd.to_numeric,
                errors="coerce",
            )
            .stack()
        )

        if valores.empty:
            return None

        return float(
            valores.mean()
        )

    lluvia24 = suma(
        ult24,
        "precipitation",
    )

    lluvia72 = suma(
        ult72,
        "precipitation",
    )

    escorrentia = suma(
        ult72,
        "runoff",
    )

    gust = None

    if "wind_gusts_10m" in df.columns:

        valores = pd.to_numeric(
            df["wind_gusts_10m"],
            errors="coerce",
        ).dropna()

        if not valores.empty:

            gust = float(
                valores.max()
            )

    cobertura = int(
        len(pasado)
    )

    return {
        "lluvia_24h": lluvia24,
        "lluvia_72h": lluvia72,
        "humedad_suelo": media_humedad(
            ult24
            if not ult24.empty
            else pasado
        ),
        "escorrentia_72h": escorrentia,
        "max_gust": gust,
        "cobertura": cobertura,
    }


# ============================================================
# SCORE TOTAL
# ============================================================

def calcular_score_componentes(
    meteo,
    hidro,
    vulnerabilidad,
):

    componentes = [
        (
            calcular_score_lluvia_24h(
                meteo["lluvia_24h"]
            ),
            20,
        ),
        (
            calcular_score_lluvia_72h(
                meteo["lluvia_72h"]
            ),
            20,
        ),
        (
            calcular_score_humedad(
                meteo["humedad_suelo"]
            ),
            15,
        ),
        (
            calcular_score_escorrentia(
                meteo["escorrentia_72h"]
            ),
            5,
        ),
        (
            hidro["score"],
            20,
        ),
        (
            float(vulnerabilidad),
            10,
        ),
    ]

    disponibles = [
        (score, peso)
        for score, peso in componentes
        if score is not None
    ]

    if not disponibles:
        return 0.0

    return (
        sum(
            score * peso
            for score, peso
            in disponibles
        )
        / sum(
            peso
            for _, peso
            in disponibles
        )
    )


def procesar_nodo(
    nodo,
    estaciones,
    max_km_ina,
):

    meteo_df = obtener_meteo(
        nodo["lat"],
        nodo["lon"],
    )

    meteo = calcular_meteo(
        meteo_df
    )

    estacion = obtener_estacion_cercana(
        nodo,
        estaciones,
        max_km=max_km_ina,
    )

    hidro = calcular_hidrologia(
        estacion
    )

    score = calcular_score_componentes(
        meteo,
        hidro,
        nodo["vulnerabilidad"],
    )

    actualizado = ahora()

    return {
        "nombre": nodo["nombre"],
        "provincia": nodo["provincia"],
        "lat": nodo["lat"],
        "lon": nodo["lon"],
        "vulnerabilidad": nodo[
            "vulnerabilidad"
        ],
        "lluvia_24h": meteo[
            "lluvia_24h"
        ],
        "lluvia_72h": meteo[
            "lluvia_72h"
        ],
        "humedad_suelo": meteo[
            "humedad_suelo"
        ],
        "escorrentia_72h": meteo[
            "escorrentia_72h"
        ],
        "max_gust": meteo[
            "max_gust"
        ],
        "cobertura_meteo": meteo[
            "cobertura"
        ],
        "hidro_score": hidro[
            "score"
        ],
        "nivel_hidro": hidro[
            "nivel"
        ],
        "tendencia_hidro": hidro[
            "tendencia"
        ],
        "estacion_ina": (
            estacion.get("nombre")
            if estacion
            else None
        ),
        "distancia_ina_km": (
            estacion.get(
                "_distancia_km"
            )
            if estacion
            else None
        ),
        "score": score,
        "nivel": nivel_riesgo(
            score
        ),
        "actualizado": actualizado,
    }


# ============================================================
# MAPA
# ============================================================

def crear_mapa(
    resultados,
    estaciones,
):

    fig = go.Figure()

    if not estaciones.empty:

        nombres_estaciones = (
            estaciones["nombre"]
            if "nombre"
            in estaciones.columns
            else pd.Series(
                [
                    "Estación INA"
                ]
                * len(estaciones)
            )
        )

        fig.add_trace(
            go.Scattergeo(
                lon=estaciones[
                    "lon"
                ],
                lat=estaciones[
                    "lat"
                ],
                mode="markers",
                marker=dict(
                    size=5,
                    color="gray",
                    opacity=0.55,
                ),
                name="Estaciones INA",
                text=nombres_estaciones,
                hovertemplate=(
                    "%{text}<br>"
                    "Lat: %{lat:.3f}<br>"
                    "Lon: %{lon:.3f}"
                    "<extra></extra>"
                ),
            )
        )

    df = pd.DataFrame(
        resultados
    )

    color_map = {
        "BAJO": "#2ca02c",
        "MODERADO": "#f1c40f",
        "ALTO": "#e67e22",
        "MUY ALTO": "#d62728",
    }

    for nivel in [
        "BAJO",
        "MODERADO",
        "ALTO",
        "MUY ALTO",
    ]:

        sub = df[
            df["nivel"] == nivel
        ]

        if sub.empty:
            continue

        fig.add_trace(
            go.Scattergeo(
                lon=sub["lon"],
                lat=sub["lat"],
                mode="markers+text",
                text=sub["nombre"],
                textposition="top center",
                marker=dict(
                    size=13,
                    color=color_map[nivel],
                    line=dict(
                        width=1,
                        color="black",
                    ),
                ),
                name=(
                    f"{emoji_riesgo(nivel)} "
                    f"{nivel}"
                ),
                customdata=sub[
                    [
                        "score",
                        "lluvia_24h",
                        "lluvia_72h",
                    ]
                ]
                .fillna(0)
                .values,
                hovertemplate=(
                    "<b>%{text}</b><br>"
                    "Riesgo: "
                    "%{customdata[0]:.1f}/100"
                    "<br>"
                    "Lluvia 24h: "
                    "%{customdata[1]:.1f} mm"
                    "<br>"
                    "Lluvia 72h: "
                    "%{customdata[2]:.1f} mm"
                    "<extra></extra>"
                ),
            )
        )

    fig.update_layout(
        height=650,
        margin=dict(
            l=0,
            r=0,
            t=10,
            b=0,
        ),
        geo=dict(
            scope="south america",
            projection_type="mercator",
            showland=True,
            landcolor="rgb(235,235,235)",
            showcountries=True,
            lonaxis=dict(
                range=[
                    -64,
                    -54,
                ]
            ),
            lataxis=dict(
                range=[
                    -35,
                    -26,
                ]
            ),
        ),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=0.01,
            xanchor="left",
            x=0,
        ),
    )

    return fig


# ============================================================
# WINDY
# ============================================================

def windy_embed_url(
    lat=-31.0,
    lon=-59.0,
    zoom=6,
    overlay="rain",
):

    params = {
        "lat": lat,
        "lon": lon,
        "detailLat": lat,
        "detailLon": lon,
        "width": "100%",
        "height": 650,
        "zoom": zoom,
        "level": "surface",
        "overlay": overlay,
        "product": "ecmwf",
        "menu": "",
        "message": "false",
        "marker": "true",
        "calendar": "now",
        "pressure": "false",
        "type": "map",
        "location": "coordinates",
        "detail": "true",
        "metricWind": "default",
        "metricTemp": "°C",
        "radarRange": -1,
        "logo": "false",
    }

    return (
        WINDY_EMBED_BASE
        + "?"
        + urlencode(params)
    )


# ============================================================
# HISTORIAL
# ============================================================

def guardar_historial(
    resultados,
):

    filas = []

    for resultado in resultados:

        filas.append(
            {
                "actualizado": resultado[
                    "actualizado"
                ],
                "nombre": resultado[
                    "nombre"
                ],
                "provincia": resultado[
                    "provincia"
                ],
                "score": resultado[
                    "score"
                ],
                "nivel": resultado[
                    "nivel"
                ],
                "lluvia_24h": resultado[
                    "lluvia_24h"
                ],
                "lluvia_72h": resultado[
                    "lluvia_72h"
                ],
                "humedad_suelo": resultado[
                    "humedad_suelo"
                ],
                "escorrentia_72h": resultado[
                    "escorrentia_72h"
                ],
                "hidro_score": resultado[
                    "hidro_score"
                ],
            }
        )

    nuevo = pd.DataFrame(
        filas
    )

    anterior = st.session_state.get(
        "historial",
        pd.DataFrame(),
    )

    st.session_state[
        "historial"
    ] = pd.concat(
        [
            anterior,
            nuevo,
        ],
        ignore_index=True,
    )


# ============================================================
# TABLA
# ============================================================

def formatear_tabla(
    resultados,
):

    rows = []

    for resultado in resultados:

        rows.append(
            {
                "Nivel": (
                    f"{emoji_riesgo(resultado['nivel'])} "
                    f"{resultado['nivel']}"
                ),
                "Nodo": resultado[
                    "nombre"
                ],
                "Provincia": resultado[
                    "provincia"
                ],
                "Riesgo": round(
                    resultado["score"],
                    1,
                ),
                "Lluvia 24h (mm)": (
                    None
                    if resultado[
                        "lluvia_24h"
                    ]
                    is None
                    else round(
                        resultado[
                            "lluvia_24h"
                        ],
                        1,
                    )
                ),
                "Lluvia 72h (mm)": (
                    None
                    if resultado[
                        "lluvia_72h"
                    ]
                    is None
                    else round(
                        resultado[
                            "lluvia_72h"
                        ],
                        1,
                    )
                ),
                "Humedad suelo": (
                    None
                    if resultado[
                        "humedad_suelo"
                    ]
                    is None
                    else round(
                        resultado[
                            "humedad_suelo"
                        ],
                        3,
                    )
                ),
                "Escorrentía 72h": (
                    None
                    if resultado[
                        "escorrentia_72h"
                    ]
                    is None
                    else round(
                        resultado[
                            "escorrentia_72h"
                        ],
                        1,
                    )
                ),
                "Hidrología": (
                    None
                    if resultado[
                        "hidro_score"
                    ]
                    is None
                    else round(
                        resultado[
                            "hidro_score"
                        ],
                        0,
                    )
                ),
                "Ráfaga máx. (km/h)": (
                    None
                    if resultado[
                        "max_gust"
                    ]
                    is None
                    else round(
                        resultado[
                            "max_gust"
                        ],
                        1,
                    )
                ),
            }
        )

    return pd.DataFrame(
        rows
    )


# ============================================================
# INTERFAZ
# ============================================================

st.title(
    "🌧️ Alerta Litoral Agro"
)

st.caption(
    "Prototipo de alerta temprana para "
    "riesgo de anegamiento agropecuario "
    f"· V{APP_VERSION}"
)

# ============================================================
# SESSION STATE
# ============================================================

if "resultados" not in st.session_state:
    st.session_state[
        "resultados"
    ] = []

if "historial" not in st.session_state:
    st.session_state[
        "historial"
    ] = pd.DataFrame()


# ============================================================
# SIDEBAR
# ============================================================

with st.sidebar:

    st.header(
        "⚙️ Configuración"
    )

    auto_telegram = st.checkbox(
        "📲 Alertas automáticas Telegram",
        value=False,
    )

    max_km_ina = st.slider(
        "Radio máximo estación INA (km)",
        25,
        250,
        150,
        25,
    )

    st.divider()

    if telegram_configurado():

        st.success(
            "Telegram configurado"
        )

    else:

        st.warning(
            "Telegram no configurado"
        )

        st.caption(
            "Configurar "
            "TELEGRAM_BOT_TOKEN y "
            "TELEGRAM_CHAT_ID en "
            "Streamlit Secrets."
        )

    st.divider()

    st.markdown(
        "**Fuentes**"
    )

    st.caption(
        "🌦️ Open-Meteo / ECMWF"
    )

    st.caption(
        "🌊 INA / DSIyAH"
    )

    st.caption(
        "🗺️ Windy"
    )

    st.caption(
        "🛰️ NOAA GOES-19"
    )

    actualizar = st.button(
        "🔄 Actualizar datos",
        use_container_width=True,
    )

    enviar_resumen = st.button(
        "📲 Enviar resumen a Telegram",
        use_container_width=True,
    )


# ============================================================
# ACTUALIZACIÓN
# ============================================================

if actualizar:

    with st.spinner(
        "Consultando meteorología, "
        "suelo e hidrología..."
    ):

        estaciones = (
            obtener_estaciones_ina()
        )

        resultados = []

        for nodo in NODOS:

            resultados.append(
                procesar_nodo(
                    nodo,
                    estaciones,
                    max_km_ina,
                )
            )

        resultados = sorted(
            resultados,
            key=lambda x: x[
                "score"
            ],
            reverse=True,
        )

        st.session_state[
            "resultados"
        ] = resultados

        guardar_historial(
            resultados
        )

        if auto_telegram:

            enviados = (
                enviar_alertas_automaticas(
                    resultados
                )
            )

            if enviados:

                st.toast(
                    "Alertas enviadas: "
                    + ", ".join(
                        enviados
                    )
                )

    st.success(
        f"Datos actualizados: "
        f"{len(resultados)} nodos procesados."
    )


# ============================================================
# VARIABLES ACTUALES
# ============================================================

resultados = st.session_state[
    "resultados"
]

estaciones = (
    obtener_estaciones_ina()
)


# ============================================================
# RESUMEN TELEGRAM
# ============================================================

if enviar_resumen:

    if resultados:

        ok, msg = (
            enviar_resumen_telegram(
                resultados
            )
        )

        if ok:
            st.success(msg)

        else:
            st.error(msg)

    else:

        st.warning(
            "Primero actualizá los datos."
        )


# ============================================================
# PANEL PRINCIPAL
# ============================================================

if resultados:

    df = pd.DataFrame(
        resultados
    )

    alto = int(
        (
            df["nivel"]
            == "ALTO"
        ).sum()
    )

    muy_alto = int(
        (
            df["nivel"]
            == "MUY ALTO"
        ).sum()
    )

    max_score = float(
        df["score"].max()
    )

    promedio = float(
        df["score"].mean()
    )

    # --------------------------------------------------------
    # MÉTRICAS
    # --------------------------------------------------------

    c1, c2, c3, c4 = st.columns(4)

    c1.metric(
        "🔴 Muy alto",
        muy_alto,
    )

    c2.metric(
        "🟠 Alto",
        alto,
    )

    c3.metric(
        "📊 Riesgo máximo",
        f"{max_score:.1f}/100",
    )

    c4.metric(
        "📈 Riesgo promedio",
        f"{promedio:.1f}/100",
    )

    # --------------------------------------------------------
    # ALERTA GENERAL
    # --------------------------------------------------------

    if muy_alto > 0:

        st.error(
            f"🔴 Hay {muy_alto} nodo(s) "
            "en nivel MUY ALTO."
        )

    elif alto > 0:

        st.warning(
            f"🟠 Hay {alto} nodo(s) "
            "en nivel ALTO."
        )

    else:

        st.success(
            "No se detectan nodos en "
            "nivel ALTO o MUY ALTO "
            "según el índice experimental."
        )

    # --------------------------------------------------------
    # TABLA
    # --------------------------------------------------------

    st.subheader(
        "📋 Estado de los nodos"
    )

    st.dataframe(
        formatear_tabla(
            resultados
        ),
        use_container_width=True,
        hide_index=True,
    )

    # --------------------------------------------------------
    # MAPA
    # --------------------------------------------------------

    st.subheader(
        "🗺️ Mapa de riesgo"
    )

    st.plotly_chart(
        crear_mapa(
            resultados,
            estaciones,
        ),
        use_container_width=True,
    )

    # --------------------------------------------------------
    # WINDY
    # --------------------------------------------------------

    st.subheader(
        "🌬️ Windy — análisis meteorológico"
    )

    opciones_windy = (
        ["Litoral completo"]
        + [
            nodo["nombre"]
            for nodo in NODOS
        ]
    )

    seleccion_windy = st.selectbox(
        "Zona de visualización",
        opciones_windy,
    )

    overlays = {
        "🌧️ Precipitación": "rain",
        "💨 Viento": "wind",
        "💨 Ráfagas": "gust",
        "🌡️ Temperatura": "temp",
        "☁️ Nubes": "clouds",
        "🔽 Presión": "pressure",
    }

    overlay_label = st.selectbox(
        "Variable Windy",
        list(overlays.keys()),
    )

    if (
        seleccion_windy
        == "Litoral completo"
    ):

        wlat = -31.0
        wlon = -59.0
        wzoom = 6

    else:

        nodo_windy = next(
            nodo
            for nodo in NODOS
            if nodo["nombre"]
            == seleccion_windy
        )

        wlat = nodo_windy[
            "lat"
        ]

        wlon = nodo_windy[
            "lon"
        ]

        wzoom = 8

    components.iframe(
        windy_embed_url(
            wlat,
            wlon,
            wzoom,
            overlays[
                overlay_label
            ],
        ),
        height=670,
        scrolling=False,
    )

    # --------------------------------------------------------
    # GOES-19
    # --------------------------------------------------------

    st.subheader(
        "🛰️ GOES-19 — vigilancia satelital"
    )

    st.caption(
        "Productos oficiales NOAA para "
        "seguimiento de nubosidad y "
        "actividad eléctrica en el "
        "sector Sudamérica Sur."
    )

    g1, g2 = st.columns(2)

    with g1:

        st.markdown(
            "### 🌎 GeoColor"
        )

        components.iframe(
            GOES19_GEOCOLOR_URL,
            height=650,
            scrolling=True,
        )

    with g2:

        st.markdown(
            "### ⚡ GLM — actividad eléctrica"
        )

        components.iframe(
            GOES19_GLM_URL,
            height=650,
            scrolling=True,
        )

    st.link_button(
        "Abrir sector GOES-19 en NOAA",
        GOES19_SECTOR_URL,
    )

    # --------------------------------------------------------
    # DETALLE POR NODO
    # --------------------------------------------------------

    st.subheader(
        "🔎 Detalle por nodo"
    )

    for resultado in resultados:

        with st.expander(
            (
                f"{emoji_riesgo(resultado['nivel'])} "
                f"{resultado['nombre']} — "
                f"{resultado['score']:.1f}/100"
            )
        ):

            a, b, c = st.columns(3)

            a.metric(
                "Nivel",
                resultado["nivel"],
            )

            b.metric(
                "Lluvia 24h",
                (
                    "s/d"
                    if resultado[
                        "lluvia_24h"
                    ]
                    is None
                    else (
                        f"{resultado['lluvia_24h']:.1f} mm"
                    )
                ),
            )

            c.metric(
                "Lluvia 72h",
                (
                    "s/d"
                    if resultado[
                        "lluvia_72h"
                    ]
                    is None
                    else (
                        f"{resultado['lluvia_72h']:.1f} mm"
                    )
                ),
            )

            st.write(
                {
                    "Humedad suelo": resultado[
                        "humedad_suelo"
                    ],
                    "Escorrentía 72h": resultado[
                        "escorrentia_72h"
                    ],
                    "Ráfaga máxima": resultado[
                        "max_gust"
                    ],
                    "Score hidrológico": resultado[
                        "hidro_score"
                    ],
                    "Nivel hidrológico observado": resultado[
                        "nivel_hidro"
                    ],
                    "Tendencia hidrológica 24h": resultado[
                        "tendencia_hidro"
                    ],
                    "Estación INA cercana": resultado[
                        "estacion_ina"
                    ],
                    "Distancia INA (km)": resultado[
                        "distancia_ina_km"
                    ],
                    "Cobertura meteorológica (horas)": resultado[
                        "cobertura_meteo"
                    ],
                    "Vulnerabilidad experimental": resultado[
                        "vulnerabilidad"
                    ],
                }
            )

    # --------------------------------------------------------
    # HISTORIAL
    # --------------------------------------------------------

    st.subheader(
        "🕒 Historial de actualizaciones"
    )

    historial = st.session_state.get(
        "historial",
        pd.DataFrame(),
    )

    if not historial.empty:

        st.dataframe(
            historial.sort_values(
                "actualizado",
                ascending=False,
            ),
            use_container_width=True,
            hide_index=True,
        )

        csv = historial.to_csv(
            index=False
        ).encode("utf-8")

        st.download_button(
            "⬇️ Descargar historial CSV",
            csv,
            "historial_alerta_litoral_agro.csv",
            "text/csv",
        )

    else:

        st.info(
            "Todavía no hay historial "
            "en esta sesión."
        )

    # --------------------------------------------------------
    # ESTACIONES INA
    # --------------------------------------------------------

    st.subheader(
        "🌊 Estaciones INA detectadas"
    )

    if estaciones.empty:

        st.warning(
            "No se pudo consultar el "
            "listado de estaciones INA "
            "en este momento."
        )

    else:

        columnas = [
            columna
            for columna in [
                "siteCode",
                "nombre",
                "lat",
                "lon",
            ]
            if columna
            in estaciones.columns
        ]

        st.dataframe(
            estaciones[columnas],
            use_container_width=True,
            hide_index=True,
        )

    # --------------------------------------------------------
    # METODOLOGÍA
    # --------------------------------------------------------

    with st.expander(
        "📚 Metodología y limitaciones"
    ):

        st.markdown(
            """
### Índice experimental de riesgo de anegamiento

El índice combina seis componentes con pesos relativos:

- **Lluvia acumulada 24 h:** 20 %
- **Lluvia acumulada 72 h:** 20 %
- **Humedad del suelo:** 15 %
- **Escorrentía modelada:** 5 %
- **Estado/tendencia hidrológica INA:** 20 %
- **Vulnerabilidad territorial experimental:** 10 %

El cálculo se normaliza cuando alguna fuente no está disponible.

### Niveles

- 🟢 **BAJO:** 0–24
- 🟡 **MODERADO:** 25–49
- 🟠 **ALTO:** 50–74
- 🔴 **MUY ALTO:** 75–100

### Limitaciones

Los umbrales meteorológicos y la vulnerabilidad territorial son parámetros experimentales del prototipo.

Antes de utilizar el sistema como una alerta oficial deberían calibrarse con:

- eventos históricos de inundación y anegamiento;
- observaciones hidrológicas;
- datos de precipitación observada;
- información de suelo;
- validación estadística;
- observaciones de campo.

Windy y GOES-19 funcionan como capas de análisis y vigilancia visual y **no modifican directamente el score**.

El historial y el control anti-spam de Telegram son actualmente de sesión. Para una implementación institucional se recomienda incorporar persistencia mediante una base de datos y auditoría de eventos.
"""
        )

else:

    st.info(
        "Presioná **🔄 Actualizar datos** "
        "para ejecutar el análisis."
    )


# ============================================================
# PIE
# ============================================================

st.divider()

st.caption(
    f"Alerta Litoral Agro V{APP_VERSION} · "
    f"Actualización de interfaz: "
    f"{ahora().strftime('%d/%m/%Y %H:%M')} · "
    "Prototipo académico/experimental"
)
