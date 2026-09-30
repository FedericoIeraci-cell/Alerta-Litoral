# Diccionario con las acciones detalladas
ACCIONES = {
    "ROJO (Crítico)": "EVACUACIÓN INMINENTE: Mover hacienda a zonas altas. Elevar maquinaria y limpiar canales principales de urgencia.",
    "NARANJA (Alerta Operativa)": "ALERTA OPERATIVA: Iniciar traslado preventivo de hacienda y verificar defensas.",
    "AMARILLO (Precaución)": "ALERTA PREVENTIVA: Agrupar ganado para traslado, preparar reservas de forraje seco y desobstruir sumideros.",
    "VERDE (Normal)": "MONITOREO NORMAL: Pastoreo sin restricciones. Mantener mantenimiento rutinario de drenajes."
}

def enviar_alerta_telegram(zona, estado, detalle):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    
    # Buscamos la acción correspondiente al estado
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
        response = requests.post(url, json=payload)
        return response.status_code == 200
    except:
        return False
