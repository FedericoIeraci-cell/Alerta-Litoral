import streamlit as st
import requests
import pandas as pd
import folium
from streamlit_folium import st_folium
from datetime import datetime
from zoneinfo import ZoneInfo

# ============================================================
# ALERTA LITORAL AGRO — V2.0
# Prototipo operativo de alerta temprana para riesgo de
# anegamiento agropecuario en Santa Fe, Corrientes y Entre Ríos.
#
# Fuentes dinámicas:
#   - Open-Meteo: precipitación reciente estimada + pronóstico
#   - Open-Meteo: humedad volumétrica superficial 0–7 cm
#   - OpenStreetMap / Esri: cartografía
#
# IMPORTANTE:
# La humedad 0–7 cm es una variable modelada y se transforma en
# un ÍNDICE DE HUMEDAD SUPERFICIAL. NO representa saturación
# hidrológica real de todo el perfil del suelo.
#
# El semáforo es un modelo experimental para apoyo a decisiones.
# No reemplaza alertas oficiales ni mediciones hidrológicas locales.
# ============================================================

st.set_page_config(
    page_title="Alerta Litoral Agro",
    page_icon="🌧️",
    layout="wide",
    initial_sidebar_state="expanded",
)

TZ_ARG = ZoneInfo("America/Argentina/Buenos_Aires")
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"

# ============================================================
# NODOS
# ============================================================

NODOS = [
    {"nombre": "Goya", "provincia": "Corrientes", "lat": -29.14, "lon": -59.26,
     "rio": "Río Paraná", "zona_alta": "Loma Batelito"},
    {"nombre": "Mercedes", "provincia": "Corrientes", "lat": -29.18, "lon": -58.07,
     "rio": "Sistema Iberá", "zona_alta": "Lomadas de Mercedes"},
    {"nombre": "Curuzú Cuatiá", "provincia": "Corrientes", "lat": -29.79, "lon": -58.05,
     "rio": "Arroyo Sarandí", "zona_alta": "Sierras de Curuzú"},
    {"nombre": "Paso de los Libres", "provincia": "Corrientes", "lat": -29.71, "lon": -57.08,
     "rio": "Río Uruguay", "zona_alta": "Zona alta de Paso de los Libres"},
    {"nombre": "Santo Tomé", "provincia": "Corrientes", "lat": -28.55, "lon": -56.04,
     "rio": "Río Uruguay", "zona_alta": "Loma Alta Santo Tomé"},
    {"nombre": "Corrientes Capital", "provincia": "Corrientes", "lat": -27.46, "lon": -58.83,
     "rio": "Río Paraná", "zona_alta": "Sectores altos de Corrientes"},

    {"nombre": "Reconquista", "provincia": "Santa Fe", "lat": -29.15, "lon": -59.65,
     "rio": "Río Paraná", "zona_alta": "Loma Alta Reconquista Oeste"},
    {"nombre": "San Javier", "provincia": "Santa Fe", "lat": -30.58, "lon": -59.93,
     "rio": "Río San Javier", "zona_alta": "Sectores altos de la zona"},
    {"nombre": "Vera", "provincia": "Santa Fe", "lat": -29.46, "lon": -60.21,
     "rio": "Cuenca Calchaquí", "zona_alta": "Cuchilla Fortín Olmos"},
    {"nombre": "Santa Fe Capital", "provincia": "Santa Fe", "lat": -31.63, "lon": -60.70,
     "rio": "Río Salado / Paraná", "zona_alta": "Sectores altos del Litoral Centro"},
    {"nombre": "Rosario", "provincia": "Santa Fe", "lat": -32.95, "lon": -60.66,
     "rio": "Río Paraná", "zona_alta": "Zonas altas del cordón industrial"},
    {"nombre": "Tostado", "provincia": "Santa Fe", "lat": -29.23, "lon": -61.77,
     "rio": "Río Salado Norte", "zona_alta": "Lomadas de Tostado"},

    {"nombre": "Concordia", "provincia": "Entre Ríos", "lat": -31.39, "lon": -58.02,
     "rio": "Río Uruguay", "zona_alta": "Lomas de Salto Grande"},
    {"nombre": "La Paz", "provincia": "Entre Ríos", "lat": -30.74, "lon": -59.64,
     "rio": "Río Paraná", "zona_alta": "Cuchilla Montiel"},
    {"nombre": "Victoria", "provincia": "Entre Ríos", "lat": -32.62, "lon": -60.15,
     "rio": "Delta del Paraná", "zona_alta": "Cuchilla Victoria"},
    {"nombre": "Gualeguay", "provincia": "Entre Ríos", "lat": -33.14, "lon": -59.31,
     "rio": "Río Gualeguay", "zona_alta": "Cuchilla de Gualeguay"},
    {"nombre": "Gualeguaychú", "provincia": "Entre Ríos", "lat": -33.01, "lon": -58.51,
     "rio": "Río Gualeguaychú", "zona_alta": "Lomas de Gualeguaychú"},
    {"nombre": "Paraná", "provincia": "Entre Ríos", "lat": -31.73, "lon": -60.52,
     "rio": "Río Paraná", "zona_alta": "Lomas de Paraná"},
]

