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
#   Open-Meteo / ECMWF IFS HRES 9 km
#   NOAA GOES-19 ABI (vigilancia satelital)
#
# NO UTILIZA DATOS DE VIALIDAD.
#
# Versión: V3.6.2
# ============================================================

import hashlib
import html
import json
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import plotly.express as px
import requests
import streamlit as st
import streamlit.components.v1 as components


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
VERSION = "V3.6.2"
MODELO = "ECMWF IFS HRES 9 km"
API_URL = "https://api.open-meteo.com/v1/ecmwf"
TZ = ZoneInfo("America/Argentina/Buenos_Aires")

HISTORY_FILE = Path("historial_alerta_litoral.csv")
STATE_FILE = Path("telegram_alert_state.json")

MAP_CENTER_LAT = -30.5
MAP_CENTER_LON = -59.8
MAP_ZOOM = 5.6


# ============================================================
# NODOS DE MONITOREO
# ============================================================

NODOS = [
    # --------------------------------------------------------
    # SANTA FE
    # --------------------------------------------------------
    {"localidad": "Reconquista", "provincia": "Santa Fe", "lat": -29.144, "lon": -59.643},
    {"localidad": "Avellaneda", "provincia": "Santa Fe", "lat": -29.117, "lon": -59.658},
    {"localidad": "Vera", "provincia": "Santa Fe", "lat": -29.460, "lon": -60.213},
    {"localidad": "Tostado", "provincia": "Santa Fe", "lat": -29.233, "lon": -61.769},
    {"localidad": "Rafaela", "provincia": "Santa Fe", "lat": -31.250, "lon": -61.486},
    {"localidad": "Santa Fe", "provincia": "Santa Fe", "lat": -31.633, "lon": -60.700},
    {"localidad": "Esperanza", "provincia": "Santa Fe", "lat": -31.448, "lon": -60.932},
    {"localidad": "Rosario", "provincia": "Santa Fe", "lat": -32.946, "lon": -60.639},
    {"localidad": "Venado Tuerto", "provincia": "Santa Fe", "lat": -33.745, "lon": -61.968},

    # --------------------------------------------------------
    # CORRIENTES
    # --------------------------------------------------------
    {"localidad": "Corrientes", "provincia": "Corrientes", "lat": -27.469, "lon": -58.830},
    {"localidad": "Goya", "provincia": "Corrientes", "lat": -29.140, "lon": -59.263},
    {"localidad": "Mercedes", "provincia": "Corrientes", "lat": -29.182, "lon": -58.075},
    {"localidad": "Curuzú Cuatiá", "provincia": "Corrientes", "lat": -29.791, "lon": -58.054},
    {"localidad": "Paso de los Libres", "provincia": "Corrientes", "lat": -29.713, "lon": -57.088},
    {"localidad": "Santo Tomé", "provincia": "Corrientes", "lat": -28.549, "lon": -56.040},

    # --------------------------------------------------------
    # ENTRE RÍOS
    # --------------------------------------------------------
    {"localidad": "Paraná", "provincia": "Entre Ríos", "lat": -31.741, "lon": -60.511},
    {"localidad": "La Paz", "provincia": "Entre Ríos", "lat": -30.744, "lon": -59.645},
    {"localidad": "Concordia", "provincia": "Entre Ríos", "lat": -31.393, "lon": -58.020},
]


# ============================================================
# UTILIDADES
# ============================================================

def ahora():
    return datetime.now(TZ)


def hora_actual_local_redondeada():
    return pd.Timestamp(
        ahora().replace(
            minute=0,
            second=0,
            microsecond=0,
            tzinfo=None,
        )
    )


def clamp(valor, minimo=0.0, maximo=100.0):
    try:
        valor = float(valor)
    except Exception:
        return minimo

    if not np.isfinite(valor):
        return minimo

    return max(minimo, min(maximo, valor))


def normalizar_0_100(valor, minimo, maximo):
    try:
        valor = float(valor)
    except Exception:
        return 0.0

    if not np.isfinite(valor) or maximo <= minimo:
        return 0.0

    resultado = ((valor - minimo) / (maximo - minimo)) * 100.0
    return clamp(resultado)


def array_numerico(valores):
    try:
        return np.asarray(valores, dtype=float)
    except Exception:
        return np.array([], dtype=float)


def ajustar_longitud(array, n):
    if len(array) == n:
        return array

    if len(array) > n:
        return array[:n]

    salida = np.full(n, np.nan, dtype=float)
    salida[:len(array)] = array
    return salida


def suma_segura(array, mascara=None):
    if array is None or len(array) == 0:
        return 0.0

    try:
        datos = array[mascara] if mascara is not None else array

        if len(datos) == 0:
            return 0.0

        return float(np.nansum(datos))

    except Exception:
        return 0.0


def media_segura(array, mascara=None, default=np.nan):
    if array is None or len(array) == 0:
        return default

    try:
        datos = array[mascara] if mascara is not None else array
        datos = datos[np.isfinite(datos)]

        return float(np.mean(datos)) if len(datos) else default

    except Exception:
        return default


def maximo_seguro(array, mascara=None, default=np.nan):
    if array is None or len(array) == 0:
        return default

    try:
        datos = array[mascara] if mascara is not None else array
        datos = datos[np.isfinite(datos)]

        return float(np.max(datos)) if len(datos) else default

    except Exception:
        return default


def ultimo_valor(array, mascara, default=np.nan):
    if array is None or len(array) == 0:
        return default

    try:
        indices = np.where(mascara & np.isfinite(array))[0]

        return float(array[indices[-1]]) if len(indices) else default

    except Exception:
        return default


def ventana_mascara(horas, inicio, fin, incluir_inicio=False):
    if incluir_inicio:
        return (horas >= inicio) & (horas <= fin)

    return (horas > inicio) & (horas <= fin)


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
        "SIN DATOS": "⚪",
    }.get(nivel, "⚪")


def accion_desde_nivel(nivel):
    acciones = {
        "MUY ALTO": (
            "Priorizar medidas preventivas. Evitar ingreso de maquinaria a "
            "sectores comprometidos y evaluar medidas preventivas sobre "
            "animales, cultivos e insumos."
        ),
        "ALTO": (
            "Reforzar el monitoreo. Evitar tareas que puedan compactar el suelo "
            "y revisar sectores bajos o con antecedentes de anegamiento."
        ),
        "MEDIO": (
            "Mantener vigilancia sobre lluvia, humedad del suelo y evolución "
            "del riesgo antes de realizar tareas sensibles."
        ),
        "BAJO": (
            "Condiciones actualmente favorables según el índice. Mantener el "
            "monitoreo meteorológico."
        ),
    }

    return acciones.get(nivel, "Sin datos suficientes.")


def direccion_compass(grados):
    try:
        grados = float(grados)
    except Exception:
        return "Sin dato"

    if not np.isfinite(grados):
        return "Sin dato"

    direcciones = ["N", "NE", "E", "SE", "S", "SO", "O", "NO"]
    indice = int((grados + 22.5) / 45.0) % 8

    return direcciones[indice]


def descripcion_wmo(codigo):
    try:
        codigo = int(codigo)
    except Exception:
        return "Sin dato"

    tabla = {
        0: "Cielo despejado",
        1: "Principalmente despejado",
        2: "Parcialmente nublado",
        3: "Cubierto",
        45: "Niebla",
        48: "Niebla con escarcha",
        51: "Llovizna ligera",
        53: "Llovizna moderada",
        55: "Llovizna intensa",
        61: "Lluvia ligera",
        63: "Lluvia moderada",
        65: "Lluvia intensa",
        66: "Lluvia helada ligera",
        67: "Lluvia helada intensa",
        71: "Nieve ligera",
        73: "Nieve moderada",
        75: "Nieve intensa",
        77: "Granos de nieve",
        80: "Chaparrones ligeros",
        81: "Chaparrones moderados",
        82: "Chaparrones intensos",
        85: "Nevadas ligeras",
        86: "Nevadas intensas",
        95: "Tormenta",
        96: "Tormenta con granizo ligero",
        99: "Tormenta con granizo intenso",
    }

    return tabla.get(codigo, f"Código WMO {codigo}")


# ============================================================
# OPEN-METEO / ECMWF
# ============================================================

@st.cache_data(ttl=900, show_spinner=False)
def obtener_datos_ecmwf():
    """Consulta los 18 nodos en una única petición multi-coordenada."""

    latitudes = ",".join(
        f'{nodo["lat"]:.3f}' for nodo in NODOS
    )

    longitudes = ",".join(
        f'{nodo["lon"]:.3f}' for nodo in NODOS
    )

    variables_horarias = [
        "temperature_2m",
        "apparent_temperature",
        "dew_point_2m",
        "relative_humidity_2m",
        "pressure_msl",
        "surface_pressure",
        "cloud_cover",
        "cloud_cover_low",
        "cloud_cover_mid",
        "cloud_cover_high",
        "wind_speed_10m",
        "wind_gusts_10m",
        "wind_direction_10m",
        "shortwave_radiation",
        "cape",
        "vapour_pressure_deficit",
        "evapotranspiration",
        "et0_fao_evapotranspiration",
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
        "weather_code",
    ]

    variables_diarias = [
        "weather_code",
        "temperature_2m_min",
        "temperature_2m_max",
        "apparent_temperature_min",
        "apparent_temperature_max",
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
        "et0_fao_evapotranspiration",
    ]

    params = {
        "latitude": latitudes,
        "longitude": longitudes,
        "hourly": ",".join(variables_horarias),
        "daily": ",".join(variables_diarias),
        "past_days": 8,
        "forecast_days": 15,
        "timezone": "America/Argentina/Buenos_Aires",
        "temperature_unit": "celsius",
        "wind_speed_unit": "kmh",
        "precipitation_unit": "mm",
        "cell_selection": "land",
    }

    headers = {
        "User-Agent": "Alerta-Litoral-Agro/3.6.2",
    }

    try:
        response = requests.get(
            API_URL,
            params=params,
            headers=headers,
            timeout=60,
        )

        response.raise_for_status()
        payload = response.json()

        if isinstance(payload, list):
            return payload

        if isinstance(payload, dict) and payload.get("error"):
            return [payload for _ in NODOS]

        return [payload]

    except Exception as error:
        return [{"error": str(error)} for _ in NODOS]


