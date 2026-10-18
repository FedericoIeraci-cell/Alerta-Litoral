"""Actualización de la evaluación visible, independiente del monitor y Telegram."""

import math
import time


INTERVALO_SEGUNDOS = 300
CLAVE_ULTIMA_EVALUACION = "_ala_ultima_evaluacion_publica"


def activar_actualizacion(st):
    """Llamar una vez por ejecución completa, antes de dibujar la aplicación.

    El fragmento revisa cada minuto si corresponde volver a ejecutar la página.
    Sólo mantiene actualizada la sesión abierta; no publica informes ni envía
    avisos, y cada fuente conserva su propia fecha de observación y su caché.
    """
    st.session_state[CLAVE_ULTIMA_EVALUACION] = time.monotonic()

    @st.fragment(run_every="60s")
    def comprobar_actualizacion():
        ahora = time.monotonic()
        ultima = st.session_state.get(CLAVE_ULTIMA_EVALUACION)
        if (
            isinstance(ultima, bool)
            or not isinstance(ultima, (int, float))
            or not math.isfinite(ultima)
            or ultima > ahora
        ):
            st.session_state[CLAVE_ULTIMA_EVALUACION] = ahora
            return
        if ahora - ultima >= INTERVALO_SEGUNDOS:
            # Reservar el siguiente intervalo antes de solicitar el rerun evita
            # repeticiones inmediatas si se vuelve a ejecutar este fragmento.
            st.session_state[CLAVE_ULTIMA_EVALUACION] = ahora
            st.rerun()

    comprobar_actualizacion()