# ============================================================
# SEMÁFORO
# ============================================================

NIVELES = {
    "ROJO": {"emoji": "🔴", "color": "#dc3545"},
    "NARANJA": {"emoji": "🟠", "color": "#fd7e14"},
    "AMARILLO": {"emoji": "🟡", "color": "#d99b00"},
    "VERDE": {"emoji": "🟢", "color": "#28a745"},
}

ACCIONES = {
    "ROJO": (
        "Evaluar traslado preventivo de hacienda hacia zonas altas, "
        "proteger maquinaria y revisar drenajes y accesos productivos."
    ),
    "NARANJA": (
        "Preparar traslado preventivo de hacienda, revisar drenajes, "
        "bajos y accesos, y anticipar tareas que puedan verse afectadas."
    ),
    "AMARILLO": (
        "Monitorear sectores bajos, revisar drenajes y mantener seguimiento "
        "de la evolución de las precipitaciones."
    ),
    "VERDE": (
        "Monitoreo normal. No se detectan condiciones meteorológicas "
        "relevantes para el modelo de anegamiento."
    ),
}


def evaluar_estado(lluvia_reciente_72h, lluvia_pronostico_72h,
                   lluvia_pronostico_7d, humedad_indice):
    """
    Modelo experimental.
    La humedad es un índice operativo, no saturación hidrológica.
    """

    suelo_vulnerable = humedad_indice >= 80.0
    suelo_muy_humedo = humedad_indice >= 90.0

    # Riesgo de corto plazo: suelo vulnerable + lluvia reciente o prevista.
    riesgo_corto = (
        (lluvia_reciente_72h >= 50.0 and suelo_vulnerable)
        or (lluvia_pronostico_72h >= 60.0 and suelo_vulnerable)
    )

    # Riesgo acumulado: combinación de condición antecedente + pronóstico.
    riesgo_acumulado = (
        lluvia_reciente_72h >= 70.0
        and lluvia_pronostico_7d >= 70.0
        and suelo_vulnerable
    )

    if riesgo_corto or riesgo_acumulado or (
        suelo_muy_humedo and lluvia_pronostico_7d >= 90.0
    ):
        return "ROJO"

    if (
        (lluvia_reciente_72h >= 40.0 or lluvia_pronostico_72h >= 50.0)
        and suelo_vulnerable
    ):
        return "NARANJA"

    if (
        lluvia_reciente_72h >= 25.0
        or lluvia_pronostico_72h >= 35.0
        or lluvia_pronostico_7d >= 60.0
        or suelo_vulnerable
    ):
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
        "past_days": 3,
        "forecast_days": 7,
    }

    response = requests.get(
        OPEN_METEO_URL,
        params=params,
        timeout=30,
        headers={"User-Agent": "Alerta-Litoral-Agro/2.0"},
    )
    response.raise_for_status()

    data = response.json()
    if isinstance(data, dict):
        data = [data]

    if len(data) != len(NODOS):
        raise RuntimeError(
            f"Open-Meteo devolvió {len(data)} ubicaciones y se esperaban {len(NODOS)}."
        )

    ahora = datetime.now(TZ_ARG)
    hoy = ahora.date()
    resultados = []

    for nodo, item in zip(NODOS, data):
        daily = item.get("daily", {})
        hourly = item.get("hourly", {})

        fechas = daily.get("time", [])
        precipitaciones = daily.get("precipitation_sum", [])

        # --------------------------------------------------------
        # Precipitación reciente y pronosticada
        # --------------------------------------------------------
        lluvia_reciente_24h = 0.0
        lluvia_reciente_72h = 0.0
        lluvia_pronostico_24h = 0.0
        lluvia_pronostico_72h = 0.0
        lluvia_pronostico_7d = 0.0

        if fechas and precipitaciones:
            pares = []
            for fecha, lluvia in zip(fechas, precipitaciones):
                try:
                    fecha_dt = datetime.fromisoformat(fecha).date()
                    pares.append((fecha_dt, float(lluvia or 0)))
                except Exception:
                    continue

            recientes = [lluvia for fecha, lluvia in pares if fecha < hoy]
            futuros = [lluvia for fecha, lluvia in pares if fecha >= hoy]

            # Los acumulados diarios recientes son una aproximación
            # meteorológica de condición antecedente.
            lluvia_reciente_24h = recientes[-1] if recientes else 0.0
            lluvia_reciente_72h = sum(recientes[-3:])

            lluvia_pronostico_24h = futuros[0] if futuros else 0.0
            lluvia_pronostico_72h = sum(futuros[:3])
            lluvia_pronostico_7d = sum(futuros[:7])

        # --------------------------------------------------------
        # Humedad superficial actual 0–7 cm
        # --------------------------------------------------------
        horas = hourly.get("time", [])
        humedad = hourly.get("soil_moisture_0_to_7cm", [])

        humedad_actual = None

        if horas and humedad:
            candidatos = []

            for h, valor in zip(horas, humedad):
                try:
                    dt = datetime.fromisoformat(h)
                    dt = dt.replace(tzinfo=TZ_ARG)
                    diferencia = abs((dt - ahora).total_seconds())

                    if valor is not None:
                        candidatos.append((diferencia, float(valor)))
                except Exception:
                    continue

            if candidatos:
                candidatos.sort(key=lambda x: x[0])
                humedad_actual = candidatos[0][1]

        # Índice operativo: referencia 0.45 m3/m3.
        # NO es saturación hidrológica.
        if humedad_actual is None:
            humedad_indice = None
        else:
            humedad_indice = max(
                0.0,
                min(100.0, (humedad_actual / 0.45) * 100.0),
            )

        if humedad_indice is None:
            estado = "AMARILLO"
        else:
            estado = evaluar_estado(
                lluvia_reciente_72h,
                lluvia_pronostico_72h,
                lluvia_pronostico_7d,
                humedad_indice,
            )

        resultados.append(
            {
                **nodo,
                "lluvia_reciente_24h": lluvia_reciente_24h,
                "lluvia_reciente_72h": lluvia_reciente_72h,
                "lluvia_pronostico_24h": lluvia_pronostico_24h,
                "lluvia_pronostico_72h": lluvia_pronostico_72h,
                "lluvia_pronostico_7d": lluvia_pronostico_7d,
                "humedad_volumetrica": humedad_actual,
                "humedad_indice": humedad_indice,
                "estado": estado,
            }
        )

    return resultados