# ============================================================
# PROCESAMIENTO DEL NODO
# ============================================================

def resultado_sin_datos(nodo, error="Sin datos suficientes"):

    campos_nan = [
        "indice",
        "temperatura",
        "sensacion",
        "punto_rocio",
        "humedad_relativa",
        "presion_msl",
        "presion_superficie",
        "nubosidad",
        "nubosidad_baja",
        "nubosidad_media",
        "nubosidad_alta",
        "viento",
        "rafaga",
        "direccion_viento",
        "cape",
        "vpd",
        "evapotranspiracion",
        "et0",
        "precipitacion_actual",
        "lluvia_actual",
        "chaparrones_actual",
        "humedad_0_7",
        "humedad_7_28",
        "humedad_28_100",
        "humedad_100_255",
        "humedad_suelo_promedio",
        "temp_suelo_0_7",
        "temp_suelo_7_28",
        "temp_suelo_28_100",
        "temp_suelo_100_255",
        "lluvia_24",
        "lluvia_72",
        "lluvia_7d",
        "lluvia_futura_24",
        "lluvia_futura_72",
        "lluvia_futura_7d",
        "rain_24",
        "rain_72",
        "rain_7d",
        "showers_24",
        "showers_72",
        "showers_7d",
        "runoff_24",
        "runoff_72",
        "runoff_7d",
        "runoff_futuro_24",
        "runoff_futuro_72",
        "runoff_futuro_7d",
        "prob_lluvia",
        "prob_lluvia_24",
        "prob_lluvia_72",
        "cape_max_24",
        "et0_24",
        "temp_min_24",
        "temp_max_24",
        "viento_max_24",
        "rafaga_max_24",
        "codigo_tiempo",
    ]

    salida = {
        **nodo,
        "estado_datos": "ERROR",
        "error_datos": str(error),
        "nivel": "SIN DATOS",
        "tendencia": "SIN DATOS",
        "direccion_hidrologica": "SIN DATOS",
        "velocidad_hidrologica": "SIN DATOS",
        "accion": "Sin datos suficientes.",
        "actualizado": ahora().strftime("%d/%m/%Y %H:%M"),
        "horas_disponibles": 0,
        "condicion_critica": False,
    }

    salida.update({
        campo: np.nan
        for campo in campos_nan
    })

    return salida


