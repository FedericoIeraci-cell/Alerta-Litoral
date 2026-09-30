import streamlit as st
import folium
from streamlit_folium import st_folium

# Configuración de la web
st.set_page_config(page_title="Prevención Litoral Agro", layout="wide")
st.title("🚨 Prevención Integral Litoral Agro")
st.markdown("Sistema de alerta temprana y semáforo hídrico para productores.")

# Inicializamos el mapa
mapa = folium.Map(location=[-28.5, -58.5], zoom_start=6)

# =====================================================================
# #!/usr/bin/env python3
"""
