import requests

TOKEN = "8835711157:AAFqiN_KCMrYYRImWP5dzc11DzM7GvWL-yY"
CHAT_ID = "8813171047"

# Simulación de lectura de datos o consulta a API de estaciones del Litoral
zona_analizada = "Goya (CR)"
saturacion_suelo = 85.5  # Dato simulado que supera el umbral

if saturacion_suelo > 80.0:
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    texto = (
        f"🚨 *ALERTA AUTOMÁTICA 24/7* 🚨\n"
        f"📍 Zona: {zona_analizada}\n"
        f"🚦 Estado: ROJO\n"
        f"📝 Monitoreo autónomo: Saturación crítica detectada ({saturacion_suelo}%)."
    )
    payload = {"chat_id": CHAT_ID, "text": texto, "parse_mode": "Markdown"}
    requests.post(url, json=payload)