def procesar_nodo(nodo, datos):

    if not isinstance(datos, dict) or datos.get("error"):

        error = (
            datos.get("reason")
            or datos.get("error")
            or "Sin datos suficientes"
            if isinstance(datos, dict)
            else "Sin datos suficientes"
        )

        return resultado_sin_datos(nodo, error)

    hourly = datos.get("hourly", {}) or {}
    tiempos = hourly.get("time", [])

    horas_raw = pd.to_datetime(
        tiempos,
        errors="coerce",
    )

    if len(horas_raw) == 0:
        return resultado_sin_datos(
            nodo,
            "La respuesta no contiene horarios",
        )

    horas = pd.DatetimeIndex(horas_raw)
    valid_time = ~horas.isna()
    horas = horas[valid_time]

    n = len(horas)

    def serie(nombre):
        return ajustar_longitud(
            array_numerico(
                hourly.get(nombre, [])
            ),
            len(horas_raw),
        )[valid_time]

    temperatura = serie("temperature_2m")
    sensacion = serie("apparent_temperature")
    punto_rocio = serie("dew_point_2m")
    humedad_relativa = serie("relative_humidity_2m")
    presion_msl = serie("pressure_msl")
    presion_superficie = serie("surface_pressure")
    nubosidad = serie("cloud_cover")
    nubosidad_baja = serie("cloud_cover_low")
    nubosidad_media = serie("cloud_cover_mid")
    nubosidad_alta = serie("cloud_cover_high")
    viento = serie("wind_speed_10m")
    rafaga = serie("wind_gusts_10m")
    direccion_viento = serie("wind_direction_10m")
    cape = serie("cape")
    vpd = serie("vapour_pressure_deficit")
    evapotranspiracion = serie("evapotranspiration")
    et0 = serie("et0_fao_evapotranspiration")
    precipitacion = serie("precipitation")
    rain = serie("rain")
    showers = serie("showers")
    probabilidad = serie("precipitation_probability")
    runoff = serie("runoff")
    codigo_tiempo = serie("weather_code")

    humedad_0_7 = serie("soil_moisture_0_to_7cm")
    humedad_7_28 = serie("soil_moisture_7_to_28cm")
    humedad_28_100 = serie("soil_moisture_28_to_100cm")
    humedad_100_255 = serie("soil_moisture_100_to_255cm")

    temp_suelo_0_7 = serie("soil_temperature_0_to_7cm")
    temp_suelo_7_28 = serie("soil_temperature_7_to_28cm")
    temp_suelo_28_100 = serie("soil_temperature_28_to_100cm")
    temp_suelo_100_255 = serie("soil_temperature_100_to_255cm")

    referencia = hora_actual_local_redondeada()

    pasado_24 = ventana_mascara(
        horas,
        referencia - pd.Timedelta(hours=24),
        referencia,
    )

    pasado_72 = ventana_mascara(
        horas,
        referencia - pd.Timedelta(hours=72),
        referencia,
    )

    pasado_7d = ventana_mascara(
        horas,
        referencia - pd.Timedelta(days=7),
        referencia,
    )

    futuro_24 = ventana_mascara(
        horas,
        referencia,
        referencia + pd.Timedelta(hours=24),
    )

    futuro_72 = ventana_mascara(
        horas,
        referencia,
        referencia + pd.Timedelta(hours=72),
    )

    futuro_7d = ventana_mascara(
        horas,
        referencia,
        referencia + pd.Timedelta(days=7),
    )

    hasta_actual = horas <= referencia

    idx_actuales = np.where(hasta_actual)[0]
    idx_actual = (
        int(idx_actuales[-1])
        if len(idx_actuales)
        else 0
    )

    # ========================================================
    # VARIABLES ACTUALES
    # ========================================================

    temperatura_actual = ultimo_valor(
        temperatura,
        hasta_actual,
    )

    sensacion_actual = ultimo_valor(
        sensacion,
        hasta_actual,
    )

    punto_rocio_actual = ultimo_valor(
        punto_rocio,
        hasta_actual,
    )

    humedad_relativa_actual = ultimo_valor(
        humedad_relativa,
        hasta_actual,
    )

    presion_msl_actual = ultimo_valor(
        presion_msl,
        hasta_actual,
    )

    presion_superficie_actual = ultimo_valor(
        presion_superficie,
        hasta_actual,
    )

    nubosidad_actual = ultimo_valor(
        nubosidad,
        hasta_actual,
    )

    nubosidad_baja_actual = ultimo_valor(
        nubosidad_baja,
        hasta_actual,
    )

    nubosidad_media_actual = ultimo_valor(
        nubosidad_media,
        hasta_actual,
    )

    nubosidad_alta_actual = ultimo_valor(
        nubosidad_alta,
        hasta_actual,
    )

    viento_actual = ultimo_valor(
        viento,
        hasta_actual,
    )

    rafaga_actual = ultimo_valor(
        rafaga,
        hasta_actual,
    )

    direccion_viento_actual = ultimo_valor(
        direccion_viento,
        hasta_actual,
    )

    cape_actual = ultimo_valor(
        cape,
        hasta_actual,
    )

    vpd_actual = ultimo_valor(
        vpd,
        hasta_actual,
    )

    evapotranspiracion_actual = ultimo_valor(
        evapotranspiracion,
        hasta_actual,
    )

    et0_actual = ultimo_valor(
        et0,
        hasta_actual,
    )

    precipitacion_actual = ultimo_valor(
        precipitacion,
        hasta_actual,
    )

    rain_actual = ultimo_valor(
        rain,
        hasta_actual,
    )

    showers_actual = ultimo_valor(
        showers,
        hasta_actual,
    )

    codigo_actual = ultimo_valor(
        codigo_tiempo,
        hasta_actual,
    )

    humedad_capas = [
        ultimo_valor(humedad_0_7, hasta_actual),
        ultimo_valor(humedad_7_28, hasta_actual),
        ultimo_valor(humedad_28_100, hasta_actual),
        ultimo_valor(humedad_100_255, hasta_actual),
    ]

    humedad_validas = [
        x for x in humedad_capas
        if np.isfinite(x)
    ]

    # IMPORTANTE:
    # Open-Meteo entrega humedad de suelo como contenido
    # volumétrico de agua en m³/m³.
    #
    # El sistema mantiene ese valor internamente para que
    # el índice continúe utilizando los umbrales 0.15–0.45.
    #
    # La conversión a porcentaje se realiza únicamente
    # en la interfaz:
    #
    # 0.341 m³/m³ = 34.1 %
    humedad_suelo_promedio = (
        float(np.mean(humedad_validas))
        if humedad_validas
        else np.nan
    )

    temp_suelo_capas = [
        ultimo_valor(
            temp_suelo_0_7,
            hasta_actual,
        ),
        ultimo_valor(
            temp_suelo_7_28,
            hasta_actual,
        ),
        ultimo_valor(
            temp_suelo_28_100,
            hasta_actual,
        ),
        ultimo_valor(
            temp_suelo_100_255,
            hasta_actual,
        ),
    ]

    # ========================================================
    # PRECIPITACIÓN ANTECEDENTE Y FUTURA
    # ========================================================

    lluvia_24 = suma_segura(
        precipitacion,
        pasado_24,
    )

    lluvia_72 = suma_segura(
        precipitacion,
        pasado_72,
    )

    lluvia_7d = suma_segura(
        precipitacion,
        pasado_7d,
    )

    lluvia_futura_24 = suma_segura(
        precipitacion,
        futuro_24,
    )

    lluvia_futura_72 = suma_segura(
        precipitacion,
        futuro_72,
    )

    lluvia_futura_7d = suma_segura(
        precipitacion,
        futuro_7d,
    )

    rain_24 = suma_segura(
        rain,
        pasado_24,
    )

    rain_72 = suma_segura(
        rain,
        pasado_72,
    )

    rain_7d = suma_segura(
        rain,
        pasado_7d,
    )

    showers_24 = suma_segura(
        showers,
        pasado_24,
    )

    showers_72 = suma_segura(
        showers,
        pasado_72,
    )

    showers_7d = suma_segura(
        showers,
        pasado_7d,
    )

    # ========================================================
    # RUNOFF
    # ========================================================

    runoff_24 = suma_segura(
        runoff,
        pasado_24,
    )

    runoff_72 = suma_segura(
        runoff,
        pasado_72,
    )

    runoff_7d = suma_segura(
        runoff,
        pasado_7d,
    )

    runoff_futuro_24 = suma_segura(
        runoff,
        futuro_24,
    )

    runoff_futuro_72 = suma_segura(
        runoff,
        futuro_72,
    )

    runoff_futuro_7d = suma_segura(
        runoff,
        futuro_7d,
    )

    # ========================================================
    # PROBABILIDAD DE PRECIPITACIÓN
    # ========================================================

    prob_lluvia_24 = media_segura(
        probabilidad,
        futuro_24,
    )

    prob_lluvia_72 = media_segura(
        probabilidad,
        futuro_72,
    )

    prob_lluvia = maximo_seguro(
        probabilidad,
        futuro_72,
    )

    # ========================================================
    # EXTREMOS / VARIABLES ADICIONALES
    # ========================================================

    temp_min_24 = (
        np.nanmin(temperatura[futuro_24])
        if np.any(futuro_24)
        and np.any(np.isfinite(temperatura[futuro_24]))
        else np.nan
    )

    temp_max_24 = (
        np.nanmax(temperatura[futuro_24])
        if np.any(futuro_24)
        and np.any(np.isfinite(temperatura[futuro_24]))
        else np.nan
    )

    viento_max_24 = (
        np.nanmax(viento[futuro_24])
        if np.any(futuro_24)
        and np.any(np.isfinite(viento[futuro_24]))
        else np.nan
    )

    rafaga_max_24 = (
        np.nanmax(rafaga[futuro_24])
        if np.any(futuro_24)
        and np.any(np.isfinite(rafaga[futuro_24]))
        else np.nan
    )

    cape_max_24 = (
        np.nanmax(cape[futuro_24])
        if np.any(futuro_24)
        and np.any(np.isfinite(cape[futuro_24]))
        else np.nan
    )

    et0_24 = suma_segura(
        et0,
        futuro_24,
    )

    # ========================================================
    # COMPONENTES DEL ÍNDICE
    # ========================================================

    humedad_score = normalizar_0_100(
        humedad_suelo_promedio,
        0.15,
        0.45,
    )

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

    runoff_score = normalizar_0_100(
        runoff_72,
        1,
        30,
    )

    acumulacion_score = normalizar_0_100(
        lluvia_7d,
        30,
        180,
    )

    lluvia_critica_score = 0.0

    if lluvia_24 >= 50:
        lluvia_critica_score += 40

    elif lluvia_24 >= 30:
        lluvia_critica_score += 25

    elif lluvia_24 >= 20:
        lluvia_critica_score += 15

    if lluvia_72 >= 100:
        lluvia_critica_score += 40

    elif lluvia_72 >= 70:
        lluvia_critica_score += 30

    elif lluvia_72 >= 50:
        lluvia_critica_score += 20

    lluvia_critica_score = clamp(
        lluvia_critica_score
    )

    # ========================================================
    # TENDENCIA HIDROLÓGICA
    # ========================================================

    reciente_12 = ventana_mascara(
        horas,
        referencia - pd.Timedelta(hours=12),
        referencia,
    )

    anterior_12_36 = ventana_mascara(
        horas,
        referencia - pd.Timedelta(hours=36),
        referencia - pd.Timedelta(hours=12),
    )

    runoff_reciente = media_segura(
        runoff,
        reciente_12,
    )

    runoff_anterior = media_segura(
        runoff,
        anterior_12_36,
    )

    if (
        np.isfinite(runoff_reciente)
        and np.isfinite(runoff_anterior)
    ):

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

    tendencia_factor = 0.0

    if direccion_hidrologica == "SUBIDA":

        tendencia_factor = (
            10.0
            if velocidad_hidrologica == "RÁPIDA"
            else 5.0
        )

    elif direccion_hidrologica == "BAJADA":

        tendencia_factor = (
            -8.0
            if velocidad_hidrologica == "RÁPIDA"
            else -4.0
        )

    # ========================================================
    # ÍNDICE FINAL
    # ========================================================

    indice = (
        humedad_score * 0.25
        + lluvia_reciente_score * 0.20
        + lluvia_futura_score * 0.20
        + runoff_score * 0.10
        + acumulacion_score * 0.10
        + lluvia_critica_score * 0.15
    )

    indice += tendencia_factor
    indice = clamp(indice)

    condicion_critica = (
        lluvia_72 >= 120
        or lluvia_7d >= 220
        or runoff_72 >= 50
    )

    if condicion_critica:
        indice = max(
            indice,
            75.0,
        )

    nivel = nivel_desde_indice(
        indice
    )

    return {
        **nodo,
        "estado_datos": "OK",
        "error_datos": "",
        "indice": round(indice, 1),
        "nivel": nivel,

        "temperatura": (
            round(temperatura_actual, 1)
            if np.isfinite(temperatura_actual)
            else np.nan
        ),

        "sensacion": (
            round(sensacion_actual, 1)
            if np.isfinite(sensacion_actual)
            else np.nan
        ),

        "punto_rocio": (
            round(punto_rocio_actual, 1)
            if np.isfinite(punto_rocio_actual)
            else np.nan
        ),

        "humedad_relativa": (
            round(humedad_relativa_actual, 1)
            if np.isfinite(humedad_relativa_actual)
            else np.nan
        ),

        "presion_msl": (
            round(presion_msl_actual, 1)
            if np.isfinite(presion_msl_actual)
            else np.nan
        ),

        "presion_superficie": (
            round(presion_superficie_actual, 1)
            if np.isfinite(presion_superficie_actual)
            else np.nan
        ),

        "nubosidad": (
            round(nubosidad_actual, 1)
            if np.isfinite(nubosidad_actual)
            else np.nan
        ),

        "nubosidad_baja": (
            round(nubosidad_baja_actual, 1)
            if np.isfinite(nubosidad_baja_actual)
            else np.nan
        ),

        "nubosidad_media": (
            round(nubosidad_media_actual, 1)
            if np.isfinite(nubosidad_media_actual)
            else np.nan
        ),

        "nubosidad_alta": (
            round(nubosidad_alta_actual, 1)
            if np.isfinite(nubosidad_alta_actual)
            else np.nan
        ),

        "viento": (
            round(viento_actual, 1)
            if np.isfinite(viento_actual)
            else np.nan
        ),

        "rafaga": (
            round(rafaga_actual, 1)
            if np.isfinite(rafaga_actual)
            else np.nan
        ),

        "direccion_viento": (
            round(direccion_viento_actual, 0)
            if np.isfinite(direccion_viento_actual)
            else np.nan
        ),

        "direccion_viento_texto":
            direccion_compass(
                direccion_viento_actual
            ),

        "cape": (
            round(cape_actual, 0)
            if np.isfinite(cape_actual)
            else np.nan
        ),

        "vpd": (
            round(vpd_actual, 2)
            if np.isfinite(vpd_actual)
            else np.nan
        ),

        "evapotranspiracion": (
            round(evapotranspiracion_actual, 2)
            if np.isfinite(evapotranspiracion_actual)
            else np.nan
        ),

        "et0": (
            round(et0_actual, 2)
            if np.isfinite(et0_actual)
            else np.nan
        ),

        "precipitacion_actual": (
            round(precipitacion_actual, 2)
            if np.isfinite(precipitacion_actual)
            else np.nan
        ),

        "lluvia_actual": (
            round(rain_actual, 2)
            if np.isfinite(rain_actual)
            else np.nan
        ),

        "chaparrones_actual": (
            round(showers_actual, 2)
            if np.isfinite(showers_actual)
            else np.nan
        ),

        # ====================================================
        # HUMEDAD DE SUELO
        #
        # SE GUARDA INTERNAMENTE EN m³/m³.
        # LA INTERFAZ LA MUESTRA COMO %.
        # ====================================================

        "humedad_0_7": (
            round(humedad_capas[0], 3)
            if np.isfinite(humedad_capas[0])
            else np.nan
        ),

        "humedad_7_28": (
            round(humedad_capas[1], 3)
            if np.isfinite(humedad_capas[1])
            else np.nan
        ),

        "humedad_28_100": (
            round(humedad_capas[2], 3)
            if np.isfinite(humedad_capas[2])
            else np.nan
        ),

        "humedad_100_255": (
            round(humedad_capas[3], 3)
            if np.isfinite(humedad_capas[3])
            else np.nan
        ),

        "humedad_suelo_promedio": (
            round(humedad_suelo_promedio, 3)
            if np.isfinite(humedad_suelo_promedio)
            else np.nan
        ),

        "temp_suelo_0_7": (
            round(temp_suelo_capas[0], 1)
            if np.isfinite(temp_suelo_capas[0])
            else np.nan
        ),

        "temp_suelo_7_28": (
            round(temp_suelo_capas[1], 1)
            if np.isfinite(temp_suelo_capas[1])
            else np.nan
        ),

        "temp_suelo_28_100": (
            round(temp_suelo_capas[2], 1)
            if np.isfinite(temp_suelo_capas[2])
            else np.nan
        ),

        "temp_suelo_100_255": (
            round(temp_suelo_capas[3], 1)
            if np.isfinite(temp_suelo_capas[3])
            else np.nan
        ),

        "lluvia_24": round(lluvia_24, 1),
        "lluvia_72": round(lluvia_72, 1),
        "lluvia_7d": round(lluvia_7d, 1),

        "lluvia_futura_24":
            round(lluvia_futura_24, 1),

        "lluvia_futura_72":
            round(lluvia_futura_72, 1),

        "lluvia_futura_7d":
            round(lluvia_futura_7d, 1),

        "rain_24":
            round(rain_24, 1),

        "rain_72":
            round(rain_72, 1),

        "rain_7d":
            round(rain_7d, 1),

        "showers_24":
            round(showers_24, 1),

        "showers_72":
            round(showers_72, 1),

        "showers_7d":
            round(showers_7d, 1),

        "runoff_24":
            round(runoff_24, 2),

        "runoff_72":
            round(runoff_72, 2),

        "runoff_7d":
            round(runoff_7d, 2),

        "runoff_futuro_24":
            round(runoff_futuro_24, 2),

        "runoff_futuro_72":
            round(runoff_futuro_72, 2),

        "runoff_futuro_7d":
            round(runoff_futuro_7d, 2),

        "prob_lluvia": (
            round(prob_lluvia, 1)
            if np.isfinite(prob_lluvia)
            else np.nan
        ),

        "prob_lluvia_24": (
            round(prob_lluvia_24, 1)
            if np.isfinite(prob_lluvia_24)
            else np.nan
        ),

        "prob_lluvia_72": (
            round(prob_lluvia_72, 1)
            if np.isfinite(prob_lluvia_72)
            else np.nan
        ),

        "temp_min_24": (
            round(temp_min_24, 1)
            if np.isfinite(temp_min_24)
            else np.nan
        ),

        "temp_max_24": (
            round(temp_max_24, 1)
            if np.isfinite(temp_max_24)
            else np.nan
        ),

        "viento_max_24": (
            round(viento_max_24, 1)
            if np.isfinite(viento_max_24)
            else np.nan
        ),

        "rafaga_max_24": (
            round(rafaga_max_24, 1)
            if np.isfinite(rafaga_max_24)
            else np.nan
        ),

        "cape_max_24": (
            round(cape_max_24, 0)
            if np.isfinite(cape_max_24)
            else np.nan
        ),

        "et0_24":
            round(et0_24, 2),

        "codigo_tiempo": (
            int(codigo_actual)
            if np.isfinite(codigo_actual)
            else np.nan
        ),

        "descripcion_tiempo":
            descripcion_wmo(
                codigo_actual
            ),

        "tendencia":
            tendencia,

        "direccion_hidrologica":
            direccion_hidrologica,

        "velocidad_hidrologica":
            velocidad_hidrologica,

        "accion":
            accion_desde_nivel(
                nivel
            ),

        "condicion_critica":
            bool(condicion_critica),

        "actualizado":
            ahora().strftime(
                "%d/%m/%Y %H:%M"
            ),

        "horas_disponibles":
            int(n),
    }


