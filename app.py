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
# Fuente meteorológica:
#   Open-Meteo / ECMWF IFS HRES
#
# NO UTILIZA DATOS DE VIALIDAD.
#
# Versión: V3.3.3
# ============================================================

import hashlib
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import plotly.express as px
import requests
import streamlit as st


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
VERSION = "V3.3.3"
MODELO = "ECMWF IFS HRES 9 km"
API_URL = "https://api.open-meteo.com/v1/ecmwf"

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


def hora_actual_local_redondeada():
    """Hora local redondeada a la hora, sin tz para compararla con API local."""
    return pd.Timestamp(
        ahora()
        .replace(minute=0, second=0, microsecond=0, tzinfo=None)
    )


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
        return 0.0

    if not np.isfinite(valor) or maximo <= minimo:
        return 0.0

    resultado = (valor - minimo) / (maximo - minimo) * 100.0
    return clamp(resultado)


def array_numerico(valores):
    try:
        return np.array(valores, dtype=float)
    except Exception:
        return np.array([], dtype=float)


def suma_segura(array, mascara=None):
    if array is None or len(array) == 0:
        return 0.0

    try:
        if mascara is not None:
            datos = array[mascara]
        else:
            datos = array

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

        if len(datos) == 0:
            return default

        return float(np.mean(datos))
    except Exception:
        return default


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
            "Mantener vigilancia sobre lluvia, humedad del suelo y evolución del "
            "riesgo antes de realizar tareas sensibles."
        ),
        "BAJO": (
            "Condiciones actualmente favorables según el índice. Mantener el "
            "monitoreo meteorológico."
        ),
    }
    return acciones.get(nivel, "Sin datos suficientes.")


def ventana_mascara(horas, inicio, fin, incluir_inicio=False):
    """Construye una máscara temporal sobre timestamps locales sin zona horaria."""
    if incluir_inicio:
        return (horas >= inicio) & (horas <= fin)
    return (horas > inicio) & (horas <= fin)


# ============================================================
# OPEN-METEO / ECMWF
# ============================================================

@st.cache_data(ttl=900, show_spinner=False)
def obtener_datos_ecmwf():
    """Obtiene todos los nodos en una sola consulta al endpoint ECMWF de Open-Meteo."""

    latitudes = ",".join(f'{nodo["lat"]:.3f}' for nodo in NODOS)
    longitudes = ",".join(f'{nodo["lon"]:.3f}' for nodo in NODOS)

    params = {
        "latitude": latitudes,
        "longitude": longitudes,
        "hourly": ",".join([
            "precipitation",
            "rain",
            "showers",
            "precipitation_probability",
            "runoff",
            "soil_moisture_0_to_7cm",
            "soil_moisture_7_to_28cm",
            "soil_moisture_28_to_100cm",
        ]),
        "past_days": 8,
        "forecast_days": 10,
        "models": "ecmwf_ifs",
        "timezone": "America/Argentina/Buenos_Aires",
        "temperature_unit": "celsius",
        "wind_speed_unit": "kmh",
        "precipitation_unit": "mm",
        "cell_selection": "land",
    }

    headers = {
        "User-Agent": "Alerta-Litoral-Agro/3.3.3",
    }

    try:
        response = requests.get(
            API_URL,
            params=params,
            headers=headers,
            timeout=45,
        )
        response.raise_for_status()
        payload = response.json()

        # La API devuelve una lista cuando se consultan múltiples coordenadas.
        if isinstance(payload, list):
            return payload

        # Fallback defensivo por si el proveedor devuelve error como objeto.
        if isinstance(payload, dict) and payload.get("error"):
            return [payload for _ in NODOS]

        return [payload]

    except Exception as error:
        return [
            {"error": str(error)}
            for _ in NODOS
        ]


# ============================================================
# PROCESAMIENTO DEL NODO
# ============================================================


