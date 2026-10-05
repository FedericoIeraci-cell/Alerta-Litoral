# ============================================================
# ALERTA LITORAL AGRO — V3.3.3
# Sistema experimental de alerta temprana para riesgo de
# anegamiento agropecuario en Santa Fe, Corrientes y Entre Ríos.
#
# Fuentes:
#   - Open-Meteo / ECMWF
#   - INA / DSIyAH
#   - Windy (visualización meteorológica)
#   - NOAA / GOES-19 (GeoColor + GLM)
#
# Funciones:
#   - Riesgo meteorológico
#   - Humedad de suelo
#   - Escorrentía
#   - Hidrología observada INA
#   - Vulnerabilidad experimental
#   - Mapa regional
#   - Windy
#   - GOES-19
#   - Telegram
#   - Historial
#   - Exportación CSV
#
# IMPORTANTE:
# Este sistema es un prototipo experimental y no reemplaza
# alertas oficiales ni sistemas de protección civil.
# ============================================================

import math
import requests
import pandas as pd
import streamlit as st
import plotly.graph_objects as go
import streamlit.components.v1 as components

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from urllib.parse import urlencode


# ============================================================
# CONFIGURACIÓN
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
# COLORES / NIVELES
# ============================================================

COLORES_RIESGO = {
    "BAJO": "#2ca02c",
    "MODERADO": "#f1c40f",
    "ALTO": "#e67e22",
    "MUY ALTO": "#e74c3c",
}

ICONOS_RIESGO = {
    "BAJO": "🟢",
    "MODERADO": "🟡",
    "ALTO": "🟠",
    "MUY ALTO": "🔴",
}


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
    "https://www.goes.noaa.gov/"
    "sector_band.php?band=GEOCOLOR&length=24&sat=G19&sector=ssa"
)

GOES19_GLM_URL = (
    "https://www.goes.noaa.gov/"
    "sector_band.php?band=EXTENT3&length=12&sat=G19&sector=ssa"
)

GOES19_SECTOR_URL = (
    "https://www.goes.noaa.gov/"
    "sector.php?sat=G19&sector=ssa"
)


# ============================================================
# NODOS DEL LITORAL
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
# UTILIDADES
# ============================================================

def ahora_local():
    return datetime.now(TZ)


def safe_float(value, default=0.0):
    try:
        if value is None:
            return default

        if isinstance(value, str):
            value = value.replace(",", ".")

        result = float(value)

        if math.isnan(result):
            return default

        return result

    except Exception:
        return default


def promedio_seguro(values):
    values = [
        safe_float(v)
        for v in values
        if v is not None
    ]

    values = [
        v for v in values
        if not math.isnan(v)
    ]

    if not values:
        return None

    return sum(values) / len(values)


def distancia_km(lat1, lon1, lat2, lon2):
    """
    Distancia aproximada mediante fórmula de Haversine.
    """

    R = 6371.0

    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)

    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)

    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1)
        * math.cos(phi2)
        * math.sin(dlambda / 2) ** 2
    )

    return 2 * R * math.asin(math.sqrt(a))


def nivel_riesgo(score):
    score = max(0, min(100, safe_float(score)))

    if score < 25:
        return "BAJO"

    if score < 50:
        return "MODERADO"

    if score < 75:
        return "ALTO"

    return "MUY ALTO"


def emoji_riesgo(nivel):
    return ICONOS_RIESGO.get(nivel, "⚪")


# ============================================================
# OPEN-METEO
# ============================================================

def obtener_datos_open_meteo(lat, lon):

    variables = [
        "precipitation",
        "soil_moisture_0_to_7cm",
        "soil_moisture_7_to_28cm",
        "soil_moisture_28_to_100cm",
        "soil_moisture_100_to_255cm",
        "runoff",
        "wind_gusts_10m",
    ]

    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": ",".join(variables),
        "past_days": 3,
        "forecast_days": 7,
        "timezone": "America/Argentina/Buenos_Aires",
        "cell_selection": "land",
    }

    try:

        response = requests.get(
            OPEN_METEO_ECMWF,
            params=params,
            timeout=REQUEST_TIMEOUT,
        )

        if response.status_code != 200:
            response = requests.get(
                OPEN_METEO_FORECAST,
                params=params,
                timeout=REQUEST_TIMEOUT,
            )

        response.raise_for_status()

        data = response.json()

        hourly = data.get("hourly", {})

        times = hourly.get("time", [])

        if not times:
            return None

        df = pd.DataFrame(hourly)

        df["time"] = pd.to_datetime(
            df["time"],
            errors="coerce",
        )

        df = df.dropna(subset=["time"])

        ahora = pd.Timestamp.now(
            tz=TZ
        ).tz_localize(None)

        df["time"] = df["time"].dt.tz_localize(
            None
        )

        # ====================================================
        # PERÍODOS
        # ====================================================

        ult_24 = df[
            df["time"] >= ahora - pd.Timedelta(hours=24)
        ]

        ult_72 = df[
            df["time"] >= ahora - pd.Timedelta(hours=72)
        ]

        # ====================================================
        # LLUVIA
        # ====================================================

        lluvia_24 = (
            ult_24["precipitation"].sum()
            if "precipitation" in ult_24
            else 0
        )

        lluvia_72 = (
            ult_72["precipitation"].sum()
            if "precipitation" in ult_72
            else 0
        )

        # ====================================================
        # HUMEDAD DE SUELO
        # ====================================================

        soil_columns = [
            "soil_moisture_0_to_7cm",
            "soil_moisture_7_to_28cm",
            "soil_moisture_28_to_100cm",
            "soil_moisture_100_to_255cm",
        ]

        humedad_values = []

        for column in soil_columns:

            if column in df.columns:

                serie = pd.to_numeric(
                    df[column],
                    errors="coerce",
                ).dropna()

                if len(serie) > 0:
                    humedad_values.append(
                        serie.iloc[-1]
                    )

        humedad = (
            promedio_seguro(humedad_values)
            if humedad_values
            else None
        )

        # ====================================================
        # ESCORRENTÍA
        # ====================================================

        if "runoff" in ult_72.columns:
            escorrentia_72 = ult_72[
                "runoff"
            ].sum()
        else:
            escorrentia_72 = 0

        # ====================================================
        # RÁFAGA MÁXIMA
        # ====================================================

        if "wind_gusts_10m" in ult_72.columns:

            gust_series = pd.to_numeric(
                ult_72["wind_gusts_10m"],
                errors="coerce",
            ).dropna()

            rafaga_max = (
                gust_series.max()
                if len(gust_series)
                else None
            )

        else:
            rafaga_max = None

        return {
            "lluvia_24h": safe_float(lluvia_24),
            "lluvia_72h": safe_float(lluvia_72),
            "humedad_suelo": (
                safe_float(humedad)
                if humedad is not None
                else None
            ),
            "escorrentia_72h": safe_float(
                escorrentia_72
            ),
            "rafaga_max": (
                safe_float(rafaga_max)
                if rafaga_max is not None
                else None
            ),
            "horas_datos": len(df),
            "cobertura": True,
            "fuente_meteo": "Open-Meteo / ECMWF",
            "actualizado": ahora_local(),
        }

    except Exception as e:

        return {
            "lluvia_24h": None,
            "lluvia_72h": None,
            "humedad_suelo": None,
            "escorrentia_72h": None,
            "rafaga_max": None,
            "horas_datos": 0,
            "cobertura": False,
            "fuente_meteo": "Error Open-Meteo",
            "error": str(e),
            "actualizado": ahora_local(),
        }


