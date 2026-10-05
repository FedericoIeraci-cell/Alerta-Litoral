# ============================================================
# ALERTA LITORAL AGRO — V3.3.2
# ============================================================
#
# Sistema prototipo de alerta temprana para riesgo de
# anegamiento agropecuario en Santa Fe, Corrientes
# y Entre Ríos.
#
# FUENTES:
#   - Open-Meteo / ECMWF
#   - INA / DSIyAH
#
# FUNCIONES:
#   - Precipitación 24 h / 72 h
#   - Humedad del suelo
#   - Runoff modelado
#   - Hidrología observada
#   - Tendencia hidrológica
#   - Índice integrado 0-100
#   - Vulnerabilidad territorial experimental
#   - Control de cobertura de datos
#   - Historial
#   - Exportación CSV
#   - Mapa Plotly
#   - Alertas Telegram
#
# ============================================================

import math
import requests
import pandas as pd
import streamlit as st
import plotly.graph_objects as go

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


# ============================================================
# CONFIGURACIÓN
# ============================================================

st.set_page_config(
    page_title="Alerta Litoral Agro",
    page_icon="🌧️",
    layout="wide",
)

APP_VERSION = "3.3.2"

TZ = ZoneInfo("America/Argentina/Buenos_Aires")


# ============================================================
# ENDPOINTS
# ============================================================

OPEN_METEO_ECMWF = (
    "https://api.open-meteo.com/v1/ecmwf"
)

OPEN_METEO_FORECAST = (
    "https://api.open-meteo.com/v1/forecast"
)


INA_BASE = (
    "https://alerta.ina.gob.ar/pub/datos"
)

INA_ESTACIONES = (
    f"{INA_BASE}/estaciones"
)

INA_SERIES = (
    f"{INA_BASE}/series"
)

INA_DATOS = (
    f"{INA_BASE}/datos"
)


# ============================================================
# VARIABLES INA
# ============================================================

# Variables utilizadas por la versión actual.
#
# 2 = altura
# 4 = caudal
#
# El sistema intenta primero altura y luego caudal.

INA_VAR_ALTURA = 2
INA_VAR_CAUDAL = 4


# ============================================================
# NODOS METEOROLÓGICOS
# ============================================================

NODOS = [

    # --------------------------------------------------------
    # SANTA FE
    # --------------------------------------------------------

    {
        "nombre": "Santa Fe",
        "provincia": "Santa Fe",
        "lat": -31.6333,
        "lon": -60.7000,
        "vulnerabilidad": 80,
    },

    {
        "nombre": "Reconquista",
        "provincia": "Santa Fe",
        "lat": -29.1500,
        "lon": -59.6500,
        "vulnerabilidad": 75,
    },

    {
        "nombre": "Rafaela",
        "provincia": "Santa Fe",
        "lat": -31.2500,
        "lon": -61.4900,
        "vulnerabilidad": 65,
    },

    {
        "nombre": "Rosario",
        "provincia": "Santa Fe",
        "lat": -32.9500,
        "lon": -60.6667,
        "vulnerabilidad": 85,
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
        "vulnerabilidad": 60,
    },


    # --------------------------------------------------------
    # CORRIENTES
    # --------------------------------------------------------

    {
        "nombre": "Corrientes",
        "provincia": "Corrientes",
        "lat": -27.4667,
        "lon": -58.8333,
        "vulnerabilidad": 85,
    },

    {
        "nombre": "Goya",
        "provincia": "Corrientes",
        "lat": -29.1400,
        "lon": -59.2600,
        "vulnerabilidad": 85,
    },

    {
        "nombre": "Bella Vista",
        "provincia": "Corrientes",
        "lat": -28.5100,
        "lon": -59.0400,
        "vulnerabilidad": 70,
    },

    {
        "nombre": "Ituzaingó",
        "provincia": "Corrientes",
        "lat": -27.5833,
        "lon": -56.6833,
        "vulnerabilidad": 70,
    },

    {
        "nombre": "Mercedes",
        "provincia": "Corrientes",
        "lat": -29.1833,
        "lon": -58.0833,
        "vulnerabilidad": 65,
    },

    {
        "nombre": "Paso de los Libres",
        "provincia": "Corrientes",
        "lat": -29.7167,
        "lon": -57.0833,
        "vulnerabilidad": 80,
    },


    # --------------------------------------------------------
    # ENTRE RÍOS
    # --------------------------------------------------------

    {
        "nombre": "Paraná",
        "provincia": "Entre Ríos",
        "lat": -31.7333,
        "lon": -60.5167,
        "vulnerabilidad": 80,
    },

    {
        "nombre": "La Paz",
        "provincia": "Entre Ríos",
        "lat": -30.7500,
        "lon": -59.6500,
        "vulnerabilidad": 80,
    },

    {
        "nombre": "Concordia",
        "provincia": "Entre Ríos",
        "lat": -31.4000,
        "lon": -58.0167,
        "vulnerabilidad": 90,
    },

    {
        "nombre": "Gualeguaychú",
        "provincia": "Entre Ríos",
        "lat": -33.0100,
        "lon": -58.5200,
        "vulnerabilidad": 80,
    },

    {
        "nombre": "Concepción del Uruguay",
        "provincia": "Entre Ríos",
        "lat": -32.4833,
        "lon": -58.2333,
        "vulnerabilidad": 80,
    },

    {
        "nombre": "Villaguay",
        "provincia": "Entre Ríos",
        "lat": -31.8500,
        "lon": -59.0167,
        "vulnerabilidad": 65,
    },
]


# ============================================================
# NIVELES DE RIESGO
# ============================================================

NIVELES_RIESGO = {
    "BAJO": {
        "min": 0,
        "max": 24,
        "emoji": "🟢",
    },

    "MODERADO": {
        "min": 25,
        "max": 49,
        "emoji": "🟡",
    },

    "ALTO": {
        "min": 50,
        "max": 74,
        "emoji": "🟠",
    },

    "MUY ALTO": {
        "min": 75,
        "max": 100,
        "emoji": "🔴",
    },
}


# ============================================================
# UTILIDADES
# ============================================================

def ahora():

    return datetime.now(TZ)


def limitar(
    valor,
    minimo=0,
    maximo=100,
):

    try:

        return max(
            minimo,
            min(
                maximo,
                float(valor),
            ),
        )

    except Exception:

        return minimo


def convertir_float(valor):

    if valor is None:
        return None

    try:

        if isinstance(
            valor,
            str,
        ):

            valor = (
                valor
                .replace(",", ".")
                .strip()
            )

        numero = float(valor)

        if math.isnan(numero):
            return None

        return numero

    except Exception:

        return None


def distancia_km(
    lat1,
    lon1,
    lat2,
    lon2,
):

    radio = 6371.0

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
        +
        math.cos(p1)
        *
        math.cos(p2)
        *
        math.sin(dlon / 2) ** 2
    )

    c = 2 * math.atan2(
        math.sqrt(a),
        math.sqrt(1 - a),
    )

    return radio * c


def nivel_riesgo(score):

    score = limitar(score)

    for nombre, info in NIVELES_RIESGO.items():

        if (
            score >= info["min"]
            and score <= info["max"]
        ):

            return nombre

    return "MUY ALTO"


def emoji_riesgo(nivel):

    return NIVELES_RIESGO.get(
        nivel,
        {"emoji": "⚪"},
    )["emoji"]


# ============================================================
# TELEGRAM
# ============================================================