# ============================================================
# INTERFAZ
# ============================================================

ahora = datetime.now(TZ_ARG)

st.title("🌧️ Alerta Litoral Agro")
st.subheader(
    "Prototipo operativo de alerta temprana para riesgo de anegamiento agropecuario"
)

st.info(
    "📍 Área principal: Santa Fe, Corrientes y Entre Ríos  •  "
    "Modelo experimental de apoyo a la toma de decisiones"
)

with st.sidebar:
    st.header("⚙️ Control")

    if st.button("🔄 Actualizar datos ahora", use_container_width=True):
        consultar_open_meteo.clear()
        st.rerun()

    st.markdown("---")
    st.markdown("### 📡 Fuentes dinámicas")
    st.markdown(
        "- 🌧️ **Open-Meteo** — precipitación y humedad del suelo\n"
        "- 🗺️ **OpenStreetMap / Esri** — cartografía\n"
        "- 🛰️ **Windy / CIRA** — monitoreo complementario\n"
    )

    st.markdown("---")
    st.markdown("### ⚠️ Alcance")
    st.caption(
        "La aplicación es un prototipo académico funcional. "
        "No reemplaza alertas oficiales, mediciones de campo ni "
        "información hidrológica de organismos competentes."
    )

# ============================================================
# CARGA DE DATOS
# ============================================================

try:
    datos = consultar_open_meteo()
    sistema_ok = True
    error_datos = None
except Exception as exc:
    datos = []
    sistema_ok = False
    error_datos = str(exc)

