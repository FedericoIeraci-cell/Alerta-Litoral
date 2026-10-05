# ============================================================
# ALERTA LITORAL AGRO — V3.0
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
# Componentes:
#   - Índice integrado de riesgo 0–100
#   - Precipitación antecedente
#   - Precipitación pronosticada
#   - Humedad del perfil de suelo
#   - Escorrentía / runoff
#   - Ráfagas de viento
#   - Hidrología INA
#   - Vulnerabilidad territorial de screening
#   - Control de calidad de datos
#   - Historial de evaluaciones
#   - Exportación CSV
#   - Alertas Telegram
#   - Metodología y limitaciones
#
# IMPORTANTE:
#   Este sistema es experimental y NO reemplaza alertas oficiales.
# ============================================================

import math
import json
import requests
import pandas as pd
import streamlit as st
import folium

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from streamlit_folium import st_folium


# ============================================================
# CONFIGURACIÓN GENERAL
# ============================================================

st.set_page_config(
    page_title="Alerta Litoral Agro V3.0",
    page_icon="🌧️",
    layout="wide",
    initial_sidebar_state="expanded",
)


TZ_ARG = ZoneInfo("America/Argentina/Buenos_Aires")

OPEN_METEO_URL = "https://api.open-meteo.com/v1/ecmwf"

INA_WFS_URL = "https://alerta.ina.gob.ar/geoserver/public2/ows"

INA_LAYER = "public2:ultimas_alturas"


# ============================================================
# NODOS DE MONITOREO
# ============================================================

NODOS = [
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
#
# Escala 0–100.
#
# NO es una clasificación oficial ni catastral.
# Es un parámetro experimental de screening territorial.
#
# En una futura versión puede reemplazarse por:
#   - DEM
#   - pendiente
#   - uso/cobertura del suelo
#   - tipo de suelo
#   - proximidad a cauces
#   - humedales
#   - zonas históricas de anegamiento
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


ACCIONES = {
    "VERDE": [
        "Monitoreo meteorológico normal.",
        "Mantener seguimiento de lluvias y humedad del suelo.",
    ],
    "AMARILLO": [
        "Vigilar bajos, drenajes y sectores con antecedentes de anegamiento.",
        "Revisar la evolución de las precipitaciones.",
        "Evitar acumular tareas sensibles en sectores bajos si aumenta la lluvia.",
    ],
    "NARANJA": [
        "Preparar medidas preventivas para hacienda y maquinaria.",
        "Revisar drenajes, alcantarillas y accesos internos.",
        "Anticipar tareas productivas que puedan verse afectadas.",
        "Controlar la evolución hidrológica de los cursos cercanos.",
    ],
    "ROJO": [
        "Evaluar movimiento preventivo de hacienda hacia sectores altos.",
        "Proteger maquinaria, insumos y equipos en zonas bajas.",
        "Inspeccionar drenajes y accesos.",
        "Evitar planificar tareas críticas en sectores vulnerables.",
        "Realizar seguimiento frecuente de la evolución meteorológica e hidrológica.",
    ],
}


# ============================================================
# FUNCIONES GENERALES
# ============================================================

def ahora():
    return datetime.now(TZ_ARG)


def safe_float(value, default=None):
    try:
        if value is None:
            return default

        if isinstance(value, str):
            value = value.strip()

            if value == "":
                return default

            value = value.replace(",", ".")

        return float(value)

    except Exception:
        return default


def distancia_km(lat1, lon1, lat2, lon2):
    """
    Distancia aproximada entre dos coordenadas mediante Haversine.
    """

    r = 6371.0

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

    return 2 * r * math.asin(math.sqrt(a))


def interpolar_score(valor, puntos):
    """
    Interpolación lineal simple.

    puntos:
        [(valor1, score1), (valor2, score2), ...]
    """

    if valor is None:
        return 0.0

    puntos = sorted(puntos)

    if valor <= puntos[0][0]:
        return float(puntos[0][1])

    if valor >= puntos[-1][0]:
        return float(puntos[-1][1])

    for i in range(len(puntos) - 1):

        x1, y1 = puntos[i]
        x2, y2 = puntos[i + 1]

        if x1 <= valor <= x2:

            if x2 == x1:
                return float(y1)

            proporcion = (valor - x1) / (x2 - x1)

            return float(y1 + proporcion * (y2 - y1))

    return 0.0


def nivel_desde_riesgo(riesgo):

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

    variables_horarias = ",".join([
        "precipitation",
        "soil_moisture_0_to_7cm",
        "soil_moisture_7_to_28cm",
        "soil_moisture_28_to_100cm",
        "runoff",
        "wind_gusts_10m",
    ])

    params = {
        "latitude": latitudes,
        "longitude": longitudes,
        "hourly": variables_horarias,
        "past_days": 3,
        "forecast_days": 7,
        "timezone": "America/Argentina/Buenos_Aires",
        "wind_speed_unit": "kmh",
        "precipitation_unit": "mm",
    }

    response = requests.get(
        OPEN_METEO_URL,
        params=params,
        timeout=60,
    )

    if response.status_code != 200:

        detalle = response.text[:1500]

        raise RuntimeError(
            f"Open-Meteo respondió HTTP "
            f"{response.status_code}.\n\n"
            f"Detalle:\n{detalle}"
        )

    data = response.json()

    if not data:
        raise RuntimeError(
            "Open-Meteo devolvió una respuesta vacía."
        )

    # Cuando se consultan múltiples coordenadas,
    # Open-Meteo devuelve una lista de estructuras.
    if isinstance(data, dict):
        data = [data]

    if len(data) != len(NODOS):

        raise RuntimeError(
            "La cantidad de respuestas meteorológicas "
            f"({len(data)}) no coincide con la cantidad "
            f"de nodos ({len(NODOS)})."
        )

    return data


# ============================================================
# PARSEO DE SERIES
# ============================================================

def obtener_hora_local(texto):

    try:

        dt = datetime.fromisoformat(texto)

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=TZ_ARG)

        return dt

    except Exception:
        return None


