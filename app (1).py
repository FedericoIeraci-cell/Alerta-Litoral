import streamlit as st
import requests
import pandas as pd
import folium
from streamlit_folium import st_folium
from datetime import datetime
from zoneinfo import ZoneInfo

# ============================================================
# ALERTA LITORAL AGRO
# Sistema de alerta temprana para anegamientos
# Santa Fe + Corrientes + Entre Ríos + nodos complementarios
#
# Fuentes dinámicas:
#   - Open-Meteo: precipitación + humedad del suelo
#   - Vialidad Nacional: consulta oficial de estado de rutas
#
# IMPORTANTE:
# El índice de "saturación" es una aproximación basada en humedad
# volumétrica del suelo 0-7 cm. No representa una medición directa
# de saturación hidrológica del perfil completo.
# ============================================================

st.set_page_config(
    page_title="Alerta Litoral Agro",
    page_icon="🌧️",
    layout="wide",
    initial_sidebar_state="expanded",
)

TZ_ARG = ZoneInfo("America/Argentina/Buenos_Aires")
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
VIALIDAD_URL = (
    "https://www.argentina.gob.ar/transporte/vialidad-nacional/estado-de-rutas"
)

# ============================================================
# NODOS
# ============================================================

NODOS = [
    {"nombre": "Goya", "provincia": "Corrientes", "lat": -29.14, "lon": -59.26,
     "rio": "Río Paraná", "refugio": "Loma Batelito", "cota": "48 m s.n.m.", "rutas": "RN 12 / RP 27"},
    {"nombre": "Mercedes", "provincia": "Corrientes", "lat": -29.18, "lon": -58.07,
     "rio": "Esteros del Iberá", "refugio": "Lomadas de Mercedes", "cota": "70 m s.n.m.", "rutas": "RN 119"},
    {"nombre": "Curuzú Cuatiá", "provincia": "Corrientes", "lat": -29.79, "lon": -58.05,
     "rio": "Arroyo Sarandí", "refugio": "Sierras de Curuzú", "cota": "85 m s.n.m.", "rutas": "RP 126"},
    {"nombre": "Paso de los Libres", "provincia": "Corrientes", "lat": -29.71, "lon": -57.08,
     "rio": "Río Uruguay", "refugio": "Zona Alta Paso de los Libres", "cota": "60 m s.n.m.", "rutas": "RN 117"},
    {"nombre": "Santo Tomé", "provincia": "Corrientes", "lat": -28.55, "lon": -56.04,
     "rio": "Río Uruguay", "refugio": "Loma Alta Santo Tomé", "cota": "92 m s.n.m.", "rutas": "RN 14"},
    {"nombre": "Corrientes Capital", "provincia": "Corrientes", "lat": -27.46, "lon": -58.83,
     "rio": "Río Paraná", "refugio": "Cota Alta Corrientes", "cota": "55 m s.n.m.", "rutas": "RN 12 / Puente Chaco-Corrientes"},
    {"nombre": "Reconquista", "provincia": "Santa Fe", "lat": -29.15, "lon": -59.65,
     "rio": "Río Paraná", "refugio": "Loma Alta Reconquista Oeste", "cota": "52 m s.n.m.", "rutas": "RP 40S / RN 11"},
    {"nombre": "San Javier", "provincia": "Santa Fe", "lat": -30.58, "lon": -59.93,
     "rio": "Río San Javier", "refugio": "Cuchilla Fortín Olmos", "cota": "68 m s.n.m.", "rutas": "RP 1"},
    {"nombre": "Vera", "provincia": "Santa Fe", "lat": -29.46, "lon": -60.21,
     "rio": "Cuenca Calchaquí", "refugio": "Cuchilla Fortín Olmos", "cota": "68 m s.n.m.", "rutas": "RP 3 / RN 11"},
    {"nombre": "Santa Fe Capital", "provincia": "Santa Fe", "lat": -31.63, "lon": -60.70,
     "rio": "Río Salado", "refugio": "Cota Alta Litoral Centro", "cota": "30 m s.n.m.", "rutas": "RN 168 / AP 01"},
    {"nombre": "Rosario", "provincia": "Santa Fe", "lat": -32.95, "lon": -60.66,
     "rio": "Río Paraná", "refugio": "Zonas Altas del Cordón Industrial", "cota": "35 m s.n.m.", "rutas": "RN 9 / RN 11"},
    {"nombre": "Tostado", "provincia": "Santa Fe", "lat": -29.23, "lon": -61.77,
     "rio": "Río Salado Norte", "refugio": "Lomadas de Tostado", "cota": "70 m s.n.m.", "rutas": "RN 95"},
    {"nombre": "Concordia", "provincia": "Entre Ríos", "lat": -31.39, "lon": -58.02,
     "rio": "Río Uruguay", "refugio": "Lomas de Salto Grande", "cota": "65 m s.n.m.", "rutas": "RP 015 / RN 14"},
    {"nombre": "La Paz", "provincia": "Entre Ríos", "lat": -30.74, "lon": -59.64,
     "rio": "Río Paraná", "refugio": "Cuchilla Montiel", "cota": "72 m s.n.m.", "rutas": "RN 12"},
    {"nombre": "Victoria", "provincia": "Entre Ríos", "lat": -32.62, "lon": -60.15,
     "rio": "Delta del Paraná", "refugio": "Cuchilla Victoria", "cota": "58 m s.n.m.", "rutas": "Enlace Victoria-Rosario"},
    {"nombre": "Gualeguay", "provincia": "Entre Ríos", "lat": -33.14, "lon": -59.31,
     "rio": "Río Gualeguay", "refugio": "Cuchilla de Gualeguay", "cota": "45 m s.n.m.", "rutas": "RN 12"},
    {"nombre": "Gualeguaychú", "provincia": "Entre Ríos", "lat": -33.01, "lon": -58.51,
     "rio": "Río Gualeguaychú", "refugio": "Lomas de Gualeguaychú", "cota": "40 m s.n.m.", "rutas": "RN 14"},
    {"nombre": "Paraná", "provincia": "Entre Ríos", "lat": -31.73, "lon": -60.52,
     "rio": "Río Paraná", "refugio": "Lomas de Paraná", "cota": "75 m s.n.m.", "rutas": "RN 12 / Túnel Subfluvial"},
    {"nombre": "Clorinda", "provincia": "Formosa", "lat": -25.28, "lon": -57.71,
     "rio": "Río Pilcomayo", "refugio": "Loma Alta Clorinda", "cota": "75 m s.n.m.", "rutas": "RN 11 / RP 86"},
    {"nombre": "Formosa Capital", "provincia": "Formosa", "lat": -26.18, "lon": -58.17,
     "rio": "Río Paraguay", "refugio": "Cota Alta Formosa", "cota": "60 m s.n.m.", "rutas": "RN 11"},
    {"nombre": "General San Martín", "provincia": "Chaco", "lat": -26.53, "lon": -59.34,
     "rio": "Río Bermejo", "refugio": "Lomas de San Martín", "cota": "80 m s.n.m.", "rutas": "RP 9"},
    {"nombre": "Resistencia", "provincia": "Chaco", "lat": -27.45, "lon": -58.98,
     "rio": "Río Negro", "refugio": "Defensa Resistencia", "cota": "50 m s.n.m.", "rutas": "RN 11 / RN 16"},
    {"nombre": "Posadas", "provincia": "Misiones", "lat": -27.36, "lon": -55.89,
     "rio": "Río Paraná", "refugio": "Sierras Posadas", "cota": "120 m s.n.m.", "rutas": "RN 12 / RN 105"},
    {"nombre": "Eldorado", "provincia": "Misiones", "lat": -26.40, "lon": -54.63,
     "rio": "Río Paraná", "refugio": "Sierras Centrales de Misiones", "cota": "180 m s.n.m.", "rutas": "RN 12"},
]