def obtener_config_telegram():

    try:

        token = st.secrets.get(
            "TELEGRAM_BOT_TOKEN"
        )

        chat_id = st.secrets.get(
            "TELEGRAM_CHAT_ID"
        )

        if not token or not chat_id:

            return None, None

        return (
            str(token).strip(),
            str(chat_id).strip(),
        )

    except Exception:

        return None, None


def telegram_configurado():

    token, chat_id = (
        obtener_config_telegram()
    )

    return bool(
        token
        and chat_id
    )


def enviar_telegram(
    mensaje,
    silencioso=False,
):

    token, chat_id = (
        obtener_config_telegram()
    )

    if not token or not chat_id:

        return {
            "ok": False,
            "error": (
                "Telegram no está configurado."
            ),
        }

    url = (
        "https://api.telegram.org/"
        f"bot{token}/sendMessage"
    )

    payload = {
        "chat_id": chat_id,
        "text": mensaje,
        "disable_web_page_preview": True,
    }

    if silencioso:

        payload[
            "disable_notification"
        ] = True

    try:

        response = requests.post(
            url,
            data=payload,
            timeout=20,
        )

        if response.status_code != 200:

            return {
                "ok": False,
                "error": (
                    f"HTTP {response.status_code}: "
                    f"{response.text[:300]}"
                ),
            }

        data = response.json()

        if not data.get("ok"):

            return {
                "ok": False,
                "error": str(data),
            }

        return {
            "ok": True,
        }

    except Exception as exc:

        return {
            "ok": False,
            "error": str(exc),
        }


def formatear_mensaje_alerta(
    fila,
):

    nivel = fila["nivel"]

    mensaje = (
        f"{emoji_riesgo(nivel)} "
        f"ALERTA LITORAL AGRO\n\n"
        f"Nivel: {nivel}\n"
        f"📍 {fila['nombre']}, "
        f"{fila['provincia']}\n\n"
        f"📊 Riesgo integrado: "
        f"{fila['riesgo']:.0f}/100\n"
        f"🌧️ Lluvia 24 h: "
        f"{fila['lluvia_24']:.1f} mm\n"
        f"🌧️ Lluvia 72 h: "
        f"{fila['lluvia_72']:.1f} mm\n"
        f"🌱 Humedad suelo: "
        f"{fila['humedad']:.1f}%\n"
        f"💧 Runoff 72 h: "
        f"{fila['runoff_72']:.1f} mm\n"
    )

    if fila.get("hidro_ok"):

        mensaje += (
            "\n🌊 HIDROLOGÍA INA\n"
            f"Variable: "
            f"{fila.get('hidro_variable', 'N/D')}\n"
            f"Valor actual: "
            f"{fila.get('hidro_valor', 0):.2f}\n"
            f"Tendencia: "
            f"{fila.get('hidro_direccion', 'N/D')}\n"
        )

        if fila.get(
            "hidro_alerta"
        ) is not None:

            mensaje += (
                f"Nivel de alerta: "
                f"{fila['hidro_alerta']:.2f}\n"
            )

        if fila.get(
            "hidro_evacuacion"
        ) is not None:

            mensaje += (
                f"Nivel evacuación: "
                f"{fila['hidro_evacuacion']:.2f}\n"
            )

    mensaje += (
        "\n🕐 Actualizado: "
        f"{ahora().strftime('%d/%m/%Y %H:%M')}\n"
        "\nSistema Alerta Litoral Agro "
        f"V{APP_VERSION}"
    )

    return mensaje


def enviar_alertas_automaticas(
    resultados,
):

    if not telegram_configurado():

        return []

    if "telegram_alertas" not in st.session_state:

        st.session_state[
            "telegram_alertas"
        ] = {}

    enviados = []

    for fila in resultados:

        nivel = fila["nivel"]

        # ----------------------------------------------------
        # Sólo ALTO y MUY ALTO generan alerta automática.
        # ----------------------------------------------------

        if nivel not in [
            "ALTO",
            "MUY ALTO",
        ]:

            continue

        nombre = fila["nombre"]

        clave = (
            f"{nombre}|{nivel}"
        )

        anterior = (
            st.session_state[
                "telegram_alertas"
            ].get(nombre)
        )

        # Evita repetir exactamente
        # la misma alerta.

        if anterior == clave:

            continue

        mensaje = formatear_mensaje_alerta(
            fila
        )

        resultado = enviar_telegram(
            mensaje
        )

        if resultado.get("ok"):

            st.session_state[
                "telegram_alertas"
            ][nombre] = clave

            enviados.append(
                nombre
            )

    return enviados


def enviar_resumen_telegram(
    resultados,
):

    if not telegram_configurado():

        return {
            "ok": False,
            "error": "Telegram no configurado.",
        }

    if not resultados:

        return {
            "ok": False,
            "error": "No hay resultados.",
        }

    orden = {
        "MUY ALTO": 4,
        "ALTO": 3,
        "MODERADO": 2,
        "BAJO": 1,
    }

    resultados_ordenados = sorted(
        resultados,
        key=lambda x: (
            orden.get(
                x["nivel"],
                0,
            ),
            x["riesgo"],
        ),
        reverse=True,
    )

    mensaje = (
        "🌎 ALERTA LITORAL AGRO\n"
        "📊 RESUMEN OPERATIVO\n\n"
        f"🕐 {ahora().strftime('%d/%m/%Y %H:%M')}\n\n"
    )

    for fila in resultados_ordenados:

        mensaje += (
            f"{emoji_riesgo(fila['nivel'])} "
            f"{fila['nombre']} — "
            f"{fila['riesgo']:.0f}/100 "
            f"({fila['nivel']})\n"
        )

    mensaje += (
        "\nSistema Alerta Litoral Agro "
        f"V{APP_VERSION}"
    )

    # Telegram admite hasta 4096 caracteres
    # por mensaje. Dejamos margen.

    mensaje = mensaje[:3900]

    return enviar_telegram(
        mensaje
    )


# ============================================================
# OPEN-METEO
# ============================================================

@st.cache_data(
    ttl=900,
    show_spinner=False,
)
def consultar_open_meteo(
    lat,
    lon,
):

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
        "hourly": ",".join(
            variables
        ),
        "past_days": 3,
        "forecast_days": 7,
        "timezone": (
            "America/Argentina/"
            "Buenos_Aires"
        ),
        "cell_selection": "land",
    }

    endpoints = [
        OPEN_METEO_ECMWF,
        OPEN_METEO_FORECAST,
    ]

    ultimo_error = None

    for endpoint in endpoints:

        try:

            response = requests.get(
                endpoint,
                params=params,
                timeout=30,
            )

            if response.status_code != 200:

                ultimo_error = (
                    f"HTTP {response.status_code}"
                )

                continue

            data = response.json()

            if "hourly" not in data:

                ultimo_error = (
                    "Respuesta sin datos horarios."
                )

                continue

            return data

        except Exception as exc:

            ultimo_error = str(exc)

    return {
        "_error": ultimo_error
        or "No se pudo consultar Open-Meteo."
    }