def serie_horaria(api_node, variable):

    hourly = api_node.get("hourly", {})

    tiempos = hourly.get("time", [])
    valores = hourly.get(variable, [])

    resultado = []

    for tiempo, valor in zip(tiempos, valores):

        dt = obtener_hora_local(tiempo)

        val = safe_float(valor)

        if dt is not None and val is not None:
            resultado.append((dt, val))

    return resultado


def suma_ultimas_horas(serie, horas):

    if not serie:
        return 0.0

    limite = ahora() - timedelta(hours=horas)

    valores = [
        valor
        for dt, valor in serie
        if dt >= limite
        and dt <= ahora() + timedelta(hours=1)
    ]

    return sum(valores)


def suma_futuras_horas(serie, horas):

    if not serie:
        return 0.0

    inicio = ahora()

    fin = inicio + timedelta(hours=horas)

    valores = [
        valor
        for dt, valor in serie
        if dt >= inicio
        and dt <= fin
    ]

    return sum(valores)


def ultimo_valor_disponible(serie):

    if not serie:
        return None

    return sorted(
        serie,
        key=lambda x: x[0]
    )[-1][1]


# ============================================================
# HIDROLOGÍA INA
# ============================================================

def encontrar_valor(diccionario, candidatos):

    """
    Busca una clave entre distintas variantes.
    """

    if not isinstance(diccionario, dict):
        return None

    normalizado = {
        str(k).lower().strip(): v
        for k, v in diccionario.items()
    }

    for candidato in candidatos:

        c = candidato.lower().strip()

        if c in normalizado:
            return normalizado[c]

    # Segunda búsqueda parcial
    for clave, valor in normalizado.items():

        for candidato in candidatos:

            if candidato.lower() in clave:
                return valor

    return None


@st.cache_data(ttl=900, show_spinner=False)
def consultar_ina():

    params = {
        "service": "WFS",
        "version": "1.0.0",
        "request": "GetFeature",
        "typeName": INA_LAYER,
        "outputFormat": "application/json",
        "srsName": "EPSG:4326",
        "maxFeatures": 5000,
    }

    try:

        response = requests.get(
            INA_WFS_URL,
            params=params,
            timeout=45,
        )

        if response.status_code != 200:

            return {
                "ok": False,
                "error": (
                    f"INA HTTP {response.status_code}: "
                    f"{response.text[:500]}"
                ),
                "features": [],
            }

        data = response.json()

        features = data.get(
            "features",
            []
        )

        return {
            "ok": True,
            "error": None,
            "features": features,
        }

    except Exception as exc:

        return {
            "ok": False,
            "error": str(exc),
            "features": [],
        }


def procesar_hidrologia(features):

    estaciones = []

    for feature in features:

        properties = feature.get(
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

        lon = safe_float(coords[0])
        lat = safe_float(coords[1])

        if lat is None or lon is None:
            continue

        nombre = encontrar_valor(
            properties,
            [
                "name",
                "nombre",
                "estacion",
                "estación",
                "nombre_estacion",
                "nombre_estación",
                "station",
            ],
        )

        nivel = encontrar_valor(
            properties,
            [
                "altura",
                "nivel",
                "altura_actual",
                "nivel_actual",
                "valor",
                "value",
            ],
        )

        nivel_24h = encontrar_valor(
            properties,
            [
                "altura_24h",
                "altura24h",
                "nivel_24h",
                "nivel24h",
                "valor_24h",
            ],
        )

        fecha = encontrar_valor(
            properties,
            [
                "fecha",
                "fecha_hora",
                "fecha_hora_obs",
                "timestamp",
                "observado",
                "date",
            ],
        )

        tendencia = encontrar_valor(
            properties,
            [
                "tendencia",
                "trend",
                "tendencia_nivel",
                "direccion",
                "dirección",
            ],
        )

        referencia = encontrar_valor(
            properties,
            [
                "alerta",
                "nivel_alerta",
                "umbral_alerta",
                "referencia",
                "nivel_referencia",
            ],
        )

        estaciones.append({
            "nombre": nombre or "Estación hidrométrica",
            "lat": lat,
            "lon": lon,
            "nivel": safe_float(nivel),
            "nivel_24h": safe_float(nivel_24h),
            "fecha": fecha,
            "tendencia": tendencia,
            "referencia": safe_float(referencia),
            "properties": properties,
        })

    return estaciones


def estacion_mas_cercana(node, estaciones, max_km=80):

    mejor = None
    menor_distancia = None

    for estacion in estaciones:

        d = distancia_km(
            node["lat"],
            node["lon"],
            estacion["lat"],
            estacion["lon"],
        )

        if d <= max_km:

            if menor_distancia is None or d < menor_distancia:

                mejor = estacion
                menor_distancia = d

    if mejor is None:
        return None

    resultado = mejor.copy()

    resultado["distancia_km"] = menor_distancia

    return resultado


# ============================================================
# COMPONENTES DEL RIESGO
# ============================================================

def score_lluvia_antecedente(mm_72h):

    return interpolar_score(
        mm_72h,
        [
            (0, 0),
            (10, 3),
            (25, 8),
            (50, 15),
            (100, 22),
            (150, 25),
        ],
    )


def score_lluvia_pronosticada(mm_72h):

    return interpolar_score(
        mm_72h,
        [
            (0, 0),
            (10, 3),
            (25, 7),
            (50, 15),
            (100, 22),
            (150, 25),
        ],
    )


def normalizar_humedad(valor, saturacion):

    if valor is None:
        return None

    if saturacion <= 0:
        return None

    return max(
        0,
        min(
            100,
            (valor / saturacion) * 100,
        ),
    )


def calcular_humedad_perfil(
    humedad_0_7,
    humedad_7_28,
    humedad_28_100,
):

    h1 = normalizar_humedad(
        humedad_0_7,
        0.45,
    )

    h2 = normalizar_humedad(
        humedad_7_28,
        0.45,
    )

    h3 = normalizar_humedad(
        humedad_28_100,
        0.40,
    )

    valores = []

    pesos = []

    if h1 is not None:
        valores.append(h1)
        pesos.append(0.45)

    if h2 is not None:
        valores.append(h2)
        pesos.append(0.35)

    if h3 is not None:
        valores.append(h3)
        pesos.append(0.20)

    if not valores:
        return None

    suma_pesos = sum(pesos)

    return sum(
        v * p
        for v, p in zip(valores, pesos)
    ) / suma_pesos


def score_humedad_perfil(humedad):

    if humedad is None:
        return 0

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
        ],
    )