# ============================================================
# LÓGICA DEL SEMÁFORO
# ============================================================

ACCIONES = {
    "ROJO": (
        "ALERTA OPERATIVA MÁXIMA: evaluar traslado preventivo de hacienda "
        "hacia zonas altas, proteger maquinaria y verificar drenajes."
    ),
    "NARANJA": (
        "ALERTA OPERATIVA: preparar traslado preventivo de hacienda, "
        "verificar defensas y mantener libres los principales drenajes."
    ),
    "AMARILLO": (
        "ALERTA PREVENTIVA: monitorear bajos, preparar forraje y verificar "
        "canales, alcantarillas y caminos de acceso."
    ),
    "VERDE": (
        "MONITOREO NORMAL: continuar seguimiento de precipitaciones, "
        "humedad del suelo y estado de los caminos."
    ),
}

NIVELES = {
    "ROJO": {"emoji": "🔴", "color": "#dc3545"},
    "NARANJA": {"emoji": "🟠", "color": "#fd7e14"},
    "AMARILLO": {"emoji": "🟡", "color": "#d99b00"},
    "VERDE": {"emoji": "🟢", "color": "#28a745"},
}


def evaluar_estado(lluvia_hoy, lluvia_3d, lluvia_7d, humedad_suelo):
    # El 0.45 m3/m3 se utiliza solamente como referencia operativa.
    # No equivale a saturación hidrológica real del perfil.
    suelo_vulnerable = humedad_suelo >= 80.0
    riesgo_corto = lluvia_3d >= 60.0 and suelo_vulnerable

    if riesgo_corto or (lluvia_7d >= 90.0 and suelo_vulnerable):
        return "ROJO"
    if lluvia_7d >= 70.0 and suelo_vulnerable:
        return "NARANJA"
    if lluvia_7d >= 35.0 or lluvia_hoy > 5.0 or suelo_vulnerable:
        return "AMARILLO"
    return "VERDE"


