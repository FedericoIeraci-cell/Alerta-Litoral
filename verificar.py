import requests

TOKEN = "8835711157:AAFqiN_KCMrYYRImWP5dzc11DzM7GvWL-yY"
CHAT_ID = "8813171047"

# Las 24 ciudades/nodos completos del Litoral extraídos de tu mapa
nodos = [
    {'nombre': 'Goya', 'provincia': 'CR', 'lat': -29.14, 'lon': -59.26, 'rio': 'Río Paraná'},
    {'nombre': 'Mercedes', 'provincia': 'CR', 'lat': -29.18, 'lon': -58.07, 'rio': 'Esteros del Iberá'},
    {'nombre': 'Curuzú Cuatiá', 'provincia': 'CR', 'lat': -29.79, 'lon': -58.05, 'rio': 'Arroyo Sarandí'},
    {'nombre': 'Paso de los Libres', 'provincia': 'CR', 'lat': -29.71, 'lon': -57.08, 'rio': 'Río Uruguay'},
    {'nombre': 'Santo Tomé', 'provincia': 'CR', 'lat': -28.55, 'lon': -56.04, 'rio': 'Río Uruguay'},
    {'nombre': 'Corrientes Capital', 'provincia': 'CR', 'lat': -27.46, 'lon': -58.83, 'rio': 'Río Paraná'},
    {'nombre': 'Reconquista', 'provincia': 'SF', 'lat': -29.15, 'lon': -59.65, 'rio': 'Río Paraná'},
    {'nombre': 'San Javier', 'provincia': 'SF', 'lat': -30.58, 'lon': -59.93, 'rio': 'Río San Javier'},
    {'nombre': 'Vera', 'provincia': 'SF', 'lat': -29.46, 'lon': -60.21, 'rio': 'Cuenca Calchaquí'},
    {'nombre': 'Santa Fe Capital', 'provincia': 'SF', 'lat': -31.63, 'lon': -60.7, 'rio': 'Río Salado'},
    {'nombre': 'Rosario', 'provincia': 'SF', 'lat': -32.95, 'lon': -60.66, 'rio': 'Río Paraná'},
    {'nombre': 'Tostado', 'provincia': 'SF', 'lat': -29.23, 'lon': -61.77, 'rio': 'Río Salado Norte'},
    {'nombre': 'Concordia', 'provincia': 'ER', 'lat': -31.39, 'lon': -58.02, 'rio': 'Río Uruguay'},
    {'nombre': 'La Paz', 'provincia': 'ER', 'lat': -30.74, 'lon': -59.64, 'rio': 'Río Paraná'},
    {'nombre': 'Victoria', 'provincia': 'ER', 'lat': -32.62, 'lon': -60.15, 'rio': 'Delta del Paraná'},
    {'nombre': 'Gualeguay', 'provincia': 'ER', 'lat': -33.14, 'lon': -59.31, 'rio': 'Río Gualeguay'},
    {'nombre': 'Gualeguaychú', 'provincia': 'ER', 'lat': -33.01, 'lon': -58.51, 'rio': 'Río Gualeguaychú'},
    {'nombre': 'Paraná', 'provincia': 'ER', 'lat': -31.73, 'lon': -60.52, 'rio': 'Río Paraná'},
    {'nombre': 'Clorinda', 'provincia': 'FM', 'lat': -25.28, 'lon': -57.71, 'rio': 'Río Pilcomayo'},
    {'nombre': 'Formosa Capital', 'provincia': 'FM', 'lat': -26.18, 'lon': -58.17, 'rio': 'Río Paraguay'},
    {'nombre': 'General San Martín', 'provincia': 'CH', 'lat': -26.53, 'lon': -59.34, 'rio': 'Río Bermejo'},
    {'nombre': 'Resistencia', 'provincia': 'CH', 'lat': -27.45, 'lon': -58.98, 'rio': 'Río Negro'},
    {'nombre': 'Posadas', 'provincia': 'MN', 'lat': -27.36, 'lon': -55.89, 'rio': 'Río Paraná'},
    {'nombre': 'Eldorado', 'provincia': 'MN', 'lat': -26.4, 'lon': -54.63, 'rio': 'Río Paraná'}
]