def score_runoff(runoff_72h):

    return interpolar_score(
        runoff_72h,
        [
            (0, 0),
            (5, 2),
            (10, 4),
            (25, 7),
            (50, 10),
        ],
    )


def score_hidrologico(estacion):

    if not estacion:
        return 0.0

    nivel = estacion.get("nivel")
    referencia = estacion.get("referencia")
    tendencia = estacion.get("tendencia")

    # Si existe nivel de referencia:
    if nivel is not None and referencia is not None:

        if referencia > 0:

            relacion = nivel / referencia

            return interpolar_score(
                relacion,
                [
                    (0.50, 0),
                    (0.75, 2),
                    (0.90, 5),
                    (1.00, 7),
                    (1.10, 9),
                    (1.20, 10),
                ],
            )

    # Si no existe referencia:
    # solamente se utiliza la tendencia.
    if tendencia:

        texto = str(tendencia).lower()

        if any(
            palabra in texto
            for palabra in [
                "asc",
                "sub",
                "rise",
                "crec",
            ]
        ):
            return 4.0

        if any(
            palabra in texto
            for palabra in [
                "estable",
                "stable",
                "sin cambio",
            ]
        ):
            return 2.0

        return 1.0

    return 0.0


def score_vulnerabilidad(valor):

    return max(
        0,
        min(
            10,
            valor / 10,
        ),
    )


# ============================================================
# CALIDAD DE DATOS
# ============================================================

def calcular_calidad_datos(
    api_node,
    estacion_hidrologica,
):

    puntos = 0
    maximo = 100

    hourly = api_node.get(
        "hourly",
        {},
    )

    precipitacion = hourly.get(
        "precipitation",
        [],
    )

    humedad1 = hourly.get(
        "soil_moisture_0_to_7cm",
        [],
    )

    humedad2 = hourly.get(
        "soil_moisture_7_to_28cm",
        [],
    )

    humedad3 = hourly.get(
        "soil_moisture_28_to_100cm",
        [],
    )

    runoff = hourly.get(
        "runoff",
        [],
    )

    if precipitacion:
        puntos += 25

    if humedad1:
        puntos += 10

    if humedad2:
        puntos += 10

    if humedad3:
        puntos += 10

    if runoff:
        puntos += 15

    if estacion_hidrologica:
        puntos += 20

    # Comprobación básica de longitud temporal.
    if len(precipitacion) >= 24:
        puntos += 5

    if len(humedad1) >= 24:
        puntos += 5

    calidad = min(
        100,
        int(puntos),
    )

    if calidad >= 85:
        categoria = "Alta"

    elif calidad >= 65:
        categoria = "Media"

    else:
        categoria = "Baja"

    return calidad, categoria


# ============================================================
# CÁLCULO PRINCIPAL DE CADA NODO
# ============================================================