# ============================================================
# INA — ESTACIONES
# ============================================================

@st.cache_data(ttl=900, show_spinner=False)
def obtener_estaciones_ina():

    try:

        response = requests.get(
            INA_ESTACIONES,
            timeout=REQUEST_TIMEOUT,
        )

        response.raise_for_status()

        data = response.json()

        if isinstance(data, dict):

            for key in [
                "data",
                "estaciones",
                "stations",
                "results",
            ]:

                if key in data:
                    data = data[key]
                    break

        if not isinstance(data, list):
            return []

        estaciones = []

        for item in data:

            if not isinstance(item, dict):
                continue

            lat = (
                item.get("lat")
                or item.get("latitude")
                or item.get("latitud")
            )

            lon = (
                item.get("lon")
                or item.get("longitude")
                or item.get("longitud")
            )

            if lat is None or lon is None:
                continue

            estaciones.append(
                {
                    "codigo": (
                        item.get("siteCode")
                        or item.get("codigo")
                        or item.get("code")
                        or item.get("id")
                    ),
                    "nombre": (
                        item.get("nombre")
                        or item.get("name")
                        or item.get("stationName")
                        or "Estación INA"
                    ),
                    "lat": safe_float(lat),
                    "lon": safe_float(lon),
                    "provincia": (
                        item.get("provincia")
                        or item.get("province")
                        or ""
                    ),
                    "nivel_alerta": (
                        item.get("nivel_de_alerta")
                        or item.get("nivel_alerta")
                    ),
                    "nivel_evacuacion": (
                        item.get("nivel_de_evacuacion")
                        or item.get("nivel_evacuacion")
                    ),
                    "raw": item,
                }
            )

        return estaciones

    except Exception:
        return []


def encontrar_estacion_cercana(
    lat,
    lon,
    estaciones,
    max_km=150,
):

    mejor = None
    mejor_distancia = None

    for estacion in estaciones:

        distancia = distancia_km(
            lat,
            lon,
            estacion["lat"],
            estacion["lon"],
        )

        if distancia <= max_km:

            if (
                mejor_distancia is None
                or distancia < mejor_distancia
            ):
                mejor = estacion
                mejor_distancia = distancia

    if mejor is None:
        return None

    resultado = mejor.copy()

    resultado["distancia_km"] = mejor_distancia

    return resultado


# ============================================================
# INA — DATOS
# ============================================================

def normalizar_registros_ina(data):

    if isinstance(data, dict):

        for key in [
            "data",
            "datos",
            "results",
            "resultados",
        ]:

            if key in data:

                data = data[key]
                break

    if not isinstance(data, list):
        return []

    registros = []

    for item in data:

        if not isinstance(item, dict):
            continue

        fecha = (
            item.get("fecha")
            or item.get("date")
            or item.get("timestamp")
            or item.get("datetime")
            or item.get("time")
        )

        valor = (
            item.get("valor")
            or item.get("value")
            or item.get("dato")
            or item.get("nivel")
        )

        if fecha is None or valor is None:
            continue

        try:

            fecha_dt = pd.to_datetime(
                fecha,
                errors="coerce",
            )

            valor_float = safe_float(
                valor,
                default=float("nan"),
            )

            if pd.isna(fecha_dt):
                continue

            if math.isnan(valor_float):
                continue

            registros.append(
                {
                    "fecha": fecha_dt,
                    "valor": valor_float,
                }
            )

        except Exception:
            continue

    return registros


def obtener_dato_ina(
    estacion,
    var_id,
):

    if not estacion:
        return []

    codigo = estacion.get("codigo")

    if codigo is None:
        return []

    params = {
        "siteCode": codigo,
        "varId": var_id,
    }

    try:

        response = requests.get(
            INA_DATOS,
            params=params,
            timeout=REQUEST_TIMEOUT,
        )

        response.raise_for_status()

        return normalizar_registros_ina(
            response.json()
        )

    except Exception:
        return []


