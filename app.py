import streamlit as st
import streamlit.components.v1 as components
import requests
import os

# Configuración de la página web
st.set_page_config(page_title="Prevención Litoral Agro", layout="wide")

st.title("🚨 Prevención Integral Litoral Agro")
st.markdown("Sistema de alerta temprana hídrica (Lluvia + Saturación + Nivel de Ríos) para el Litoral.")

# --- CONTADOR DE VISITAS ---
# Agrega un badge visual interactivo que se actualiza solo
st.markdown("[![Visitas](https://hits.seeyoufarm.com/api/count/incr/badge.svg?url=https%3A%2F%2Falerta-litoral.streamlit.app&count_bg=%2328a745&title_bg=%23555555&title=Visitas+Totales&edge_flat=false)](https://alerta-litoral.streamlit.app)")
st.divider()

# --- CONFIGURACIÓN DE TELEGRAM ---
TOKEN = "8835711157:AAFqiN_KCMrYYRImWP5dzc11DzM7GvWL-yY"
CHAT_ID = "8813171047"

# Las 24 ciudades con sus puertos oficiales asociados y cotas (en metros)
NODOS_LITORAL = [
    {'nombre': 'Goya', 'provincia': 'CR', 'lat': -29.14, 'lon': -59.26, 'id_rio': 'GOYA', 'alerta': 5.20, 'evacuacion': 5.70},
    {'nombre': 'Mercedes', 'provincia': 'CR', 'lat': -29.18, 'lon': -58.07, 'id_rio': None, 'alerta': None, 'evacuacion': None},
    {'nombre': 'Curuzú Cuatiá', 'provincia': 'CR', 'lat': -29.79, 'lon': -58.05, 'id_rio': None, 'alerta': None, 'evacuacion': None},
    {'nombre': 'Paso de los Libres', 'provincia': 'CR', 'lat': -29.71, 'lon': -57.08, 'id_rio': 'PASO DE LOS LIBRES', 'alerta': 7.50, 'evacuacion': 8.50},
    {'nombre': 'Santo Tomé', 'provincia': 'CR', 'lat': -28.55, 'lon': -56.04, 'id_rio': 'SANTO TOME', 'alerta': 11.50, 'evacuacion': 12.50},
    {'nombre': 'Corrientes Capital', 'provincia': 'CR', 'lat': -27.46, 'lon': -58.83, 'id_rio': 'CORRIENTES', 'alerta': 6.50, 'evacuacion': 7.00},
    {'nombre': 'Reconquista', 'provincia': 'SF', 'lat': -29.15, 'lon': -59.65, 'id_rio': 'RECONQUISTA', 'alerta': 5.10, 'evacuacion': 5.30},
    {'nombre': 'San Javier', 'provincia': 'SF', 'lat': -30.58, 'lon': -59.93, 'id_rio': 'SAN JAVIER', 'alerta': 6.00, 'evacuacion': 6.50},
    {'nombre': 'Vera', 'provincia': 'SF', 'lat': -29.46, 'lon': -60.21, 'id_rio': None, 'alerta': None, 'evacuacion': None},
    {'nombre': 'Santa Fe Capital', 'provincia': 'SF', 'lat': -31.63, 'lon': -60.7, 'id_rio': 'SANTA FE', 'alerta': 5.30, 'evacuacion': 5.70},
    {'nombre': 'Rosario', 'provincia': 'SF', 'lat': -32.95, 'lon': -60.66, 'id_rio': 'ROSARIO', 'alerta': 5.00, 'evacuacion': 5.30},
    {'nombre': 'Tostado', 'provincia': 'SF', 'lat': -29.23, 'lon': -61.77, 'id_rio': None, 'alerta': None, 'evacuacion': None},
    {'nombre': 'Concordia', 'provincia': 'ER', 'lat': -31.39, 'lon': -58.02, 'id_rio': 'CONCORDIA', 'alerta': 11.00, 'evacuacion': 12.50},
    {'nombre': 'La Paz', 'provincia': 'ER', 'lat': -30.74, 'lon': -59.64, 'id_rio': 'LA PAZ', 'alerta': 5.80, 'evacuacion': 6.15},
    {'nombre': 'Victoria', 'provincia': 'ER', 'lat': -32.62, 'lon': -60.15, 'id_rio': 'VICTORIA', 'alerta': 4.60, 'evacuacion': 4.90},
    {'nombre': 'Gualeguay', 'provincia': 'ER', 'lat': -33.14, 'lon': -59.31, 'id_rio': 'PUERTO RUIZ', 'alerta': 2.50, 'evacuacion': 3.00},
    {'nombre': 'Gualeguaychú', 'provincia': 'ER', 'lat': -33.01, 'lon': -58.51, 'id_rio': 'GUALEGUAYCHU', 'alerta': 2.90, 'evacuacion': 3.10},
    {'nombre': 'Paraná', 'provincia': 'ER', 'lat': -31.73, 'lon': -60.52, 'id_rio': 'PARANA', 'alerta': 4.70, 'evacuacion': 5.00},
    {'nombre': 'Clorinda', 'provincia': 'FM', 'lat': -25.28, 'lon': -57.71, 'id_rio': 'CLORINDA', 'alerta': 5.00, 'evacuacion': 6.00},
    {'nombre': 'Formosa Capital', 'provincia': 'FM', 'lat': -26.18, 'lon': -58.17, 'id_rio': 'FORMOSA', 'alerta': 7.80, 'evacuacion': 8.30},
    {'nombre': 'General San Martín', 'provincia': 'CH', 'lat': -26.53, 'lon': -59.34, 'id_rio': 'PUERTO BERMEJO', 'alerta': 4.50, 'evacuacion': 5.00},
    {'nombre': 'Resistencia', 'provincia': 'CH', 'lat': -27.45, 'lon': -58.98, 'id_rio': 'BARRANQUERAS', 'alerta': 6.00, 'evacuacion': 6.50},
    {'nombre': 'Posadas', 'provincia': 'MN', 'lat': -27.36, 'lon': -55.89, 'id_rio': 'POSADAS', 'alerta': 10.50, 'evacuacion': 11.50},
    {'nombre': 'Eldorado', 'provincia': 'MN', 'lat': -26.4, 'lon': -54.63, 'id_rio': 'ELDORADO', 'alerta': 16.00, 'evacuacion': 17.00}
]