def resultado_sin_datos(nodo, error="Sin datos suficientes"):
    return {
        **nodo,
        "estado_datos": "ERROR",
        "error_datos": str(error),
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
        "runoff_futuro_24": np.nan,
        "runoff_futuro_72": np.nan,
        "runoff_futuro_7d": np.nan,
        "prob_lluvia": np.nan,
        "prob_lluvia_24": np.nan,
        "prob_lluvia_72": np.nan,
        "tendencia": "SIN DATOS",
        "direccion_hidrologica": "SIN DATOS",
        "velocidad_hidrologica": "SIN DATOS",
        "accion": "Sin datos suficientes.",
        "actualizado": ahora().strftime("%d/%m/%Y %H:%M"),
        "horas_disponibles": 0,
    }


def procesar_nodo(nodo, datos):
    if not datos or not isinstance(datos, dict) or datos.get("error"):
        error = datos.get("error", "Sin datos suficientes") if isinstance(datos, dict) else "Sin datos suficientes"
        return resultado_sin_datos(nodo, error)

    hourly = datos.get("hourly", {})

    tiempos = hourly.get("time", [])
    horas = pd.to_datetime(
        tiempos,
        errors="coerce",
    )

    if len(horas) == 0:
        return resultado_sin_datos(nodo, "La respuesta no contiene horarios horarios")

    horas = pd.DatetimeIndex(horas)

    # Quitamos cualquier timestamp inválido.
    valid_time = ~horas.isna()
    horas = horas[valid_time]

    precipitacion = array_numerico(hourly.get("precipitation", []))[valid_time]
    runoff = array_numerico(hourly.get("runoff", []))[valid_time]
    probabilidad = array_numerico(hourly.get("precipitation_probability", []))[valid_time]

    soil_0_7 = array_numerico(hourly.get("soil_moisture_0_to_7cm", []))[valid_time]
    soil_7_28 = array_numerico(hourly.get("soil_moisture_7_to_28cm", []))[valid_time]
    soil_28_100 = array_numerico(hourly.get("soil_moisture_28_to_100cm", []))[valid_time]

    n = len(horas)

    # Ajuste defensivo si una variable llega con menor cantidad de datos.
    def ajustar_longitud(array):
        if len(array) == n:
            return array
        if len(array) > n:
            return array[:n]
        salida = np.full(n, np.nan, dtype=float)
        salida[:len(array)] = array
        return salida

    precipitacion = ajustar_longitud(precipitacion)
    runoff = ajustar_longitud(runoff)
    probabilidad = ajustar_longitud(probabilidad)
    soil_0_7 = ajustar_longitud(soil_0_7)
    soil_7_28 = ajustar_longitud(soil_7_28)
    soil_28_100 = ajustar_longitud(soil_28_100)

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

    # ========================================================
    # HUMEDAD DEL SUELO
    # ========================================================
    # Las variables de ECMWF IFS HRES se expresan como contenido
    # volumétrico de agua en el suelo (m³/m³).
    # Se toma el valor más reciente disponible hasta la hora actual.

    candidatos_actuales = np.where(np.array(horas <= referencia))[0]

    if len(candidatos_actuales) > 0:
        idx_actual = int(candidatos_actuales[-1])
    else:
        idx_actual = int(
            np.argmin(
                np.abs(
                    horas - referencia
                )
            )
        )

    humedad_componentes = []

    for array in [soil_0_7, soil_7_28, soil_28_100]:
        if len(array) > idx_actual and np.isfinite(array[idx_actual]):
            humedad_componentes.append(float(array[idx_actual]))

    humedad = (
        float(np.mean(humedad_componentes))
        if humedad_componentes
        else np.nan
    )

    # ========================================================
    # LLUVIA: VENTANAS TEMPORALES REALES
    # ========================================================

    lluvia_24 = suma_segura(precipitacion, pasado_24)
    lluvia_72 = suma_segura(precipitacion, pasado_72)
    lluvia_7d = suma_segura(precipitacion, pasado_7d)

    lluvia_futura_24 = suma_segura(precipitacion, futuro_24)
    lluvia_futura_72 = suma_segura(precipitacion, futuro_72)
    lluvia_futura_7d = suma_segura(precipitacion, futuro_7d)

    # ========================================================
    # RUNOFF: VENTANAS TEMPORALES REALES
    # ========================================================

    runoff_24 = suma_segura(runoff, pasado_24)
    runoff_72 = suma_segura(runoff, pasado_72)
    runoff_7d = suma_segura(runoff, pasado_7d)

    runoff_futuro_24 = suma_segura(runoff, futuro_24)
    runoff_futuro_72 = suma_segura(runoff, futuro_72)
    runoff_futuro_7d = suma_segura(runoff, futuro_7d)

    # ========================================================
    # PROBABILIDAD DE LLUVIA
    # ========================================================

    prob_lluvia_24 = media_segura(
        probabilidad,
        futuro_24,
        default=np.nan,
    )

    prob_lluvia_72 = media_segura(
        probabilidad,
        futuro_72,
        default=np.nan,
    )

    max_prob_futuro = (
        np.nanmax(probabilidad[futuro_72])
        if np.any(futuro_72) and np.any(np.isfinite(probabilidad[futuro_72]))
        else np.nan
    )

    prob_lluvia = float(max_prob_futuro) if np.isfinite(max_prob_futuro) else np.nan

    # ========================================================
    # COMPONENTE HUMEDAD
    # ========================================================

    humedad_score = (
        normalizar_0_100(humedad, 0.15, 0.45)
        if np.isfinite(humedad)
        else 0.0
    )

    # ========================================================
    # COMPONENTE LLUVIA RECIENTE
    # ========================================================

    lluvia_reciente_score = normalizar_0_100(
        lluvia_72,
        10,
        100,
    )

    # ========================================================
    # COMPONENTE LLUVIA FUTURA
    # ========================================================

    lluvia_futura_score = normalizar_0_100(
        lluvia_futura_72,
        10,
        100,
    )

    # ========================================================
    # COMPONENTE RUNOFF
    # ========================================================

    runoff_score = normalizar_0_100(
        runoff_72,
        1,
        30,
    )

    # ========================================================
    # COMPONENTE ACUMULACIÓN
    # ========================================================

    acumulacion_score = normalizar_0_100(
        lluvia_7d,
        30,
        180,
    )

    # ========================================================
    # COMPONENTE DE LLUVIA CRÍTICA
    # ========================================================
    # Sustituye la etiqueta anterior de "referencia INA".
    # Son umbrales internos del prototipo y no se presentan como
    # umbrales oficiales del INA.

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

    lluvia_critica_score = clamp(lluvia_critica_score)

    # ========================================================
    # TENDENCIA HIDROLÓGICA
    # ========================================================
    # Compara únicamente períodos pasados, evitando mezclar
    # tendencia con el pronóstico futuro.

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
        default=np.nan,
    )

    runoff_anterior = media_segura(
        runoff,
        anterior_12_36,
        default=np.nan,
    )

    if np.isfinite(runoff_reciente) and np.isfinite(runoff_anterior):
        diferencia = runoff_reciente - runoff_anterior

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
    # Pesos explícitos del prototipo:
    #   Humedad            25%
    #   Lluvia reciente    20%
    #   Lluvia futura      20%
    #   Runoff             10%
    #   Acumulación        10%
    #   Lluvia crítica     15%
    # La tendencia actúa como ajuste posterior independiente.

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

    # ========================================================
    # CONDICIÓN CRÍTICA
    # ========================================================

    condicion_critica = (
        lluvia_72 >= 120
        or lluvia_7d >= 220
        or runoff_72 >= 50
    )

    if condicion_critica:
        indice = max(indice, 75.0)

    nivel = nivel_desde_indice(indice)
    accion = accion_desde_nivel(nivel)

    return {
        **nodo,
        "estado_datos": "OK",
        "error_datos": "",
        "indice": round(indice, 1),
        "nivel": nivel,
        "humedad": round(humedad, 3) if np.isfinite(humedad) else np.nan,
        "lluvia_24": round(lluvia_24, 1),
        "lluvia_72": round(lluvia_72, 1),
        "lluvia_7d": round(lluvia_7d, 1),
        "lluvia_futura_24": round(lluvia_futura_24, 1),
        "lluvia_futura_72": round(lluvia_futura_72, 1),
        "lluvia_futura_7d": round(lluvia_futura_7d, 1),
        "runoff_24": round(runoff_24, 2),
        "runoff_72": round(runoff_72, 2),
        "runoff_7d": round(runoff_7d, 2),
        "runoff_futuro_24": round(runoff_futuro_24, 2),
        "runoff_futuro_72": round(runoff_futuro_72, 2),
        "runoff_futuro_7d": round(runoff_futuro_7d, 2),
        "prob_lluvia": round(prob_lluvia, 1) if np.isfinite(prob_lluvia) else np.nan,
        "prob_lluvia_24": round(prob_lluvia_24, 1) if np.isfinite(prob_lluvia_24) else np.nan,
        "prob_lluvia_72": round(prob_lluvia_72, 1) if np.isfinite(prob_lluvia_72) else np.nan,
        "tendencia": tendencia,
        "direccion_hidrologica": direccion_hidrologica,
        "velocidad_hidrologica": velocidad_hidrologica,
        "accion": accion,
        "condicion_critica": bool(condicion_critica),
        "actualizado": ahora().strftime("%d/%m/%Y %H:%M"),
        "horas_disponibles": int(len(horas)),
    }


