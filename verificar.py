import requests

TOKEN = "8835711157:AAFqiN_KCMrYYRImWP5dzc11DzM7GvWL-yY"
CHAT_ID = "8813171047"

# Nodos principales del Litoral configurados en el mapa
nodos = [
    {"nombre": "Goya", "provincia": "CR", "lat": -29.14, "lon": -59.26},
    {"nombre": "Concordia", "provincia": "ER", "lat": -31.39, "lon": -58.02},
    {"nombre": "Mercedes", "provincia": "CR", "lat": -29.18, "lon": -58.07},
    {"nombre": "Reconquista", "provincia": "SF", "lat": -29.15, "lon": -59.65},
    {"nombre": "Santa Fe Capital", "provincia": "SF", "lat": -31.63, "lon": -60.7},
    {"nombre": "Paraná", "provincia": "ER", "lat": -31.73, "lon": -60.52}
]

def enviar_alerta_telegram(zona, detalle):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    texto = f"🚨 *ALERTA HÍDRICA REAL (LITORAL)* 🚨\n📍 Zona: {zona}\n🚦 Estado: ROJO (Crítico)\n📝 {detalle}"
    payload = {"chat_id": CHAT_ID, "text": texto, "parse_mode": "Markdown"}
    requests.post(url, json=payload)

def verificar_datos_reales():
    lats = ",".join([str(n["lat"]) for n in nodos])
    lons = ",".join([str(n["lon"]) for n in nodos])
    
    # Consulta idéntica a la que realiza el mapa en tiempo real
    url_api = f"https://api.open-meteo.com/v1/forecast?latitude={lats}&longitude={lons}&daily=precipitation_sum&hourly=soil_moisture_0_to_7cm&timezone=America%2FArgentina%2FBuenos_Aires&past_days=1"
    
    try:
        response = requests.get(url_api)
        datos = response.json()
        
        # Si la API devuelve una lista de nodos
        resultados = datos if isinstance(datos, list) else [datos]
        
        for i, nodo in enumerate(nodos):
            if i < len(resultados):
                node_data = resultados[i]
                daily = node_data.get("daily", {}).get("precipitation_sum", [0]*8)
                hourly_sm = node_data.get("hourly", {}).get("soil_moisture_0_to_7cm", [0.25])
                
                lluvia_corta = sum(daily[1:4]) if len(daily) >= 4 else 0
                lluvia_media = sum(daily[4:8]) if len(daily) >= 8 else 0
                lluvia_7d = lluvia_corta + lluvia_media
                
                sm_actual = hourly_sm[0] if len(hourly_sm) > 0 else 0.25
                saturacion_suelo = min(100.0, max(0.0, (sm_actual / 0.45) * 100.0))
                
                # Regla estricta del semáforo hídrico
                suelo_vulnerable = saturacion_suelo >= 80.0
                riesgo_corto = lluvia_corta >= 60.0 and suelo_vulnerable
                
                if riesgo_corto or (lluvia_7d >= 90.0 and suelo_vulnerable):
                    detalle = f"Saturación de suelo: {saturacion_suelo:.1f}%. Lluvia acumulada 7d: {lluvia_7d:.1f} mm."
                    enviar_alerta_telegram(f"{nodo['nombre']} ({nodo['provincia']})", detalle)
    except Exception as e:
        print(f"Error al consultar datos en vivo: {e}")

if __name__ == "__main__":
    verificar_datos_reales()