# ============================================================
# OBTENER TODOS LOS NODOS
# ============================================================

def obtener_todos_los_nodos():

    datos_lista = obtener_datos_ecmwf()
    resultados = []

    if len(datos_lista) != len(NODOS):

        datos_lista = list(
            datos_lista
        )

        while len(datos_lista) < len(NODOS):
            datos_lista.append(
                {
                    "error":
                    "Respuesta incompleta de Open-Meteo"
                }
            )

        datos_lista = datos_lista[
            :len(NODOS)
        ]

    for nodo, datos in zip(
        NODOS,
        datos_lista,
    ):
        resultados.append(
            procesar_nodo(
                nodo,
                datos,
            )
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
        "temperatura",
        "humedad_relativa",
        "presion_msl",
        "viento",
        "rafaga",
        "cape",
        "humedad_suelo_promedio",
        "humedad_0_7",
        "humedad_7_28",
        "humedad_28_100",
        "humedad_100_255",
        "lluvia_24",
        "lluvia_72",
        "lluvia_7d",
        "lluvia_futura_24",
        "lluvia_futura_72",
        "lluvia_futura_7d",
        "rain_24",
        "rain_72",
        "rain_7d",
        "showers_24",
        "showers_72",
        "showers_7d",
        "runoff_24",
        "runoff_72",
        "runoff_7d",
        "prob_lluvia",
        "prob_lluvia_24",
        "prob_lluvia_72",
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
                    nuevo,
                ],
                ignore_index=True,
            )

        else:
            combinado = nuevo

        if len(combinado) > 50000:
            combinado = combinado.tail(
                50000
            ).copy()

        combinado.to_csv(
            HISTORY_FILE,
            index=False,
        )

    except Exception:
        pass


# ============================================================
# TELEGRAM
# ============================================================

def obtener_secrets_telegram():

    try:

        token = str(
            st.secrets.get(
                "TELEGRAM_BOT_TOKEN",
                "",
            )
        ).strip()

        chat_id = str(
            st.secrets.get(
                "TELEGRAM_CHAT_ID",
                "",
            )
        ).strip()

        return token, chat_id

    except Exception:
        return "", ""


def cargar_estado_telegram():

    if "telegram_alert_state" in st.session_state:
        return dict(
            st.session_state[
                "telegram_alert_state"
            ]
        )

    if not STATE_FILE.exists():
        estado = {}

    else:

        try:

            with open(
                STATE_FILE,
                "r",
                encoding="utf-8",
            ) as archivo:

                estado = json.load(
                    archivo
                )

        except Exception:
            estado = {}

    st.session_state[
        "telegram_alert_state"
    ] = dict(estado)

    return dict(estado)


def guardar_estado_telegram(estado):

    estado = dict(estado)

    st.session_state[
        "telegram_alert_state"
    ] = estado

    try:

        with open(
            STATE_FILE,
            "w",
            encoding="utf-8",
        ) as archivo:

            json.dump(
                estado,
                archivo,
                ensure_ascii=False,
                indent=2,
            )

    except Exception:
        pass


def enviar_telegram(mensaje):

    token, chat_id = obtener_secrets_telegram()

    if not token or not chat_id:
        return False

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
            timeout=15,
        )

        response.raise_for_status()

        return True

    except Exception:
        return False


def generar_mensaje_alerta(row):

    emoji = emoji_nivel(
        row["nivel"]
    )

    prob = row.get(
        "prob_lluvia",
        np.nan,
    )

    prob_texto = (
        f"{prob:.0f}%"
        if pd.notna(prob)
        else "Sin dato"
    )

    # La humedad se convierte de m³/m³ a %
    # únicamente para presentación.
    humedad_suelo_pct = (
        row["humedad_suelo_promedio"] * 100
        if pd.notna(
            row["humedad_suelo_promedio"]
        )
        else np.nan
    )

    humedad_suelo_texto = (
        f"{humedad_suelo_pct:.1f}%"
        if np.isfinite(humedad_suelo_pct)
        else "Sin dato"
    )

    return f"""
{emoji} <b>ALERTA LITORAL AGRO</b>

<b>Nivel:</b> {row["nivel"]}
<b>Localidad:</b> {html.escape(str(row["localidad"]))}
<b>Provincia:</b> {html.escape(str(row["provincia"]))}

<b>Índice de riesgo:</b> {row["indice"]:.1f}/100

<b>Condiciones actuales:</b>
Temperatura: {row["temperatura"]:.1f} °C
Humedad relativa: {row["humedad_relativa"]:.0f}%
Viento: {row["viento"]:.1f} km/h ({row["direccion_viento_texto"]})
Ráfaga: {row["rafaga"]:.1f} km/h
CAPE: {row["cape"]:.0f} J/kg

<b>Humedad del suelo media:</b>
{humedad_suelo_texto}

<b>Precipitación antecedente:</b>
24 h: {row["lluvia_24"]:.1f} mm
72 h: {row["lluvia_72"]:.1f} mm
7 días: {row["lluvia_7d"]:.1f} mm

<b>Pronóstico:</b>
24 h: {row["lluvia_futura_24"]:.1f} mm
72 h: {row["lluvia_futura_72"]:.1f} mm
7 días: {row["lluvia_futura_7d"]:.1f} mm

<b>Runoff antecedente 72 h:</b>
{row["runoff_72"]:.2f} mm

<b>Probabilidad máxima de precipitación en 72 h:</b>
{prob_texto}

<b>Tendencia hidrológica:</b>
{row["tendencia"]}

<b>Acción recomendada:</b>
{row["accion"]}

<i>Actualizado: {row["actualizado"]}</i>

⚠️ Sistema experimental.
No reemplaza avisos oficiales.
""".strip()