def evaluar_nodo(
    node,
    api_node,
    estaciones_hidrologicas,
):

    precip = serie_horaria(
        api_node,
        "precipitation",
    )

    humedad_0_7 = serie_horaria(
        api_node,
        "soil_moisture_0_to_7cm",
    )

    humedad_7_28 = serie_horaria(
        api_node,
        "soil_moisture_7_to_28cm",
    )

    humedad_28_100 = serie_horaria(
        api_node,
        "soil_moisture_28_to_100cm",
    )

    runoff = serie_horaria(
        api_node,
        "runoff",
    )

    wind = serie_horaria(
        api_node,
        "wind_gusts_10m",
    )

    lluvia_24 = suma_ultimas_horas(
        precip,
        24,
    )

    lluvia_72 = suma_ultimas_horas(
        precip,
        72,
    )

    lluvia_futura_24 = suma_futuras_horas(
        precip,
        24,
    )

    lluvia_futura_72 = suma_futuras_horas(
        precip,
        72,
    )

    lluvia_futura_7d = suma_futuras_horas(
        precip,
        24 * 7,
    )

    runoff_24 = suma_ultimas_horas(
        runoff,
        24,
    )

    runoff_72 = suma_ultimas_horas(
        runoff,
        72,
    )

    runoff_futuro_72 = suma_futuras_horas(
        runoff,
        72,
    )

    h1 = ultimo_valor_disponible(
        humedad_0_7,
    )

    h2 = ultimo_valor_disponible(
        humedad_7_28,
    )

    h3 = ultimo_valor_disponible(
        humedad_28_100,
    )

    humedad_perfil = calcular_humedad_perfil(
        h1,
        h2,
        h3,
    )

    rafaga = ultimo_valor_disponible(
        wind,
    )

    estacion = estacion_mas_cercana(
        node,
        estaciones_hidrologicas,
        max_km=80,
    )

    componente_lluvia_antecedente = score_lluvia_antecedente(
        lluvia_72
    )

    componente_lluvia_pronostico = score_lluvia_pronosticada(
        lluvia_futura_72
    )

    componente_humedad = score_humedad_perfil(
        humedad_perfil
    )

    componente_runoff = score_runoff(
        runoff_72 + runoff_futuro_72
    )

    componente_hidrologia = score_hidrologico(
        estacion
    )

    vulnerabilidad = VULNERABILIDAD_BASE.get(
        node["nombre"],
        50,
    )

    componente_vulnerabilidad = score_vulnerabilidad(
        vulnerabilidad
    )

    riesgo = (
        componente_lluvia_antecedente
        + componente_lluvia_pronostico
        + componente_humedad
        + componente_runoff
        + componente_hidrologia
        + componente_vulnerabilidad
    )

    riesgo = max(
        0,
        min(
            100,
            round(riesgo),
        ),
    )

    nivel = nivel_desde_riesgo(
        riesgo
    )

    calidad, categoria_calidad = calcular_calidad_datos(
        api_node,
        estacion,
    )

    return {
        "nombre": node["nombre"],
        "provincia": node["provincia"],
        "lat": node["lat"],
        "lon": node["lon"],
        "rio": node["rio"],
        "zona_alta": node["zona_alta"],

        "riesgo": riesgo,
        "nivel": nivel,

        "lluvia_24h": round(lluvia_24, 1),
        "lluvia_72h": round(lluvia_72, 1),

        "lluvia_futura_24h": round(
            lluvia_futura_24,
            1,
        ),

        "lluvia_futura_72h": round(
            lluvia_futura_72,
            1,
        ),

        "lluvia_futura_7d": round(
            lluvia_futura_7d,
            1,
        ),

        "humedad_0_7": h1,
        "humedad_7_28": h2,
        "humedad_28_100": h3,

        "humedad_perfil": (
            round(humedad_perfil, 1)
            if humedad_perfil is not None
            else None
        ),

        "runoff_24h": round(
            runoff_24,
            1,
        ),

        "runoff_72h": round(
            runoff_72,
            1,
        ),

        "runoff_futuro_72h": round(
            runoff_futuro_72,
            1,
        ),

        "rafaga_max": (
            round(
                max(
                    [v for _, v in wind]
                ),
                1,
            )
            if wind
            else None
        ),

        "vulnerabilidad": vulnerabilidad,

        "score_lluvia_antecedente": round(
            componente_lluvia_antecedente,
            1,
        ),

        "score_lluvia_pronostico": round(
            componente_lluvia_pronostico,
            1,
        ),

        "score_humedad": round(
            componente_humedad,
            1,
        ),

        "score_runoff": round(
            componente_runoff,
            1,
        ),

        "score_hidrologia": round(
            componente_hidrologia,
            1,
        ),

        "score_vulnerabilidad": round(
            componente_vulnerabilidad,
            1,
        ),

        "calidad_datos": calidad,
        "categoria_calidad": categoria_calidad,

        "estacion_ina": estacion,
    }


# ============================================================
# TELEGRAM
# ============================================================

def obtener_telegram_config():

    try:

        token = st.secrets.get(
            "TELEGRAM_BOT_TOKEN",
            "",
        )

        chat_id = st.secrets.get(
            "TELEGRAM_CHAT_ID",
            "",
        )

        return token, chat_id

    except Exception:

        return "", ""