def analizar_hidrologia_ina(estacion):

    if not estacion:

        return {
            "valor_actual": None,
            "tendencia_24h": None,
            "nivel_alerta": None,
            "nivel_evacuacion": None,
            "score": 0,
            "estado": "SIN DATOS",
            "serie": [],
        }

    registros = obtener_dato_ina(
        estacion,
        INA_VAR_ALTURA,
    )

    if not registros:

        registros = obtener_dato_ina(
            estacion,
            INA_VAR_CAUDAL,
        )

    if not registros:

        return {
            "valor_actual": None,
            "tendencia_24h": None,
            "nivel_alerta": estacion.get(
                "nivel_alerta"
            ),
            "nivel_evacuacion": estacion.get(
                "nivel_evacuacion"
            ),
            "score": 0,
            "estado": "SIN DATOS",
            "serie": [],
        }

    df = pd.DataFrame(registros)

    df = df.sort_values("fecha")

    valor_actual = safe_float(
        df.iloc[-1]["valor"]
    )

    limite_24 = (
        df["fecha"].max()
        - pd.Timedelta(hours=24)
    )

    antiguos = df[
        df["fecha"] <= limite_24
    ]

    if len(antiguos) > 0:

        valor_24 = safe_float(
            antiguos.iloc[-1]["valor"]
        )

        tendencia = (
            valor_actual - valor_24
        )

    else:
        tendencia = None

    nivel_alerta = safe_float(
        estacion.get("nivel_alerta"),
        default=float("nan"),
    )

    nivel_evacuacion = safe_float(
        estacion.get("nivel_evacuacion"),
        default=float("nan"),
    )

    score = 0

    # --------------------------------------------------------
    # NIVEL HIDROLÓGICO
    # --------------------------------------------------------

    if not math.isnan(nivel_evacuacion):

        if valor_actual >= nivel_evacuacion:
            score = 100

        elif valor_actual >= nivel_evacuacion * 0.9:
            score = max(score, 85)

        elif valor_actual >= nivel_evacuacion * 0.75:
            score = max(score, 65)

    if not math.isnan(nivel_alerta):

        if valor_actual >= nivel_alerta:
            score = max(score, 70)

        elif valor_actual >= nivel_alerta * 0.9:
            score = max(score, 50)

        elif valor_actual >= nivel_alerta * 0.75:
            score = max(score, 30)

    # --------------------------------------------------------
    # TENDENCIA
    # --------------------------------------------------------

    if tendencia is not None:

        if tendencia > 0.50:
            score = max(score, 80)

        elif tendencia > 0.25:
            score = max(score, 60)

        elif tendencia > 0.10:
            score = max(score, 40)

        elif tendencia > 0:
            score = max(score, 20)

    if score >= 75:
        estado = "MUY ALTO"

    elif score >= 50:
        estado = "ALTO"

    elif score >= 25:
        estado = "MODERADO"

    else:
        estado = "BAJO"

    return {
        "valor_actual": valor_actual,
        "tendencia_24h": tendencia,
        "nivel_alerta": (
            None
            if math.isnan(nivel_alerta)
            else nivel_alerta
        ),
        "nivel_evacuacion": (
            None
            if math.isnan(nivel_evacuacion)
            else nivel_evacuacion
        ),
        "score": score,
        "estado": estado,
        "serie": registros,
    }


# ============================================================
# CÁLCULO DE RIESGO
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

    # Open-Meteo expresa la humedad volumétrica
    # aproximadamente en m3/m3.

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


def calcular_score_vulnerabilidad(valor):

    if valor is None:
        return None

    return max(
        0,
        min(100, safe_float(valor))
    )


def calcular_riesgo(
    lluvia_24,
    lluvia_72,
    humedad,
    escorrentia,
    hidrologia,
    vulnerabilidad,
):

    componentes = [
        (
            "lluvia_24h",
            calcular_score_lluvia_24h(
                lluvia_24
            ),
            20,
        ),
        (
            "lluvia_72h",
            calcular_score_lluvia_72h(
                lluvia_72
            ),
            20,
        ),
        (
            "humedad",
            calcular_score_humedad(
                humedad
            ),
            15,
        ),
        (
            "escorrentia",
            calcular_score_escorrentia(
                escorrentia
            ),
            5,
        ),
        (
            "hidrologia",
            hidrologia,
            20,
        ),
        (
            "vulnerabilidad",
            calcular_score_vulnerabilidad(
                vulnerabilidad
            ),
            10,
        ),
    ]

    suma = 0
    pesos_disponibles = 0

    for _, score, peso in componentes:

        if score is None:
            continue

        suma += score * peso
        pesos_disponibles += peso

    if pesos_disponibles == 0:
        return 0

    score_final = (
        suma / pesos_disponibles
    )

    return round(
        max(0, min(100, score_final)),
        1,
    )


# ============================================================
# PROCESAMIENTO DE NODO
# ============================================================