def procesar_open_meteo(
    data,
):

    if not data or "_error" in data:

        return {
            "ok": False,
            "error": data.get(
                "_error",
                "Error meteorológico.",
            ),
        }

    hourly = data.get(
        "hourly",
        {},
    )

    times = hourly.get(
        "time",
        [],
    )

    if not times:

        return {
            "ok": False,
            "error": "Sin datos horarios.",
        }

    df = pd.DataFrame(
        hourly
    )

    df["time"] = pd.to_datetime(
        df["time"],
        errors="coerce",
    )

    df = df.dropna(
        subset=["time"]
    )

    if df.empty:

        return {
            "ok": False,
            "error": "Serie temporal inválida.",
        }

    # --------------------------------------------------------
    # Convertir variables numéricas
    # --------------------------------------------------------

    variables = [
        "precipitation",
        "soil_moisture_0_to_7cm",
        "soil_moisture_7_to_28cm",
        "soil_moisture_28_to_100cm",
        "soil_moisture_100_to_255cm",
        "runoff",
        "wind_gusts_10m",
    ]

    for variable in variables:

        if variable in df.columns:

            df[variable] = pd.to_numeric(
                df[variable],
                errors="coerce",
            )

    # --------------------------------------------------------
    # Nos interesa el pasado para el diagnóstico.
    # Evitamos mezclar pronóstico con observación
    # en los acumulados actuales.
    # --------------------------------------------------------

    ahora_local = pd.Timestamp(
        ahora()
    )

    if df["time"].dt.tz is None:

        df["time"] = (
            df["time"]
            .dt.tz_localize(
                TZ,
                ambiguous="NaT",
                nonexistent="NaT",
            )
        )

    pasado = df[
        df["time"] <= ahora_local
    ].copy()

    if pasado.empty:

        pasado = df.copy()

    pasado = pasado.sort_values(
        "time"
    )

    ultimas_24 = pasado.tail(24)
    ultimas_72 = pasado.tail(72)

    # --------------------------------------------------------
    # Precipitación
    # --------------------------------------------------------

    lluvia_24 = (
        ultimas_24["precipitation"]
        .sum(min_count=1)
        if "precipitation" in ultimas_24
        else None
    )

    lluvia_72 = (
        ultimas_72["precipitation"]
        .sum(min_count=1)
        if "precipitation" in ultimas_72
        else None
    )

    # --------------------------------------------------------
    # Humedad del suelo
    # --------------------------------------------------------

    humedad_vars = [
        "soil_moisture_0_to_7cm",
        "soil_moisture_7_to_28cm",
        "soil_moisture_28_to_100cm",
        "soil_moisture_100_to_255cm",
    ]

    humedad_valores = []

    for variable in humedad_vars:

        if variable in ultimas_24:

            serie = ultimas_24[
                variable
            ].dropna()

            if not serie.empty:

                humedad_valores.append(
                    serie.mean()
                )

    humedad = (
        sum(humedad_valores)
        / len(humedad_valores)
        if humedad_valores
        else None
    )

    # --------------------------------------------------------
    # Runoff
    # --------------------------------------------------------

    runoff_72 = (
        ultimas_72["runoff"]
        .sum(min_count=1)
        if "runoff" in ultimas_72
        else None
    )

    # --------------------------------------------------------
    # Viento
    # --------------------------------------------------------

    max_gust = (
        ultimas_24[
            "wind_gusts_10m"
        ].max()
        if "wind_gusts_10m" in ultimas_24
        else None
    )

    # --------------------------------------------------------
    # Cobertura real
    # --------------------------------------------------------

    expected = [
        "precipitation",
        "soil_moisture_0_to_7cm",
        "soil_moisture_7_to_28cm",
        "soil_moisture_28_to_100cm",
        "soil_moisture_100_to_255cm",
        "runoff",
        "wind_gusts_10m",
    ]

    cobertura = []

    for variable in expected:

        if variable not in ultimas_72:

            cobertura.append(0)

            continue

        serie = ultimas_72[
            variable
        ]

        cobertura.append(
            serie.notna().mean()
        )

    calidad = (
        sum(cobertura)
        / len(cobertura)
        * 100
        if cobertura
        else 0
    )

    return {
        "ok": True,
        "lluvia_24": (
            float(lluvia_24)
            if pd.notna(lluvia_24)
            else None
        ),
        "lluvia_72": (
            float(lluvia_72)
            if pd.notna(lluvia_72)
            else None
        ),
        "humedad": (
            float(humedad)
            if humedad is not None
            else None
        ),
        "runoff_72": (
            float(runoff_72)
            if pd.notna(runoff_72)
            else None
        ),
        "max_gust": (
            float(max_gust)
            if pd.notna(max_gust)
            else None
        ),
        "calidad": limitar(
            calidad
        ),
    }


# ============================================================
# INA — EXTRACCIÓN ROBUSTA
# ============================================================

def extraer_lista_datos(
    data,
):

    if data is None:
        return []

    if isinstance(
        data,
        list,
    ):

        return data

    if isinstance(
        data,
        dict,
    ):

        for clave in [
            "data",
            "datos",
            "results",
            "result",
            "features",
        ]:

            valor = data.get(
                clave
            )

            if isinstance(
                valor,
                list,
            ):

                # GeoJSON
                if clave == "features":

                    salida = []

                    for feature in valor:

                        if not isinstance(
                            feature,
                            dict,
                        ):

                            continue

                        properties = (
                            feature.get(
                                "properties",
                                {},
                            )
                        )

                        geometry = (
                            feature.get(
                                "geometry",
                                {},
                            )
                        )

                        fila = dict(
                            properties
                        )

                        coords = (
                            geometry.get(
                                "coordinates"
                            )
                            if isinstance(
                                geometry,
                                dict,
                            )
                            else None
                        )

                        if (
                            coords
                            and len(coords) >= 2
                        ):

                            fila["lon"] = (
                                coords[0]
                            )

                            fila["lat"] = (
                                coords[1]
                            )

                        salida.append(
                            fila
                        )

                    return salida

                return valor

    return []


# ============================================================
# INA — ESTACIONES
# ============================================================

@st.cache_data(
    ttl=1800,
    show_spinner=False,
)
def consultar_estaciones_ina():

    params = {
        "format": "json",
    }

    try:

        response = requests.get(
            INA_ESTACIONES,
            params=params,
            timeout=30,
        )

        if response.status_code != 200:

            return []

        data = response.json()

        estaciones = (
            extraer_lista_datos(
                data
            )
        )

        resultado = []

        for estacion in estaciones:

            if not isinstance(
                estacion,
                dict,
            ):

                continue

            sitecode = estacion.get(
                "sitecode"
            )

            nombre = estacion.get(
                "nombre"
            )

            lat = convertir_float(
                estacion.get("lat")
            )

            lon = convertir_float(
                estacion.get("lon")
            )

            if (
                sitecode is None
                or lat is None
                or lon is None
            ):

                continue

            tipo = str(
                estacion.get(
                    "tipo",
                    "",
                )
            ).upper()

            tipo_nombre = str(
                estacion.get(
                    "tipo_nombre",
                    "",
                )
            ).lower()

            rio = estacion.get(
                "rio"
            )

            # ------------------------------------------------
            # Selección amplia de estaciones hidrológicas.
            # La serie observada será la que determine
            # posteriormente si realmente es utilizable.
            # ------------------------------------------------

            es_hidro = (
                tipo == "H"
                or "hidrol" in tipo_nombre
                or "limnim" in tipo_nombre
                or rio not in [
                    None,
                    "",
                ]
            )

            if not es_hidro:

                continue

            estacion_normalizada = {

                "sitecode": str(
                    sitecode
                ),

                "nombre": (
                    nombre
                    or f"Estación {sitecode}"
                ),

                "lat": lat,
                "lon": lon,

                "provincia": (
                    estacion.get(
                        "distrito"
                    )
                ),

                "rio": rio,

                "tipo": tipo,

                "tipo_nombre": (
                    tipo_nombre
                ),

                "automatica": (
                    estacion.get(
                        "automatica"
                    )
                ),

                "real": (
                    estacion.get(
                        "real"
                    )
                ),

                "nivel_alerta": convertir_float(
                    estacion.get(
                        "nivel_de_alerta"
                    )
                ),

                "nivel_evacuacion": convertir_float(
                    estacion.get(
                        "nivel_de_evacuacion"
                    )
                ),
            }

            resultado.append(
                estacion_normalizada
            )

        return resultado

    except Exception:

        return []