def enviar_telegram(mensaje):

    token, chat_id = obtener_telegram_config()

    if not token or not chat_id:

        return False, (
            "Telegram no está configurado. "
            "Agregá TELEGRAM_BOT_TOKEN y "
            "TELEGRAM_CHAT_ID en Secrets."
        )

    url = (
        f"https://api.telegram.org/"
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

            return False, response.text[:500]

        return True, "Mensaje enviado correctamente."

    except Exception as exc:

        return False, str(exc)


def construir_mensaje_telegram(resultados):

    ahora_texto = ahora().strftime(
        "%d/%m/%Y %H:%M"
    )

    ordenados = sorted(
        resultados,
        key=lambda x: x["riesgo"],
        reverse=True,
    )

    criticos = [
        r
        for r in ordenados
        if r["nivel"] in [
            "ROJO",
            "NARANJA",
        ]
    ]

    lineas = [
        "🌧️ ALERTA LITORAL AGRO V3.0",
        "",
        f"Actualización: {ahora_texto}",
        "",
    ]

    if not criticos:

        lineas.append(
            "No se detectan nodos en "
            "nivel ROJO o NARANJA."
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
                f"{emoji} {r['nombre']} "
                f"({r['provincia']}): "
                f"{r['riesgo']}/100"
            )

    maximo = ordenados[0]

    lineas.extend([
        "",
        (
            f"Máximo regional: "
            f"{maximo['riesgo']}/100 — "
            f"{maximo['nombre']}"
        ),
        "",
        "Sistema experimental.",
        "No reemplaza alertas oficiales.",
    ])

    return "\n".join(lineas)


# ============================================================
# HISTORIAL
# ============================================================

def actualizar_historial(resultados):

    if "historial" not in st.session_state:
        st.session_state.historial = []

    if not resultados:
        return

    maximo = max(
        resultados,
        key=lambda x: x["riesgo"],
    )

    firma = (
        ahora().strftime("%Y-%m-%d-%H-%M")
        + "-"
        + str(maximo["riesgo"])
        + "-"
        + maximo["nombre"]
    )

    if st.session_state.get(
        "ultima_firma_historial"
    ) == firma:
        return

    st.session_state.historial.append({
        "fecha": ahora().strftime(
            "%Y-%m-%d %H:%M"
        ),
        "nodo_maximo": maximo["nombre"],
        "provincia": maximo["provincia"],
        "riesgo_maximo": maximo["riesgo"],
        "nivel": maximo["nivel"],
        "calidad_datos": maximo["calidad_datos"],
    })

    st.session_state.ultima_firma_historial = firma


# ============================================================
# MAPA
# ============================================================

def crear_mapa(resultados):

    mapa = folium.Map(
        location=[
            -31.0,
            -59.5,
        ],
        zoom_start=6,
        tiles="CartoDB positron",
        control_scale=True,
    )

    for resultado in resultados:

        nivel = resultado["nivel"]

        info = NIVELES[nivel]

        popup_html = f"""
        <div style="width:300px">

        <h4>
        {info['emoji']} {resultado['nombre']}
        </h4>

        <b>Provincia:</b>
        {resultado['provincia']}<br>

        <b>Índice:</b>
        {resultado['riesgo']}/100<br>

        <b>Estado:</b>
        {nivel}<br>

        <hr>

        <b>Lluvia 24 h:</b>
        {resultado['lluvia_24h']} mm<br>

        <b>Lluvia 72 h:</b>
        {resultado['lluvia_72h']} mm<br>

        <b>Pronóstico 72 h:</b>
        {resultado['lluvia_futura_72h']} mm<br>

        <b>Pronóstico 7 días:</b>
        {resultado['lluvia_futura_7d']} mm<br>

        <b>Humedad perfil:</b>
        {resultado['humedad_perfil']
        if resultado['humedad_perfil'] is not None
        else 'N/D'} %<br>

        <b>Runoff 72 h:</b>
        {resultado['runoff_72h']} mm<br>

        <b>Vulnerabilidad:</b>
        {resultado['vulnerabilidad']}/100<br>

        <b>Calidad:</b>
        {resultado['calidad_datos']}/100
        ({resultado['categoria_calidad']})

        </div>
        """

        folium.CircleMarker(
            location=[
                resultado["lat"],
                resultado["lon"],
            ],
            radius=11,
            color=info["color"],
            fill=True,
            fill_color=info["color"],
            fill_opacity=0.85,
            popup=folium.Popup(
                popup_html,
                max_width=350,
            ),
            tooltip=(
                f"{info['emoji']} "
                f"{resultado['nombre']} — "
                f"{resultado['riesgo']}/100"
            ),
        ).add_to(mapa)

        # Estación INA cercana
        estacion = resultado.get(
            "estacion_ina"
        )

        if estacion:

            folium.CircleMarker(
                location=[
                    estacion["lat"],
                    estacion["lon"],
                ],
                radius=5,
                color="#0077b6",
                fill=True,
                fill_color="#0077b6",
                fill_opacity=0.9,
                tooltip=(
                    "INA: "
                    + str(
                        estacion["nombre"]
                    )
                ),
                popup=folium.Popup(
                    f"""
                    <b>Estación hidrométrica INA</b><br>
                    {estacion['nombre']}<br>
                    Nivel: {
                        estacion['nivel']
                        if estacion['nivel'] is not None
                        else 'N/D'
                    }<br>
                    Tendencia: {
                        estacion['tendencia']
                        if estacion['tendencia']
                        else 'N/D'
                    }<br>
                    Distancia al nodo:
                    {estacion['distancia_km']:.1f} km
                    """,
                    max_width=300,
                ),
            ).add_to(mapa)

    return mapa


# ============================================================
# TABLA DE RESULTADOS
# ============================================================

def construir_dataframe(resultados):

    filas = []

    for r in resultados:

        filas.append({
            "Provincia": r["provincia"],
            "Nodo": r["nombre"],
            "Riesgo": r["riesgo"],
            "Estado": (
                NIVELES[r["nivel"]]["emoji"]
                + " "
                + r["nivel"]
            ),
            "Lluvia 24h (mm)": r["lluvia_24h"],
            "Lluvia 72h (mm)": r["lluvia_72h"],
            "Pronóstico 72h (mm)": r[
                "lluvia_futura_72h"
            ],
            "Pronóstico 7d (mm)": r[
                "lluvia_futura_7d"
            ],
            "Humedad perfil (%)": r[
                "humedad_perfil"
            ],
            "Runoff 72h (mm)": r[
                "runoff_72h"
            ],
            "Vulnerabilidad": r[
                "vulnerabilidad"
            ],
            "Calidad datos": r[
                "calidad_datos"
            ],
        })

    return pd.DataFrame(filas)


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
    unsafe_allow_html=True,
)