# ============================================================
# OBTENER TODOS LOS NODOS
# ============================================================


def obtener_todos_los_nodos():
    datos_lista = obtener_datos_ecmwf()
    resultados = []

    if len(datos_lista) != len(NODOS):
        datos_lista = list(datos_lista)
        while len(datos_lista) < len(NODOS):
            datos_lista.append({
                "error": "Respuesta incompleta de Open-Meteo"
            })
        datos_lista = datos_lista[:len(NODOS)]

    for nodo, datos in zip(NODOS, datos_lista):
        resultados.append(
            procesar_nodo(nodo, datos)
        )

    return pd.DataFrame(resultados)


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
        "runoff_futuro_24",
        "runoff_futuro_72",
        "runoff_futuro_7d",
        "prob_lluvia",
        "prob_lluvia_24",
        "prob_lluvia_72",
        "tendencia",
    ]

    columnas_validas = [
        columna for columna in columnas
        if columna in df.columns
    ]

    nuevo = df[columnas_validas].copy()

    try:
        if HISTORY_FILE.exists():
            anterior = pd.read_csv(HISTORY_FILE)
            combinado = pd.concat(
                [anterior, nuevo],
                ignore_index=True,
            )
        else:
            combinado = nuevo

        # Evita crecimiento indefinido del archivo local.
        if len(combinado) > 50000:
            combinado = combinado.tail(50000).copy()

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
        token = st.secrets.get(
            "TELEGRAM_BOT_TOKEN",
            "",
        )
        chat_id = st.secrets.get(
            "TELEGRAM_CHAT_ID",
            "",
        )
        return str(token).strip(), str(chat_id).strip()
    except Exception:
        return "", ""