# ============================================================
# ESTADO DEL SISTEMA
# ============================================================

if sistema_ok:
    st.success(
        f"🟢 **SISTEMA OPERATIVO** — datos meteorológicos disponibles. "
        f"Consulta: {ahora.strftime('%d/%m/%Y %H:%M')} ART"
    )
else:
    st.error(
        "🔴 **DATOS NO ACTUALIZADOS** — no fue posible consultar Open-Meteo. "
        "No se debe interpretar el semáforo como información vigente."
    )
    st.code(error_datos)

if not sistema_ok:
    st.stop()

df = pd.DataFrame(datos)

st.caption(
    f"Última consulta realizada: **{ahora.strftime('%d/%m/%Y %H:%M')} ART** · "
    "La hora de consulta no implica que todas las variables tengan esa misma "
    "hora de observación."
)

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
    location=[-30.5, -59.5],
    zoom_start=6,
    tiles="OpenStreetMap",
    control_scale=True,
)

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
        f"{row['humedad_indice']:.1f}%"
        if row["humedad_indice"] is not None
        else "Sin dato"
    )

    humedad_vol = (
        f"{row['humedad_volumetrica']:.3f} m³/m³"
        if row["humedad_volumetrica"] is not None
        else "Sin dato"
    )

    popup_html = f"""
    <div style="width:320px;font-family:Arial,sans-serif">
        <h3 style="margin-bottom:5px">
            {nivel["emoji"]} {row["nombre"]}
        </h3>

        <b>Provincia:</b> {row["provincia"]}<br>
        <b>Estado del modelo:</b> {nivel["emoji"]} {row["estado"]}<br>

        <hr>

        🌧️ <b>Reciente 24 h:</b> {row["lluvia_reciente_24h"]:.1f} mm<br>
        🌧️ <b>Reciente 72 h:</b> {row["lluvia_reciente_72h"]:.1f} mm<br>
        🔮 <b>Pronóstico 24 h:</b> {row["lluvia_pronostico_24h"]:.1f} mm<br>
        🔮 <b>Pronóstico 72 h:</b> {row["lluvia_pronostico_72h"]:.1f} mm<br>
        🔮 <b>Pronóstico 7 días:</b> {row["lluvia_pronostico_7d"]:.1f} mm<br>

        <hr>

        💧 <b>Índice de humedad superficial:</b> {humedad_texto}<br>
        <small>Humedad modelada 0–7 cm: {humedad_vol}</small>

        <hr>

        🌊 <b>Referencia hídrica:</b> {row["rio"]}<br>
        ⛰️ <b>Zona alta de referencia:</b> {row["zona_alta"]}<br>

        <hr>

        <b>Acción sugerida:</b><br>
        {ACCIONES[row["estado"]]}

        <hr>
        <small>
        ⚠️ El semáforo es un modelo experimental y no representa
        una alerta oficial ni una medición directa de inundación.
        </small>
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
        popup=folium.Popup(popup_html, max_width=370),
        tooltip=(
            f'{nivel["emoji"]} {row["nombre"]} — {row["estado"]} '
            f'| Humedad: {humedad_texto}'
        ),
    ).add_to(m)

folium.LayerControl(collapsed=False).add_to(m)

st_folium(
    m,
    width=None,
    height=700,
    returned_objects=[],
)

# ============================================================
# TABLA OPERATIVA
# ============================================================

st.markdown("## 📊 Estado por nodo")

tabla = df[
    [
        "nombre",
        "provincia",
        "estado",
        "lluvia_reciente_24h",
        "lluvia_reciente_72h",
        "lluvia_pronostico_24h",
        "lluvia_pronostico_72h",
        "lluvia_pronostico_7d",
        "humedad_indice",
    ]
].copy()

tabla.columns = [
    "Localidad",
    "Provincia",
    "Semáforo",
    "Reciente 24 h (mm)",
    "Reciente 72 h (mm)",
    "Pronóstico 24 h (mm)",
    "Pronóstico 72 h (mm)",
    "Pronóstico 7 días (mm)",
    "Índice humedad 0–7 cm (%)",
]

tabla["Semáforo"] = tabla["Semáforo"].map(
    lambda x: f'{NIVELES[x]["emoji"]} {x}'
)

for columna in tabla.columns[3:]:
    tabla[columna] = tabla[columna].round(1)

st.dataframe(
    tabla,
    use_container_width=True,
    hide_index=True,
)

# ============================================================
# ACCIONES OPERATIVAS
# ============================================================

st.markdown("---")
st.markdown("## 🎯 Acciones orientativas según el nivel")

acciones_df = pd.DataFrame(
    [
        ["🔴 ROJO", "Acción inmediata",
         "Evaluar traslado preventivo de hacienda, proteger maquinaria y revisar drenajes."],
        ["🟠 NARANJA", "Preparación",
         "Preparar hacienda, revisar bajos, drenajes y accesos productivos."],
        ["🟡 AMARILLO", "Vigilancia",
         "Monitorear sectores bajos y evolución de lluvia y humedad."],
        ["🟢 VERDE", "Monitoreo normal",
         "Sin condiciones relevantes detectadas por el modelo."],
    ],
    columns=["Nivel", "Interpretación", "Acción sugerida"],
)

st.dataframe(
    acciones_df,
    use_container_width=True,
    hide_index=True,
)

# ============================================================
# MONITOREO COMPLEMENTARIO
# ============================================================

st.markdown("---")
st.markdown("## 🛰️ Monitoreo meteorológico complementario")

r1, r2 = st.columns(2)

with r1:
    st.markdown("### 🌀 Radar y modelos")
    st.markdown(
        "Consulta complementaria de precipitación, radar, viento y modelos."
    )

    st.components.v1.iframe(
        "https://embed.windy.com/embed2.html"
        "?lat=-30.5&lon=-59.5"
        "&detailLat=-30.5&detailLon=-59.5"
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
        "Visor externo para seguimiento de nubosidad y actividad eléctrica."
    )

    st.link_button(
        "⚡ Abrir visor CIRA GOES-19 / GLM",
        "https://slider.cira.colostate.edu/",
        use_container_width=True,
    )

# ============================================================
# METODOLOGÍA
# ============================================================

st.markdown("---")
st.markdown("## 🧭 ¿Cómo funciona el semáforo?")

st.markdown(
    """