# ============================================================
# ENCABEZADO
# ============================================================

st.markdown(
    '<div class="titulo">'
    '🌧️ Alerta Litoral Agro V3.0'
    '</div>',
    unsafe_allow_html=True,
)

st.markdown(
    '<div class="subtitulo">'
    'Sistema experimental de alerta temprana para '
    'riesgo de anegamiento agropecuario · '
    'Santa Fe · Corrientes · Entre Ríos'
    '</div>',
    unsafe_allow_html=True,
)


st.warning(
    "⚠️ Sistema experimental. "
    "El índice no representa una probabilidad matemática "
    "de inundación ni reemplaza las alertas oficiales."
)


# ============================================================
# SIDEBAR
# ============================================================

with st.sidebar:

    st.header("⚙️ Control")

    st.write(
        "Actualización automática de datos "
        "meteorológicos e hidrológicos."
    )

    actualizar = st.button(
        "🔄 Actualizar datos",
        use_container_width=True,
    )

    if actualizar:

        consultar_open_meteo.clear()
        consultar_ina.clear()

        st.rerun()

    st.divider()

    st.subheader("📡 Fuentes")

    st.write(
        "🌦️ Open-Meteo / ECMWF IFS"
    )

    st.write(
        "🌊 INA — Sistema de Información "
        "Hidrológica de la Cuenca del Plata"
    )

    st.divider()

    token, chat_id = obtener_telegram_config()

    if token and chat_id:

        st.success(
            "Telegram configurado"
        )

    else:

        st.info(
            "Telegram no configurado"
        )


# ============================================================
# OBTENCIÓN DE DATOS
# ============================================================

try:

    with st.spinner(
        "Consultando datos meteorológicos..."
    ):

        meteorologia = consultar_open_meteo()

    error_meteorologia = None

except Exception as exc:

    meteorologia = None

    error_meteorologia = str(exc)


if meteorologia is None:

    st.error(
        "### ❌ NO FUE POSIBLE ACTUALIZAR "
        "LOS DATOS METEOROLÓGICOS"
    )

    st.code(
        error_meteorologia
        or "Error desconocido"
    )

    st.info(
        "La aplicación detuvo el cálculo para evitar "
        "mostrar un índice de riesgo sin datos "
        "meteorológicos válidos."
    )

    st.stop()


# ============================================================
# HIDROLOGÍA
# ============================================================

with st.spinner(
    "Consultando información hidrológica..."
):

    ina_data = consultar_ina()

if ina_data["ok"]:

    estaciones = procesar_hidrologia(
        ina_data["features"]
    )

else:

    estaciones = []


# ============================================================
# EVALUACIÓN
# ============================================================

resultados = []

for node, api_node in zip(
    NODOS,
    meteorologia,
):

    try:

        resultado = evaluar_nodo(
            node,
            api_node,
            estaciones,
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
        "No fue posible generar ninguna evaluación."
    )

    st.stop()


# ============================================================
# HISTORIAL
# ============================================================

actualizar_historial(
    resultados
)


# ============================================================
# RESUMEN REGIONAL
# ============================================================

maximo = max(
    resultados,
    key=lambda x: x["riesgo"],
)

cantidad_rojos = sum(
    1
    for r in resultados
    if r["nivel"] == "ROJO"
)

cantidad_naranjas = sum(
    1
    for r in resultados
    if r["nivel"] == "NARANJA"
)

cantidad_amarillos = sum(
    1
    for r in resultados
    if r["nivel"] == "AMARILLO"
)

cantidad_verdes = sum(
    1
    for r in resultados
    if r["nivel"] == "VERDE"
)


# ============================================================
# MÉTRICAS SUPERIORES
# ============================================================

col1, col2, col3, col4, col5 = st.columns(5)

with col1:

    st.metric(
        "Máximo regional",
        f"{maximo['riesgo']}/100",
    )

with col2:

    st.metric(
        "🔴 Rojo",
        cantidad_rojos,
    )

with col3:

    st.metric(
        "🟠 Naranja",
        cantidad_naranjas,
    )

with col4:

    st.metric(
        "🟡 Amarillo",
        cantidad_amarillos,
    )

with col5:

    st.metric(
        "🟢 Verde",
        cantidad_verdes,
    )


st.caption(
    "Última actualización: "
    + ahora().strftime(
        "%d/%m/%Y %H:%M:%S"
    )
    + " ART"
)


