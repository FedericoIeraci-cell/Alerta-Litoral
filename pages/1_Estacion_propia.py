"""Página independiente; no carga el procesamiento regional de app.py."""
import streamlit as st

from panel_estacion import mostrar_estacion_propia

st.set_page_config(page_title="Estación propia · ALA", page_icon="🌡️", layout="wide")
mostrar_estacion_propia()