# ============================================================
# OPEN-METEO
# ============================================================

@st.cache_data(ttl=900, show_spinner=False)
def consultar_open_meteo():
    latitudes = ",".join(str(n["lat"]) for n in NODOS)
    longitudes = ",".join(str(n["lon"]) for n in NODOS)

    params = {
        "latitude": latitudes,
        "longitude": longitudes,
        "daily": "precipitation_sum",
        "hourly": "soil_moisture_0_to_7cm",
        "timezone": "America/Argentina/Buenos_Aires",
        "past_days": 1,
        "forecast_days": 8,
    }

    response = requests.get(
        OPEN_METEO_URL,
        params=params,
        timeout=30,
        headers={"User-Agent": "Alerta-Litoral-Agro/1.0"},
    )
    response.raise_for_status()

    data = response.json()

    # Open-Meteo devuelve una lista cuando se solicitan varias coordenadas.
    if isinstance(data, dict):
        data = [data]

    resultados = []

    ahora = datetime.now(TZ_ARG)

    for nodo, item in zip(NODOS, data):
        daily = item.get("daily", {})
        hourly = item.get("hourly", {})

        fechas = daily.get("time", [])
        precipitaciones = daily.get("precipitation_sum", [])

        # Buscar el día actual por fecha, evitando depender de índices fijos.
        hoy_str = ahora.date().isoformat()

        lluvia_hoy = 0.0
        lluvia_3d = 0.0
        lluvia_7d = 0.0

        if fechas and precipitaciones:
            for fecha, lluvia in zip(fechas, precipitaciones):
                if fecha >= hoy_str and fecha <= (
                    ahora.date().replace(day=ahora.day).isoformat()
                ):
                    pass

            # El primer día disponible >= hoy es hoy.
            futuros = [
                float(p or 0)
                for f, p in zip(fechas, precipitaciones)
                if f >= hoy_str
            ]
            if futuros:
                lluvia_hoy = futuros[0]
                lluvia_3d = sum(futuros[:3])
                lluvia_7d = sum(futuros[:7])

        # Humedad de suelo más cercana a la hora actual.
        horas = hourly.get("time", [])
        humedad = hourly.get("soil_moisture_0_to_7cm", [])

        humedad_actual = None

        if horas and humedad:
            candidatos = []
            ahora_sin_tz = ahora.replace(tzinfo=None)
            for h, valor in zip(horas, humedad):
                try:
                    dt = datetime.fromisoformat(h)
                    diferencia = abs((dt - ahora_sin_tz).total_seconds())
                    candidatos.append((diferencia, valor))
                except Exception:
                    continue

            if candidatos:
                candidatos.sort(key=lambda x: x[0])
                humedad_actual = candidatos[0][1]

        if humedad_actual is None:
            humedad_pct = None
        else:
            humedad_pct = max(0.0, min(100.0, (float(humedad_actual) / 0.45) * 100.0))

        estado = (
            evaluar_estado(
                lluvia_hoy,
                lluvia_3d,
                lluvia_7d,
                humedad_pct if humedad_pct is not None else 0,
            )
            if humedad_pct is not None
            else "AMARILLO"
        )

        resultados.append(
            {
                **nodo,
                "lluvia_hoy": lluvia_hoy,
                "lluvia_3d": lluvia_3d,
                "lluvia_7d": lluvia_7d,
                "humedad_suelo": humedad_pct,
                "humedad_volumetrica": humedad_actual,
                "estado": estado,
            }
        )

    return resultados


# ============================================================
# INTERFAZ
# ============================================================

st.title("🌧️ Alerta Litoral Agro")
st.subheader("Sistema de alerta temprana para anegamientos, producción y caminos")

ahora = datetime.now(TZ_ARG)

st.markdown(
    f"""
**Última actualización de datos meteorológicos:**  
`{ahora.strftime("%d/%m/%Y %H:%M")} hs (Argentina)`

El sistema combina precipitación prevista y humedad superficial del suelo
para generar un **semáforo operativo por nodo**.
"""
)