def procesar_nodo(
    nodo,
    estaciones,
    max_km_ina=150,
):

    meteo = obtener_datos_open_meteo(
        nodo["lat"],
        nodo["lon"],
    )

    estacion = encontrar_estacion_cercana(
        nodo["lat"],
        nodo["lon"],
        estaciones,
        max_km=max_km_ina,
    )

    hidro = analizar_hidrologia_ina(
        estacion
    )

    score = calcular_riesgo(
        meteo.get("lluvia_24h"),
        meteo.get("lluvia_72h"),
        meteo.get("humedad_suelo"),
        meteo.get("escorrentia_72h"),
        hidro.get("score"),
        nodo.get("vulnerabilidad"),
    )

    nivel = nivel_riesgo(score)

    resultado = {
        "nombre": nodo["nombre"],
        "provincia": nodo["provincia"],
        "lat": nodo["lat"],
        "lon": nodo["lon"],
        "vulnerabilidad": nodo[
            "vulnerabilidad"
        ],

        "lluvia_24h": meteo.get(
            "lluvia_24h"
        ),
        "lluvia_72h": meteo.get(
            "lluvia_72h"
        ),
        "humedad_suelo": meteo.get(
            "humedad_suelo"
        ),
        "escorrentia_72h": meteo.get(
            "escorrentia_72h"
        ),
        "rafaga_max": meteo.get(
            "rafaga_max"
        ),

        "hidro_score": hidro.get(
            "score"
        ),
        "nivel_hidro": hidro.get(
            "estado"
        ),
        "nivel_hidrologico_actual": hidro.get(
            "valor_actual"
        ),
        "tendencia_hidro_24h": hidro.get(
            "tendencia_24h"
        ),

        "nivel_alerta": hidro.get(
            "nivel_alerta"
        ),
        "nivel_evacuacion": hidro.get(
            "nivel_evacuacion"
        ),

        "score": score,
        "nivel": nivel,

        "estacion_ina": (
            estacion.get("nombre")
            if estacion
            else None
        ),

        "estacion_ina_codigo": (
            estacion.get("codigo")
            if estacion
            else None
        ),

        "distancia_ina_km": (
            estacion.get("distancia_km")
            if estacion
            else None
        ),

        "cobertura_meteo": meteo.get(
            "cobertura",
            False,
        ),

        "fuente_meteo": meteo.get(
            "fuente_meteo"
        ),

        "actualizado": ahora_local(),
    }

    return resultado


# ============================================================
# MAPA
# ============================================================