def clave_alerta(row):

    contenido = (
        f'{row["localidad"]}|'
        f'{row["nivel"]}|'
        f'{round(float(row["indice"]) / 5)}'
    )

    return hashlib.md5(
        contenido.encode("utf-8")
    ).hexdigest()


def procesar_alertas_telegram(df):

    token, chat_id = obtener_secrets_telegram()

    resultado = {
        "configurado":
            bool(token and chat_id),

        "enviadas":
            0,

        "fallidas":
            0,

        "pendientes":
            0,
    }

    if (
        not token
        or not chat_id
        or df.empty
    ):
        return resultado

    estado = cargar_estado_telegram()

    for _, row in df.iterrows():

        clave_localidad = (
            f'{row["provincia"]}_'
            f'{row["localidad"]}'
        )

        if row["nivel"] not in [
            "ALTO",
            "MUY ALTO",
        ]:

            estado.pop(
                clave_localidad,
                None,
            )

            continue

        resultado["pendientes"] += 1

        clave = clave_alerta(
            row
        )

        if (
            estado.get(
                clave_localidad
            )
            == clave
        ):
            continue

        if enviar_telegram(
            generar_mensaje_alerta(row)
        ):

            estado[
                clave_localidad
            ] = clave

            resultado["enviadas"] += 1

        else:
            resultado["fallidas"] += 1

    guardar_estado_telegram(
        estado
    )

    return resultado


# ============================================================
# MAPA PLOTLY
# ============================================================

def crear_mapa(df):

    colores = {
        "BAJO": "green",
        "MEDIO": "yellow",
        "ALTO": "orange",
        "MUY ALTO": "red",
    }

    hover_data = {
        "provincia": True,
        "indice": True,
        "nivel": True,
        "temperatura": True,
        "humedad_relativa": True,
        "viento": True,
        "rafaga": True,
        "direccion_viento_texto": True,
        "humedad_suelo_promedio": True,
        "lluvia_24": True,
        "lluvia_72": True,
        "lluvia_7d": True,
        "lluvia_futura_24": True,
        "lluvia_futura_72": True,
        "runoff_72": True,
        "cape": True,
        "prob_lluvia": True,
        "tendencia": True,
        "lat": False,
        "lon": False,
    }

    fig = px.scatter_map(
        df,
        lat="lat",
        lon="lon",
        color="nivel",
        size="indice",
        size_max=24,
        hover_name="localidad",
        hover_data=hover_data,
        color_discrete_map=colores,
        center={
            "lat": MAP_CENTER_LAT,
            "lon": MAP_CENTER_LON,
        },
        zoom=MAP_ZOOM,
        height=680,
    )

    fig.update_layout(
        map_style="open-street-map",
        margin={
            "r": 0,
            "t": 0,
            "l": 0,
            "b": 0,
        },
        legend={
            "title": "Nivel de riesgo"
        },
        uirevision="litoral-agro",
    )

    return fig


# ============================================================
# WINDY
# ============================================================

WINDY_CENTER_LAT = -30.5
WINDY_CENTER_LON = -59.8
WINDY_ZOOM = 5.5


def construir_windy_url(
    overlay="wind"
):

    parametros = {
        "type": "map",
        "location": "coordinates",
        "metricRain": "default",
        "metricTemp": "default",
        "metricWind": "default",
        "zoom": WINDY_ZOOM,
        "overlay": overlay,
        "product": "ecmwf",
        "level": "surface",
        "lat": WINDY_CENTER_LAT,
        "lon": WINDY_CENTER_LON,
    }

    return (
        "https://embed.windy.com/embed.html?"
        + urlencode(parametros)
    )


def mostrar_windy(
    overlay="wind"
):

    windy_url = construir_windy_url(
        overlay
    )

    iframe = f'''
    <iframe
        width="100%"
        height="700"
        src="{html.escape(windy_url, quote=True)}"
        frameborder="0"
        style="border:0; border-radius:10px;"
        title="Windy — pronóstico meteorológico del Litoral"
        allowfullscreen>
    </iframe>
    '''

    components.html(
        iframe,
        height=720,
        scrolling=False,
    )


# ============================================================
# GOES-19
# ============================================================

GOES19_URL = (
    "https://www.goes.noaa.gov/sector_band.php"
    "?band=GEOCOLOR&length=24&sat=G19&sector=ssa"
)


def mostrar_goes19():

    iframe = f'''
    <iframe
        src="{html.escape(GOES19_URL, quote=True)}"
        width="100%"
        height="760"
        frameborder="0"
        style="border:0; border-radius:10px;"
        title="GOES-19 — Sudamérica Sur — NOAA"
        allowfullscreen>
    </iframe>
    '''

    components.html(
        iframe,
        height=780,
        scrolling=False,
    )


# ============================================================
# PRONÓSTICO EXTENDIDO
# ============================================================

def extraer_pronostico_diario(
    datos
):

    daily = (
        datos or {}
    ).get(
        "daily",
        {}
    ) or {}

    fechas = daily.get(
        "time",
        []
    )

    if not fechas:
        return pd.DataFrame()

    def arr(nombre):

        valores = daily.get(
            nombre,
            []
        )

        salida = list(valores)[
            :len(fechas)
        ]

        if len(salida) < len(fechas):

            salida += [
                np.nan
            ] * (
                len(fechas)
                - len(salida)
            )

        return salida

    tabla = pd.DataFrame({

        "Fecha":
            pd.to_datetime(
                fechas,
                errors="coerce",
            ),

        "Tiempo":
            arr("weather_code"),

        "Temp. mínima":
            arr("temperature_2m_min"),

        "Temp. máxima":
            arr("temperature_2m_max"),

        "Sensación mínima":
            arr("apparent_temperature_min"),

        "Sensación máxima":
            arr("apparent_temperature_max"),

        "Precipitación":
            arr("precipitation_sum"),

        "Lluvia":
            arr("rain_sum"),

        "Chaparrones":
            arr("showers_sum"),

        "Prob. precipitación":
            arr(
                "precipitation_probability_max"
            ),

        "Horas con precipitación":
            arr("precipitation_hours"),

        "Viento máximo":
            arr("wind_speed_10m_max"),

        "Ráfaga máxima":
            arr("wind_gusts_10m_max"),

        "Dirección viento":
            arr(
                "wind_direction_10m_dominant"
            ),

        "CAPE máximo":
            arr("cape_max"),

        "Radiación solar":
            arr(
                "shortwave_radiation_sum"
            ),

        "Horas de sol":
            arr("sunshine_duration"),

        "ET₀":
            arr(
                "et0_fao_evapotranspiration"
            ),
    })

    tabla["Tiempo"] = tabla[
        "Tiempo"
    ].apply(
        lambda x:
            descripcion_wmo(x)
            if pd.notna(x)
            else "Sin dato"
    )

    tabla[
        "Dirección viento"
    ] = tabla[
        "Dirección viento"
    ].apply(
        lambda x:
            f"{float(x):.0f}° "
            f"{direccion_compass(x)}"
            if pd.notna(x)
            else "Sin dato"
    )

    tabla[
        "Horas de sol"
    ] = (
        tabla["Horas de sol"]
        / 3600.0
    )

    fecha_hoy = pd.Timestamp(
        ahora().date()
    )

    tabla = tabla[
        tabla["Fecha"] >= fecha_hoy
    ].copy()

    return (
        tabla
        .dropna(subset=["Fecha"])
        .head(15)
        .reset_index(drop=True)
    )


# ============================================================
# TELEGRAM — ESTADO
# ============================================================

def mensaje_prueba_telegram():

    return (
        "📡 <b>ALERTA LITORAL AGRO — "
        "PRUEBA TELEGRAM</b>\n\n"
        "✅ El canal de alertas está correctamente conectado.\n"
        f"🕒 {ahora().strftime('%d/%m/%Y %H:%M:%S')}\n\n"
        "Sistema experimental."
    )