with st.sidebar:
    st.header("⚙️ Control")

    if st.button("🔄 Actualizar datos ahora", use_container_width=True):
        consultar_open_meteo.clear()
        st.rerun()

    st.markdown("---")
    st.markdown("### 📡 Fuentes")

    st.markdown(
        "- 🌧️ **Open-Meteo** — precipitación y humedad del suelo\n"
        "- 🛣️ **Vialidad Nacional** — estado oficial de rutas\n"
        "- 🗺️ **OpenStreetMap / Esri** — cartografía\n"
    )

    st.markdown("---")
    st.caption(
        "La humedad del suelo es una variable modelada. "
        "El porcentaje mostrado es un índice operativo y no una medición "
        "directa de saturación del perfil completo."
    )

# ============================================================
# CARGA DE DATOS
# ============================================================

try:
    datos = consultar_open_meteo()
except Exception as exc:
    st.error(
        "No se pudieron actualizar los datos de Open-Meteo. "
        "Revisá la conexión o intentá nuevamente."
    )
    st.exception(exc)
    st.stop()

df = pd.DataFrame(datos)

# ============================================================
# RESUMEN
# ============================================================

conteo = df["estado"].value_counts().to_dict()

c1, c2, c3, c4 = st.columns(4)

c1.metric("🔴 Rojo", conteo.get("ROJO", 0))
c2.metric("🟠 Naranja", conteo.get("NARANJA", 0))
c3.metric("🟡 Amarillo", conteo.get("AMARILLO", 0))
c4.metric("🟢 Verde", conteo.get("VERDE", 0))

st.markdown("---")

# ============================================================
# MAPA
# ============================================================

st.markdown("## 🗺️ Mapa operativo del Litoral")

m = folium.Map(
    location=[-29.2, -59.0],
    zoom_start=6,
    tiles="OpenStreetMap",
    control_scale=True,
)

# Capas base
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
    nivel = NIVELES[row["estado"]]

    humedad_texto = (
        f"{row['humedad_suelo']:.1f}%"
        if row["humedad_suelo"] is not None
        else "Sin dato"
    )

    popup_html = f"""
    <div style="width:300px;font-family:Arial,sans-serif">
        <h3 style="margin-bottom:5px">
            {nivel["emoji"]} {row["nombre"]}
        </h3>

        <b>Provincia:</b> {row["provincia"]}<br>
        <b>Estado:</b> {nivel["emoji"]} {row["estado"]}<br>
        <hr>

        🌧️ <b>Lluvia hoy:</b> {row["lluvia_hoy"]:.1f} mm<br>
        🌧️ <b>Lluvia próximos 3 días:</b> {row["lluvia_3d"]:.1f} mm<br>
        🌧️ <b>Lluvia próximos 7 días:</b> {row["lluvia_7d"]:.1f} mm<br>
        💧 <b>Índice de humedad 0–7 cm:</b> {humedad_texto}<br>

        <hr>

        🌊 <b>Referencia hídrica:</b> {row["rio"]}<br>
        ⛰️ <b>Zona alta:</b> {row["refugio"]}<br>
        📏 <b>Cota:</b> {row["cota"]}<br>
        🛣️ <b>Rutas:</b> {row["rutas"]}<br>

        <hr>

        <b>Acción sugerida:</b><br>
        {ACCIONES[row["estado"]]}
    </div>
    """

    folium.CircleMarker(
        location=[row["lat"], row["lon"]],
        radius=10,
        color=nivel["color"],
        fill=True,
        fill_color=nivel["color"],
        fill_opacity=0.80,
        weight=3,
        popup=folium.Popup(popup_html, max_width=350),
        tooltip=f'{nivel["emoji"]} {row["nombre"]} — {row["estado"]}',
    ).add_to(m)

folium.LayerControl(collapsed=False).add_to(m)

st_folium(
    m,
    width=None,
    height=700,
    returned_objects=[],
)

# ============================================================
# TABLA
# ============================================================

st.markdown("## 📊 Estado por nodo")

tabla = df[
    [
        "nombre",
        "provincia",
        "estado",
        "lluvia_hoy",
        "lluvia_3d",
        "lluvia_7d",
        "humedad_suelo",
        "rio",
        "rutas",
    ]
].copy()

tabla.columns = [
    "Localidad",
    "Provincia",
    "Semáforo",
    "Lluvia hoy (mm)",
    "Lluvia 3 días (mm)",
    "Lluvia 7 días (mm)",
    "Humedad suelo 0–7 cm (%)",
    "Referencia hídrica",
    "Rutas principales",
]