def crear_mapa(
    resultados,
    estaciones,
):

    fig = go.Figure()

    if resultados:

        lats = [
            r["lat"]
            for r in resultados
        ]

        lons = [
            r["lon"]
            for r in resultados
        ]

        scores = [
            r["score"]
            for r in resultados
        ]

        nombres = [
            r["nombre"]
            for r in resultados
        ]

        niveles = [
            r["nivel"]
            for r in resultados
        ]

        hover = []

        for r in resultados:

            hover.append(
                f"<b>{r['nombre']}</b><br>"
                f"{r['provincia']}<br>"
                f"Riesgo: {r['score']:.1f}/100<br>"
                f"Nivel: {emoji_riesgo(r['nivel'])} "
                f"{r['nivel']}<br>"
                f"Lluvia 24h: "
                f"{r['lluvia_24h']:.1f} mm<br>"
                f"Lluvia 72h: "
                f"{r['lluvia_72h']:.1f} mm"
            )

        fig.add_trace(
            go.Scattergeo(
                lat=lats,
                lon=lons,
                mode="markers",
                text=nombres,
                customdata=[
                    [n]
                    for n in niveles
                ],
                hovertext=hover,
                hoverinfo="text",
                marker=dict(
                    size=15,
                    color=scores,
                    colorscale=[
                        [0.00, "#2ca02c"],
                        [0.25, "#2ca02c"],
                        [0.26, "#f1c40f"],
                        [0.49, "#f1c40f"],
                        [0.50, "#e67e22"],
                        [0.74, "#e67e22"],
                        [0.75, "#e74c3c"],
                        [1.00, "#e74c3c"],
                    ],
                    cmin=0,
                    cmax=100,
                    colorbar=dict(
                        title="Riesgo",
                    ),
                    line=dict(
                        color="white",
                        width=1,
                    ),
                ),
                name="Nodos de riesgo",
            )
        )

    # --------------------------------------------------------
    # ESTACIONES INA
    # --------------------------------------------------------

    if estaciones:

        fig.add_trace(
            go.Scattergeo(
                lat=[
                    e["lat"]
                    for e in estaciones
                ],
                lon=[
                    e["lon"]
                    for e in estaciones
                ],
                mode="markers",
                text=[
                    e["nombre"]
                    for e in estaciones
                ],
                hovertemplate=(
                    "<b>%{text}</b>"
                    "<extra>INA</extra>"
                ),
                marker=dict(
                    size=6,
                    color="#1f77b4",
                    symbol="circle",
                    line=dict(
                        color="white",
                        width=1,
                    ),
                ),
                name="Estaciones INA",
            )
        )

    fig.update_geos(
        scope="south america",
        projection_type="mercator",
        lonaxis=dict(
            range=[-64, -54]
        ),
        lataxis=dict(
            range=[-35, -26]
        ),
        showland=True,
        showcountries=True,
        showsubunits=True,
        showocean=True,
        showlakes=True,
        coastlinecolor="#777777",
        countrycolor="#777777",
        subunitcolor="#aaaaaa",
    )

    fig.update_layout(
        height=650,
        margin=dict(
            l=0,
            r=0,
            t=20,
            b=0,
        ),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.01,
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


def mostrar_windy(resultados):

    st.subheader(
        "🌬️ Windy — análisis meteorológico"
    )

    st.caption(
        "Herramienta complementaria para "
        "analizar precipitación, viento, "
        "ráfagas, temperatura, nubosidad "
        "y presión."
    )

    opciones = {
        "Litoral completo": (
            -31.0,
            -59.0,
        )
    }

    for r in resultados:

        opciones[
            f"{r['nombre']} — "
            f"{r['provincia']}"
        ] = (
            r["lat"],
            r["lon"],
        )

    col1, col2 = st.columns(2)

    with col1:

        seleccion = st.selectbox(
            "Centro del mapa",
            list(opciones.keys()),
            key="windy_node",
        )

    with col2:

        overlay = st.selectbox(
            "Variable meteorológica",
            [
                "rain",
                "wind",
                "gust",
                "temp",
                "clouds",
                "pressure",
            ],
            format_func=lambda x: {
                "rain": "🌧️ Precipitación",
                "wind": "💨 Viento",
                "gust": "💨 Ráfagas",
                "temp": "🌡️ Temperatura",
                "clouds": "☁️ Nubosidad",
                "pressure": "🧭 Presión",
            }.get(x, x),
            key="windy_overlay",
        )

    lat, lon = opciones[
        seleccion
    ]

    zoom = (
        6
        if seleccion == "Litoral completo"
        else 8
    )

    url = windy_embed_url(
        lat,
        lon,
        zoom,
        overlay,
    )

    components.iframe(
        url,
        height=650,
        scrolling=False,
    )

    st.caption(
        "Windy funciona aquí como capa de "
        "visualización y análisis meteorológico. "
        "Actualmente no modifica el índice "
        "numérico de riesgo."
    )


# ============================================================
# GOES-19
# ============================================================

def mostrar_goes19():

    st.subheader(
        "🛰️ GOES-19 — observación satelital"
    )

    st.caption(
        "Observación complementaria de la "
        "actividad atmosférica sobre Sudamérica."
    )

    col1, col2 = st.columns(2)

    with col1:

        st.markdown(
            "### 🌎 GOES-19 GeoColor"
        )

        components.iframe(
            GOES19_GEOCOLOR_URL,
            height=650,
            scrolling=True,
        )

        st.link_button(
            "Abrir GeoColor directamente en NOAA",
            GOES19_GEOCOLOR_URL,
        )

    with col2:

        st.markdown(
            "### ⚡ GOES-19 GLM"
        )

        components.iframe(
            GOES19_GLM_URL,
            height=650,
            scrolling=True,
        )

        st.link_button(
            "Abrir GLM directamente en NOAA",
            GOES19_GLM_URL,
        )

    st.link_button(
        "🛰️ Abrir sector completo GOES-19 / South America–Southern",
        GOES19_SECTOR_URL,
    )

    st.info(
        "GeoColor permite observar la estructura "
        "nubosa y convectiva. GLM permite seguir "
        "la actividad de descargas eléctricas. "
        "En esta versión ambos productos son "
        "herramientas de observación y todavía "
        "no ingresan matemáticamente al índice "
        "de riesgo."
    )


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

    token, chat_id = (
        obtener_config_telegram()
    )

    return bool(
        token and chat_id
    )


def enviar_telegram(mensaje):

    token, chat_id = (
        obtener_config_telegram()
    )

    if not token or not chat_id:

        return False, (
            "Telegram no está configurado."
        )

    url = (
        f"https://api.telegram.org/"
        f"bot{token}/sendMessage"
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
            timeout=20,
        )

        response.raise_for_status()

        return True, "Mensaje enviado."

    except Exception as e:

        return False, str(e)


def formatear_mensaje_alerta(
    resultado
):

    nivel = resultado["nivel"]

    return (
        f"{emoji_riesgo(nivel)} "
        f"<b>ALERTA LITORAL AGRO</b>\n\n"
        f"<b>{resultado['nombre']}</b> — "
        f"{resultado['provincia']}\n"
        f"Riesgo: <b>"
        f"{resultado['score']:.1f}/100</b>\n"
        f"Nivel: <b>{nivel}</b>\n\n"
        f"🌧️ Lluvia 24h: "
        f"{resultado['lluvia_24h']:.1f} mm\n"
        f"🌧️ Lluvia 72h: "
        f"{resultado['lluvia_72h']:.1f} mm\n"
        f"💧 Humedad suelo: "
        f"{(
            resultado['humedad_suelo']:.3f
            if resultado['humedad_suelo'] is not None
            else 's/d'
        )}\n"
        f"🌊 Escorrentía 72h: "
        f"{resultado['escorrentia_72h']:.1f}\n"
        f"📈 Hidrología: "
        f"{resultado['hidro_score']:.0f}/100\n\n"
        f"Actualizado: "
        f"{resultado['actualizado'].strftime('%d/%m/%Y %H:%M')}"
    )


def enviar_alertas_automaticas(
    resultados
):

    if not telegram_configurado():
        return []

    enviados = []

    for resultado in resultados:

        if resultado["nivel"] not in [
            "ALTO",
            "MUY ALTO",
        ]:
            continue

        key = (
            f"telegram_alerta_"
            f"{resultado['nombre']}_"
            f"{resultado['nivel']}"
        )

        if st.session_state.get(
            key,
            False,
        ):
            continue

        mensaje = (
            formatear_mensaje_alerta(
                resultado
            )
        )

        ok, _ = enviar_telegram(
            mensaje
        )

        if ok:

            st.session_state[
                key
            ] = True

            enviados.append(
                resultado["nombre"]
            )

    return enviados


def enviar_resumen_telegram(
    resultados
):

    if not resultados:
        return False

    ordenados = sorted(
        resultados,
        key=lambda x: x["score"],
        reverse=True,
    )

    lineas = [
        "<b>🌧️ ALERTA LITORAL AGRO</b>",
        "",
        "<b>Resumen regional</b>",
        "",
    ]

    for r in ordenados:

        lineas.append(
            f"{emoji_riesgo(r['nivel'])} "
            f"<b>{r['nombre']}</b>: "
            f"{r['score']:.1f}/100 "
            f"({r['nivel']})"
        )

    lineas.extend(
        [
            "",
            f"Actualizado: "
            f"{ahora_local().strftime('%d/%m/%Y %H:%M')}",
        ]
    )

    ok, _ = enviar_telegram(
        "\n".join(lineas)
    )

    return ok