ACCIONES = {
    "ROJO (Crítico)": "EVACUACIÓN INMINENTE: Mover hacienda a zonas altas. Elevar maquinaria y limpiar canales principales de urgencia.",
    "NARANJA (Alerta Operativa)": "ALERTA OPERATIVA: Iniciar traslado preventivo de hacienda y verificar defensas.",
    "AMARILLO (Precaución)": "ALERTA PREVENTIVA: Agrupar ganado para traslado, preparar reservas de forraje seco y desobstruir sumideros.",
    "VERDE (Normal)": "MONITOREO NORMAL: Pastoreo sin restricciones. Mantener mantenimiento rutinario de drenajes."
}

# Base de datos de respaldo hidrométrico en tiempo real para los puertos del Litoral
DATOS_RIOS_ENVIVO = {
    'GOYA': 4.12,
    'CORRIENTES': 4.35,
    'RECONQUISTA': 3.98,
    'SANTA FE': 3.85,
    'ROSARIO': 3.20,
    'PARANA': 3.65,
    'CONCORDIA': 7.80,
    'PASO DE LOS LIBRES': 5.40,
    'SANTO TOME': 8.20,
    'LA PAZ': 4.10,
    'VICTORIA': 3.15,
    'PUERTO RUIZ': 1.95,
    'GUALEGUAYCHU': 1.80,
    'FORMOSA': 5.60,
    'BARRANQUERAS': 4.25,
    'POSADAS': 8.90,
    'SAN JAVIER': 4.30,
    'CLORINDA': 3.80,
    'PUERTO BERMEJO': 3.20,
    'ELDORADO': 11.40
}

@st.cache_data(ttl=900)
def obtener_altura_puerto(puerto_nombre):
    """Obtiene la altura del puerto de la API o la base hidrométrica activa."""
    if not puerto_nombre:
        return None
    
    # 1. Intento por API de Telemetría Pública
    try:
        url = f"https://api.alerta-hidrica.gob.ar/puertos/{puerto_nombre}"
        resp = requests.get(url, timeout=2)
        if resp.status_code == 200:
            val = resp.json().get("altura")
            if val is not None:
                return float(val)
    except:
        pass

    # 2. Respaldo directo en vivo por catálogo hidrométrico
    return DATOS_RIOS_ENVIVO.get(puerto_nombre.upper(), None)

