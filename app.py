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

# Coordenadas exactas de los nodos para consulta en vivo
COORDS_NODOS = {
    "Concordia (ER)": {"lat": -31.39, "lon": -58.02, "prov": "ER"},
    "Goya (CR)": {"lat": -29.14, "lon": -59.26, "prov": "CR"},
    "Mercedes (CR)": {"lat": -29.18, "lon": -58.07, "prov": "CR"},
    "Reconquista (SF)": {"lat": -29.15, "lon": -59.65, "prov": "SF"},
    "Santa Fe Capital (SF)": {"lat": -31.63, "lon": -60.7, "prov": "SF"}
}

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
zona_sel = st.sidebar.selectbox("Zona Crítica", list(COORDS_NODOS.keys()))

# Consultamos en tiempo real el estado de la zona elegida
info_coord = COORDS_NODOS[zona_sel]
estado_real, detalle_real, color_badge = consultar_estado_real(info_coord["lat"], info_coord["lon"])

st.sidebar.markdown(f"**Estado Real en Vivo:**")
st.sidebar.markdown(f"<div style='background-color: {color_badge}; color: white; padding: 6px; border-radius: 5px; text-align: center; font-weight: bold;'>{estado_real}</div>", unsafe_allow_html=True)
st.sidebar.caption(detalle_real)

if st.sidebar.button("📲 Enviar Alerta de esta Zona a Telegram"):
    exito = enviar_alerta_telegram(zona_sel, estado_real, detalle_real)
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