# ============================================================
# HISTORIAL
# ============================================================

def guardar_historial(
    resultados
):

    if "historial" not in st.session_state:
        st.session_state[
            "historial"
        ] = []

    timestamp = ahora_local()

    for r in resultados:

        st.session_state[
            "historial"
        ].append(
            {
                "fecha": timestamp,
                "nodo": r["nombre"],
                "provincia": r[
                    "provincia"
                ],
                "score": r["score"],
                "nivel": r["nivel"],
                "lluvia_24h": r[
                    "lluvia_24h"
                ],
                "lluvia_72h": r[
                    "lluvia_72h"
                ],
                "humedad_suelo": r[
                    "humedad_suelo"
                ],
                "hidrologia": r[
                    "hidro_score"
                ],
            }
        )

    # Mantener solamente los últimos
    # 1000 registros de sesión.

    st.session_state[
        "historial"
    ] = st.session_state[
        "historial"
    ][-1000:]


# ============================================================
# INTERFAZ
# ============================================================

st.title(
    "🌧️ Alerta Litoral Agro"
)

st.markdown(
    """
### Sistema experimental de alerta temprana

Monitorea condiciones meteorológicas,
humedad del suelo y estado hidrológico
para estimar el riesgo de anegamiento
agropecuario en **Santa Fe, Corrientes
y Entre Ríos**.
"""
)


# ============================================================
# SIDEBAR
# ============================================================

with st.sidebar:

    st.header("⚙️ Configuración")

    auto_telegram = st.checkbox(
        "📲 Alertas automáticas Telegram",
        value=False,
    )

    max_km_ina = st.slider(
        "📍 Distancia máxima a estación INA",
        min_value=25,
        max_value=250,
        value=150,
        step=25,
    )

    st.divider()

    st.subheader(
        "📡 Fuentes de datos"
    )

    st.write(
        "🌦️ Open-Meteo / ECMWF"
    )

    st.write(
        "🌊 INA / DSIyAH"
    )

    st.write(
        "🌬️ Windy"
    )

    st.write(
        "🛰️ NOAA / GOES-19"
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
        "Los secretos de Telegram se "
        "configuran en Streamlit Cloud."
    )


# ============================================================
# BOTONES
# ============================================================

col1, col2 = st.columns(2)

with col1:

    actualizar = st.button(
        "🔄 Actualizar datos",
        type="primary",
        use_container_width=True,
    )

with col2:

    enviar_resumen = st.button(
        "📲 Enviar resumen a Telegram",
        use_container_width=True,
    )


# ============================================================
# CARGA INICIAL / ACTUALIZACIÓN
# ============================================================

if (
    "resultados"
    not in st.session_state
):

    st.session_state[
        "resultados"
    ] = []

    st.session_state[
        "ultima_actualizacion"
    ] = None


if actualizar:

    with st.spinner(
        "Consultando Open-Meteo e INA..."
    ):

        estaciones = (
            obtener_estaciones_ina()
        )

        resultados = []

        progress = st.progress(
            0
        )

        total = len(NODOS)

        for i, nodo in enumerate(
            NODOS,
            start=1,
        ):

            resultado = procesar_nodo(
                nodo,
                estaciones,
                max_km_ina=max_km_ina,
            )

            resultados.append(
                resultado
            )

            progress.progress(
                i / total
            )

        progress.empty()

        st.session_state[
            "resultados"
        ] = resultados

        st.session_state[
            "estaciones"
        ] = estaciones

        st.session_state[
            "ultima_actualizacion"
        ] = ahora_local()

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

                st.success(
                    "Alertas Telegram enviadas: "
                    + ", ".join(enviados)
                )


resultados = st.session_state[
    "resultados"
]

estaciones = st.session_state.get(
    "estaciones",
    [],
)


# ============================================================
# TELEGRAM MANUAL
# ============================================================

if enviar_resumen:

    if not resultados:

        st.warning(
            "Primero actualizá los datos."
        )

    elif not telegram_configurado():

        st.error(
            "Telegram no está configurado "
            "en Streamlit Secrets."
        )

    else:

        with st.spinner(
            "Enviando resumen..."
        ):

            ok = enviar_resumen_telegram(
                resultados
            )

        if ok:

            st.success(
                "Resumen enviado correctamente."
            )

        else:

            st.error(
                "No se pudo enviar el resumen."
            )


# ============================================================
# ESTADO GENERAL
# ============================================================

if resultados:

    scores = [
        r["score"]
        for r in resultados
    ]

    max_score = max(scores)

    cantidad_alertas = sum(
        r["nivel"] in [
            "ALTO",
            "MUY ALTO",
        ]
        for r in resultados
    )

    cantidad_muy_alto = sum(
        r["nivel"] == "MUY ALTO"
        for r in resultados
    )

    promedio = sum(scores) / len(
        scores
    )

    nivel_general = nivel_riesgo(
        max_score
    )

    st.divider()

    st.subheader(
        "🚨 Estado actual del Litoral"
    )

    m1, m2, m3, m4 = st.columns(4)

    with m1:

        st.metric(
            "Riesgo máximo",
            f"{max_score:.1f}/100",
        )

    with m2:

        st.metric(
            "Riesgo regional medio",
            f"{promedio:.1f}/100",
        )

    with m3:

        st.metric(
            "Alertas ALTO+",
            cantidad_alertas,
        )

    with m4:

        st.metric(
            "MUY ALTO",
            cantidad_muy_alto,
        )

    if nivel_general == "MUY ALTO":

        st.error(
            "🔴 NIVEL REGIONAL MUY ALTO"
        )

    elif nivel_general == "ALTO":

        st.warning(
            "🟠 NIVEL REGIONAL ALTO"
        )

    elif nivel_general == "MODERADO":

        st.warning(
            "🟡 NIVEL REGIONAL MODERADO"
        )

    else:

        st.success(
            "🟢 NIVEL REGIONAL BAJO"
        )