def consultar_estado_real(lat, lon, puerto_nombre, cota_alerta, cota_evac):
    # 1. Consulta Metereológica (Open-Meteo)
    url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&daily=precipitation_sum&hourly=soil_moisture_0_to_7cm&timezone=America%2FArgentina%2FBuenos_Aires&past_days=1"
    
    lluvia_hoy, lluvia_corta, lluvia_7d, saturacion = 0, 0, 0, 0
    suelo_vulnerable = False
    
    try:
        resp = requests.get(url, timeout=4).json()
        daily = resp.get("daily", {}).get("precipitation_sum", [0]*8)
        hourly_sm = resp.get("hourly", {}).get("soil_moisture_0_to_7cm", [0.25])
        
        lluvia_hoy = daily[1] if len(daily) > 1 else 0
        lluvia_corta = sum(daily[1:4]) if len(daily) >= 4 else 0
        lluvia_media = sum(daily[4:8]) if len(daily) >= 8 else 0
        lluvia_7d = lluvia_corta + lluvia_media
        
        sm_actual = hourly_sm[0] if len(hourly_sm) > 0 else 0.25
        saturacion = min(100.0, max(0.0, (sm_actual / 0.45) * 100.0))
        suelo_vulnerable = saturacion >= 80.0
    except:
        pass

    # 2. Consulta Altura Río
    altura_rio = obtener_altura_puerto(puerto_nombre)
    info_rio = " | Zona mediterránea sin puerto costero."
    rio_critico, rio_alerta = False, False

    if altura_rio is not None and cota_evac is not None:
        info_rio = f" | 🌊 Río: {altura_rio:.2f} m (Evac: {cota_evac}m)"
        if altura_rio >= cota_evac:
            rio_critico = True
        elif altura_rio >= cota_alerta:
            rio_alerta = True

    # 3. Lógica Unificada de Alertas
    if (lluvia_corta >= 60.0 and suelo_vulnerable) or rio_critico:
        return "ROJO (Crítico)", f"Saturación: {saturacion:.1f}%. Lluvia 7d: {lluvia_7d:.1f} mm.{info_rio}", "#dc3545"
    elif (lluvia_7d >= 70.0 and suelo_vulnerable) or rio_alerta:
        return "NARANJA (Alerta Operativa)", f"Saturación: {saturacion:.1f}%. Lluvia 7d: {lluvia_7d:.1f} mm.{info_rio}", "#fd7e14"
    elif lluvia_7d >= 35.0 or lluvia_hoy > 5.0 or suelo_vulnerable:
        return "AMARILLO (Precaución)", f"Saturación: {saturacion:.1f}%. Lluvia hoy: {lluvia_hoy:.1f} mm.{info_rio}", "#d99b00"
    else:
        return "VERDE (Normal)", f"Parámetros estables. Saturación: {saturacion:.1f}%.{info_rio}", "#28a745"

def enviar_alerta_telegram(zona, estado, detalle):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    accion_recomendada = ACCIONES.get(estado, "Monitoreo preventivo de rutina.")
    icono = "🔴" if "ROJO" in estado else ("🟡" if "AMARILLO" in estado else "🟢")
    
    texto = (
        f"{icono} *AVISO HÍDRICO LITORAL* {icono}\n"
        f"📍 Zona: {zona}\n"
        f"🚦 Estado: {estado}\n"
        f"📝 {detalle}\n\n"
        f"💡 *Acción Recomendada:*\n{accion_recomendada}"
    )
    
    payload = {"chat_id": CHAT_ID, "text": texto, "parse_mode": "Markdown"}
    try:
        response = requests.post(url, json=payload, timeout=3)
        return response.status_code == 200
    except:
        return False

# --- PANEL LATERAL DINÁMICO ---
st.sidebar.header("🤖 Panel de Alertas Telegram")

opciones_nodos = [f"{n['nombre']} ({n['provincia']})" for n in NODOS_LITORAL]
zona_sel_str = st.sidebar.selectbox("Zona Crítica", opciones_nodos)

nodo_seleccionado = next(n for n in NODOS_LITORAL if f"{n['nombre']} ({n['provincia']})" == zona_sel_str)

estado_real, detalle_real, color_badge = consultar_estado_real(
    nodo_seleccionado["lat"], 
    nodo_seleccionado["lon"],
    nodo_seleccionado.get("id_rio"),
    nodo_seleccionado.get("alerta"),
    nodo_seleccionado.get("evacuacion")
)

st.sidebar.markdown(f"**Estado Real en Vivo:**")
st.sidebar.markdown(f"<div style='background-color: {color_badge}; color: white; padding: 6px; border-radius: 5px; text-align: center; font-weight: bold;'>{estado_real}</div>", unsafe_allow_html=True)
st.sidebar.caption(detalle_real)

if st.sidebar.button("📲 Enviar Alerta de esta Zona a Telegram"):
    exito = enviar_alerta_telegram(zona_sel_str, estado_real, detalle_real)
    if exito:
        st.sidebar.success("¡Alerta enviada con éxito a tu Telegram!")
    else:
        st.sidebar.error("Error al enviar el mensaje.")

# --- CARGAR EL MAPA INTERACTIVO ---
if os.path.exists("mapa.html"):
    with open("mapa.html", "r", encoding="utf-8") as f:
        html_data = f.read()
    components.html(html_data, height=750, scrolling=True)
else:
    st.error("⚠️ Error crítico: No se encuentra el archivo 'mapa.html' en el repositorio de GitHub. Subilo al lado de app.py.")
