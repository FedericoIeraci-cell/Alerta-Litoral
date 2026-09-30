import streamlit as st
import streamlit.components.v1 as components

# Configuración de la página web
st.set_page_config(page_title="Prevención Litoral Agro", layout="wide")

st.title("🚨 Prevención Integral Litoral Agro")
st.markdown("Sistema de alerta temprana y semáforo hídrico para productores del Litoral.")

# Cargamos el archivo HTML del mapa interactivo
try:
    with open("mapa.html", "r", encoding="utf-8") as f:
        html_data = f.read()
    # Mostramos el mapa dentro de la página web de Streamlit
    components.html(html_data, height=700, scrolling=True)
except FileNotFoundError:
    st.error("No se encontró el archivo 'mapa.html'. Asegurate de haberlo subido al repositorio.")