def encontrar_estacion_cercana(
    nodo,
    estaciones,
    max_km=150,
):

    if not estaciones:

        return None

    candidatos = []

    for estacion in estaciones:

        try:

            distancia = distancia_km(
                nodo["lat"],
                nodo["lon"],
                estacion["lat"],
                estacion["lon"],
            )

            if distancia <= max_km:

                candidatos.append(
                    (
                        distancia,
                        estacion,
                    )
                )

        except Exception:

            continue

    if not candidatos:

        return None

    candidatos.sort(
        key=lambda x: x[0]
    )

    distancia, estacion = (
        candidatos[0]
    )

    estacion = dict(
        estacion
    )

    estacion[
        "distancia_km"
    ] = distancia

    return estacion


# ============================================================
# INA — DATOS OBSERVADOS
# ============================================================

def normalizar_datos_ina(
    registros,
):

    filas = []

    for registro in registros:

        if not isinstance(
            registro,
            dict,
        ):

            continue

        valor = registro.get(
            "valor"
        )

        if valor is None:

            valor = registro.get(
                "value"
            )

        tiempo = registro.get(
            "timestart"
        )

        if tiempo is None:

            tiempo = registro.get(
                "time"
            )

        if valor is None or tiempo is None:

            continue

        valor_num = convertir_float(
            valor
        )

        if valor_num is None:

            continue

        filas.append(
            {
                "time": tiempo,
                "valor": valor_num,
            }
        )

    if not filas:

        return pd.DataFrame()

    df = pd.DataFrame(
        filas
    )

    df["time"] = pd.to_datetime(
        df["time"],
        errors="coerce",
    )

    df["valor"] = pd.to_numeric(
        df["valor"],
        errors="coerce",
    )

    df = df.dropna(
        subset=[
            "time",
            "valor",
        ]
    )

    df = (
        df
        .sort_values("time")
        .drop_duplicates(
            subset=["time"],
            keep="last",
        )
        .reset_index(drop=True)
    )

    return df


def calcular_tendencia_hidrologica(
    df,
    horas=24,
    min_observaciones=3,
):

    if df is None or df.empty:

        return None

    if (
        "time" not in df.columns
        or "valor" not in df.columns
    ):

        return None

    datos = df.copy()

    datos["time"] = pd.to_datetime(
        datos["time"],
        errors="coerce",
    )

    datos["valor"] = pd.to_numeric(
        datos["valor"],
        errors="coerce",
    )

    datos = datos.dropna(
        subset=[
            "time",
            "valor",
        ]
    )

    if len(datos) < 2:

        return None

    datos = (
        datos
        .sort_values("time")
        .drop_duplicates(
            subset=["time"],
            keep="last",
        )
    )

    ultimo_tiempo = (
        datos["time"].iloc[-1]
    )

    inicio_periodo = (
        ultimo_tiempo
        - pd.Timedelta(
            hours=horas
        )
    )

    periodo = datos[
        datos["time"]
        >= inicio_periodo
    ].copy()

    suficiente = (
        len(periodo)
        >= min_observaciones
    )

    if len(periodo) < 2:

        return None

    primero = periodo.iloc[0]
    ultimo = periodo.iloc[-1]

    horas_reales = (
        ultimo["time"]
        - primero["time"]
    ).total_seconds() / 3600.0

    if horas_reales <= 0:

        return None

    valor_inicio = float(
        primero["valor"]
    )

    valor_actual = float(
        ultimo["valor"]
    )

    cambio = (
        valor_actual
        - valor_inicio
    )

    cambio_por_hora = (
        cambio
        / horas_reales
    )

    if abs(valor_inicio) > 0.000001:

        cambio_porcentaje = (
            cambio
            / abs(valor_inicio)
            * 100
        )

    else:

        cambio_porcentaje = None

    tolerancia = max(
        abs(valor_inicio) * 0.005,
        0.001,
    )

    if cambio > tolerancia:

        direccion = "ASCENDENTE"

    elif cambio < -tolerancia:

        direccion = "DESCENDENTE"

    else:

        direccion = "ESTABLE"

    return {
        "valor_actual": valor_actual,
        "valor_inicio": valor_inicio,
        "cambio_absoluto": cambio,
        "cambio_por_hora": cambio_por_hora,
        "cambio_porcentaje": cambio_porcentaje,
        "horas_analizadas": horas_reales,
        "direccion": direccion,
        "n_observaciones": len(periodo),
        "suficientes_datos": suficiente,
    }


def evaluar_actualidad_hidrologica(
    fecha_ultimo,
    max_horas=6,
):

    if fecha_ultimo is None:

        return False

    try:

        fecha = pd.Timestamp(
            fecha_ultimo
        )

        ahora_ts = pd.Timestamp(
            ahora()
        )

        if fecha.tzinfo is None:

            fecha = fecha.tz_localize(
                TZ
            )

        if ahora_ts.tzinfo is None:

            ahora_ts = ahora_ts.tz_localize(
                TZ
            )

        diferencia = (
            ahora_ts - fecha
        ).total_seconds() / 3600

        return (
            diferencia >= 0
            and diferencia <= max_horas
        )

    except Exception:

        return False


# ============================================================
# COMPONENTE HIDROLÓGICO
# ============================================================

def componente_estado_hidrologico(
    valor_actual,
    nivel_alerta=None,
    nivel_evacuacion=None,
):

    if valor_actual is None:

        return None

    valor_actual = float(
        valor_actual
    )

    if (
        nivel_evacuacion is not None
        and nivel_evacuacion > 0
    ):

        if valor_actual >= nivel_evacuacion:

            return 100.0

        if (
            nivel_alerta is not None
            and valor_actual >= nivel_alerta
        ):

            rango = (
                nivel_evacuacion
                - nivel_alerta
            )

            if rango > 0:

                return limitar(
                    70
                    + (
                        (
                            valor_actual
                            - nivel_alerta
                        )
                        / rango
                    )
                    * 30
                )

            return 70.0

        if (
            nivel_alerta is not None
            and nivel_alerta > 0
        ):

            return limitar(
                (
                    valor_actual
                    / nivel_alerta
                )
                * 70
            )

        return limitar(
            (
                valor_actual
                / nivel_evacuacion
            )
            * 70
        )

    if (
        nivel_alerta is not None
        and nivel_alerta > 0
    ):

        if valor_actual >= nivel_alerta:

            return 100.0

        return limitar(
            (
                valor_actual
                / nivel_alerta
            )
            * 80
        )

    return None


