"""Vista del TP sobre la misma fila de riesgo que utiliza el panel principal."""
from __future__ import annotations

import json
import math
import pandas as pd

from ficha_proyecto import FICHA, construir_inventario, horizontes_lluvia


def mostrar_panel_proyecto(st, contexto):
    from decisiones_agro import construir_plan

    st.subheader("Plan por localidad y usuario")
    st.write("Integrá el estado del río, la lluvia y la evidencia territorial para preparar las decisiones de tu actividad.")
    st.caption("Ficha del problema: productores, comités de cuenca, municipios y Defensa Civil. Ámbito original: Entre Ríos, Corrientes y Santa Fe; ampliado a seis provincias.")
    filas = contexto["filas"]
    if filas.empty or "localidad" not in filas:
        st.info("Todavía no hay una evaluación local. Consultá los datos en Alerta Litoral Agro; los faltantes no representan riesgo bajo.")
        return
    localidades = sorted(filas.localidad.tolist())
    elegida = st.session_state.get("localidad_detalle")
    if st.session_state.get("tp_localidad") not in localidades:
        st.session_state.pop("tp_localidad", None)
    a, b = st.columns(2)
    localidad = a.selectbox("Localidad del plan", localidades,
                            index=localidades.index(elegida) if elegida in localidades else 0,
                            key="tp_localidad")
    usuario = b.selectbox("Para quién se prepara", ["Productor", "Comité de cuenca", "Municipio / Defensa Civil"], key="tp_usuario")
    row = filas[filas.localidad.eq(localidad)].iloc[0].to_dict()
    nodo = next(n for n in contexto["nodos"] if n["localidad"] == localidad)
    referencia = contexto["ahora"]()
    clave = localidad + " · " + nodo["provincia"]
    agua, suelo, _, _ = contexto["componentes"](nodo)
    relieve = contexto["relieve"](nodo)
    datos = contexto["datos"](nodo)
    nivel = row.get("nivel", "SIN DATOS")
    st.markdown("**Semáforo integrado: " + {"ROJO": "🔴", "AMARILLO": "🟡", "VERDE": "🟢"}.get(nivel, "⚪") + " " + nivel + "**")
    st.write(row.get("accion") or "Completá las fuentes y coordiná la evaluación local.")
    st.caption("Es la evaluación de la pantalla principal. Las consultas complementarias de esta pestaña no modifican los avisos del monitor horario.")
    horizontes = horizontes_lluvia(row, datos, referencia)
    st.write("**Lluvia prevista y plazo de planificación**")
    columnas = st.columns(4)
    for columna, h in zip(columnas, horizontes):
        valor = h["precipitacion_mm"]
        columna.metric(h["horizonte"], "Sin cobertura" if valor is None else f"{valor:.1f} mm")
    st.caption("Acumulados de grilla desde la evaluación meteorológica del " + str(row.get("actualizado", "sin fecha")) + "; no confirman portancia ni una ventana segura. Días 8–14: " +
               horizontes[-1]["inicio"] + " a " + horizontes[-1]["fin"] + ". Sólo se suma cuando están disponibles los siete días completos.")
    with st.expander("Lluvia observada de una estación cercana · INTA SIGA"):
        st.write("Contrastá la estimación de grilla con un pluviómetro. La distancia y la fecha delimitan qué representa la medición.")
        if st.button("Consultar observaciones INTA cercanas", key="tp_siga_consultar"):
            with st.spinner("Consultando estaciones y registros diarios oficiales…"):
                res = contexto["siga"](nodo)
            st.session_state.setdefault("tp_siga", {})[clave] = res
        res = st.session_state.get("tp_siga", {}).get(clave)
        if res:
            if res.get("error"):
                st.warning(res["error"])
            else:
                st.caption(res.get("etiqueta", "Estación INTA; no es una medición de cada lote."))
                diarios = pd.DataFrame(res.get("diarios", []))
                if not diarios.empty:
                    st.dataframe(diarios, hide_index=True, use_container_width=True)
                    st.download_button("Descargar observaciones SIGA utilizadas", diarios.to_csv(index=False).encode("utf-8-sig"),
                                       "INTA_SIGA_observaciones.csv", "text/csv", key="tp_siga_csv")
        st.link_button("Fuente y consulta oficial INTA SIGA", "https://siga.inta.gob.ar/")
    consulta_siga = st.session_state.get("tp_siga", {}).get(clave, {})
    siga = consulta_siga.get("resumen")
    evidencias = construir_inventario(row, agua=agua, suelo=suelo, relieve=relieve, siga=siga,
                    trimestral=st.session_state.get("ficha_trimestral"), referencia=referencia)
    fila_plan = dict(row, lluvia_dias_8_14=horizontes[-1]["precipitacion_mm"],
                     cobertura_dias_8_14=horizontes[-1]["precipitacion_mm"] is not None)
    plan = construir_plan(fila_plan, usuario, evidencias=evidencias, referencia=referencia)
    contexto["mostrar_plan"](st, plan)
    st.write("**Datos disponibles para esta decisión**")
    vista = pd.DataFrame(evidencias).drop(columns=["id"]).rename(columns={
        "variable": "Variable", "valor": "Dato", "estado": "Estado de esta consulta",
        "fuente": "Fuente", "fecha": "Fecha de referencia", "alcance": "Alcance y límite",
        "monitor_horario": "En el monitor horario"})
    st.dataframe(vista, hide_index=True, use_container_width=True)
    st.caption("Disponible describe el dato de esta consulta. Sin carga, sin escena válida y sin confirmación local son faltantes; no equivalen a cero ni a condiciones seguras. Revisá Suelo y ríos, SMN y Territorio para completar las evidencias.")
    st.download_button("Descargar inventario de datos de esta localidad", vista.to_csv(index=False).encode("utf-8-sig"),
                       "inventario_datos_localidad.csv", "text/csv", key="tp_inventario_csv")
    informe = {"version": contexto["version"], "generado": referencia.isoformat(), "ficha": FICHA,
               "localidad": localidad, "provincia": nodo["provincia"], "usuario": usuario,
               "semaforo": nivel, "fecha_evaluacion": row.get("fecha_evaluacion_integrada"),
               "motivos": row.get("motivos_semaforo"), "horizontes": horizontes,
               "evidencias": evidencias, "plan": plan,
               "observaciones_inta_siga": consulta_siga.get("evidencia")}
    def finito(d):
        if isinstance(d, dict):
            return {k: finito(v) for k, v in d.items()}
        if isinstance(d, (list, tuple)):
            return [finito(v) for v in d]
        if isinstance(d, float) and not math.isfinite(d):
            return None
        return d
    st.download_button("Descargar plan completo con fuentes y faltantes", json.dumps(finito(informe), ensure_ascii=False,
                       indent=2, allow_nan=False, default=str), "plan_operativo_localidad.json", "application/json", key="tp_plan_json")
    with st.expander("Objetivo y alcance de la ficha del proyecto"):
        st.write("**Pregunta guía.** " + FICHA["pregunta"])
        st.write("**Fenómeno.** " + FICHA["fenomeno"])
        st.write("**Decisión.** " + FICHA["decision"])
        st.write("**Método.** " + FICHA["metodo"])
        st.write("El caso de abril de 2016 y Vera 2025 está en Caso histórico y ficha. La calibración local conserva sus límites; una evaluación exploratoria no acredita validación operativa de todas las provincias.")