else:

    st.info(
        "Presioná «🔄 Actualizar datos» "
        "para ejecutar el monitoreo."
    )


# ============================================================
# TABLA PRINCIPAL
# ============================================================

if resultados:

    st.divider()

    st.subheader(
        "📊 Monitoreo por nodo"
    )

    tabla = []

    for r in sorted(
        resultados,
        key=lambda x: x["score"],
        reverse=True,
    ):

        tabla.append(
            {
                "Nivel": (
                    f"{emoji_riesgo(r['nivel'])} "
                    f"{r['nivel']}"
                ),
                "Localidad": r[
                    "nombre"
                ],
                "Provincia": r[
                    "provincia"
                ],
                "Riesgo": r[
                    "score"
                ],
                "Lluvia 24h (mm)": round(
                    r["lluvia_24h"],
                    1,
                ),
                "Lluvia 72h (mm)": round(
                    r["lluvia_72h"],
                    1,
                ),
                "Humedad suelo": (
                    round(
                        r[
                            "humedad_suelo"
                        ],
                        3,
                    )
                    if r[
                        "humedad_suelo"
                    ]
                    is not None
                    else None
                ),
                "Escorrentía 72h": round(
                    r[
                        "escorrentia_72h"
                    ],
                    1,
                ),
                "Hidrología": round(
                    r[
                        "hidro_score"
                    ],
                    1,
                ),
                "Estación INA": (
                    r[
                        "estacion_ina"
                    ]
                    or "Sin estación"
                ),
            }
        )

    df_tabla = pd.DataFrame(
        tabla
    )

    st.dataframe(
        df_tabla,
        use_container_width=True,
        hide_index=True,
    )


# ============================================================
# MAPA
# ============================================================

if resultados:

    st.divider()

    st.subheader(
        "🗺️ Mapa regional de riesgo"
    )

    mapa = crear_mapa(
        resultados,
        estaciones,
    )

    st.plotly_chart(
        mapa,
        use_container_width=True,
        config={
            "displayModeBar": True,
            "scrollZoom": True,
        },
    )


# ============================================================
# WINDY
# ============================================================

if resultados:

    st.divider()

    mostrar_windy(
        resultados
    )


# ============================================================
# GOES-19
# ============================================================

st.divider()

mostrar_goes19()


# ============================================================
# DETALLE DE NODOS
# ============================================================

if resultados:

    st.divider()

    st.subheader(
        "🔎 Detalle por nodo"
    )

    for r in sorted(
        resultados,
        key=lambda x: x["score"],
        reverse=True,
    ):

        titulo = (
            f"{emoji_riesgo(r['nivel'])} "
            f"{r['nombre']} — "
            f"{r['nivel']} "
            f"({r['score']:.1f}/100)"
        )

        with st.expander(
            titulo
        ):

            c1, c2, c3 = st.columns(
                3
            )

            with c1:

                st.metric(
                    "Lluvia 24h",
                    (
                        f"{r['lluvia_24h']:.1f} mm"
                        if r[
                            "lluvia_24h"
                        ]
                        is not None
                        else "s/d"
                    ),
                )

                st.metric(
                    "Lluvia 72h",
                    (
                        f"{r['lluvia_72h']:.1f} mm"
                        if r[
                            "lluvia_72h"
                        ]
                        is not None
                        else "s/d"
                    ),
                )

            with c2:

                st.metric(
                    "Humedad de suelo",
                    (
                        f"{r['humedad_suelo']:.3f}"
                        if r[
                            "humedad_suelo"
                        ]
                        is not None
                        else "s/d"
                    ),
                )

                st.metric(
                    "Escorrentía 72h",
                    (
                        f"{r['escorrentia_72h']:.1f}"
                        if r[
                            "escorrentia_72h"
                        ]
                        is not None
                        else "s/d"
                    ),
                )

            with c3:

                st.metric(
                    "Hidrología",
                    f"{r['hidro_score']:.0f}/100",
                )

                st.metric(
                    "Vulnerabilidad",
                    f"{r['vulnerabilidad']}/100",
                )

            st.markdown(
                "---"
            )

            st.write(
                f"**Estación INA asociada:** "
                f"{r['estacion_ina'] or 'Sin estación cercana'}"
            )

            if r[
                "distancia_ina_km"
            ] is not None:

                st.write(
                    f"**Distancia a estación INA:** "
                    f"{r['distancia_ina_km']:.1f} km"
                )

            if r[
                "nivel_hidrologico_actual"
            ] is not None:

                st.write(
                    f"**Nivel hidrológico actual:** "
                    f"{r['nivel_hidrologico_actual']:.2f}"
                )

            if r[
                "tendencia_hidro_24h"
            ] is not None:

                tendencia = r[
                    "tendencia_hidro_24h"
                ]

                if tendencia > 0:

                    texto = (
                        f"📈 En ascenso "
                        f"(+{tendencia:.2f})"
                    )

                elif tendencia < 0:

                    texto = (
                        f"📉 En descenso "
                        f"({tendencia:.2f})"
                    )

                else:

                    texto = (
                        "➡️ Estable"
                    )

                st.write(
                    f"**Tendencia 24h:** {texto}"
                )

            if r[
                "nivel_alerta"
            ] is not None:

                st.write(
                    f"**Nivel de alerta INA:** "
                    f"{r['nivel_alerta']:.2f}"
                )

            if r[
                "nivel_evacuacion"
            ] is not None:

                st.write(
                    f"**Nivel de evacuación INA:** "
                    f"{r['nivel_evacuacion']:.2f}"
                )

            st.caption(
                "Los valores de vulnerabilidad "
                "y algunos umbrales de riesgo "
                "son experimentales y deberán "
                "ser calibrados con datos históricos."
            )


