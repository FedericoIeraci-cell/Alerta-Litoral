"""Vista aislada de la estación personal: no modifica nodos ni envía avisos."""
import os
from zoneinfo import ZoneInfo

import streamlit as st

from estacion_propia import acquire, assess, advance_reference, configuration, utc

LABELS = {
    "temperature": "Temperatura exterior", "humidity": "Humedad exterior",
    "pressure_absolute": "Presión en la estación", "pressure_relative": "Presión reducida (requiere calibración)",
    "wind_speed": "Viento", "wind_gust": "Ráfaga", "wind_direction": "Dirección del viento",
    "rain_rate": "Intensidad de lluvia · balancín", "rain_daily": "Lluvia del día · balancín",
    "rain_piezo_rate": "Intensidad de lluvia · piezoeléctrica", "rain_piezo_daily": "Lluvia del día · piezoeléctrica",
}
FLAGS = {
    "sin_dato": "Sin dato", "fuera_de_rango": "Fuera del rango de plausibilidad",
    "unidad_desconocida": "Unidad desconocida", "valor_invalido": "Valor inválido",
    "fecha_invalida": "Fecha ausente o inválida", "fecha_futura": "Fecha futura: revisar reloj",
    "dato_vencido": "Sin actualización por más de 10 minutos", "fuera_de_orden": "Llegó una lectura anterior",
    "conflicto_misma_fecha": "Valor distinto para la misma fecha", "salto_a_revisar": "Cambio brusco: revisar",
    "reinicio_acumulado": "Contador reiniciado o cambio de día",
}


@st.cache_data(ttl=60, show_spinner=False)
def _read(config):
    return acquire(config)


def mostrar_estacion_propia():
    st.subheader("Estación propia · observación local")
    st.caption("Medición particular de terraza. Representatividad y calibración pendientes. "
               "No interviene en el índice, los colores del mapa ni los avisos de ALA.")
    values = dict(os.environ)
    try:
        values.update(dict(st.secrets))
    except (FileNotFoundError, st.errors.StreamlitSecretNotFoundError):
        pass
    config = configuration(values)
    if not config["enabled"]:
        st.info("Integración preparada y desactivada. No hay una estación propia conectada.")
        return
    result = _read(config)
    if result["record"] is None:
        st.warning("No se pudo obtener una lectura de la estación. No se confirma su conexión.")
        st.caption("Diagnóstico: " + result["status"])
        return
    key = "pws_previous_" + config["station_id"]
    quality = assess(result["record"], previous=st.session_state.get(key))
    st.session_state[key] = advance_reference(st.session_state.get(key), quality)
    st.write("Identificador: " + quality["station_id"])
    st.caption("Consulta al proveedor: " + quality["received_at"] + " · Las fechas de los sensores se muestran abajo.")
    st.info("Los controles automáticos detectan faltantes y anomalías evidentes; no certifican la exactitud. "
            "La lluvia del día depende del reloj de la estación y no equivale a las últimas 24 horas.")
    rows = []
    for name, item in quality["measurements"].items():
        date = utc(item["observed_at"])
        rows.append({"Variable": LABELS[name],
                     "Valor": f'{item["value"]:.2f}' if item["usable"] else "—",
                     "Unidad": item["unit"],
                     "Observación (Argentina)": date.astimezone(ZoneInfo("America/Argentina/Buenos_Aires")).strftime("%d/%m/%Y %H:%M:%S") if date else "Sin fecha",
                     "Control": "; ".join(FLAGS[f] for f in item["flags"]) or "Sin objeciones automáticas"})
    st.dataframe(rows, hide_index=True, use_container_width=True)
    with st.expander("Valores recibidos y controles"):
        st.json(quality)
    st.caption("La consulta se reutiliza durante 60 segundos. El historial continuo requiere el colector independiente.")