def estado_telegram_ui():

    token, chat_id = obtener_secrets_telegram()
    configurado = bool(
        token and chat_id
    )

    st.subheader(
        "📲 Alertas por Telegram"
    )

    if configurado:

        st.success(
            "🟢 Telegram configurado y disponible "
            "para enviar alertas."
        )

        st.caption(
            "Las alertas automáticas se generan para "
            "nodos que alcanzan ALTO o MUY ALTO y se "
            "evita repetir la misma alerta mientras no "
            "cambie el nivel/índice."
        )

        resultado = st.session_state.get(
            "telegram_resultado",
            {
                "enviadas": 0,
                "fallidas": 0,
                "pendientes": 0,
            },
        )

        ta, tb, tc = st.columns(3)

        with ta:
            st.metric(
                "Alertas enviadas",
                resultado.get(
                    "enviadas",
                    0,
                ),
            )

        with tb:
            st.metric(
                "Nodos actualmente ALTO/MUY ALTO",
                resultado.get(
                    "pendientes",
                    0,
                ),
            )

        with tc:
            st.metric(
                "Envíos fallidos",
                resultado.get(
                    "fallidas",
                    0,
                ),
            )

        if (
            resultado.get(
                "fallidas",
                0,
            ) > 0
        ):

            st.error(
                "Hay alertas que no pudieron enviarse "
                "por Telegram. Revisá el bot y las "
                "credenciales."
            )

        col_t1, col_t2 = st.columns(
            [1, 2]
        )

        with col_t1:

            probar = st.button(
                "📨 Enviar prueba a Telegram",
                use_container_width=True,
                key="probar_telegram",
            )

        with col_t2:

            st.caption(
                f"Chat configurado: {chat_id}"
            )

        if probar:

            if enviar_telegram(
                mensaje_prueba_telegram()
            ):

                st.success(
                    "✅ Mensaje de prueba enviado correctamente."
                )

            else:

                st.error(
                    "❌ No se pudo enviar el mensaje. "
                    "Revisá TELEGRAM_BOT_TOKEN, "
                    "TELEGRAM_CHAT_ID y que el bot tenga "
                    "acceso al chat."
                )

    else:

        st.warning(
            "⚪ Alertas Telegram no configuradas. "
            "Agregá TELEGRAM_BOT_TOKEN y "
            "TELEGRAM_CHAT_ID en Settings → Secrets."
        )


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
    f"Versión {VERSION} · Santa Fe · Corrientes · "
    f"Entre Ríos · {MODELO}"
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

    if actualizar:

        obtener_datos_ecmwf.clear()

        st.session_state.pop(
            "df_alerta",
            None,
        )

    st.divider()

    st.markdown(
        """
### Fuentes

**Meteorología principal**
- Open-Meteo
- ECMWF IFS HRES 9 km
- Precipitación / lluvia / chaparrones
- Humedad y temperatura del suelo
- Runoff
- Probabilidad de precipitación
- Viento y ráfagas
- CAPE / VPD
- Presión / nubosidad

**Vigilancia satelital**
- NOAA / GOES-19 ABI
- GeoColor
- Sector Sudamérica Sur

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
    or "df_alerta" not in st.session_state
):

    with st.spinner(
        "Consultando datos meteorológicos de ECMWF..."
    ):

        raw_ecmwf = obtener_datos_ecmwf()

        raw_ecmwf = (
            list(raw_ecmwf)
            if isinstance(
                raw_ecmwf,
                list,
            )
            else []
        )

        if len(raw_ecmwf) < len(NODOS):

            raw_ecmwf.extend(
                [
                    {
                        "error":
                        "Respuesta incompleta de Open-Meteo"
                    }
                ]
                * (
                    len(NODOS)
                    - len(raw_ecmwf)
                )
            )

        raw_ecmwf = raw_ecmwf[
            :len(NODOS)
        ]

        resultados = [
            procesar_nodo(
                nodo,
                datos,
            )
            for nodo, datos
            in zip(
                NODOS,
                raw_ecmwf,
            )
        ]

        df_alerta = pd.DataFrame(
            resultados
        )

        st.session_state[
            "df_alerta"
        ] = df_alerta

        st.session_state[
            "raw_ecmwf"
        ] = raw_ecmwf

        guardar_historial(
            df_alerta
        )

else:

    df_alerta = st.session_state[
        "df_alerta"
    ]

    if (
        "raw_ecmwf"
        not in st.session_state
    ):

        st.session_state[
            "raw_ecmwf"
        ] = obtener_datos_ecmwf()

    raw_ecmwf = st.session_state[
        "raw_ecmwf"
    ]


# ============================================================
# TELEGRAM AUTOMÁTICO
# ============================================================

telegram_resultado = (
    procesar_alertas_telegram(
        df_alerta
    )
)

st.session_state[
    "telegram_resultado"
] = telegram_resultado


# ============================================================
# DATOS VÁLIDOS
# ============================================================

df_validos = df_alerta[
    df_alerta["estado_datos"]
    == "OK"
].copy()

if df_validos.empty:

    st.error(
        "No se pudieron obtener datos meteorológicos válidos."
    )

    errores = df_alerta[
        [
            "localidad",
            "provincia",
            "error_datos",
        ]
    ].copy()

    st.dataframe(
        errores,
        use_container_width=True,
        hide_index=True,
    )

    st.stop()


# ============================================================
# MÉTRICAS
# ============================================================

maximo = df_validos[
    "indice"
].max()

promedio = df_validos[
    "indice"
].mean()

cantidad_alto = int(
    (
        df_validos["nivel"]
        == "ALTO"
    ).sum()
)

cantidad_muy_alto = int(
    (
        df_validos["nivel"]
        == "MUY ALTO"
    ).sum()
)

cantidad_medio = int(
    (
        df_validos["nivel"]
        == "MEDIO"
    ).sum()
)

col1, col2, col3, col4 = st.columns(4)

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
        f"🔴 Se detectan {cantidad_muy_alto} "
        "nodos en nivel MUY ALTO."
    )

elif cantidad_alto > 0:

    st.warning(
        f"🟠 Se detectan {cantidad_alto} "
        "nodos en nivel ALTO."
    )

elif cantidad_medio > 0:

    st.info(
        f"🟡 Se detectan {cantidad_medio} "
        "nodos en nivel MEDIO; se recomienda vigilancia."
    )

else:

    st.success(
        "🟢 No se detectan nodos en nivel "
        "ALTO o MUY ALTO según el índice actual."
    )


# ============================================================
# TELEGRAM VISIBLE
# ============================================================

estado_telegram_ui()


# ============================================================
# MAPAS
# ============================================================

st.header(
    "🗺️ Mapa de riesgo del Litoral"
)

st.caption(
    "El encuadre inicial está fijado sobre Santa Fe, "
    "Corrientes y Entre Ríos."
)

fig_mapa = crear_mapa(
    df_validos
)

st.plotly_chart(
    fig_mapa,
    use_container_width=True,
    config={
        "scrollZoom": True,
        "displaylogo": False,
    },
)


st.header(
    "🌐 Windy — pronóstico meteorológico interactivo"
)

st.caption(
    "Windy se muestra como herramienta complementaria "
    "de visualización. El mapa arranca centrado sobre "
    "Santa Fe, Corrientes y Entre Ríos."
)

windy_overlay = st.selectbox(
    "Variable de Windy",
    options=[
        "wind",
        "rain",
        "temp",
        "clouds",
        "pressure",
    ],
    format_func=lambda x: {
        "wind": "💨 Viento",
        "rain": "🌧️ Lluvia",
        "temp": "🌡️ Temperatura",
        "clouds": "☁️ Nubosidad",
        "pressure": "🌀 Presión",
    }[x],
    key="windy_overlay",
)

mostrar_windy(
    windy_overlay
)

st.link_button(
    "Abrir Windy",
    construir_windy_url(
        windy_overlay
    ),
    use_container_width=True,
)


st.header(
    "🛰️ GOES-19 — vigilancia satelital del Litoral"
)

st.caption(
    "Imagen GeoColor del GOES-19 / ABI. Sector oficial "
    "de NOAA para Sudamérica Sur. La imagen es una "
    "herramienta de observación y no alimenta directamente "
    "el índice de riesgo."
)

mostrar_goes19()

st.link_button(
    "Abrir GOES-19 en NOAA",
    GOES19_URL,
    use_container_width=True,
)


# ============================================================
# LOCALIDAD SELECCIONADA
# ============================================================

localidades = sorted(
    df_validos[
        "localidad"
    ].tolist()
)

if localidades:

    localidad_seleccionada = st.selectbox(
        "Seleccionar localidad",
        localidades,
        key="localidad_detalle",
    )

else:
    localidad_seleccionada = None


# ============================================================
# PRONÓSTICO EXTENDIDO 15 DÍAS
# ============================================================

st.header(
    "📅 Pronóstico extendido — 15 días"
)

st.caption(
    "Pronóstico diario del ECMWF IFS HRES. El horizonte "
    "extendido sirve para planificación y tendencia general; "
    "la incertidumbre aumenta con el plazo."
)

try:

    indice_nodo = next(
        i
        for i, nodo in enumerate(NODOS)
        if nodo["localidad"]
        == localidad_seleccionada
    )

    datos_seleccionados = (
        raw_ecmwf[indice_nodo]
    )

except Exception:

    datos_seleccionados = {}


pronostico_15 = extraer_pronostico_diario(
    datos_seleccionados
)

if pronostico_15.empty:

    st.warning(
        "No hay pronóstico extendido disponible para esta localidad."
    )

else:

    horizonte = st.slider(
        "Mostrar horizonte",
        min_value=5,
        max_value=min(
            15,
            len(pronostico_15),
        ),
        value=min(
            15,
            len(pronostico_15),
        ),
        key="horizonte_pronostico",
    )

    pronostico_mostrar = (
        pronostico_15
        .head(horizonte)
        .copy()
    )

    pronostico_mostrar[
        "Fecha"
    ] = pronostico_mostrar[
        "Fecha"
    ].dt.strftime("%d/%m")

    c1, c2 = st.columns(2)

    with c1:

        fig_lp_prec = px.bar(
            pronostico_mostrar,
            x="Fecha",
            y="Precipitación",
            title=(
                f"Precipitación diaria — "
                f"{localidad_seleccionada}"
            ),
            labels={
                "Precipitación": "mm",
                "Fecha": "Fecha",
            },
        )

        st.plotly_chart(
            fig_lp_prec,
            use_container_width=True,
        )

    with c2:

        temp_largo = pronostico_mostrar.melt(
            id_vars=["Fecha"],
            value_vars=[
                "Temp. mínima",
                "Temp. máxima",
            ],
            var_name="Variable",
            value_name="Temperatura",
        )

        fig_lp_temp = px.line(
            temp_largo,
            x="Fecha",
            y="Temperatura",
            color="Variable",
            markers=True,
            title=(
                f"Temperatura mínima y máxima — "
                f"{localidad_seleccionada}"
            ),
            labels={
                "Temperatura": "°C",
                "Fecha": "Fecha",
            },
        )

        st.plotly_chart(
            fig_lp_temp,
            use_container_width=True,
        )

    tabla_lp = (
        pronostico_mostrar
        .copy()
    )

    tabla_lp = tabla_lp.round({
        "Temp. mínima": 1,
        "Temp. máxima": 1,
        "Sensación mínima": 1,
        "Sensación máxima": 1,
        "Precipitación": 1,
        "Lluvia": 1,
        "Chaparrones": 1,
        "Prob. precipitación": 0,
        "Horas con precipitación": 1,
        "Viento máximo": 1,
        "Ráfaga máxima": 1,
        "CAPE máximo": 0,
        "Radiación solar": 1,
        "Horas de sol": 1,
        "ET₀": 2,
    })

    st.dataframe(
        tabla_lp,
        use_container_width=True,
        hide_index=True,
    )

    csv_lp = (
        tabla_lp
        .to_csv(index=False)
        .encode("utf-8")
    )

    st.download_button(
        "⬇️ Descargar pronóstico extendido CSV",
        csv_lp,
        file_name=(
            "pronostico_15_dias_"
            f"{localidad_seleccionada.lower().replace(' ', '_')}.csv"
        ),
        mime="text/csv",
    )


# ============================================================
# TABLA RESUMEN
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
        "temperatura",
        "humedad_relativa",
        "viento",
        "rafaga",
        "direccion_viento_texto",
        "humedad_suelo_promedio",
        "lluvia_24",
        "lluvia_72",
        "lluvia_7d",
        "lluvia_futura_24",
        "lluvia_futura_72",
        "lluvia_futura_7d",
        "runoff_72",
        "prob_lluvia",
        "cape",
        "tendencia",
    ]
].copy()

# ------------------------------------------------------------
# Conversión SOLO PARA PRESENTACIÓN.
# Internamente humedad_suelo_promedio continúa siendo m³/m³.
# ------------------------------------------------------------
tabla[
    "humedad_suelo_promedio"
] = (
    tabla[
        "humedad_suelo_promedio"
    ] * 100
)

tabla.columns = [
    "Localidad",
    "Provincia",
    "Riesgo",
    "Nivel",
    "Temp. °C",
    "HR %",
    "Viento km/h",
    "Ráfaga km/h",
    "Dirección",
    "Humedad suelo (%)",
    "Lluvia ant. 24h",
    "Lluvia ant. 72h",
    "Lluvia ant. 7d",
    "Pronóstico 24h",
    "Pronóstico 72h",
    "Pronóstico 7d",
    "Runoff ant. 72h",
    "Prob. lluvia 72h",
    "CAPE",
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

if not localidad_seleccionada:

    st.warning(
        "No hay localidades disponibles para mostrar el detalle."
    )

    st.stop()


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

**Condición meteorológica:** {row["descripcion_tiempo"]}

**Tendencia hidrológica:** {row["tendencia"]}

**Dirección hidrológica:** {row["direccion_hidrologica"]}

**Velocidad hidrológica:** {row["velocidad_hidrologica"]}
"""
)