# ============================================================
# HISTORIAL
# ============================================================

if (
    "historial"
    in st.session_state
    and st.session_state[
        "historial"
    ]
):

    st.divider()

    st.subheader(
        "🕒 Historial de monitoreo"
    )

    df_historial = pd.DataFrame(
        st.session_state[
            "historial"
        ]
    )

    if not df_historial.empty:

        st.dataframe(
            df_historial.tail(100),
            use_container_width=True,
            hide_index=True,
        )

        csv = df_historial.to_csv(
            index=False
        ).encode("utf-8")

        st.download_button(
            label="📥 Descargar historial CSV",
            data=csv,
            file_name=(
                "alerta_litoral_agro_historial.csv"
            ),
            mime="text/csv",
        )


# ============================================================
# ESTACIONES INA
# ============================================================

if estaciones:

    st.divider()

    with st.expander(
        "🌊 Estaciones hidrológicas INA detectadas"
    ):

        tabla_ina = []

        for e in estaciones:

            tabla_ina.append(
                {
                    "Código": e[
                        "codigo"
                    ],
                    "Estación": e[
                        "nombre"
                    ],
                    "Provincia": e[
                        "provincia"
                    ],
                    "Latitud": e[
                        "lat"
                    ],
                    "Longitud": e[
                        "lon"
                    ],
                    "Nivel alerta": e[
                        "nivel_alerta"
                    ],
                    "Nivel evacuación": e[
                        "nivel_evacuacion"
                    ],
                }
            )

        st.dataframe(
            pd.DataFrame(
                tabla_ina
            ),
            use_container_width=True,
            hide_index=True,
        )


# ============================================================
# METODOLOGÍA
# ============================================================

st.divider()

with st.expander(
    "📚 Metodología y limitaciones"
):

    st.markdown(
        """
### Objetivo

Alerta Litoral Agro es un prototipo de
sistema de alerta temprana orientado a
identificar condiciones favorables al
anegamiento agropecuario en el Litoral
argentino.

### Variables meteorológicas

Se utilizan datos de Open-Meteo con el
modelo ECMWF para analizar:

- precipitación acumulada de 24 horas;
- precipitación acumulada de 72 horas;
- humedad del suelo;
- escorrentía;
- ráfagas de viento.

### Hidrología

Los datos hidrológicos se obtienen del
Instituto Nacional del Agua (INA), cuando
existe una estación disponible dentro del
radio configurado.

Se analiza:

- nivel hidrológico;
- tendencia de las últimas 24 horas;
- niveles de alerta;
- niveles de evacuación.

### Índice de riesgo

El índice combina:

- lluvia 24 h: 20%;
- lluvia 72 h: 20%;
- humedad del suelo: 15%;
- escorrentía: 5%;
- hidrología: 20%;
- vulnerabilidad experimental: 10%.

Cuando alguna variable no está disponible,
el sistema normaliza el cálculo utilizando
solamente los componentes disponibles.

### Windy

Windy se incorpora como herramienta
complementaria de visualización y análisis.

Permite observar:

- precipitación;
- viento;
- ráfagas;
- temperatura;
- nubosidad;
- presión.

Actualmente Windy **no modifica
matemáticamente el índice de riesgo**.

### GOES-19

GOES-19 se incorpora como herramienta
de observación satelital complementaria.

Se incluyen:

- GeoColor;
- GLM.

GeoColor permite observar la evolución
de la nubosidad y de sistemas convectivos.

GLM permite analizar la actividad de
descargas eléctricas y su evolución.

Actualmente los productos GOES-19
**no ingresan matemáticamente al índice
de riesgo**.

### Vulnerabilidad

La vulnerabilidad asignada a cada nodo
es experimental y representa una primera
aproximación para priorizar áreas.

Debe ser calibrada posteriormente mediante:

- antecedentes de inundación;
- topografía;
- uso del suelo;
- drenaje;
- cobertura vegetal;
- infraestructura;
- registros históricos.

### Limitaciones

Este sistema es un prototipo académico/
experimental.

No reemplaza:

- alertas oficiales;
- Defensa Civil;
- organismos provinciales;
- Servicio Meteorológico Nacional;
- Instituto Nacional del Agua;
- autoridades locales.

Los resultados deben interpretarse como
apoyo para análisis y toma de decisiones,
no como una orden automática de evacuación.
"""
    )


# ============================================================
# FUENTES
# ============================================================

with st.expander(
    "🔗 Fuentes utilizadas"
):

    st.markdown(
        """
- Open-Meteo / ECMWF
- Instituto Nacional del Agua (INA)
- Windy
- NOAA / GOES-19
"""
    )


# ============================================================
# PIE
# ============================================================

st.divider()

ultima = st.session_state.get(
    "ultima_actualizacion"
)

if ultima:

    ultima_texto = ultima.strftime(
        "%d/%m/%Y %H:%M:%S"
    )

else:

    ultima_texto = "Sin actualización"


telegram_estado = (
    "Configurado"
    if telegram_configurado()
    else "No configurado"
)

st.caption(
    f"Alerta Litoral Agro V{APP_VERSION} "
    f"| Última actualización: "
    f"{ultima_texto} "
    f"| Telegram: {telegram_estado}"
)

st.caption(
    "Prototipo experimental desarrollado "
    "para análisis meteorológico e hidrológico "
    "aplicado al riesgo agropecuario."
)
