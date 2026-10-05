# ============================================================
# ALERTA LITORAL AGRO — V3.2
# ============================================================
#
# Sistema experimental de alerta temprana para riesgo de
# anegamiento agropecuario.
#
# Área:
#   Santa Fe
#   Corrientes
#   Entre Ríos
#
# V3.2
#
# CAMBIOS PRINCIPALES:
#   - Eliminada dependencia de INA/WFS.
#   - Eliminada dependencia de CARTO.
#   - OpenStreetMap como mapa base.
#   - Open-Meteo como fuente meteorológica.
#   - Fallback meteorológico.
#   - Índice integrado correctamente normalizado 0–100.
#   - Redistribución matemática de pesos.
#   - Control de calidad.
#   - Vulnerabilidad territorial experimental.
#   - Historial de sesión.
#   - Exportación CSV.
#   - Telegram opcional.
#
# IMPORTANTE:
#   Sistema experimental.
#   No reemplaza alertas oficiales.
# ============================================================

import requests
import pandas as pd
import streamlit as st
import folium

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from streamlit_folium import st_folium


# ============================================================
# CONFIGURACIÓN
# ============================================================

st.set_page_config(
    page_title="Alerta Litoral Agro V3.2",
    page_icon="🌧️",
    layout="wide",
    initial_sidebar_state="expanded",
)

TZ_ARG = ZoneInfo(
    "America/Argentina/Buenos_Aires"
)

OPEN_METEO_ECMWF = (
    "https://api.open-meteo.com/v1/ecmwf"
)

OPEN_METEO_GENERAL = (
    "https://api.open-meteo.com/v1/forecast"
)


# ============================================================
# NODOS
# ============================================================

NODOS = [

    # --------------------------------------------------------
    # CORRIENTES
    # --------------------------------------------------------

    {
        "nombre": "Goya",
        "provincia": "Corrientes",
        "lat": -29.14,
        "lon": -59.26,
        "rio": "Río Paraná",
        "zona_alta": "Loma Batelito",
    },

    {
        "nombre": "Mercedes",
        "provincia": "Corrientes",
        "lat": -29.18,
        "lon": -58.07,
        "rio": "Sistema Iberá",
        "zona_alta": "Lomadas de Mercedes",
    },

    {
        "nombre": "Curuzú Cuatiá",
        "provincia": "Corrientes",
        "lat": -29.79,
        "lon": -58.05,
        "rio": "Arroyo Sarandí",
        "zona_alta": "Sierras de Curuzú",
    },

    {
        "nombre": "Paso de los Libres",
        "provincia": "Corrientes",
        "lat": -29.71,
        "lon": -57.08,
        "rio": "Río Uruguay",
        "zona_alta": "Zona alta de Paso de los Libres",
    },

    {
        "nombre": "Santo Tomé",
        "provincia": "Corrientes",
        "lat": -28.55,
        "lon": -56.04,
        "rio": "Río Uruguay",
        "zona_alta": "Loma Alta Santo Tomé",
    },

    {
        "nombre": "Corrientes Capital",
        "provincia": "Corrientes",
        "lat": -27.46,
        "lon": -58.83,
        "rio": "Río Paraná",
        "zona_alta": "Sectores altos de Corrientes",
    },

    # --------------------------------------------------------
    # SANTA FE
    # --------------------------------------------------------

    {
        "nombre": "Reconquista",
        "provincia": "Santa Fe",
        "lat": -29.15,
        "lon": -59.65,
        "rio": "Río Paraná",
        "zona_alta": "Loma Alta Reconquista Oeste",
    },

    {
        "nombre": "San Javier",
        "provincia": "Santa Fe",
        "lat": -30.58,
        "lon": -59.93,
        "rio": "Río San Javier",
        "zona_alta": "Sectores altos de la zona",
    },

    {
        "nombre": "Vera",
        "provincia": "Santa Fe",
        "lat": -29.46,
        "lon": -60.21,
        "rio": "Cuenca Calchaquí",
        "zona_alta": "Cuchilla Fortín Olmos",
    },

    {
        "nombre": "Santa Fe Capital",
        "provincia": "Santa Fe",
        "lat": -31.63,
        "lon": -60.70,
        "rio": "Río Salado / Paraná",
        "zona_alta": "Sectores altos del Litoral Centro",
    },

    {
        "nombre": "Rosario",
        "provincia": "Santa Fe",
        "lat": -32.95,
        "lon": -60.66,
        "rio": "Río Paraná",
        "zona_alta": "Zonas altas del cordón industrial",
    },

    {
        "nombre": "Tostado",
        "provincia": "Santa Fe",
        "lat": -29.23,
        "lon": -61.77,
        "rio": "Río Salado Norte",
        "zona_alta": "Lomadas de Tostado",
    },

    # --------------------------------------------------------
    # ENTRE RÍOS
    # --------------------------------------------------------

    {
        "nombre": "Concordia",
        "provincia": "Entre Ríos",
        "lat": -31.39,
        "lon": -58.02,
        "rio": "Río Uruguay",
        "zona_alta": "Lomas de Salto Grande",
    },

    {
        "nombre": "La Paz",
        "provincia": "Entre Ríos",
        "lat": -30.74,
        "lon": -59.64,
        "rio": "Río Paraná",
        "zona_alta": "Cuchilla Montiel",
    },

    {
        "nombre": "Victoria",
        "provincia": "Entre Ríos",
        "lat": -32.62,
        "lon": -60.15,
        "rio": "Delta del Paraná",
        "zona_alta": "Cuchilla Victoria",
    },

    {
        "nombre": "Gualeguay",
        "provincia": "Entre Ríos",
        "lat": -33.14,
        "lon": -59.31,
        "rio": "Río Gualeguay",
        "zona_alta": "Cuchilla de Gualeguay",
    },

    {
        "nombre": "Gualeguaychú",
        "provincia": "Entre Ríos",
        "lat": -33.01,
        "lon": -58.51,
        "rio": "Río Gualeguaychú",
        "zona_alta": "Lomas de Gualeguaychú",
    },

    {
        "nombre": "Paraná",
        "provincia": "Entre Ríos",
        "lat": -31.73,
        "lon": -60.52,
        "rio": "Río Paraná",
        "zona_alta": "Lomas de Paraná",
    },
]