def componente_tendencia_hidrologica(
    tendencia,
):

    if not tendencia:

        return None

    direccion = tendencia.get(
        "direccion"
    )

    cambio_pct = tendencia.get(
        "cambio_porcentaje"
    )

    if cambio_pct is None:

        return None

    cambio_pct = float(
        cambio_pct
    )

    if direccion == "DESCENDENTE":

        return 0.0

    if direccion == "ESTABLE":

        return 10.0

    if direccion == "ASCENDENTE":

        ascenso = max(
            0.0,
            cambio_pct,
        )

        if ascenso < 1:

            return 20.0

        if ascenso < 3:

            return 40.0

        if ascenso < 5:

            return 60.0

        if ascenso < 10:

            return 80.0

        return 100.0

    return None


def componente_hidrologia(
    hidro,
):

    if not hidro:

        return None

    if not hidro.get("ok"):

        return None

    estado = (
        componente_estado_hidrologico(
            valor_actual=hidro.get(
                "valor_actual"
            ),
            nivel_alerta=hidro.get(
                "nivel_alerta"
            ),
            nivel_evacuacion=hidro.get(
                "nivel_evacuacion"
            ),
        )
    )

    tendencia_score = (
        componente_tendencia_hidrologica(
            hidro.get(
                "tendencia"
            )
        )
    )

    if estado is not None:

        if tendencia_score is not None:

            return (
                estado * 0.70
                +
                tendencia_score * 0.30
            )

        return estado

    if tendencia_score is not None:

        return tendencia_score

    return None


# ============================================================
# CONSULTA INA
# ============================================================

def consultar_datos_ina(
    estacion,
    dias=7,
):

    if not estacion:

        return {
            "ok": False,
            "error": "No hay estación INA.",
        }

    sitecode = estacion.get(
        "sitecode"
    )

    if not sitecode:

        return {
            "ok": False,
            "error": (
                "La estación no posee sitecode."
            ),
        }

    fecha_fin = ahora()

    fecha_inicio = (
        fecha_fin
        - timedelta(
            days=dias
        )
    )

    params_base = {
        "timeStart": fecha_inicio.strftime(
            "%Y-%m-%dT%H:%M:%S"
        ),
        "timeEnd": fecha_fin.strftime(
            "%Y-%m-%dT%H:%M:%S"
        ),
        "siteCode": sitecode,
        "format": "json",
    }

    # Primero altura.
    # Luego caudal como alternativa.

    for var_id in [
        INA_VAR_ALTURA,
        INA_VAR_CAUDAL,
    ]:

        params = (
            params_base.copy()
        )

        params["varId"] = var_id

        try:

            response = requests.get(
                INA_DATOS,
                params=params,
                timeout=30,
            )

            if response.status_code != 200:

                continue

            data = response.json()

            registros = (
                extraer_lista_datos(
                    data
                )
            )

            if not registros:

                continue

            df = (
                normalizar_datos_ina(
                    registros
                )
            )

            if df.empty:

                continue

            tendencia = (
                calcular_tendencia_hidrologica(
                    df,
                    horas=24,
                    min_observaciones=3,
                )
            )

            if tendencia is None:

                continue

            fecha_ultimo = (
                df["time"].iloc[-1]
            )

            actualidad = (
                evaluar_actualidad_hidrologica(
                    fecha_ultimo,
                    max_horas=6,
                )
            )

            variable = (
                "altura"
                if var_id
                == INA_VAR_ALTURA
                else "caudal"
            )

            return {

                "ok": True,

                "variable": variable,

                "var_id": var_id,

                "valor_actual": tendencia[
                    "valor_actual"
                ],

                "valor_24h": tendencia[
                    "valor_inicio"
                ],

                "cambio_absoluto": tendencia[
                    "cambio_absoluto"
                ],

                "cambio_por_hora": tendencia[
                    "cambio_por_hora"
                ],

                "cambio_porcentaje": tendencia[
                    "cambio_porcentaje"
                ],

                "direccion": tendencia[
                    "direccion"
                ],

                "horas_analizadas": tendencia[
                    "horas_analizadas"
                ],

                "n_datos": tendencia[
                    "n_observaciones"
                ],

                "suficientes_datos": (
                    tendencia.get(
                        "suficientes_datos",
                        False,
                    )
                ),

                "nivel_alerta": (
                    estacion.get(
                        "nivel_alerta"
                    )
                ),

                "nivel_evacuacion": (
                    estacion.get(
                        "nivel_evacuacion"
                    )
                ),

                "tendencia": tendencia,

                "fecha_ultimo": fecha_ultimo,

                "actualidad": actualidad,

                "sitecode": sitecode,

                "estacion": estacion.get(
                    "nombre"
                ),

                "distancia_km": estacion.get(
                    "distancia_km"
                ),
            }

        except Exception:

            continue

    return {
        "ok": False,
        "error": (
            "No se encontraron "
            "observaciones hidrológicas "
            "compatibles."
        ),
    }


# ============================================================
# COMPONENTES METEOROLÓGICOS
# ============================================================

def componente_lluvia_24(
    lluvia,
):

    if lluvia is None:

        return None

    # Escala experimental.

    if lluvia < 20:

        return 0

    if lluvia < 40:

        return 25

    if lluvia < 70:

        return 50

    if lluvia < 100:

        return 75

    return 100


def componente_lluvia_72(
    lluvia,
):

    if lluvia is None:

        return None

    if lluvia < 50:

        return 0

    if lluvia < 100:

        return 25

    if lluvia < 150:

        return 50

    if lluvia < 200:

        return 75

    return 100


def componente_humedad(
    humedad,
):

    if humedad is None:

        return None

    # Humedad volumétrica aproximada.
    #
    # Escala experimental para el prototipo.

    if humedad < 0.15:

        return 0

    if humedad < 0.25:

        return 30

    if humedad < 0.35:

        return 60

    if humedad < 0.45:

        return 80

    return 100


def componente_runoff(
    runoff,
):

    if runoff is None:

        return None

    if runoff < 5:

        return 0

    if runoff < 15:

        return 25

    if runoff < 30:

        return 50

    if runoff < 50:

        return 75

    return 100


# ============================================================
# RIESGO INTEGRADO
# ============================================================

def calcular_riesgo(
    lluvia24,
    lluvia72,
    humedad,
    runoff,
    hidrologia,
    vulnerabilidad,
):

    componentes = []

    pesos = []

    # --------------------------------------------------------
    # Lluvia 24 h
    # --------------------------------------------------------

    c = componente_lluvia_24(
        lluvia24
    )

    if c is not None:

        componentes.append(c)
        pesos.append(20)

    # --------------------------------------------------------
    # Lluvia 72 h
    # --------------------------------------------------------

    c = componente_lluvia_72(
        lluvia72
    )

    if c is not None:

        componentes.append(c)
        pesos.append(20)

    # --------------------------------------------------------
    # Humedad
    # --------------------------------------------------------

    c = componente_humedad(
        humedad
    )

    if c is not None:

        componentes.append(c)
        pesos.append(15)

    # --------------------------------------------------------
    # Runoff
    # --------------------------------------------------------

    c = componente_runoff(
        runoff
    )

    if c is not None:

        componentes.append(c)
        pesos.append(5)

    # --------------------------------------------------------
    # Hidrología
    # --------------------------------------------------------

    c = componente_hidrologia(
        hidrologia
    )

    if c is not None:

        # Si los datos están demasiado viejos,
        # no los utilizamos como estado actual.

        if hidrologia.get(
            "actualidad",
            False,
        ):

            componentes.append(c)
            pesos.append(20)

        else:

            # La tendencia puede conservarse como
            # información contextual, pero no pesa
            # como observación actual.

            pass

    # --------------------------------------------------------
    # Vulnerabilidad
    # --------------------------------------------------------

    if vulnerabilidad is not None:

        componentes.append(
            limitar(
                vulnerabilidad
            )
        )

        pesos.append(10)

    if not componentes:

        return 0.0

    return limitar(
        sum(
            valor * peso
            for valor, peso
            in zip(
                componentes,
                pesos,
            )
        )
        / sum(pesos)
    )


