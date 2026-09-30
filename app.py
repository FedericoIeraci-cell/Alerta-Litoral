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

def enviar_alerta_telegram(zona, estado, mensaje_extra):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    texto = f"🚨 *ALERTA HÍDRICA LITORAL* 🚨\n📍 Zona: {zona}\n🚦 Estado: {estado}\n📝 {mensaje_extra}"
    payload = {"chat_id": CHAT_ID, "text": texto, "parse_mode": "Markdown"}
    try:
        response = requests.post(url, json=payload)
        return response.status_code == 200
    except:
        return False

# --- PANEL LATERAL ---
st.sidebar.header("🤖 Panel de Alertas Telegram")
zona_sel = st.sidebar.selectbox("Zona Crítica", ["Concordia (ER)", "Goya (CR)", "Mercedes (CR)"])
estado_sel = st.sidebar.selectbox("Estado del Semáforo", ["ROJO (Crítico)", "AMARILLO (Precaución)", "VERDE (Normal)"])
detalle = st.sidebar.text_input("Detalle operativo", "Saturación de suelo elevada. Riesgo de desborde.")

if st.sidebar.button("📲 Disparar Alerta a Telegram"):
    exito = enviar_alerta_telegram(zona_sel, estado_sel, detalle)
    if exito:
        st.sidebar.success("¡Alerta enviada con éxito a tu Telegram!")
    else:
        st.sidebar.error("Error al enviar el mensaje. Verificá la conexión.")

# --- CARGAR EL MAPA INTERACTIVO ---
try:
    with open("mapa.html", "r", encoding="utf-8") as f:
        html_data = f.read()
    components.html(html_data, height=700, scrolling=True)
except FileNotFoundError:
    st.error("No se encontró el archivo 'mapa.html'. Asegurate de haberlo subido al repositorio.")
# --- EVALUACIÓN AUTOMÁTICA AL CARGAR LA PÁGINA ---
# Aquí simulas la lectura de tu fuente de datos (ej. si el nivel supera el umbral crítico)
estado_actual_semaforo = "ROJO (Crítico)"  # Esto vendría de tu lógica de datos
zona_critica = "Goya (CR)"

# Bandera para evitar spam: Streamlit guarda estado en la sesión
if "alerta_enviada" not in st.session_state:
    if "ROJO" in estado_actual_semaforo:
        mensaje_auto = f"🚨 *AVISO AUTOMÁTICO DE RIESGO* 🚨\nLa estación de {zona_critica} registró parámetros en nivel ROJO. Se recomienda activar protocolos preventivos."
        
        # Envío automático al canal o chat configurado
        url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
        payload = {"chat_id": CHAT_ID, "text": mensaje_auto, "parse_mode": "Markdown"}
        
        try:
            requests.post(url, json=payload)
            st.session_state.alerta_enviada = True  # Marca que ya se envió en esta sesión
        except:
            pass