if bool(
    row.get(
        "condicion_critica",
        False,
    )
):

    st.error(
        "⚠️ Se activó una condición crítica por "
        "acumulación de precipitación y/o runoff."
    )


# ------------------------------------------------------------
# Meteorología actual
# ------------------------------------------------------------

st.subheader(
    "🌡️ Condiciones meteorológicas actuales"
)

m1, m2, m3, m4, m5 = st.columns(5)

with m1:
    st.metric(
        "Temperatura",
        f'{row["temperatura"]:.1f} °C',
    )

with m2:
    st.metric(
        "Sensación",
        f'{row["sensacion"]:.1f} °C',
    )

with m3:
    st.metric(
        "Humedad relativa",
        f'{row["humedad_relativa"]:.0f}%',
    )

with m4:
    st.metric(
        "Viento",
        f'{row["viento"]:.1f} km/h',
    )

with m5:
    st.metric(
        "Ráfaga",
        f'{row["rafaga"]:.1f} km/h',
    )


m6, m7, m8, m9, m10 = st.columns(5)

with m6:
    st.metric(
        "Dirección",
        f'{row["direccion_viento"]:.0f}° '
        f'{row["direccion_viento_texto"]}',
    )

with m7:
    st.metric(
        "Punto de rocío",
        f'{row["punto_rocio"]:.1f} °C',
    )

with m8:
    st.metric(
        "Presión MSL",
        f'{row["presion_msl"]:.1f} hPa',
    )

with m9:
    st.metric(
        "Nubosidad",
        f'{row["nubosidad"]:.0f}%',
    )

with m10:
    st.metric(
        "CAPE",
        f'{row["cape"]:.0f} J/kg',
    )


m11, m12, m13, m14 = st.columns(4)

with m11:
    st.metric(
        "VPD",
        f'{row["vpd"]:.2f} kPa',
    )

with m12:
    st.metric(
        "ET",
        f'{row["evapotranspiracion"]:.2f} mm',
    )

with m13:
    st.metric(
        "ET₀",
        f'{row["et0"]:.2f} mm',
    )

with m14:
    st.metric(
        "Tiempo",
        row["descripcion_tiempo"],
    )


# ------------------------------------------------------------
# Suelo
# ------------------------------------------------------------

st.subheader(
    "🌱 Estado del suelo"
)

st.caption(
    "Contenido volumétrico de agua del suelo. "
    "Los datos originales del modelo están expresados "
    "en m³/m³ y se muestran aquí como porcentaje."
)

s1, s2, s3, s4, s5 = st.columns(5)

with s1:

    st.metric(
        "0–7 cm",
        (
            f'{row["humedad_0_7"] * 100:.1f}%'
            if pd.notna(
                row["humedad_0_7"]
            )
            else "Sin dato"
        ),
    )

with s2:

    st.metric(
        "7–28 cm",
        (
            f'{row["humedad_7_28"] * 100:.1f}%'
            if pd.notna(
                row["humedad_7_28"]
            )
            else "Sin dato"
        ),
    )

with s3:

    st.metric(
        "28–100 cm",
        (
            f'{row["humedad_28_100"] * 100:.1f}%'
            if pd.notna(
                row["humedad_28_100"]
            )
            else "Sin dato"
        ),
    )

with s4:

    st.metric(
        "100–255 cm",
        (
            f'{row["humedad_100_255"] * 100:.1f}%'
            if pd.notna(
                row["humedad_100_255"]
            )
            else "Sin dato"
        ),
    )

with s5:

    st.metric(
        "Promedio",
        (
            f'{row["humedad_suelo_promedio"] * 100:.1f}%'
            if pd.notna(
                row["humedad_suelo_promedio"]
            )
            else "Sin dato"
        ),
    )


temp_suelo_df = pd.DataFrame({

    "Profundidad": [
        "0–7 cm",
        "7–28 cm",
        "28–100 cm",
        "100–255 cm",
    ],

    "Temperatura °C": [
        row["temp_suelo_0_7"],
        row["temp_suelo_7_28"],
        row["temp_suelo_28_100"],
        row["temp_suelo_100_255"],
    ],
})


fig_temp_suelo = px.bar(
    temp_suelo_df,
    x="Profundidad",
    y="Temperatura °C",
    title="Temperatura del suelo por profundidad",
)

st.plotly_chart(
    fig_temp_suelo,
    use_container_width=True,
)


# ------------------------------------------------------------
# Precipitación y escorrentía
# ------------------------------------------------------------

st.subheader(
    "🌧️ Precipitación y runoff"
)