def cargar_estado_telegram():
    # Session state para conservar el anti-spam dentro de la sesión.
    if "telegram_alert_state" in st.session_state:
        return dict(st.session_state["telegram_alert_state"])

    if not STATE_FILE.exists():
        estado = {}
    else:
        try:
            with open(
                STATE_FILE,
                "r",
                encoding="utf-8",
            ) as archivo:
                estado = json.load(archivo)
        except Exception:
            estado = {}

    st.session_state["telegram_alert_state"] = dict(estado)
    return dict(estado)


def guardar_estado_telegram(estado):
    estado = dict(estado)
    st.session_state["telegram_alert_state"] = estado

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
            timeout=15,
        )
        response.raise_for_status()
        return True
    except Exception:
        return False


def generar_mensaje_alerta(row):
    emoji = emoji_nivel(row["nivel"])

    prob = row.get("prob_lluvia", np.nan)
    prob_texto = (
        f"{prob:.0f}%"
        if pd.notna(prob)
        else "Sin dato"
    )

    return f"""
{emoji} <b>ALERTA LITORAL AGRO</b>

<b>Nivel:</b> {row["nivel"]}
<b>Localidad:</b> {row["localidad"]}
<b>Provincia:</b> {row["provincia"]}

<b>Índice de riesgo:</b> {row["indice"]:.1f}/100

<b>Humedad del suelo:</b>
{row["humedad"]:.3f} m³/m³

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

    if not token or not chat_id or df.empty:
        return

    estado = cargar_estado_telegram()

    for _, row in df.iterrows():
        clave_localidad = (
            f'{row["provincia"]}_{row["localidad"]}'
        )

        # Si el nodo salió de nivel de alerta, liberamos su estado para
        # permitir una nueva alerta si vuelve a subir más adelante.
        if row["nivel"] not in [
            "ALTO",
            "MUY ALTO",
        ]:
            estado.pop(clave_localidad, None)
            continue

        clave = clave_alerta(row)
        ultima = estado.get(clave_localidad)

        if ultima == clave:
            continue

        mensaje = generar_mensaje_alerta(row)
        enviado = enviar_telegram(mensaje)

        if enviado:
            estado[clave_localidad] = clave

    guardar_estado_telegram(estado)


# ============================================================
# MAPA
# ============================================================


def crear_mapa(df):
    colores = {
        "BAJO": "green",
        "MEDIO": "yellow",
        "ALTO": "orange",
        "MUY ALTO": "red",
    }

    fig = px.scatter_map(
        df,
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
            "prob_lluvia": True,
            "tendencia": True,
            "lat": False,
            "lon": False,
        },
        color_discrete_map=colores,
        zoom=5,
        height=650,
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
            "title": "Nivel de riesgo",
        },
    )

    return fig


# ============================================================
# INTERFAZ
# ============================================================

st.title("🌧️ Alerta Litoral Agro")

st.subheader(
    "Sistema experimental de alerta temprana "
    "para riesgo de anegamiento agropecuario"
)

st.caption(
    f"Versión {VERSION} · "
    "Santa Fe · Corrientes · Entre Ríos · "
    f"{MODELO}"
)


# ============================================================
# SIDEBAR
# ============================================================

with st.sidebar:
    st.header("⚙️ Control")

    actualizar = st.button(
        "🔄 Actualizar datos",
        use_container_width=True,
    )

    if actualizar:
        # El botón debe invalidar la caché para no devolver datos viejos.
        obtener_datos_ecmwf.clear()
        st.session_state.pop("df_alerta", None)

    st.divider()

    st.markdown(
        """