# ============================================================
# ALERTA DESTACADA
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
    Provincia: {maximo['provincia']}<br>
    Curso asociado: {maximo['rio']}<br>
    Vulnerabilidad territorial:
    {maximo['vulnerabilidad']}/100<br>
    Calidad de datos:
    {maximo['calidad_datos']}/100
    ({maximo['categoria_calidad']})
    </p>

    </div>
    """,
    unsafe_allow_html=True,
)


# ============================================================
# MAPA
# ============================================================

st.header("🗺️ Mapa integrado de riesgo")

mapa = crear_mapa(
    resultados
)

st_folium(
    mapa,
    width=None,
    height=650,
    returned_objects=[],
)


# ============================================================
# TABLA
# ============================================================

st.header("📊 Estado de los nodos")

df = construir_dataframe(
    resultados
)

st.dataframe(
    df,
    use_container_width=True,
    hide_index=True,
)


# ============================================================
# DETALLE DE NODOS
# ============================================================

st.header("🔎 Detalle por nodo")

for resultado in sorted(
    resultados,
    key=lambda x: x["riesgo"],
    reverse=True,
):

    nivel = resultado["nivel"]

    with st.expander(
        f"{NIVELES[nivel]['emoji']} "
        f"{resultado['nombre']} — "
        f"{resultado['riesgo']}/100"
    ):

        c1, c2, c3 = st.columns(3)

        with c1:

            st.metric(
                "Riesgo integrado",
                f"{resultado['riesgo']}/100",
            )

            st.metric(
                "Lluvia 72 h",
                f"{resultado['lluvia_72h']} mm",
            )

            st.metric(
                "Pronóstico 72 h",
                f"{resultado['lluvia_futura_72h']} mm",
            )

        with c2:

            humedad_texto = (
                f"{resultado['humedad_perfil']} %"
                if resultado["humedad_perfil"]
                is not None
                else "N/D"
            )

            st.metric(
                "Humedad perfil",
                humedad_texto,
            )

            st.metric(
                "Runoff 72 h",
                f"{resultado['runoff_72h']} mm",
            )

            st.metric(
                "Pronóstico 7 días",
                f"{resultado['lluvia_futura_7d']} mm",
            )

        with c3:

            st.metric(
                "Vulnerabilidad",
                f"{resultado['vulnerabilidad']}/100",
            )

            st.metric(
                "Calidad datos",
                f"{resultado['calidad_datos']}/100",
            )

            st.metric(
                "Ráfaga máxima",
                (
                    f"{resultado['rafaga_max']} km/h"
                    if resultado["rafaga_max"]
                    is not None
                    else "N/D"
                ),
            )

        st.markdown("#### Componentes del índice")

        componentes = pd.DataFrame({
            "Componente": [
                "Lluvia antecedente",
                "Lluvia pronosticada",
                "Humedad del perfil",
                "Runoff",
                "Hidrología",
                "Vulnerabilidad territorial",
            ],
            "Puntaje": [
                resultado[
                    "score_lluvia_antecedente"
                ],
                resultado[
                    "score_lluvia_pronostico"
                ],
                resultado[
                    "score_humedad"
                ],
                resultado[
                    "score_runoff"
                ],
                resultado[
                    "score_hidrologia"
                ],
                resultado[
                    "score_vulnerabilidad"
                ],
            ],
        })

        st.dataframe(
            componentes,
            use_container_width=True,
            hide_index=True,
        )

        st.markdown("#### 🌊 Hidrología")

        estacion = resultado.get(
            "estacion_ina"
        )

        if estacion:

            st.write(
                f"**Estación INA cercana:** "
                f"{estacion['nombre']}"
            )

            st.write(
                f"**Distancia:** "
                f"{estacion['distancia_km']:.1f} km"
            )

            st.write(
                f"**Nivel observado:** "
                f"{estacion['nivel']}"
            )

            st.write(
                f"**Nivel 24 h previo:** "
                f"{estacion['nivel_24h']}"
            )

            st.write(
                f"**Tendencia:** "
                f"{estacion['tendencia'] or 'N/D'}"
            )

            if estacion["fecha"]:

                st.write(
                    f"**Fecha de observación:** "
                    f"{estacion['fecha']}"
                )

            st.caption(
                "La estación hidrométrica puede no "
                "coincidir exactamente con el nodo "
                "meteorológico. Se utiliza como "
                "referencia espacial cercana."
            )

        else:

            st.info(
                "No se encontró una estación "
                "hidrométrica INA dentro del radio "
                "de búsqueda de 80 km."
            )

        st.markdown("#### 🎯 Acciones sugeridas")

        for accion in ACCIONES[nivel]:

            st.write(
                f"• {accion}"
            )


# ============================================================
# TELEGRAM
# ============================================================

st.header("📲 Alertas Telegram")

st.write(
    "Permite enviar manualmente el resumen "
    "regional al canal o grupo configurado."
)

token, chat_id = obtener_telegram_config()

if token and chat_id:

    if st.button(
        "📲 Enviar resumen a Telegram",
        use_container_width=True,
    ):

        mensaje = construir_mensaje_telegram(
            resultados
        )

        ok, detalle = enviar_telegram(
            mensaje
        )

        if ok:

            st.success(
                "Telegram: mensaje enviado."
            )

        else:

            st.error(
                f"Telegram: {detalle}"
            )

else:

    st.info(
        "Para activar Telegram agregá en "
        "Streamlit Secrets:"
    )

    st.code(
        """
TELEGRAM_BOT_TOKEN = "123456:ABC..."
TELEGRAM_CHAT_ID = "-1001234567890"
        """.strip()
    )

    st.caption(
        "No coloques estas credenciales directamente "
        "dentro de app.py."
    )


# ============================================================
# HISTORIAL
# ============================================================

st.header("📚 Historial de evaluaciones")

historial = st.session_state.get(
    "historial",
    [],
)

if historial:

    df_historial = pd.DataFrame(
        historial
    )

    st.dataframe(
        df_historial,
        use_container_width=True,
        hide_index=True,
    )

    csv_historial = (
        df_historial
        .to_csv(index=False)
        .encode("utf-8")
    )

    st.download_button(
        "⬇️ Descargar historial CSV",
        data=csv_historial,
        file_name=(
            "historial_alerta_litoral_agro.csv"
        ),
        mime="text/csv",
        use_container_width=True,
    )

    if st.button(
        "🗑️ Limpiar historial de esta sesión"
    ):

        st.session_state.historial = []

        st.session_state.ultima_firma_historial = None

        st.rerun()

else:

    st.info(
        "Todavía no hay registros en esta sesión."
    )

st.caption(
    "El historial de esta versión se conserva "
    "durante la sesión de Streamlit. Para disponer "
    "de un historial persistente entre reinicios "
    "se requiere una base de datos o almacenamiento "
    "externo."
)


# ============================================================
# CONTROL DE CALIDAD
# ============================================================

st.header("🧪 Control de calidad de datos")

promedio_calidad = round(
    sum(
        r["calidad_datos"]
        for r in resultados
    )
    / len(resultados)
)

if promedio_calidad >= 85:

    st.success(
        f"Calidad promedio: "
        f"{promedio_calidad}/100 — Alta"
    )

elif promedio_calidad >= 65:

    st.warning(
        f"Calidad promedio: "
        f"{promedio_calidad}/100 — Media"
    )

else:

    st.error(
        f"Calidad promedio: "
        f"{promedio_calidad}/100 — Baja"
    )

st.write(
    """
