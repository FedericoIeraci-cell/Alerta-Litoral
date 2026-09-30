import streamlit as st
import streamlit.components.v1 as components
import requests

# Configuración de la página web
st.set_page_config(page_title="Prevención Litoral Agro", layout="wide")

st.title("🚨 Prevención Integral Litoral Agro")
st.markdown("Sistema de alerta temprana y semáforo hídrico para productores del Litoral.")

# --- CONFIGURACIÓN DE TELEGRAM ---
TOKEN = "8835711157:AAFqiN_KCMrYYRImWP5dzc11DzM7GvWL-yY"
CHAT_ID = "8813171047"

# Las 24 ciudades completas del Litoral sincronizadas con el mapa
NODOS_LITORAL = [
    {'nombre': 'Goya', 'provincia': 'CR', 'lat': -29.14, 'lon': -59.26},
    {'nombre': 'Mercedes', 'provincia': 'CR', 'lat': -29.18, 'lon': -58.07},
    {'nombre': 'Curuzú Cuatiá', 'provincia': 'CR', 'lat': -29.79, 'lon': -58.05},
    {'nombre': 'Paso de los Libres', 'provincia': 'CR', 'lat': -29.71, 'lon': -57.08},
    {'nombre': 'Santo Tomé', 'provincia': 'CR', 'lat': -28.55, 'lon': -56.04},
    {'nombre': 'Corrientes Capital', 'provincia': 'CR', 'lat': -27.46, 'lon': -58.83},
    {'nombre': 'Reconquista', 'provincia': 'SF', 'lat': -29.15, 'lon': -59.65},
    {'nombre': 'San Javier', 'provincia': 'SF', 'lat': -30.58, 'lon': -59.93},
    {'nombre': 'Vera', 'provincia': 'SF', 'lat': -29.46, 'lon': -60.21},
    {'nombre': 'Santa Fe Capital', 'provincia': 'SF', 'lat': -31.63, 'lon': -60.7},
    {'nombre': 'Rosario', 'provincia': 'SF', 'lat': -32.95, 'lon': -60.66},
    {'nombre': 'Tostado', 'provincia': 'SF', 'lat': -29.23, 'lon': -61.77},
    {'nombre': 'Concordia', 'provincia': 'ER', 'lat': -31.39, 'lon': -58.02},
    {'nombre': 'La Paz', 'provincia': 'ER', 'lat': -30.74, 'lon': -59.64},
    {'nombre': 'Victoria', 'provincia': 'ER', 'lat': -32.62, 'lon': -60.15},
    {'nombre': 'Gualeguay', 'provincia': 'ER', 'lat': -33.14, 'lon': -59.31},
    {'nombre': 'Gualeguaychú', 'provincia': 'ER', 'lat': -33.01, 'lon': -58.51},
    {'nombre': 'Paraná', 'provincia': 'ER', 'lat': -31.73, 'lon': -60.52},
    {'nombre': 'Clorinda', 'provincia': 'FM', 'lat': -25.28, 'lon': -57.71},
    {'nombre': 'Formosa Capital', 'provincia': 'FM', 'lat': -26.18, 'lon': -58.17},
    {'nombre': 'General San Martín', 'provincia': 'CH', 'lat': -26.53, 'lon': -59.34},
    {'nombre': 'Resistencia', 'provincia': 'CH', 'lat': -27.45, 'lon': -58.98},
    {'nombre': 'Posadas', 'provincia': 'MN', 'lat': -27.36, 'lon': -55.89},
    {'nombre': 'Eldorado', 'provincia': 'MN', 'lat': -26.4, 'lon': -54.63}
]

def consultar_estado_real(lat, lon):
    url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&daily=precipitation_sum&hourly=soil_moisture_0_to_7cm&timezone=America%2FArgentina%2FBuenos_Aires&past_days=1"
    try:
        resp = requests.get(url).json()
        daily = resp.get("daily", {}).get("precipitation_sum", [0]*8)
        hourly_sm = resp.get("hourly", {}).get("soil_moisture_0_to_7cm", [0.25])
        
        lluvia_corta = sum(daily[1:4]) if len(daily) >= 4 else 0
        lluvia_media = sum(daily[4:8]) if len(daily) >= 8 else 0
        lluvia_7d = lluvia_corta + lluvia_media
        lluvia_hoy = daily[1] if len(daily) > 1 else 0
        
        sm_actual = hourly_sm[0] if len(hourly_sm) > 0 else 0.25
        saturacion = min(100.0, max(0.0, (sm_actual / 0.45) * 100.0))
        
        # Misma lógica de umbrales del mapa interactivo
        suelo_vulnerable = saturacion >= 80.0
        if (lluvia_corta >= 60.0 and suelo_vulnerable) or (lluvia_7d >= 90.0 and suelo_vulnerable):
            return "ROJO (Crítico)", f"Saturación crítica: {saturacion:.1f}%. Lluvia 7d: {lluvia_7d:.1f} mm.", "#dc3545"
        elif lluvia_7d >= 70.0 and suelo_vulnerable:
            return "NARANJA (Alerta Operativa)", f"Saturación alta: {saturacion:.1f}%. Lluvia 7d: {lluvia_7d:.1f} mm.", "#fd7e14"
        elif lluvia_7d >= 35.0 or lluvia_hoy > 5.0 or suelo_vulnerable:
            return "AMARILLO (Precaución)", f"Saturación de suelo: {saturacion:.1f}%. Lluvia hoy: {lluvia_hoy:.1f} mm.", "#d99b00"
        else:
            return "VERDE (Normal)", f"Parámetros estables. Saturación: {saturacion:.1f}%.", "#28a745"
    except:
        return "VERDE (Normal)", "Sin conexión en vivo con la API.", "#28a745"

def enviar_alerta_telegram(zona, estado, detalle):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    texto = f"🚨 *ESTADO HÍDRICO LITORAL* 🚨\n📍 Zona: {zona}\n🚦 Estado: {estado}\n📝 {detalle}"
    payload = {"chat_id": CHAT_ID, "text": texto, "parse_mode": "Markdown"}
    try:
        response = requests.post(url, json=payload)
        return response.status_code == 200
    except:
        return False

# --- PANEL LATERAL DINÁMICO ---
st.sidebar.header("🤖 Panel de Alertas Telegram")

# Creamos las opciones formateadas para el selectbox con las 24 ciudades
opciones_nodos = [f"{n['nombre']} ({n['provincia']})" for n in NODOS_LITORAL]
zona_sel_str = st.sidebar.selectbox("Zona Crítica", opciones_nodos)

# Buscamos los datos de la ciudad seleccionada
nodo_seleccionado = next(n for n in NODOS_LITORAL if f"{n['nombre']} ({n['provincia']})" == zona_sel_str)

# Consultamos en tiempo real el estado de la zona elegida
estado_real, detalle_real, color_badge = consultar_estado_real(nodo_seleccionado["lat"], nodo_seleccionado["lon"])

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
try:
    with open("mapa.html", "r", encoding="utf-8") as f:
        html_data = f.read()
    components.html(html_data, height=700, scrolling=True)
except FileNotFoundError:
    st.error("No se encontró el archivo 'mapa.html'. Asegurate de haberlo subido al repositorio.")