El sistema combina tres componentes:

**1. Condición antecedente**
- Precipitación reciente estimada por Open-Meteo.
- Acumulado de las últimas 72 horas disponibles.

**2. Condición actual del suelo**
- Humedad volumétrica modelada en los primeros 0–7 cm.
- Se transforma en un **índice de humedad superficial** para el modelo.

**3. Lluvia futura**
- Pronóstico de 24 horas.
- Pronóstico de 72 horas.
- Acumulado pronosticado de 7 días.

### Interpretación

| Nivel | Lógica general |
|---|---|
| 🔴 **ROJO** | Condición antecedente o futura importante + suelo vulnerable |
| 🟠 **NARANJA** | Lluvia relevante + suelo vulnerable |
| 🟡 **AMARILLO** | Lluvia moderada o humedad elevada |
| 🟢 **VERDE** | Sin condiciones relevantes según el modelo |

**Los umbrales son experimentales.** Deben calibrarse y validarse con
eventos históricos, estaciones de observación y/o información hidrológica
antes de utilizarse para decisiones oficiales de emergencia.
"""
)

# ============================================================
# LIMITACIONES Y PRÓXIMA EVOLUCIÓN
# ============================================================

st.markdown("---")
st.markdown("## 🔬 Limitaciones actuales y evolución prevista")

st.markdown(
    """
### Limitaciones actuales

- La precipitación reciente utilizada por el modelo es una estimación
  meteorológica de Open-Meteo y no una red propia de pluviómetros.
- La humedad del suelo es modelada y corresponde a una capa superficial
  de 0–7 cm; no equivale a la saturación de todo el perfil.
- El sistema todavía no incorpora niveles hidrométricos en tiempo real.
- Los umbrales del semáforo son experimentales y requieren validación histórica.
- El sistema no reemplaza alertas oficiales.

### Próximas mejoras

1. Incorporar niveles de ríos y tendencia hidrométrica.
2. Incorporar estaciones/pluviómetros observados.
3. Validar los umbrales con eventos históricos de anegamiento.
4. Incorporar un índice de vulnerabilidad por tipo de suelo y uso agropecuario.
5. Generar historial de alertas para evaluar el desempeño del modelo.
"""
)

st.markdown("---")
st.caption(
    "Alerta Litoral Agro v2.0 — prototipo académico funcional. "
    "Sistema experimental de apoyo a la toma de decisiones frente al "
    "riesgo de anegamiento agropecuario."
)