# ============================================================
# VULNERABILIDAD TERRITORIAL
# ============================================================

VULNERABILIDAD_BASE = {

    "Goya": 80,
    "Mercedes": 55,
    "Curuzú Cuatiá": 45,
    "Paso de los Libres": 60,
    "Santo Tomé": 65,
    "Corrientes Capital": 75,

    "Reconquista": 85,
    "San Javier": 90,
    "Vera": 50,
    "Santa Fe Capital": 90,
    "Rosario": 70,
    "Tostado": 55,

    "Concordia": 75,
    "La Paz": 85,
    "Victoria": 95,
    "Gualeguay": 80,
    "Gualeguaychú": 80,
    "Paraná": 60,
}


# ============================================================
# SEMÁFORO
# ============================================================

NIVELES = {

    "VERDE": {
        "emoji": "🟢",
        "color": "#28a745",
        "descripcion": "Riesgo bajo",
    },

    "AMARILLO": {
        "emoji": "🟡",
        "color": "#d99b00",
        "descripcion": "Vigilancia",
    },

    "NARANJA": {
        "emoji": "🟠",
        "color": "#fd7e14",
        "descripcion": "Riesgo elevado",
    },

    "ROJO": {
        "emoji": "🔴",
        "color": "#dc3545",
        "descripcion": "Riesgo muy elevado",
    },
}


# ============================================================
# ACCIONES
# ============================================================

ACCIONES = {

    "VERDE": [
        "Monitoreo meteorológico normal.",
        "Mantener seguimiento de lluvias.",
        "Controlar evolución de humedad del suelo.",
    ],

    "AMARILLO": [
        "Vigilar bajos y sectores con antecedentes de anegamiento.",
        "Controlar drenajes y accesos.",
        "Seguir la evolución de las precipitaciones.",
    ],

    "NARANJA": [
        "Preparar medidas preventivas para hacienda.",
        "Revisar drenajes y accesos internos.",
        "Proteger maquinaria e insumos ubicados en sectores bajos.",
        "Anticipar tareas productivas sensibles.",
    ],

    "ROJO": [
        "Evaluar movimiento preventivo de hacienda.",
        "Trasladar maquinaria e insumos vulnerables.",
        "Inspeccionar drenajes y accesos.",
        "Evitar tareas críticas en sectores bajos.",
        "Aumentar frecuencia de monitoreo.",
    ],
}


# ============================================================
# FUNCIONES GENERALES
# ============================================================

def ahora():

    return datetime.now(
        TZ_ARG
    )


def safe_float(
    valor,
    default=None
):

    try:

        if valor is None:
            return default

        if isinstance(
            valor,
            str
        ):

            valor = valor.strip()

            if not valor:
                return default

            valor = valor.replace(
                ",",
                "."
            )

        return float(valor)

    except Exception:

        return default


def interpolar_score(
    valor,
    puntos
):

    if valor is None:
        return 0.0

    puntos = sorted(
        puntos
    )

    if valor <= puntos[0][0]:
        return float(
            puntos[0][1]
        )

    if valor >= puntos[-1][0]:
        return float(
            puntos[-1][1]
        )

    for i in range(
        len(puntos) - 1
    ):

        x1, y1 = puntos[i]
        x2, y2 = puntos[i + 1]

        if x1 <= valor <= x2:

            if x2 == x1:
                return float(y1)

            fraccion = (
                (valor - x1)
                / (x2 - x1)
            )

            return float(
                y1
                + fraccion
                * (y2 - y1)
            )

    return 0.0


def nivel_desde_riesgo(
    riesgo
):

    if riesgo >= 75:
        return "ROJO"

    if riesgo >= 50:
        return "NARANJA"

    if riesgo >= 25:
        return "AMARILLO"

    return "VERDE"


# ============================================================
# OPEN-METEO
# ============================================================