La calidad de datos evalúa la disponibilidad de
precipitación, humedad del suelo en diferentes
profundidades, escorrentía y referencia hidrológica.
No constituye una validación estadística de la
calidad del pronóstico.
"""
)


if not ina_data["ok"]:

    st.warning(
        "⚠️ La fuente hidrológica INA no respondió. "
        "El índice meteorológico continúa calculándose, "
        "pero la componente hidrológica puede quedar "
        "sin información."
    )


# ============================================================
# METODOLOGÍA
# ============================================================

st.header("📐 Metodología profesional")

st.markdown(
    """
### Índice integrado de riesgo 0–100

El índice combina seis componentes:

| Componente | Peso máximo |
|---|---:|
| Precipitación antecedente 72 h | 25 |
| Precipitación pronosticada 72 h | 25 |
| Humedad del perfil de suelo | 20 |
| Escorrentía / runoff | 10 |
| Hidrología | 10 |
| Vulnerabilidad territorial | 10 |
| **Total** | **100** |

### Semáforo

- 🟢 **0–24:** riesgo bajo
- 🟡 **25–49:** vigilancia
- 🟠 **50–74:** riesgo elevado
- 🔴 **75–100:** riesgo muy elevado

### Interpretación

El índice representa un **screening integrado del
potencial de anegamiento**.

No representa una probabilidad de inundación,
una altura de agua esperada ni una predicción
determinista de daños.

El objetivo es priorizar:

- sectores a vigilar;
- evolución de la humedad antecedente;
- acumulados de precipitación;
- sectores con mayor vulnerabilidad;
- interacción entre precipitación y condiciones
  hidrológicas.

### Humedad del suelo

Se utiliza información de humedad modelada en:

- 0–7 cm;
- 7–28 cm;
- 28–100 cm.

El indicador de humedad del perfil es un índice
normalizado y **no equivale directamente a
saturación hidrológica medida en campo**.

### Vulnerabilidad territorial

La vulnerabilidad utilizada actualmente es un
parámetro experimental de screening.

Para convertirla en una capa profesional debería
incorporar progresivamente:

- modelo digital de elevación;
- pendiente;
- hidrología superficial;
- distancia a cauces;
- humedales;
- cobertura/uso del suelo;
- tipo de suelo;
- antecedentes de anegamiento;
- infraestructura productiva expuesta.

### Hidrología

Cuando existe una estación cercana, se incorpora
la última información hidrométrica disponible.

La estación puede encontrarse a cierta distancia
del nodo meteorológico y por eso debe interpretarse
como **referencia hidrológica espacial**, no como
medición exacta del establecimiento.

### Fuentes

**Meteorología:** Open-Meteo / ECMWF IFS.

**Hidrología:** Sistema de Información Hidrológica
de la Cuenca del Plata — INA.

Los datos hidrológicos del INA deben interpretarse
de acuerdo con las condiciones y advertencias
publicadas por el propio servicio.
"""
)


# ============================================================
# ACCIONES SEGÚN ESTADO
# ============================================================

st.header("🚜 Acciones orientativas")

for nivel in [
    "ROJO",
    "NARANJA",
    "AMARILLO",
    "VERDE",
]:

    info = NIVELES[nivel]

    with st.expander(
        f"{info['emoji']} {nivel}"
    ):

        for accion in ACCIONES[nivel]:

            st.write(
                f"• {accion}"
            )


# ============================================================
# LIMITACIONES
# ============================================================

st.header("⚠️ Limitaciones del sistema")

st.markdown(
    """
1. El sistema es experimental.

2. El índice 0–100 no constituye una probabilidad
   estadística de anegamiento.

3. Los valores meteorológicos proceden de modelos
   numéricos y pueden diferir de observaciones locales.

4. La humedad del suelo es modelada.

5. La estación hidrométrica más cercana no
   necesariamente representa las condiciones
   hidráulicas exactas del nodo.

6. La vulnerabilidad territorial todavía no se
   calcula mediante una capa GIS de alta resolución.

7. La aplicación no reemplaza información,
   alertas o recomendaciones de organismos oficiales.

8. Telegram es un canal de distribución de la
   información y no constituye por sí mismo una
   validación oficial de la alerta.
"""
)


# ============================================================
# PIE
# ============================================================

st.divider()

st.caption(
    "Alerta Litoral Agro V3.0 · "
    "Prototipo experimental de alerta temprana "
    "para riesgo de anegamiento agropecuario."
)

st.caption(
    "Última evaluación: "
    + ahora().strftime(
        "%d/%m/%Y %H:%M:%S"
    )
    + " ART"
)