# ============================================================
# PROCESAMIENTO DE NODOS
# ============================================================

def procesar_nodo(
    nodo,
    estaciones_ina,
):

    # --------------------------------------------------------
    # Meteorología
    # --------------------------------------------------------

    meteo_raw = consultar_open_meteo(
        nodo["lat"],
        nodo["lon"],
    )

    meteo = procesar_open_meteo(
        meteo_raw
    )

    if not meteo.get("ok"):

        return {
            "nombre": nodo["nombre"],
            "provincia": nodo["provincia"],
            "lat": nodo["lat"],
            "lon": nodo["lon"],
            "riesgo": 0,
            "nivel": "BAJO",
            "calidad_meteo": 0,
            "calidad_hidro": 0,
            "error": meteo.get(
                "error"
            ),
            "hidro_ok": False,
        }

    # --------------------------------------------------------
    # Estación INA cercana
    # --------------------------------------------------------

    estacion = (
        encontrar_estacion_cercana(
            nodo,
            estaciones_ina,
            max_km=150,
        )
    )

    hidro = None

    if estacion:

        hidro = consultar_datos_ina(
            estacion,
            dias=7,
        )

    # --------------------------------------------------------
    # Riesgo
    # --------------------------------------------------------

    riesgo = calcular_riesgo(

        lluvia24=meteo.get(
            "lluvia_24"
        ),

        lluvia72=meteo.get(
            "lluvia_72"
        ),

        humedad=meteo.get(
            "humedad"
        ),

        runoff=meteo.get(
            "runoff_72"
        ),

        hidrologia=hidro,

        vulnerabilidad=nodo[
            "vulnerabilidad"
        ],
    )

    nivel = nivel_riesgo(
        riesgo
    )

    # --------------------------------------------------------
    # Calidad hidrológica
    # --------------------------------------------------------

    if (
        hidro
        and hidro.get("ok")
    ):

        if (
            hidro.get(
                "actualidad",
                False,
            )
            and
            (
                hidro.get(
                    "nivel_alerta"
                )
                is not None
                or
                hidro.get(
                    "nivel_evacuacion"
                )
                is not None
            )
        ):

            calidad_hidro = 100

        elif hidro.get(
            "actualidad",
            False,
        ):

            calidad_hidro = 70

        else:

            calidad_hidro = 30

    else:

        calidad_hidro = 0

    # --------------------------------------------------------
    # Resultado
    # --------------------------------------------------------

    resultado = {

        "nombre": nodo[
            "nombre"
        ],

        "provincia": nodo[
            "provincia"
        ],

        "lat": nodo[
            "lat"
        ],

        "lon": nodo[
            "lon"
        ],

        "riesgo": riesgo,

        "nivel": nivel,

        "lluvia_24": (
            meteo.get(
                "lluvia_24"
            )
            or 0
        ),

        "lluvia_72": (
            meteo.get(
                "lluvia_72"
            )
            or 0
        ),

        "humedad": (
            (
                meteo.get(
                    "humedad"
                )
                or 0
            )
            * 100
        ),

        "runoff_72": (
            meteo.get(
                "runoff_72"
            )
            or 0
        ),

        "max_gust": (
            meteo.get(
                "max_gust"
            )
            or 0
        ),

        "calidad_meteo": (
            meteo.get(
                "calidad",
                0,
            )
        ),

        "calidad_hidro": (
            calidad_hidro
        ),

        "vulnerabilidad": nodo[
            "vulnerabilidad"
        ],

        "hidro_ok": bool(
            hidro
            and hidro.get(
                "ok"
            )
        ),

        "hidro_estacion": (
            hidro.get(
                "estacion"
            )
            if hidro
            else None
        ),

        "hidro_variable": (
            hidro.get(
                "variable"
            )
            if hidro
            else None
        ),

        "hidro_valor": (
            hidro.get(
                "valor_actual"
            )
            if hidro
            else None
        ),

        "hidro_direccion": (
            hidro.get(
                "direccion"
            )
            if hidro
            else None
        ),

        "hidro_alerta": (
            hidro.get(
                "nivel_alerta"
            )
            if hidro
            else None
        ),

        "hidro_evacuacion": (
            hidro.get(
                "nivel_evacuacion"
            )
            if hidro
            else None
        ),

        "hidro_actualidad": (
            hidro.get(
                "actualidad",
                False,
            )
            if hidro
            else False
        ),

        "hidro_distancia": (
            hidro.get(
                "distancia_km"
            )
            if hidro
            else None
        ),

        "error": None,
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

    # --------------------------------------------------------
    # Nodos meteorológicos
    # --------------------------------------------------------

    lat = [
        r["lat"]
        for r in resultados
    ]

    lon = [
        r["lon"]
        for r in resultados
    ]

    textos = []

    for r in resultados:

        textos.append(
            (
                f"<b>{r['nombre']}</b><br>"
                f"Provincia: {r['provincia']}<br>"
                f"Riesgo: {r['riesgo']:.0f}/100<br>"
                f"Nivel: {r['nivel']}<br>"
                f"Lluvia 24 h: "
                f"{r['lluvia_24']:.1f} mm<br>"
                f"Lluvia 72 h: "
                f"{r['lluvia_72']:.1f} mm<br>"
                f"Humedad suelo: "
                f"{r['humedad']:.1f}%"
            )
        )

    fig.add_trace(
        go.Scattergeo(

            lon=lon,
            lat=lat,

            text=textos,

            hoverinfo="text",

            mode="markers",

            marker=dict(
                size=11,
                color=[
                    r["riesgo"]
                    for r in resultados
                ],
                colorscale=[
                    [0.00, "green"],
                    [0.25, "green"],
                    [0.50, "yellow"],
                    [0.75, "orange"],
                    [1.00, "red"],
                ],
                cmin=0,
                cmax=100,
                colorbar=dict(
                    title="Riesgo"
                ),
                line=dict(
                    width=1,
                    color="black",
                ),
            ),

            name="Nodos de riesgo",
        )
    )

    # --------------------------------------------------------
    # Estaciones INA
    # --------------------------------------------------------

    estaciones_mapa = []

    for estacion in estaciones:

        estaciones_mapa.append(
            estacion
        )

    if estaciones_mapa:

        fig.add_trace(
            go.Scattergeo(

                lon=[
                    e["lon"]
                    for e
                    in estaciones_mapa
                ],

                lat=[
                    e["lat"]
                    for e
                    in estaciones_mapa
                ],

                mode="markers",

                marker=dict(
                    size=6,
                    symbol="circle",
                ),

                text=[
                    (
                        f"<b>{e['nombre']}</b><br>"
                        f"INA sitecode: "
                        f"{e['sitecode']}<br>"
                        f"Río: "
                        f"{e.get('rio') or 'N/D'}"
                    )
                    for e
                    in estaciones_mapa
                ],

                hoverinfo="text",

                name="Estaciones INA",
            )
        )

    # --------------------------------------------------------
    # Geografía
    # --------------------------------------------------------

    fig.update_geos(

        scope="south america",

        projection_type="mercator",

        showland=True,

        showocean=True,

        showcountries=True,

        showcoastlines=True,

        showsubunits=True,

        lonaxis_range=[
            -64,
            -54,
        ],

        lataxis_range=[
            -35,
            -26,
        ],
    )

    fig.update_layout(

        height=650,

        margin=dict(
            l=0,
            r=0,
            t=40,
            b=0,
        ),

        title=(
            "Alerta Litoral Agro — "
            "Riesgo territorial"
        ),

        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=0.01,
            xanchor="left",
            x=0.01,
        ),
    )

    return fig