ACCIONES = {
    "ROJO": "EVACUACIÓN INMINENTE: Mover hacienda a zonas altas. Elevar maquinaria y limpiar canales principales de urgencia.",
    "AMARILLO": "ALERTA PREVENTIVA: Agrupar ganado para traslado, preparar reservas de forraje seco y desobstruir sumideros."
}

def enviar_alerta_telegram(zona, estado, detalle, accion, rio):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    icono = "🔴" if estado == "ROJO" else "🟡"
    texto = (
        f"{icono} *AVISO HÍDRICO LITORAL ({estado})* {icono}\n"
        f"📍 Zona: {zona}\n"
        f"🌊 Río Referencia: {rio}\n"
        f"📝 {detalle}\n\n"
        f"💡 *Acción Recomendada:*\n{accion}"
    )
    payload = {"chat_id": CHAT_ID, "text": texto, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload)
    except:
        pass

def verificar_todo_el_litoral():
    lats = ",".join([str(n["lat"]) for n in nodos])
    lons = ",".join([str(n["lon"]) for n in nodos])
    
    # Consulta masiva en tiempo real a Open-Meteo para las 24 coordenadas
    url_api = f"https://api.open-meteo.com/v1/forecast?latitude={lats}&longitude={lons}&daily=precipitation_sum&hourly=soil_moisture_0_to_7cm&timezone=America%2FArgentina%2FBuenos_Aires&past_days=1"
    
    try:
        response = requests.get(url_api)
        datos = response.json()
        resultados = datos if isinstance(datos, list) else [datos]
        
        for i, nodo in enumerate(nodos):
            if i < len(resultados):
                node_data = resultados[i]
                daily = node_data.get("daily", {}).get("precipitation_sum", [0]*8)
                hourly_sm = node_data.get("hourly", {}).get("soil_moisture_0_to_7cm", [0.25])
                
                lluvia_hoy = daily[1] if len(daily) > 1 else 0.0
                lluvia_corta = sum(daily[1:4]) if len(daily) >= 4 else 0
                lluvia_media = sum(daily[4:8]) if len(daily) >= 8 else 0
                lluvia_7d = lluvia_corta + lluvia_media
                
                sm_actual = hourly_sm[0] if len(hourly_sm) > 0 else 0.25
                saturacion_suelo = min(100.0, max(0.0, (sm_actual / 0.45) * 100.0))
                
                # Mismas reglas matemáticas que el mapa interactivo[cite: 2]
                suelo_vulnerable = saturacion_suelo >= 80.0
                riesgo_corto = lluvia_corta >= 60.0 and suelo_vulnerable
                
                zona_str = f"{nodo['nombre']} ({nodo['provincia']})"
                
                if riesgo_corto or (lluvia_7d >= 90.0 and suelo_vulnerable):
                    detalle = f"Saturación crítica: {saturacion_suelo:.1f}%. Lluvia acumulada 7d: {lluvia_7d:.1f} mm."
                    enviar_alerta_telegram(zona_str, "ROJO", detalle, ACCIONES["ROJO"], nodo['rio'])
                elif lluvia_7d >= 35.0 or lluvia_hoy > 5.0 or suelo_vulnerable:
                    detalle = f"Saturación de suelo: {saturacion_suelo:.1f}%. Lluvia hoy: {lluvia_hoy:.1f} mm, Lluvia 7d: {lluvia_7d:.1f} mm."
                    enviar_alerta_telegram(zona_str, "AMARILLO", detalle, ACCIONES["AMARILLO"], nodo['rio'])
                    
    except Exception as e:
        print(f"Error al procesar el monitoreo general: {e}")

if __name__ == "__main__":
    verificar_todo_el_litoral()