precipitacion_df = pd.DataFrame({

    "Periodo": [
        "Antecedente 24h",
        "Antecedente 72h",
        "Antecedente 7d",
        "Pronóstico 24h",
        "Pronóstico 72h",
        "Pronóstico 7d",
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
    title="Precipitación antecedente y prevista",
)

st.plotly_chart(
    fig_prec,
    use_container_width=True,
)


runoff_df = pd.DataFrame({

    "Periodo": [
        "Antecedente 24h",
        "Antecedente 72h",
        "Antecedente 7d",
        "Futuro 24h",
        "Futuro 72h",
        "Futuro 7d",
    ],

    "Runoff mm": [
        row["runoff_24"],
        row["runoff_72"],
        row["runoff_7d"],
        row["runoff_futuro_24"],
        row["runoff_futuro_72"],
        row["runoff_futuro_7d"],
    ],
})


fig_runoff = px.bar(
    runoff_df,
    x="Periodo",
    y="Runoff mm",
    title="Escorrentía / runoff antecedente y prevista",
)

st.plotly_chart(
    fig_runoff,
    use_container_width=True,
)


# ------------------------------------------------------------
# Variables atmosféricas y convectivas
# ------------------------------------------------------------

st.subheader(
    "⛈️ Variables atmosféricas y convectivas"
)

convectiva_df = pd.DataFrame({

    "Variable": [
        "CAPE actual",
        "CAPE máximo próximo 24h",
        "Prob. precipitación 24h",
        "Prob. precipitación 72h",
        "Viento máximo 24h",
        "Ráfaga máxima 24h",
        "Nubosidad total",
        "Nubosidad baja",
        "Nubosidad media",
        "Nubosidad alta",
    ],

    "Valor": [
        row["cape"],
        row["cape_max_24"],
        row["prob_lluvia_24"],
        row["prob_lluvia_72"],
        row["viento_max_24"],
        row["rafaga_max_24"],
        row["nubosidad"],
        row["nubosidad_baja"],
        row["nubosidad_media"],
        row["nubosidad_alta"],
    ],
})


fig_conv = px.bar(
    convectiva_df,
    x="Variable",
    y="Valor",
    title="Variables meteorológicas destacadas",
)

fig_conv.update_xaxes(
    tickangle=-35
)

st.plotly_chart(
    fig_conv,
    use_container_width=True,
)


# ------------------------------------------------------------
# Componentes del riesgo
# ------------------------------------------------------------

st.subheader(
    "📊 Componentes del riesgo"
)

componentes = pd.DataFrame({

    "Componente": [
        "Humedad del suelo",
        "Lluvia reciente",
        "Lluvia futura",
        "Runoff",
        "Acumulación",
        "Lluvia crítica",
    ],

    "Valor": [
        normalizar_0_100(
            row["humedad_suelo_promedio"],
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

        min(
            100,
            (
                (
                    40
                    if row["lluvia_24"] >= 50
                    else 25
                    if row["lluvia_24"] >= 30
                    else 15
                    if row["lluvia_24"] >= 20
                    else 0
                )
                +
                (
                    40
                    if row["lluvia_72"] >= 100
                    else 30
                    if row["lluvia_72"] >= 70
                    else 20
                    if row["lluvia_72"] >= 50
                    else 0
                )
            ),
        ),
    ],
})


fig_componentes = px.bar(
    componentes,
    x="Componente",
    y="Valor",
    range_y=[0, 100],
    title="Componentes normalizados del riesgo",
)

st.plotly_chart(
    fig_componentes,
    use_container_width=True,
)


st.info(
    f'💡 **Acción recomendada:** '
    f'{row["accion"]}'
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

        if (
            not historial.empty
            and "localidad"
            in historial.columns
        ):

            localidades_historial = sorted(
                historial[
                    "localidad"
                ]
                .dropna()
                .unique()
                .tolist()
            )

            localidad_hist = st.selectbox(
                "Localidad para historial",
                localidades_historial,
                key="hist_localidad",
            )

            hist_local = historial[
                historial["localidad"]
                == localidad_hist
            ].copy()

            if (
                not hist_local.empty
                and "indice"
                in hist_local.columns
            ):

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
                    .dropna(
                        subset=[
                            "actualizado"
                        ]
                    )
                    .sort_values(
                        "actualizado"
                    )
                )

                if not hist_local.empty:

                    fig_hist = px.line(
                        hist_local,
                        x="actualizado",
                        y="indice",
                        markers=True,
                        title=(
                            f"Evolución del riesgo — "
                            f"{localidad_hist}"
                        ),
                    )

                    fig_hist.update_yaxes(
                        range=[0, 100]
                    )

                    st.plotly_chart(
                        fig_hist,
                        use_container_width=True,
                    )

                csv_hist = (
                    hist_local
                    .to_csv(index=False)
                    .encode("utf-8")
                )

                st.download_button(
                    "⬇️ Descargar historial CSV",
                    csv_hist,
                    file_name=(
                        "historial_alerta_litoral.csv"
                    ),
                    mime="text/csv",
                )

    except Exception as error:

        st.warning(
            f"No se pudo cargar el historial: {error}"
        )

else:

    st.info(
        "El historial comenzará a generarse cuando "
        "la aplicación procese datos."
    )


# ============================================================
# EXPORTAR
# ============================================================

st.header(
    "⬇️ Exportar estado actual"
)

csv_actual = (
    df_validos
    .to_csv(index=False)
    .encode("utf-8")
)

st.download_button(
    "Descargar estado actual CSV",
    csv_actual,
    file_name="alerta_litoral_agro_actual.csv",
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

El índice experimental de anegamiento integra seis componentes principales:

- humedad del suelo;
- precipitación antecedente de 72 horas;
- precipitación prevista para 72 horas;
- runoff antecedente de 72 horas;
- acumulación de precipitación antecedente de 7 días;
- componente de lluvia crítica por acumulación intensa.

Las restantes variables meteorológicas —temperatura, viento, ráfagas, presión,
nubosidad, CAPE, VPD, etc.— se muestran para ampliar el diagnóstico y no se
incorporan automáticamente al índice mientras no exista una calibración histórica.

### Humedad del suelo

La humedad del suelo proviene de las variables de contenido volumétrico de agua
del modelo ECMWF consultadas mediante Open-Meteo.

Los datos originales se expresan como **m³/m³**.

Para facilitar la interpretación, la interfaz los presenta como porcentaje:

**0.341 m³/m³ = 34.1 %**

La conversión a porcentaje es únicamente de presentación. El cálculo interno
del índice conserva los valores originales en m³/m³ y utiliza actualmente el
intervalo experimental de **0.15–0.45 m³/m³** para normalizar el componente
de humedad del suelo.

### Ventanas temporales

Las ventanas de lluvia y runoff se calculan utilizando los timestamps horarios
devueltos por Open-Meteo:

- **Antecedente:** período previo a la hora actual.
- **Pronóstico:** período posterior a la hora actual.

De esta forma, "24 h", "72 h" y "7 días" no dependen de la posición arbitraria
dentro del array de respuesta.

### Tendencia hidrológica

Se compara el runoff medio de las últimas 12 horas con el período comprendido
entre 12 y 36 horas antes.

**SUBIDA:** aumenta el riesgo.

**ESTABLE:** efecto neutro.

**BAJADA:** reduce el riesgo.

También se diferencia entre subida rápida, subida lenta, bajada rápida,
bajada lenta y estado estable.

### Índice de riesgo

| Índice | Nivel |
|---:|---|
| 0–34.9 | 🟢 BAJO |
| 35–54.9 | 🟡 MEDIO |
| 55–74.9 | 🟠 ALTO |
| 75–100 | 🔴 MUY ALTO |

### Pesos

| Componente | Peso |
|---|---:|
| Humedad del suelo | 25% |
| Lluvia reciente | 20% |
| Lluvia futura | 20% |
| Runoff | 10% |
| Acumulación | 10% |
| Lluvia crítica | 15% |

La tendencia hidrológica funciona como ajuste posterior.

### Condición crítica

El índice se eleva como mínimo a 75 cuando se cumple alguna de estas condiciones
internas del prototipo:

- precipitación antecedente de 72 h ≥ 120 mm;
- precipitación antecedente de 7 días ≥ 220 mm;
- runoff antecedente de 72 h ≥ 50 mm.

Estos valores son **umbrales experimentales del proyecto**, no umbrales oficiales.

### Fuentes

**Open-Meteo / ECMWF:** fuente meteorológica principal del índice y de las variables
mostradas en las fichas de los nodos.

**NOAA / GOES-19 ABI:** vigilancia satelital complementaria mediante GeoColor
en el sector Sudamérica Sur. Se mantiene separada del índice porque la imagen
satelital es una observación/visualización y no un predictor numérico calibrado
dentro de la fórmula actual.

### Pronóstico extendido

La aplicación muestra hasta **15 días** de pronóstico diario del ECMWF IFS HRES 9 km.
Este horizonte es útil para planificación y tendencia general, pero la incertidumbre
aumenta con el plazo y no debe interpretarse con la misma precisión que el corto plazo.

### Limitaciones

Es un sistema experimental. No constituye una alerta oficial, pronóstico
hidrológico oficial ni reemplaza la información de organismos competentes.

Los datos meteorológicos son salidas de modelos numéricos; los valores de
precipitación/runoff no deben presentarse como mediciones directas de un
pluviómetro local.

La calibración de pesos, umbrales y desempeño debe validarse contra episodios
históricos reales de anegamiento antes de un uso institucional operativo.
"""
    )


# ============================================================
# ESTADO DEL SISTEMA
# ============================================================

st.header(
    "🟢 Estado del sistema"
)

st.write(
    "Última actualización de interfaz: "
    f"**{ahora().strftime('%d/%m/%Y %H:%M:%S')}**"
)

st.write(
    "Nodos procesados: "
    f"**{len(df_validos)} / {len(NODOS)}**"
)

st.write(
    "Fuente meteorológica principal: "
    f"**Open-Meteo / {MODELO}**"
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
        "Alertas Telegram: **⚪ no configuradas**"
    )

st.write(
    "Satélite: **🛰️ NOAA GOES-19 / ABI — Sudamérica Sur**"
)

st.caption(
    f"{APP_NAME} · {VERSION} · "
    "Sistema experimental · Sin datos de Vialidad"
)