# ============================================================
# HISTORIAL
# ============================================================

def guardar_historial(
    resultados,
):

    if not resultados:

        return

    registros = []

    fecha = ahora()

    for r in resultados:

        registros.append(
            {
                "fecha": fecha,
                "nodo": r["nombre"],
                "provincia": r[
                    "provincia"
                ],
                "riesgo": r[
                    "riesgo"
                ],
                "nivel": r[
                    "nivel"
                ],
                "lluvia_24": r[
                    "lluvia_24"
                ],
                "lluvia_72": r[
                    "lluvia_72"
                ],
                "humedad": r[
                    "humedad"
                ],
                "runoff_72": r[
                    "runoff_72"
                ],
                "calidad_meteo": r[
                    "calidad_meteo"
                ],
                "calidad_hidro": r[
                    "calidad_hidro"
                ],
            }
        )

    nuevo = pd.DataFrame(
        registros
    )

    if (
        "historial"
        not in st.session_state
    ):

        st.session_state[
            "historial"
        ] = nuevo

    else:

        st.session_state[
            "historial"
        ] = pd.concat(
            [
                st.session_state[
                    "historial"
                ],
                nuevo,
            ],
            ignore_index=True,
        )

        # Limitar tamaño
        st.session_state[
            "historial"
        ] = (
            st.session_state[
                "historial"
            ]
            .tail(5000)
            .reset_index(
                drop=True
            )
        )


# ============================================================
# INTERFAZ
# ============================================================

st.title(
    "🌧️ Alerta Litoral Agro"
)

st.caption(
    f"Prototipo operativo de alerta temprana "
    f"para riesgo de anegamiento agropecuario "
    f"— V{APP_VERSION}"
)


# ============================================================
# SIDEBAR
# ============================================================

with st.sidebar:

    st.header(
        "⚙️ Configuración"
    )

    auto_telegram = st.checkbox(
        "Activar alertas automáticas Telegram",
        value=True,
    )

    max_km_ina = st.slider(
        "Distancia máxima a estación INA (km)",
        min_value=25,
        max_value=250,
        value=150,
        step=25,
    )

    st.divider()

    st.subheader(
        "📲 Telegram"
    )

    if telegram_configurado():

        st.success(
            "Telegram configurado"
        )

    else:

        st.warning(
            "Telegram no configurado"
        )

    st.caption(
        "El token se obtiene exclusivamente "
        "desde Streamlit Secrets."
    )

    st.divider()

    st.subheader(
        "ℹ️ Fuentes"
    )

    st.write(
        "• Open-Meteo / ECMWF"
    )

    st.write(
        "• INA / DSIyAH"
    )

    st.write(
        "• Datos meteorológicos modelados"
    )

    st.write(
        "• Datos hidrológicos observados"
    )


# ============================================================
# BOTÓN ACTUALIZAR
# ============================================================

col_actualizar, col_telegram = (
    st.columns(
        [1, 1]
    )
)


with col_actualizar:

    actualizar = st.button(
        "🔄 Actualizar datos",
        type="primary",
        use_container_width=True,
    )


with col_telegram:

    enviar_resumen = st.button(
        "📲 Enviar resumen a Telegram",
        use_container_width=True,
    )


# ============================================================
# CARGA DE DATOS
# ============================================================

if (
    actualizar
    or
    "resultados" not in st.session_state
):

    with st.spinner(
        "Consultando meteorología e hidrología..."
    ):

        estaciones_ina = (
            consultar_estaciones_ina()
        )

        resultados = []

        for nodo in NODOS:

            resultado = procesar_nodo(
                nodo,
                estaciones_ina,
            )

            resultados.append(
                resultado
            )

        st.session_state[
            "resultados"
        ] = resultados

        st.session_state[
            "estaciones_ina"
        ] = estaciones_ina

        guardar_historial(
            resultados
        )

else:

    resultados = (
        st.session_state[
            "resultados"
        ]
    )

    estaciones_ina = (
        st.session_state.get(
            "estaciones_ina",
            [],
        )
    )


# ============================================================
# TELEGRAM AUTOMÁTICO
# ============================================================

if (
    auto_telegram
    and actualizar
    and telegram_configurado()
):

    enviados = (
        enviar_alertas_automaticas(
            resultados
        )
    )

    if enviados:

        st.toast(
            "Alertas Telegram enviadas: "
            + ", ".join(
                enviados
            )
        )


# ============================================================
# RESUMEN TELEGRAM MANUAL
# ============================================================

if enviar_resumen:

    respuesta = (
        enviar_resumen_telegram(
            resultados
        )
    )

    if respuesta.get("ok"):

        st.success(
            "Resumen enviado correctamente a Telegram."
        )

    else:

        st.error(
            "No se pudo enviar el resumen: "
            + str(
                respuesta.get(
                    "error"
                )
            )
        )


# ============================================================
# MÉTRICAS GENERALES
# ============================================================

total = len(
    resultados
)

muy_alto = sum(
    r["nivel"]
    == "MUY ALTO"
    for r in resultados
)

alto = sum(
    r["nivel"]
    == "ALTO"
    for r in resultados
)

moderado = sum(
    r["nivel"]
    == "MODERADO"
    for r in resultados
)

bajo = sum(
    r["nivel"]
    == "BAJO"
    for r in resultados
)

riesgo_promedio = (
    sum(
        r["riesgo"]
        for r in resultados
    )
    / total
    if total
    else 0
)


# ============================================================
# MÉTRICAS
# ============================================================

c1, c2, c3, c4, c5 = (
    st.columns(5)
)

c1.metric(
    "Nodos",
    total,
)

c2.metric(
    "🔴 Muy alto",
    muy_alto,
)

c3.metric(
    "🟠 Alto",
    alto,
)

c4.metric(
    "🟡 Moderado",
    moderado,
)

c5.metric(
    "Riesgo promedio",
    f"{riesgo_promedio:.0f}/100",
)


# ============================================================
# ALERTA GENERAL
# ============================================================

if muy_alto > 0:

    st.error(
        f"🔴 Se detectaron "
        f"{muy_alto} nodo(s) con riesgo MUY ALTO."
    )

elif alto > 0:

    st.warning(
        f"🟠 Se detectaron "
        f"{alto} nodo(s) con riesgo ALTO."
    )

else:

    st.success(
        "🟢 No se detectan nodos "
        "en niveles ALTO o MUY ALTO."
    )


# ============================================================
# TABLA PRINCIPAL
# ============================================================

st.subheader(
    "🚦 Situación territorial"
)