### Fuentes

- Open-Meteo
- ECMWF IFS HRES 9 km
- Precipitación
- Humedad del suelo
- Runoff
- Probabilidad de precipitación
- Umbrales internos del prototipo

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
        df_alerta = obtener_todos_los_nodos()
        st.session_state["df_alerta"] = df_alerta
        guardar_historial(df_alerta)
else:
    df_alerta = st.session_state["df_alerta"]


# ============================================================
# TELEGRAM
# ============================================================

procesar_alertas_telegram(df_alerta)


# ============================================================
# DATOS VÁLIDOS
# ============================================================

df_validos = df_alerta[
    df_alerta["estado_datos"] == "OK"
].copy()

if df_validos.empty:
    st.error(
        "No se pudieron obtener datos meteorológicos válidos."
    )

    errores = df_alerta[
        ["localidad", "provincia", "error_datos"]
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

maximo = df_validos["indice"].max()
promedio = df_validos["indice"].mean()

cantidad_alto = len(
    df_validos[
        df_validos["nivel"] == "ALTO"
    ]
)

cantidad_muy_alto = len(
    df_validos[
        df_validos["nivel"] == "MUY ALTO"
    ]
)

cantidad_medio = len(
    df_validos[
        df_validos["nivel"] == "MEDIO"
    ]
)


# ============================================================
# MÉTRICAS SUPERIORES
# ============================================================

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
        f"🔴 Se detectan {cantidad_muy_alto} nodos "
        "en nivel MUY ALTO."
    )

elif cantidad_alto > 0:
    st.warning(
        f"🟠 Se detectan {cantidad_alto} nodos "
        "en nivel ALTO."
    )