tabla["Semáforo"] = tabla["Semáforo"].map(
    lambda x: f'{NIVELES[x]["emoji"]} {x}'
)

for columna in [
    "Lluvia hoy (mm)",
    "Lluvia 3 días (mm)",
    "Lluvia 7 días (mm)",
    "Humedad suelo 0–7 cm (%)",
]:
    tabla[columna] = tabla[columna].round(1)

st.dataframe(
    tabla,
    use_container_width=True,
    hide_index=True,
)

# ============================================================
# ESTADO DE RUTAS — VIALIDAD NACIONAL
# ============================================================

st.markdown("---")
st.markdown("## 🛣️ Estado oficial de rutas")

st.warning(
    """
    **Esta capa no utiliza cortes de ruta inventados ni coordenadas
    hardcodeadas.** La información vial se deriva de la consulta oficial
    de Vialidad Nacional.

    Para evitar presentar como vigente un corte que pudo haber cambiado,
    el sistema dirige al usuario al parte oficial actualizado de Vialidad.
    """
)

vc1, vc2 = st.columns([3, 1])

with vc1:
    st.markdown(
        """
        ### Dirección Nacional de Vialidad

        Antes de circular por una ruta nacional, consultar el estado oficial
        de transitabilidad.
        """
    )

with vc2:
    st.link_button(
        "🛣️ Abrir Estado de Rutas",
        VIALIDAD_URL,
        use_container_width=True,
    )

# Intento de mostrar la página oficial dentro de la app.
# Si el servidor impide iframe, el botón anterior sigue funcionando.
st.markdown("#### Consulta oficial")

try:
    st.components.v1.iframe(
        VIALIDAD_URL,
        height=650,
        scrolling=True,
    )
except Exception:
    st.info(
        "El sitio oficial no permite incrustación dentro de esta aplicación. "
        "Utilizá el botón 'Abrir Estado de Rutas'."
    )

st.caption(
    f"Fuente: Dirección Nacional de Vialidad — Estado de rutas. "
    f"Consulta desde la aplicación: {ahora.strftime('%d/%m/%Y %H:%M')} hs."
)

# ============================================================
# RADAR / SATÉLITE
# ============================================================

st.markdown("---")
st.markdown("## 🛰️ Monitoreo complementario")

r1, r2 = st.columns(2)

with r1:
    st.markdown("### 🌀 Radar y modelos")
    st.markdown(
        """
        El siguiente visor permite consultar precipitación, radar,
        viento y modelos meteorológicos en el Litoral.
        """
    )

    st.components.v1.iframe(
        "https://embed.windy.com/embed2.html"
        "?lat=-28.5&lon=-58.5"
        "&detailLat=-28.5&detailLon=-58.5"
        "&width=700&height=460&zoom=6"
        "&level=surface&overlay=rain"
        "&product=ecmwf"
        "&menu=&message=true&marker="
        "&calendar=now&pressure=true&type=map"
        "&location=coordinates&detail="
        "&metricWind=km%2Fh&metricTemp=%C2%B0C"
        "&radarRange=-1",
        height=500,
        scrolling=False,
    )

with r2:
    st.markdown("### ⚡ GOES-19 / GLM")
    st.markdown(
        """
        Visor externo para seguimiento de nubosidad y actividad eléctrica.
        """
    )

    st.link_button(
        "⚡ Abrir visor CIRA GOES-19 / GLM",
        "https://slider.cira.colostate.edu/",
        use_container_width=True,
    )

# ============================================================
# INTERPRETACIÓN
# ============================================================

st.markdown("---")
st.markdown("## 🧭 ¿Cómo funciona el semáforo?")

st.markdown(
    """
| Nivel | Criterio operativo |
|---|---|
| 🔴 **ROJO** | Lluvia de corto plazo elevada con suelo vulnerable, o acumulado de 7 días muy elevado con suelo vulnerable |
| 🟠 **NARANJA** | Acumulado de 7 días elevado y suelo vulnerable |
| 🟡 **AMARILLO** | Acumulado moderado, lluvia diaria significativa o suelo vulnerable |
| 🟢 **VERDE** | Sin condiciones que activen los umbrales anteriores |

**Importante:** estos umbrales son parte del modelo operativo del TP y deben
validarse/calibrarse con datos observados antes de utilizarse como sistema
oficial de protección civil o producción.
"""
)

st.markdown("---")
st.caption(
    "Alerta Litoral Agro — prototipo académico de alerta temprana. "
    "Los datos meteorológicos se actualizan automáticamente mediante Open-Meteo "
    "y el estado vial se consulta mediante la fuente oficial de Vialidad Nacional."
)