tabla = pd.DataFrame(
    [
        {
            "Nodo": r["nombre"],
            "Provincia": r[
                "provincia"
            ],
            "Riesgo": round(
                r["riesgo"],
                1,
            ),
            "Nivel": r[
                "nivel"
            ],
            "Lluvia 24 h (mm)": round(
                r["lluvia_24"],
                1,
            ),
            "Lluvia 72 h (mm)": round(
                r["lluvia_72"],
                1,
            ),
            "Humedad suelo (%)": round(
                r["humedad"],
                1,
            ),
            "Runoff 72 h (mm)": round(
                r["runoff_72"],
                1,
            ),
            "Calidad meteo (%)": round(
                r[
                    "calidad_meteo"
                ],
                0,
            ),
            "Calidad hidro (%)": round(
                r[
                    "calidad_hidro"
                ],
                0,
            ),
        }
        for r in resultados
    ]
)

st.dataframe(
    tabla,
    use_container_width=True,
    hide_index=True,
)


# ============================================================
# MAPA
# ============================================================

st.subheader(
    "🗺️ Mapa de riesgo"
)

figura_mapa = crear_mapa(
    resultados,
    estaciones_ina,
)

st.plotly_chart(
    figura_mapa,
    use_container_width=True,
)


# ============================================================
# DETALLE DE NODOS
# ============================================================

st.subheader(
    "🔎 Detalle de nodos"
)

for r in resultados:

    with st.expander(
        (
            f"{emoji_riesgo(r['nivel'])} "
            f"{r['nombre']} — "
            f"{r['riesgo']:.0f}/100"
        )
    ):

        a, b, c = st.columns(3)

        with a:

            st.metric(
                "Riesgo",
                f"{r['riesgo']:.0f}/100",
            )

            st.metric(
                "Lluvia 24 h",
                f"{r['lluvia_24']:.1f} mm",
            )

            st.metric(
                "Lluvia 72 h",
                f"{r['lluvia_72']:.1f} mm",
            )

        with b:

            st.metric(
                "Humedad del suelo",
                f"{r['humedad']:.1f} %",
            )

            st.metric(
                "Runoff 72 h",
                f"{r['runoff_72']:.1f} mm",
            )

            st.metric(
                "Calidad meteorológica",
                f"{r['calidad_meteo']:.0f} %",
            )

        with c:

            st.metric(
                "Calidad hidrológica",
                f"{r['calidad_hidro']:.0f} %",
            )

            if r["hidro_ok"]:

                st.write(
                    f"**Estación INA:** "
                    f"{r['hidro_estacion']}"
                )

                st.write(
                    f"**Variable:** "
                    f"{r['hidro_variable']}"
                )

                valor = r[
                    "hidro_valor"
                ]

                if valor is not None:

                    st.write(
                        f"**Valor actual:** "
                        f"{valor:.2f}"
                    )

                st.write(
                    f"**Tendencia:** "
                    f"{r['hidro_direccion']}"
                )

                if (
                    r[
                        "hidro_alerta"
                    ]
                    is not None
                ):

                    st.write(
                        f"**Alerta INA:** "
                        f"{r['hidro_alerta']:.2f}"
                    )

                if (
                    r[
                        "hidro_evacuacion"
                    ]
                    is not None
                ):

                    st.write(
                        f"**Evacuación INA:** "
                        f"{r['hidro_evacuacion']:.2f}"
                    )

                if (
                    r[
                        "hidro_distancia"
                    ]
                    is not None
                ):

                    st.write(
                        f"**Distancia:** "
                        f"{r['hidro_distancia']:.1f} km"
                    )

            else:

                st.info(
                    "No se obtuvo una serie "
                    "hidrológica compatible "
                    "para este nodo."
                )


# ============================================================
# HISTORIAL
# ============================================================

st.subheader(
    "📜 Historial de evaluaciones"
)

historial = (
    st.session_state.get(
        "historial",
        pd.DataFrame(),
    )
)

if not historial.empty:

    st.dataframe(
        historial.tail(100),
        use_container_width=True,
        hide_index=True,
    )

    csv = historial.to_csv(
        index=False
    ).encode(
        "utf-8"
    )

    st.download_button(
        label="📥 Descargar historial CSV",
        data=csv,
        file_name=(
            "historial_alerta_litoral_agro.csv"
        ),
        mime="text/csv",
    )

else:

    st.info(
        "Todavía no hay historial."
    )


# ============================================================
# ESTACIONES INA
# ============================================================

with st.expander(
    "🌊 Estaciones hidrológicas INA detectadas"
):

    if estaciones_ina:

        tabla_ina = pd.DataFrame(
            [
                {
                    "Sitecode": e[
                        "sitecode"
                    ],
                    "Estación": e[
                        "nombre"
                    ],
                    "Río": e.get(
                        "rio"
                    ),
                    "Lat": e[
                        "lat"
                    ],
                    "Lon": e[
                        "lon"
                    ],
                    "Alerta": e.get(
                        "nivel_alerta"
                    ),
                    "Evacuación": e.get(
                        "nivel_evacuacion"
                    ),
                }
                for e
                in estaciones_ina
            ]
        )

        st.dataframe(
            tabla_ina,
            use_container_width=True,
            hide_index=True,
        )

    else:

        st.warning(
            "No se pudieron obtener "
            "estaciones INA."
        )


# ============================================================
# METODOLOGÍA
# ============================================================

with st.expander(
    "📚 Metodología"
):

    st.markdown(
        """
### Objetivo

Alerta Litoral Agro es un prototipo de sistema
de alerta temprana orientado a identificar
condiciones meteorológicas e hidrológicas
favorables al anegamiento agropecuario en
Santa Fe, Corrientes y Entre Ríos.

### Variables meteorológicas

Se utilizan:

- precipitación acumulada 24 h;
- precipitación acumulada 72 h;
- humedad del suelo;
- runoff superficial modelado;
- ráfagas máximas.

### Hidrología

La componente hidrológica utiliza información
de estaciones del Instituto Nacional del Agua
(INA / DSIyAH).

Se consideran:

- nivel o variable hidrométrica disponible;
- umbral de alerta;
- umbral de evacuación;
- tendencia de las últimas 24 horas;
- actualidad del dato.

### Tendencia hidrológica

La tendencia se calcula utilizando los tiempos
reales de observación.

Se diferencian:

- ASCENDENTE;
- ESTABLE;
- DESCENDENTE.

Una creciente genera mayor componente de riesgo,
mientras que una bajante reduce dicha componente.

### Índice

El índice integrado se expresa entre 0 y 100.

La ponderación utilizada actualmente combina:

- lluvia 24 h: 20%;
- lluvia 72 h: 20%;
- humedad del suelo: 15%;
- runoff: 5%;
- hidrología: 20%;
- vulnerabilidad territorial: 10%.

La disponibilidad de componentes se controla
por separado mediante indicadores de cobertura.

### Importante

Los umbrales meteorológicos y la vulnerabilidad
territorial utilizados en esta versión son
experimentales y deben calibrarse con datos
históricos antes de utilizar el sistema como
servicio oficial de alerta.

El índice debe interpretarse como herramienta
de apoyo a la decisión y no como sustituto de
los organismos oficiales.
"""
    )


# ============================================================
# ESTADO DEL SISTEMA
# ============================================================

st.divider()

st.caption(
    (
        f"Alerta Litoral Agro V{APP_VERSION} | "
        f"Última actualización: "
        f"{ahora().strftime('%d/%m/%Y %H:%M:%S')} | "
        f"Telegram: "
        f"{'CONFIGURADO' if telegram_configurado() else 'NO CONFIGURADO'}"
    )
)