elif cantidad_medio > 0:
    st.info(
        f"🟡 Se detectan {cantidad_medio} nodos "
        "en nivel MEDIO; se recomienda vigilancia."
    )

else:
    st.success(
        "🟢 No se detectan nodos en nivel ALTO o MUY ALTO "
        "según el índice actual."
    )


# ============================================================
# MAPA
# ============================================================

st.header("🗺️ Mapa de riesgo")

fig_mapa = crear_mapa(df_validos)

st.plotly_chart(
    fig_mapa,
    use_container_width=True,
)


# ============================================================
# TABLA
# ============================================================

st.header("📋 Estado de los nodos")

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
        "prob_lluvia",
        "tendencia",
    ]
].copy()

tabla.columns = [
    "Localidad",
    "Provincia",
    "Riesgo",
    "Nivel",
    "Humedad",
    "Lluvia antecedente 24h",
    "Lluvia antecedente 72h",
    "Lluvia antecedente 7d",
    "Pronóstico 24h",
    "Pronóstico 72h",
    "Pronóstico 7d",
    "Runoff antecedente 72h",
    "Prob. lluvia 72h",
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

st.header("🔎 Análisis por localidad")

localidades = sorted(
    df_validos["localidad"].tolist()
)

localidad_seleccionada = st.selectbox(
    "Seleccionar localidad",
    localidades,
)

row = df_validos[
    df_validos["localidad"] == localidad_seleccionada
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

if bool(row.get("condicion_critica", False)):
    st.error(
        "⚠️ Se activó una condición crítica por acumulación de "
        "precipitación y/o runoff."
    )


d1, d2, d3, d4 = st.columns(4)

with d1:
    humedad_texto = (
        f'{row["humedad"]:.3f}'
        if pd.notna(row["humedad"])
        else "Sin dato"
    )
    st.metric(
        "Humedad",
        humedad_texto,
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
        f'{row["runoff_72"]:.2f} mm',
    )


p1, p2 = st.columns(2)

with p1:
    prob24 = row["prob_lluvia_24"]
    st.metric(
        "Prob. media de lluvia próxima 24h",
        f"{prob24:.0f}%" if pd.notna(prob24) else "Sin dato",
    )

with p2:
    prob72 = row["prob_lluvia_72"]
    st.metric(
        "Prob. media de lluvia próxima 72h",
        f"{prob72:.0f}%" if pd.notna(prob72) else "Sin dato",
    )

st.info(
    f'💡 **Acción recomendada:** {row["accion"]}'
)


# ============================================================
# PRECIPITACIÓN
# ============================================================

st.header("🌧️ Precipitación")

precipitacion_df = pd.DataFrame({
    "Periodo": [
        "Antecedente 24 h",
        "Antecedente 72 h",
        "Antecedente 7 días",
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
    title="Precipitación antecedente y prevista",
)

st.plotly_chart(
    fig_prec,
    use_container_width=True,
)


# ============================================================
# COMPONENTES
# ============================================================

st.header("📊 Componentes del riesgo")

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
        (
            min(
                100,
                (
                    (40 if row["lluvia_24"] >= 50 else
                     25 if row["lluvia_24"] >= 30 else
                     15 if row["lluvia_24"] >= 20 else 0)
                    +
                    (40 if row["lluvia_72"] >= 100 else
                     30 if row["lluvia_72"] >= 70 else
                     20 if row["lluvia_72"] >= 50 else 0)
                )
            )
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


# ============================================================
# HISTORIAL
# ============================================================

st.header("📈 Historial")

if HISTORY_FILE.exists():
    try:
        historial = pd.read_csv(HISTORY_FILE)

        if not historial.empty and "localidad" in historial.columns:
            localidades_historial = sorted(
                historial["localidad"]
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
                historial["localidad"] == localidad_hist
            ].copy()

            if not hist_local.empty and "indice" in hist_local.columns:
                hist_local["actualizado"] = pd.to_datetime(
                    hist_local["actualizado"],
                    dayfirst=True,
                    errors="coerce",
                )

                hist_local = (
                    hist_local
                    .dropna(subset=["actualizado"])
                    .sort_values("actualizado")
                )

                if not hist_local.empty:
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
                    file_name="historial_alerta_litoral.csv",
                    mime="text/csv",
                )

    except Exception as error:
        st.warning(
            "No se pudo cargar el historial: "
            f"{error}"
        )
else:
    st.info(
        "El historial comenzará a generarse cuando la aplicación procese datos."
    )


# ============================================================
# EXPORTAR
# ============================================================

st.header("⬇️ Exportar estado actual")

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

with st.expander("📚 Metodología y limitaciones"):
    st.markdown(
        """
### ¿Qué evalúa el sistema?

El índice experimental integra seis componentes:

- humedad del suelo;
- precipitación antecedente de 72 horas;
- precipitación prevista para 72 horas;
- runoff antecedente de 72 horas;
- acumulación de precipitación antecedente de 7 días;
- componente de lluvia crítica por acumulación intensa.

### Ventanas temporales

El sistema separa explícitamente:

- **Antecedente:** períodos anteriores a la hora actual;
- **Pronóstico:** períodos posteriores a la hora actual.

Las ventanas se calculan usando los timestamps horarios devueltos por Open-Meteo, en lugar de asumir que las primeras posiciones del array representan las últimas horas.

### Tendencia hidrológica

La tendencia se obtiene comparando el runoff medio de las últimas 12 horas con el período de 12 a 36 horas anteriores.

**SUBIDA:** aumenta el riesgo.

**ESTABLE:** efecto neutro.

**BAJADA:** disminuye el riesgo.

También se diferencia entre:

- subida rápida;
- subida lenta;
- bajada rápida;
- bajada lenta;
- estado estable.

### Índice de riesgo

El resultado se expresa entre **0 y 100**.

| Índice | Nivel |
|---:|---|
| 0–34.9 | 🟢 BAJO |
| 35–54.9 | 🟡 MEDIO |
| 55–74.9 | 🟠 ALTO |
| 75–100 | 🔴 MUY ALTO |

### Pesos del índice

| Componente | Peso |
|---|---:|
| Humedad del suelo | 25% |
| Lluvia reciente | 20% |
| Lluvia futura | 20% |
| Runoff | 10% |
| Acumulación | 10% |
| Lluvia crítica | 15% |

La tendencia hidrológica actúa como ajuste posterior de la puntuación.

### Condición crítica

El índice se eleva como mínimo a 75 cuando se cumple alguna de estas condiciones internas del prototipo:

- precipitación antecedente de 72 h ≥ 120 mm;
- precipitación antecedente de 7 días ≥ 220 mm;
- runoff antecedente de 72 h ≥ 50 mm.

Estos valores son **umbrales experimentales del proyecto** y no deben interpretarse como umbrales oficiales de un organismo público.

### Fuente y modelo

Los datos meteorológicos se consultan mediante Open-Meteo utilizando ECMWF IFS HRES de aproximadamente 9 km de resolución.

Las variables de precipitación y runoff son acumulados horarios derivados del modelo. Los valores antecedentes incluidos en la aplicación corresponden a la serie temporal del servicio y no deben presentarse como una medición directa de pluviómetro local.

### Uso y limitaciones

El sistema busca identificar anticipadamente condiciones potencialmente favorables al anegamiento agropecuario.

Es un sistema experimental y no constituye una alerta oficial, pronóstico hidrológico oficial ni reemplaza la información de organismos competentes.

La calibración de umbrales y pesos debería validarse con registros históricos de lluvia, humedad, runoff y episodios reales de anegamiento antes de considerarlo un sistema operativo institucional.
"""
    )


# ============================================================
# ESTADO DEL SISTEMA
# ============================================================

st.header("🟢 Estado del sistema")

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

telegram_token, telegram_chat = obtener_secrets_telegram()

if telegram_token and telegram_chat:
    st.write("Alertas Telegram: **🟢 configuradas**")
else:
    st.write("Alertas Telegram: **⚪ no configuradas**")

st.caption(
    f"{APP_NAME} · {VERSION} · Sistema experimental · Sin datos de Vialidad"
)