@st.cache_data(
    ttl=900,
    show_spinner=False
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

    # --------------------------------------------------------
    # INTENTO ECMWF
    # --------------------------------------------------------

    variables = ",".join([
        "precipitation",
        "soil_moisture_0_to_7cm",
        "soil_moisture_7_to_28cm",
        "soil_moisture_28_to_100cm",
        "runoff",
        "wind_gusts_10m",
    ])

    params = {

        "latitude":
            latitudes,

        "longitude":
            longitudes,

        "hourly":
            variables,

        "past_days":
            3,

        "forecast_days":
            7,

        "timezone":
            "America/Argentina/Buenos_Aires",

        "wind_speed_unit":
            "kmh",

        "precipitation_unit":
            "mm",
    }

    errores = []

    try:

        response = requests.get(
            OPEN_METEO_ECMWF,
            params=params,
            timeout=60,
        )

        if response.status_code == 200:

            data = response.json()

            if isinstance(
                data,
                dict
            ):

                data = [data]

            if len(data) == len(NODOS):

                return {
                    "datos": data,
                    "fuente":
                        "ECMWF IFS / Open-Meteo",
                    "modo":
                        "completo",
                    "error":
                        None,
                }

        errores.append(
            "ECMWF: "
            + response.text[:500]
        )

    except Exception as exc:

        errores.append(
            f"ECMWF: {exc}"
        )

    # --------------------------------------------------------
    # FALLBACK GENERAL
    # --------------------------------------------------------

    variables_fallback = ",".join([
        "precipitation",
        "soil_moisture_0_to_1cm",
        "soil_moisture_1_to_3cm",
        "soil_moisture_3_to_9cm",
        "soil_moisture_9_to_27cm",
        "soil_moisture_27_to_81cm",
        "wind_gusts_10m",
    ])

    params_fallback = {

        "latitude":
            latitudes,

        "longitude":
            longitudes,

        "hourly":
            variables_fallback,

        "past_days":
            3,

        "forecast_days":
            7,

        "timezone":
            "America/Argentina/Buenos_Aires",

        "wind_speed_unit":
            "kmh",

        "precipitation_unit":
            "mm",
    }

    try:

        response = requests.get(
            OPEN_METEO_GENERAL,
            params=params_fallback,
            timeout=60,
        )

        if response.status_code == 200:

            data = response.json()

            if isinstance(
                data,
                dict
            ):

                data = [data]

            if len(data) == len(NODOS):

                return {
                    "datos": data,
                    "fuente":
                        "Open-Meteo Weather API",
                    "modo":
                        "respaldo",
                    "error":
                        None,
                }

        errores.append(
            "Open-Meteo general: "
            + response.text[:500]
        )

    except Exception as exc:

        errores.append(
            f"Open-Meteo general: {exc}"
        )

    raise RuntimeError(
        "No fue posible obtener datos meteorológicos.\n\n"
        + "\n".join(errores)
    )


# ============================================================
# TIEMPOS
# ============================================================

def obtener_datetime(
    texto
):

    try:

        dt = datetime.fromisoformat(
            texto
        )

        if dt.tzinfo is None:

            dt = dt.replace(
                tzinfo=TZ_ARG
            )

        return dt

    except Exception:

        return None


def serie_horaria(
    api_node,
    variable
):

    hourly = api_node.get(
        "hourly",
        {}
    )

    tiempos = hourly.get(
        "time",
        []
    )

    valores = hourly.get(
        variable,
        []
    )

    resultado = []

    for tiempo, valor in zip(
        tiempos,
        valores
    ):

        dt = obtener_datetime(
            tiempo
        )

        valor = safe_float(
            valor
        )

        if (
            dt is not None
            and valor is not None
        ):

            resultado.append(
                (
                    dt,
                    valor
                )
            )

    return resultado


def sumar_periodo(
    serie,
    inicio,
    fin
):

    if not serie:
        return 0.0

    return sum(
        valor
        for dt, valor in serie
        if inicio <= dt <= fin
    )


def suma_pasadas(
    serie,
    horas
):

    ahora_local = ahora()

    inicio = (
        ahora_local
        - timedelta(
            hours=horas
        )
    )

    return sumar_periodo(
        serie,
        inicio,
        ahora_local
        + timedelta(hours=1)
    )


def suma_futuras(
    serie,
    horas
):

    ahora_local = ahora()

    fin = (
        ahora_local
        + timedelta(
            hours=horas
        )
    )

    return sumar_periodo(
        serie,
        ahora_local,
        fin
    )


def ultimo_valor(
    serie
):

    if not serie:
        return None

    return sorted(
        serie,
        key=lambda x: x[0]
    )[-1][1]


def maximo_serie(
    serie
):

    if not serie:
        return None

    return max(
        valor
        for _, valor in serie
    )


# ============================================================
# HUMEDAD ECMWF
# ============================================================

def calcular_humedad_principal(
    api_node
):

    h1 = ultimo_valor(
        serie_horaria(
            api_node,
            "soil_moisture_0_to_7cm"
        )
    )

    h2 = ultimo_valor(
        serie_horaria(
            api_node,
            "soil_moisture_7_to_28cm"
        )
    )

    h3 = ultimo_valor(
        serie_horaria(
            api_node,
            "soil_moisture_28_to_100cm"
        )
    )

    disponibles = [
        (h1, 0.45),
        (h2, 0.35),
        (h3, 0.20),
    ]

    disponibles = [
        item
        for item in disponibles
        if item[0] is not None
    ]

    if not disponibles:

        return (
            None,
            None,
            None,
            None
        )

    suma_pesos = sum(
        peso
        for _, peso
        in disponibles
    )

    perfil = sum(
        valor * peso
        for valor, peso
        in disponibles
    ) / suma_pesos

    h1_pct = (
        min(
            100,
            max(
                0,
                h1 / 0.45 * 100
            )
        )
        if h1 is not None
        else None
    )

    h2_pct = (
        min(
            100,
            max(
                0,
                h2 / 0.45 * 100
            )
        )
        if h2 is not None
        else None
    )

    h3_pct = (
        min(
            100,
            max(
                0,
                h3 / 0.40 * 100
            )
        )
        if h3 is not None
        else None
    )

    perfil_pct = min(
        100,
        max(
            0,
            (
                perfil
                / 0.45
            )
            * 100
        )
    )

    return (
        h1_pct,
        h2_pct,
        h3_pct,
        perfil_pct
    )


# ============================================================
# HUMEDAD FALLBACK
# ============================================================

def calcular_humedad_fallback(
    api_node
):

    capas = [
        (
            "soil_moisture_0_to_1cm",
            0.15
        ),
        (
            "soil_moisture_1_to_3cm",
            0.10
        ),
        (
            "soil_moisture_3_to_9cm",
            0.20
        ),
        (
            "soil_moisture_9_to_27cm",
            0.30
        ),
        (
            "soil_moisture_27_to_81cm",
            0.25
        ),
    ]

    valores = []

    pesos = []

    for variable, peso in capas:

        valor = ultimo_valor(
            serie_horaria(
                api_node,
                variable
            )
        )

        if valor is not None:

            valores.append(
                valor
            )

            pesos.append(
                peso
            )

    if not valores:
        return None

    humedad_media = (
        sum(
            valor * peso
            for valor, peso
            in zip(
                valores,
                pesos
            )
        )
        / sum(pesos)
    )

    return min(
        100,
        max(
            0,
            humedad_media
            / 0.45
            * 100
        )
    )


# ============================================================
# SCORES
# ============================================================

def score_lluvia_antecedente(
    lluvia
):

    return interpolar_score(
        lluvia,
        [
            (0, 0),
            (10, 3),
            (25, 8),
            (50, 15),
            (100, 22),
            (150, 25),
        ]
    )


def score_lluvia_pronostico(
    lluvia
):

    return interpolar_score(
        lluvia,
        [
            (0, 0),
            (10, 3),
            (25, 7),
            (50, 15),
            (100, 22),
            (150, 25),
        ]
    )


def score_humedad(
    humedad
):

    return interpolar_score(
        humedad,
        [
            (0, 0),
            (40, 3),
            (60, 8),
            (70, 12),
            (80, 16),
            (90, 19),
            (100, 20),
        ]
    )


def score_runoff(
    runoff
):

    return interpolar_score(
        runoff,
        [
            (0, 0),
            (5, 2),
            (10, 4),
            (25, 7),
            (50, 10),
        ]
    )


def score_vulnerabilidad(
    vulnerabilidad
):

    return min(
        10,
        max(
            0,
            vulnerabilidad / 10
        )
    )


# ============================================================
# EVALUACIÓN
# ============================================================

def evaluar_nodo(
    node,
    api_node,
    modo
):

    precip = serie_horaria(
        api_node,
        "precipitation"
    )

    lluvia_24 = suma_pasadas(
        precip,
        24
    )

    lluvia_72 = suma_pasadas(
        precip,
        72
    )

    lluvia_futura_24 = suma_futuras(
        precip,
        24
    )

    lluvia_futura_72 = suma_futuras(
        precip,
        72
    )

    lluvia_futura_7d = suma_futuras(
        precip,
        168
    )

    # --------------------------------------------------------
    # HUMEDAD
    # --------------------------------------------------------

    h1 = None
    h2 = None
    h3 = None

    if modo == "completo":

        (
            h1,
            h2,
            h3,
            humedad_perfil
        ) = calcular_humedad_principal(
            api_node
        )

    else:

        humedad_perfil = (
            calcular_humedad_fallback(
                api_node
            )
        )

    # --------------------------------------------------------
    # RUNOFF
    # --------------------------------------------------------

    runoff = serie_horaria(
        api_node,
        "runoff"
    )

    runoff_disponible = bool(
        runoff
    )

    runoff_72 = suma_pasadas(
        runoff,
        72
    )

    runoff_futuro_72 = suma_futuras(
        runoff,
        72
    )

    # Utilizamos el máximo de antecedente
    # y pronóstico para evitar sumar dos períodos
    # que pueden representar el mismo fenómeno.

    runoff_representativo = max(
        runoff_72,
        runoff_futuro_72
    )

    # --------------------------------------------------------
    # VIENTO
    # --------------------------------------------------------

    wind = serie_horaria(
        api_node,
        "wind_gusts_10m"
    )

    rafaga_max = maximo_serie(
        wind
    )

    # --------------------------------------------------------
    # COMPONENTES
    # --------------------------------------------------------

    componentes = {

        "lluvia_antecedente":
            score_lluvia_antecedente(
                lluvia_72
            ),

        "lluvia_pronostico":
            score_lluvia_pronostico(
                lluvia_futura_72
            ),

        "humedad":
            score_humedad(
                humedad_perfil
            ),

        "vulnerabilidad":
            score_vulnerabilidad(
                VULNERABILIDAD_BASE.get(
                    node["nombre"],
                    50
                )
            ),
    }

    pesos_base = {

        "lluvia_antecedente":
            25,

        "lluvia_pronostico":
            25,

        "humedad":
            20,

        "vulnerabilidad":
            10,
    }

    # --------------------------------------------------------
    # RUNOFF
    # --------------------------------------------------------

    if runoff_disponible:

        componentes["runoff"] = (
            score_runoff(
                runoff_representativo
            )
        )

        pesos_base["runoff"] = 10

    # --------------------------------------------------------
    # REDISTRIBUCIÓN
    #
    # Las puntuaciones están expresadas sobre
    # su peso original.
    #
    # Si falta runoff, se escala cada componente
    # disponible para conservar el rango final 0–100.
    # --------------------------------------------------------

    maximos_componentes = {

        "lluvia_antecedente":
            25,

        "lluvia_pronostico":
            25,

        "humedad":
            20,

        "vulnerabilidad":
            10,
    }

    if runoff_disponible:

        maximos_componentes[
            "runoff"
        ] = 10

    suma_maximos = sum(
        maximos_componentes[
            clave
        ]
        for clave in componentes
    )

    suma_scores = sum(
        componentes[
            clave
        ]
        for clave in componentes
    )

    if suma_maximos > 0:

        riesgo = (
            suma_scores
            / suma_maximos
            * 100
        )

    else:

        riesgo = 0

    riesgo = round(
        min(
            100,
            max(
                0,
                riesgo
            )
        )
    )

    nivel = nivel_desde_riesgo(
        riesgo
    )

    # --------------------------------------------------------
    # CALIDAD DE DATOS
    # --------------------------------------------------------

    calidad = 0

    # Precipitación: 30
    if len(precip) >= 24:

        calidad += 30

    elif precip:

        calidad += 20

    # Humedad: 30
    if modo == "completo":

        humedad_variables = 0

        for variable in [
            "soil_moisture_0_to_7cm",
            "soil_moisture_7_to_28cm",
            "soil_moisture_28_to_100cm",
        ]:

            if serie_horaria(
                api_node,
                variable
            ):

                humedad_variables += 1

        calidad += round(
            humedad_variables
            / 3
            * 30
        )

    else:

        humedad_variables = 0

        for variable in [
            "soil_moisture_0_to_1cm",
            "soil_moisture_1_to_3cm",
            "soil_moisture_3_to_9cm",
            "soil_moisture_9_to_27cm",
            "soil_moisture_27_to_81cm",
        ]:

            if serie_horaria(
                api_node,
                variable
            ):

                humedad_variables += 1

        calidad += round(
            humedad_variables
            / 5
            * 30
        )

    # Viento: 10
    if wind:

        calidad += 10

    # Runoff: 20
    if runoff_disponible:

        calidad += 20

    calidad = min(
        100,
        round(calidad)
    )

    if calidad >= 85:

        categoria_calidad = "Alta"

    elif calidad >= 65:

        categoria_calidad = "Media"

    else:

        categoria_calidad = "Baja"

    vulnerabilidad = (
        VULNERABILIDAD_BASE.get(
            node["nombre"],
            50
        )
    )

    return {

        "nombre":
            node["nombre"],

        "provincia":
            node["provincia"],

        "lat":
            node["lat"],

        "lon":
            node["lon"],

        "rio":
            node["rio"],

        "zona_alta":
            node["zona_alta"],

        "riesgo":
            riesgo,

        "nivel":
            nivel,

        "lluvia_24h":
            round(
                lluvia_24,
                1
            ),

        "lluvia_72h":
            round(
                lluvia_72,
                1
            ),

        "lluvia_futura_24h":
            round(
                lluvia_futura_24,
                1
            ),

        "lluvia_futura_72h":
            round(
                lluvia_futura_72,
                1
            ),

        "lluvia_futura_7d":
            round(
                lluvia_futura_7d,
                1
            ),

        "humedad_0_7":
            h1,

        "humedad_7_28":
            h2,

        "humedad_28_100":
            h3,

        "humedad_perfil":
            (
                round(
                    humedad_perfil,
                    1
                )
                if humedad_perfil
                is not None
                else None
            ),

        "runoff_disponible":
            runoff_disponible,

        "runoff_72h":
            round(
                runoff_72,
                1
            ),

        "runoff_futuro_72h":
            round(
                runoff_futuro_72,
                1
            ),

        "rafaga_max":
            (
                round(
                    rafaga_max,
                    1
                )
                if rafaga_max
                is not None
                else None
            ),

        "vulnerabilidad":
            vulnerabilidad,

        "score_lluvia_antecedente":
            round(
                componentes[
                    "lluvia_antecedente"
                ],
                1
            ),

        "score_lluvia_pronostico":
            round(
                componentes[
                    "lluvia_pronostico"
                ],
                1
            ),

        "score_humedad":
            round(
                componentes[
                    "humedad"
                ],
                1
            ),

        "score_runoff":
            round(
                componentes.get(
                    "runoff",
                    0
                ),
                1
            ),

        "score_vulnerabilidad":
            round(
                componentes[
                    "vulnerabilidad"
                ],
                1
            ),

        "calidad_datos":
            calidad,

        "categoria_calidad":
            categoria_calidad,

        "modo_meteorologico":
            modo,
    }


# ============================================================
# TELEGRAM
# ============================================================

def obtener_telegram():

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


def enviar_telegram(
    mensaje
):

    token, chat_id = obtener_telegram()

    if not token or not chat_id:

        return False, (
            "Telegram no está configurado."
        )

    url = (
        "https://api.telegram.org/"
        f"bot{token}/sendMessage"
    )

    try:

        response = requests.post(
            url,
            data={
                "chat_id": chat_id,
                "text": mensaje,
            },
            timeout=20,
        )

        if response.status_code != 200:

            return False, (
                response.text[:500]
            )

        return True, (
            "Mensaje enviado correctamente."
        )

    except Exception as exc:

        return False, str(exc)


def construir_mensaje_telegram(
    resultados
):

    ordenados = sorted(
        resultados,
        key=lambda x: x["riesgo"],
        reverse=True
    )

    criticos = [
        r
        for r in ordenados
        if r["nivel"]
        in [
            "ROJO",
            "NARANJA"
        ]
    ]

    lineas = [

        "🌧️ ALERTA LITORAL AGRO V3.2",

        "",

        "Actualización: "
        + ahora().strftime(
            "%d/%m/%Y %H:%M"
        ),

        "",
    ]

    if not criticos:

        lineas.append(
            "No hay nodos ROJO o NARANJA."
        )

    else:

        lineas.append(
            "Nodos de mayor riesgo:"
        )

        for r in criticos[:8]:

            emoji = NIVELES[
                r["nivel"]
            ]["emoji"]

            lineas.append(
                f"{emoji} "
                f"{r['nombre']} — "
                f"{r['riesgo']}/100"
            )

    maximo = ordenados[0]

    lineas.extend([

        "",

        "Máximo regional: "
        f"{maximo['riesgo']}/100 — "
        f"{maximo['nombre']}",

        "",

        "Sistema experimental.",

        "No reemplaza alertas oficiales.",
    ])

    return "\n".join(
        lineas
    )


# ============================================================
# HISTORIAL
# ============================================================

def actualizar_historial(
    resultados
):

    if (
        "historial"
        not in st.session_state
    ):

        st.session_state.historial = []

    if not resultados:

        return

    maximo = max(
        resultados,
        key=lambda x: x["riesgo"]
    )

    firma = (
        ahora().strftime(
            "%Y-%m-%d-%H-%M"
        )
        + "-"
        + str(
            maximo["riesgo"]
        )
        + "-"
        + maximo["nombre"]
    )

    if (
        st.session_state.get(
            "ultima_firma_historial"
        )
        == firma
    ):

        return

    st.session_state.historial.append({

        "fecha":
            ahora().strftime(
                "%Y-%m-%d %H:%M"
            ),

        "nodo_maximo":
            maximo["nombre"],

        "provincia":
            maximo["provincia"],

        "riesgo_maximo":
            maximo["riesgo"],

        "nivel":
            maximo["nivel"],

        "calidad_datos":
            maximo["calidad_datos"],
    })

    st.session_state[
        "ultima_firma_historial"
    ] = firma


# ============================================================
# MAPA
# ============================================================

def crear_mapa(
    resultados
):

    mapa = folium.Map(

        location=[
            -31.0,
            -59.5
        ],

        zoom_start=6,

        tiles="OpenStreetMap",

        control_scale=True,

        prefer_canvas=True,
    )

    # --------------------------------------------------------
    # CAPAS DE REFERENCIA
    # --------------------------------------------------------

    grupos = {}

    for provincia in [
        "Corrientes",
        "Santa Fe",
        "Entre Ríos",
    ]:

        grupos[provincia] = folium.FeatureGroup(
            name=provincia,
            show=True
        )

        grupos[provincia].add_to(
            mapa
        )

    # --------------------------------------------------------
    # NODOS
    # --------------------------------------------------------

    for r in resultados:

        nivel = r["nivel"]

        info = NIVELES[
            nivel
        ]

        popup = f"""
        <div style="width:300px">

        <h4>
        {info['emoji']}
        {r['nombre']}
        </h4>

        <b>Provincia:</b>
        {r['provincia']}<br>

        <b>Índice:</b>
        {r['riesgo']}/100<br>

        <b>Estado:</b>
        {nivel}<br>

        <hr>

        <b>Lluvia 24 h:</b>
        {r['lluvia_24h']} mm<br>

        <b>Lluvia 72 h:</b>
        {r['lluvia_72h']} mm<br>

        <b>Pronóstico 72 h:</b>
        {r['lluvia_futura_72h']} mm<br>

        <b>Pronóstico 7 días:</b>
        {r['lluvia_futura_7d']} mm<br>

        <b>Humedad perfil:</b>
        {
            r['humedad_perfil']
            if r['humedad_perfil']
            is not None
            else 'N/D'
        } %<br>

        <b>Runoff:</b>
        {
            str(r['runoff_72h'])
            + ' mm'
            if r['runoff_disponible']
            else 'N/D'
        }<br>

        <b>Vulnerabilidad:</b>
        {r['vulnerabilidad']}/100<br>

        <b>Calidad:</b>
        {r['calidad_datos']}/100

        </div>
        """

        marker = folium.CircleMarker(

            location=[
                r["lat"],
                r["lon"]
            ],

            radius=11,

            color=info["color"],

            fill=True,

            fill_color=info["color"],

            fill_opacity=0.85,

            popup=folium.Popup(
                popup,
                max_width=350
            ),

            tooltip=(
                f"{info['emoji']} "
                f"{r['nombre']} — "
                f"{r['riesgo']}/100"
            ),
        )

        marker.add_to(
            grupos[
                r["provincia"]
            ]
        )

    folium.LayerControl(
        collapsed=False
    ).add_to(
        mapa
    )

    return mapa


# ============================================================
# DATAFRAME
# ============================================================

def dataframe_resultados(
    resultados
):

    filas = []

    for r in resultados:

        filas.append({

            "Provincia":
                r["provincia"],

            "Nodo":
                r["nombre"],

            "Riesgo":
                r["riesgo"],

            "Estado":
                (
                    NIVELES[
                        r["nivel"]
                    ]["emoji"]
                    + " "
                    + r["nivel"]
                ),

            "Lluvia 24h (mm)":
                r["lluvia_24h"],

            "Lluvia 72h (mm)":
                r["lluvia_72h"],

            "Pronóstico 72h (mm)":
                r["lluvia_futura_72h"],

            "Pronóstico 7d (mm)":
                r["lluvia_futura_7d"],

            "Humedad perfil (%)":
                r["humedad_perfil"],

            "Runoff 72h (mm)":
                (
                    r["runoff_72h"]
                    if r["runoff_disponible"]
                    else None
                ),

            "Vulnerabilidad":
                r["vulnerabilidad"],

            "Calidad":
                r["calidad_datos"],
        })

    return pd.DataFrame(
        filas
    )


# ============================================================
# CSS
# ============================================================

st.markdown(
    """
    <style>

    .titulo {
        font-size: 2.2rem;
        font-weight: 700;
        margin-bottom: 0.2rem;
    }

    .subtitulo {
        color: #666;
        font-size: 1rem;
        margin-bottom: 1rem;
    }

    .card {
        padding: 18px;
        border-radius: 12px;
        border: 1px solid #ddd;
        background-color: #ffffff;
        margin-bottom: 10px;
    }

    .indice {
        font-size: 2.4rem;
        font-weight: 700;
    }

    </style>
    """,
    unsafe_allow_html=True
)


# ============================================================
# ENCABEZADO
# ============================================================

st.markdown(
    '<div class="titulo">'
    '🌧️ Alerta Litoral Agro V3.2'
    '</div>',
    unsafe_allow_html=True
)

st.markdown(
    '<div class="subtitulo">'
    'Sistema experimental de alerta temprana para '
    'riesgo de anegamiento agropecuario · '
    'Santa Fe · Corrientes · Entre Ríos'
    '</div>',
    unsafe_allow_html=True
)

st.warning(
    "⚠️ Sistema experimental. "
    "El índice 0–100 es un indicador compuesto "
    "de screening y no reemplaza las alertas oficiales."
)


# ============================================================
# SIDEBAR
# ============================================================

with st.sidebar:

    st.header(
        "⚙️ Control"
    )

    st.write(
        "Fuente meteorológica:"
    )

    st.write(
        "🌦️ ECMWF IFS / Open-Meteo"
    )

    st.write(
        "La aplicación posee "
        "un mecanismo de respaldo."
    )

    st.divider()

    if st.button(
        "🔄 Actualizar datos",
        use_container_width=True
    ):

        consultar_open_meteo.clear()

        st.rerun()

    st.divider()

    st.subheader(
        "📡 Estado de fuentes"
    )

    st.write(
        "🌦️ Meteorología: Open-Meteo"
    )

    st.write(
        "🌊 Hidrología externa: desactivada"
    )

    st.write(
        "🗺️ Vulnerabilidad: "
        "screening experimental"
    )

    st.divider()

    token, chat_id = obtener_telegram()

    if token and chat_id:

        st.success(
            "Telegram configurado"
        )

    else:

        st.info(
            "Telegram no configurado"
        )


# ============================================================
# DATOS
# ============================================================

try:

    with st.spinner(
        "Consultando datos meteorológicos..."
    ):

        respuesta = (
            consultar_open_meteo()
        )

except Exception as exc:

    st.error(
        "### ❌ NO FUE POSIBLE ACTUALIZAR "
        "LOS DATOS METEOROLÓGICOS"
    )

    st.code(
        str(exc)
    )

    st.info(
        "La aplicación detuvo el cálculo "
        "para evitar mostrar un índice "
        "sin datos válidos."
    )

    st.stop()


meteorologia = respuesta[
    "datos"
]

fuente_meteorologica = respuesta[
    "fuente"
]

modo_meteorologico = respuesta[
    "modo"
]


# ============================================================
# EVALUACIÓN
# ============================================================

resultados = []

for node, api_node in zip(
    NODOS,
    meteorologia
):

    try:

        resultado = evaluar_nodo(
            node,
            api_node,
            modo_meteorologico
        )

        resultados.append(
            resultado
        )

    except Exception as exc:

        st.warning(
            f"No fue posible evaluar "
            f"{node['nombre']}: {exc}"
        )


if not resultados:

    st.error(
        "No fue posible generar evaluaciones."
    )

    st.stop()


# ============================================================
# HISTORIAL
# ============================================================

actualizar_historial(
    resultados
)


# ============================================================
# FUENTE
# ============================================================

if modo_meteorologico == "completo":

    st.success(
        "🟢 Datos obtenidos mediante "
        "ECMWF IFS / Open-Meteo."
    )

else:

    st.warning(
        "🟡 Se utiliza el modo meteorológico "
        "de respaldo de Open-Meteo. "
        "Runoff no disponible en este modo."
    )


# ============================================================
# RESUMEN
# ============================================================

maximo = max(
    resultados,
    key=lambda x: x["riesgo"]
)

rojos = sum(
    1
    for r in resultados
    if r["nivel"] == "ROJO"
)

naranjas = sum(
    1
    for r in resultados
    if r["nivel"] == "NARANJA"
)

amarillos = sum(
    1
    for r in resultados
    if r["nivel"] == "AMARILLO"
)

verdes = sum(
    1
    for r in resultados
    if r["nivel"] == "VERDE"
)


# ============================================================
# MÉTRICAS
# ============================================================

c1, c2, c3, c4, c5 = st.columns(5)

with c1:

    st.metric(
        "Máximo regional",
        f"{maximo['riesgo']}/100"
    )

with c2:

    st.metric(
        "🔴 Rojo",
        rojos
    )

with c3:

    st.metric(
        "🟠 Naranja",
        naranjas
    )

with c4:

    st.metric(
        "🟡 Amarillo",
        amarillos
    )

with c5:

    st.metric(
        "🟢 Verde",
        verdes
    )


st.caption(
    "Actualización: "
    + ahora().strftime(
        "%d/%m/%Y %H:%M:%S"
    )
    + " ART"
)


# ============================================================
# MÁXIMO REGIONAL
# ============================================================

info_max = NIVELES[
    maximo["nivel"]
]

st.markdown(
    f"""
    <div class="card">

    <div class="indice">
    {info_max['emoji']}
    {maximo['riesgo']}/100
    </div>

    <h3>
    {maximo['nombre']} —
    {info_max['descripcion']}
    </h3>

    <p>

    Provincia:
    {maximo['provincia']}<br>

    Curso:
    {maximo['rio']}<br>

    Vulnerabilidad territorial:
    {maximo['vulnerabilidad']}/100<br>

    Calidad de datos:
    {maximo['calidad_datos']}/100
    ({maximo['categoria_calidad']})

    </p>

    </div>
    """,
    unsafe_allow_html=True
)


# ============================================================
# MAPA
# ============================================================

st.header(
    "🗺️ Mapa integrado de riesgo"
)

st.caption(
    "Mapa base OpenStreetMap. "
    "Los marcadores representan nodos "
    "de monitoreo meteorológico."
)

mapa = crear_mapa(
    resultados
)

try:

    st_folium(
        mapa,
        width=None,
        height=650,
        returned_objects=[]
    )

except Exception as exc:

    st.warning(
        "El mapa interactivo no pudo "
        "cargarse en el navegador."
    )

    st.caption(
        f"Detalle técnico: {exc}"
    )

    st.info(
        "Los datos meteorológicos y "
        "el índice continúan disponibles "
        "en la tabla inferior."
    )


# ============================================================
# TABLA
# ============================================================

st.header(
    "📊 Estado de los nodos"
)

df = dataframe_resultados(
    resultados
)

st.dataframe(
    df,
    use_container_width=True,
    hide_index=True
)


# ============================================================
# DETALLE
# ============================================================

st.header(
    "🔎 Detalle por nodo"
)

for r in sorted(
    resultados,
    key=lambda x: x["riesgo"],
    reverse=True
):

    nivel = r["nivel"]

    with st.expander(
        f"{NIVELES[nivel]['emoji']} "
        f"{r['nombre']} — "
        f"{r['riesgo']}/100"
    ):

        c1, c2, c3 = st.columns(3)

        with c1:

            st.metric(
                "Índice",
                f"{r['riesgo']}/100"
            )

            st.metric(
                "Lluvia 24 h",
                f"{r['lluvia_24h']} mm"
            )

            st.metric(
                "Lluvia 72 h",
                f"{r['lluvia_72h']} mm"
            )

        with c2:

            st.metric(
                "Pronóstico 72 h",
                f"{r['lluvia_futura_72h']} mm"
            )

            st.metric(
                "Pronóstico 7 días",
                f"{r['lluvia_futura_7d']} mm"
            )

            st.metric(
                "Humedad perfil",
                (
                    f"{r['humedad_perfil']} %"
                    if r["humedad_perfil"]
                    is not None
                    else "N/D"
                )
            )

        with c3:

            st.metric(
                "Vulnerabilidad",
                f"{r['vulnerabilidad']}/100"
            )

            st.metric(
                "Calidad",
                f"{r['calidad_datos']}/100"
            )

            st.metric(
                "Ráfaga máxima",
                (
                    f"{r['rafaga_max']} km/h"
                    if r["rafaga_max"]
                    is not None
                    else "N/D"
                )
            )

        st.markdown(
            "#### Componentes del índice"
        )

        componentes = pd.DataFrame({

            "Componente": [

                "Lluvia antecedente",

                "Lluvia pronosticada",

                "Humedad del perfil",

                "Runoff",

                "Vulnerabilidad territorial",
            ],

            "Puntaje": [

                r[
                    "score_lluvia_antecedente"
                ],

                r[
                    "score_lluvia_pronostico"
                ],

                r[
                    "score_humedad"
                ],

                (
                    r["score_runoff"]
                    if r["runoff_disponible"]
                    else None
                ),

                r[
                    "score_vulnerabilidad"
                ],
            ]
        })

        st.dataframe(
            componentes,
            use_container_width=True,
            hide_index=True
        )

        st.markdown(
            "#### 🚜 Acciones orientativas"
        )

        for accion in ACCIONES[
            nivel
        ]:

            st.write(
                "• " + accion
            )


# ============================================================
# TELEGRAM
# ============================================================

st.header(
    "📲 Alertas Telegram"
)

st.write(
    "El envío es manual para evitar "
    "spam automático durante cada actualización."
)

token, chat_id = obtener_telegram()

if token and chat_id:

    if st.button(
        "📲 Enviar resumen a Telegram",
        use_container_width=True
    ):

        mensaje = (
            construir_mensaje_telegram(
                resultados
            )
        )

        ok, detalle = enviar_telegram(
            mensaje
        )

        if ok:

            st.success(
                detalle
            )

        else:

            st.error(
                detalle
            )

else:

    st.info(
        "Telegram está desactivado porque "
        "no existen credenciales configuradas."
    )


# ============================================================
# HISTORIAL
# ============================================================

st.header(
    "📚 Historial"
)

historial = st.session_state.get(
    "historial",
    []
)

if historial:

    df_historial = pd.DataFrame(
        historial
    )

    st.dataframe(
        df_historial,
        use_container_width=True,
        hide_index=True
    )

    csv = (
        df_historial
        .to_csv(
            index=False
        )
        .encode(
            "utf-8"
        )
    )

    st.download_button(

        "⬇️ Descargar historial CSV",

        data=csv,

        file_name=(
            "historial_alerta_litoral_agro.csv"
        ),

        mime="text/csv",

        use_container_width=True
    )

else:

    st.info(
        "Todavía no existen registros."
    )


# ============================================================
# CONTROL DE CALIDAD
# ============================================================

st.header(
    "🧪 Control de calidad"
)

calidad_promedio = round(
    sum(
        r["calidad_datos"]
        for r in resultados
    )
    / len(resultados)
)

if calidad_promedio >= 85:

    st.success(
        f"Calidad promedio: "
        f"{calidad_promedio}/100 — Alta"
    )

elif calidad_promedio >= 65:

    st.warning(
        f"Calidad promedio: "
        f"{calidad_promedio}/100 — Media"
    )

else:

    st.error(
        f"Calidad promedio: "
        f"{calidad_promedio}/100 — Baja"
    )


st.write(
    """
El control de calidad verifica la disponibilidad
de las series meteorológicas utilizadas por el
sistema. No constituye una validación estadística
del pronóstico.
"""
)


# ============================================================
# METODOLOGÍA
# ============================================================

st.header(
    "📐 Metodología"
)

st.markdown(
    """
### Índice integrado 0–100

El sistema combina:

| Componente | Peso máximo |
|---|---:|
| Precipitación antecedente 72 h | 25 |
| Precipitación pronosticada 72 h | 25 |
| Humedad del perfil | 20 |
| Escorrentía / runoff | 10 |
| Vulnerabilidad territorial | 10 |
| **Total** | **100** |

Cuando runoff no está disponible, el índice se
normaliza sobre los componentes disponibles para
mantener una escala comparable de 0–100.

### Semáforo

- 🟢 **0–24:** riesgo bajo
- 🟡 **25–49:** vigilancia
- 🟠 **50–74:** riesgo elevado
- 🔴 **75–100:** riesgo muy elevado

### Interpretación

El índice es un **indicador compuesto de screening**.

No representa:

- una probabilidad de inundación;
- una altura de agua;
- una predicción determinista;
- una estimación exacta de daños.

Su objetivo es identificar sectores donde la
combinación de lluvia antecedente, lluvia prevista,
humedad del suelo y vulnerabilidad territorial
justifica mayor vigilancia.

### Humedad

En modo ECMWF se utilizan:

- 0–7 cm;
- 7–28 cm;
- 28–100 cm.

En modo de respaldo se utilizan capas de suelo
disponibles en el endpoint meteorológico general.

La humedad se expresa como un índice normalizado
operacional y no como una medición directa de
saturación hidrológica.

### Vulnerabilidad territorial

La vulnerabilidad actual es experimental.

Una futura versión profesional debería reemplazar
este parámetro por capas GIS derivadas de:

- elevación;
- pendiente;
- tipo de suelo;
- uso del suelo;
- humedales;
- proximidad a cauces;
- zonas históricas de anegamiento;
- infraestructura productiva expuesta.
"""
)


# ============================================================
# FUENTES
# ============================================================

st.header(
    "📡 Fuentes de información"
)

st.markdown(
    """
**Meteorología principal:** ECMWF IFS mediante
Open-Meteo.

**Meteorología de respaldo:** Open-Meteo Weather API.

**Mapa base:** OpenStreetMap.

**Hidrología:** no integrada en V3.2.

La eliminación de la fuente hidrológica externa es
intencional para evitar que una falla de autenticación
o disponibilidad de un servicio secundario impida
la generación del componente meteorológico.
"""
)


# ============================================================
# LIMITACIONES
# ============================================================

st.header(
    "⚠️ Limitaciones"
)

st.markdown(
    """
1. El sistema es experimental.

2. El índice 0–100 no es una probabilidad.

3. Los datos meteorológicos son productos de modelos
   numéricos y pueden diferir de mediciones locales.

4. La humedad del suelo es modelada.

5. La vulnerabilidad territorial actual es un
   parámetro de screening y no una capa GIS oficial.

6. V3.2 no incorpora niveles de ríos ni estaciones
   hidrométricas.

7. Telegram es únicamente un canal de distribución.

8. El mapa es una herramienta de visualización y no
   constituye una capa oficial de riesgo.

9. El sistema no reemplaza información ni alertas
   emitidas por organismos oficiales.
"""
)


# ============================================================
# ESTADO OPERATIVO
# ============================================================

st.header(
    "🟢 Estado del sistema"
)

st.success(
    "Sistema meteorológico operativo."
)

st.write(
    f"Fuente utilizada: "
    f"**{fuente_meteorologica}**"
)

st.write(
    f"Nodos procesados: "
    f"**{len(resultados)} / {len(NODOS)}**"
)

st.write(
    f"Calidad promedio: "
    f"**{calidad_promedio}/100**"
)


# ============================================================
# PIE
# ============================================================

st.divider()

st.caption(
    "Alerta Litoral Agro V3.2 · "
    "Prototipo experimental de alerta temprana "
    "para riesgo de anegamiento agropecuario."
)

st.caption(
    "Actualizado: "
    + ahora().strftime(
        "%d/%m/%Y %H:%M:%S"
    )
    + " ART"
)
