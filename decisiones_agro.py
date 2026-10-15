"""Planes de preparación por rol, sin consultas ni habilitaciones operativas.

Entrada: fila integrada con nivel, fechas, lluvia_futura_24/72/7d,
cobertura_pronostico_7d, hidrologia_* y smn_*. Evidencias: lista de registros
id/variable/valor/estado/fuente/fecha/alcance/monitor_horario del inventario.
Opcional lluvia_dias_8_14 con cobertura_dias_8_14; nunca se obtiene restando
acumulados de horizontes distintos. Los plazos siguientes organizan revisiones,
no estiman el momento de llegada del agua. La salida admite JSON estricto.
"""
from __future__ import annotations

from datetime import date, datetime
from math import isfinite
import re
import unicodedata
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

ART = ZoneInfo("America/Argentina/Buenos_Aires")
FUENTES = (
    ("INTA EEA Reconquista · agosto de 2026",
     "https://www.argentina.gob.ar/sites/default/files/2026/08/acciones_ante_el_fenomeno_del_nino_en_espacios_rurales.pdf"),
    ("INTA · manejo del rodeo · 22/09/2026",
     "https://www.argentina.gob.ar/noticias/anticiparse-al-agua-las-claves-del-inta-para-preservar-al-rodeo"),
    ("INTA · decisiones por lote · 07/09/2026",
     "https://www.argentina.gob.ar/noticias/campana-gruesa-el-nino-obliga-ajustar-el-manejo-de-los-lotes"),
    ("SINAGIR · prevención de inundaciones",
     "https://www.argentina.gob.ar/sinagir/riesgos-frecuentes/inundacion/prevencion"),
)
LIMITACIONES = (
    "Plan de apoyo experimental: no emite alertas oficiales ni órdenes de evacuación.",
    "La grilla meteorológica y una estación cercana no miden cada lote; verificar piso y accesos en campo sin exponerse.",
    "Humedad en m³/m³ y agua útil del perfil no determinan saturación física ni portancia.",
    "No restar cotas DEM y alturas INA: sus referencias verticales pueden ser diferentes; una loma relativa no certifica un destino seguro.",
    "Un directorio, un punto de encuentro o una casilla marcada no confirma refugio abierto, cupos, guardia ni stock de sueros.",
    "Los horizontes de lluvia no certifican ventanas seguras de siembra o cosecha; la decisión depende del lote, cultivo y asesoramiento local.",
    "La limpieza o modificación de canales requiere coordinación y autorizaciones; no trasladar el problema a predios vecinos.",
)


def _normalizar(value):
    texto = unicodedata.normalize("NFKD", str(value or ""))
    return " ".join("".join(c for c in texto if not unicodedata.combining(c)).lower().split())


def _numero(value):
    if isinstance(value, bool):
        return None
    try:
        n = float(value)
        return n if isfinite(n) else None
    except (ValueError, TypeError, OverflowError):
        return None


def _fecha(value):
    if isinstance(value, datetime):
        return value.replace(tzinfo=ART) if value.tzinfo is None else value.astimezone(ART)
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time(), ART)
    if not isinstance(value, str):
        return None
    try:
        f = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        try:
            f = datetime.strptime(value, "%d/%m/%Y %H:%M")
        except ValueError:
            return None
    return f.replace(tzinfo=ART) if f.tzinfo is None else f.astimezone(ART)


def _vigente(value, horas, referencia):
    f = _fecha(value)
    return bool(f and -300 <= (referencia - f).total_seconds() <= horas * 3600)


def _json(value):
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if isfinite(value) else None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _json(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json(v) for v in value]
    if hasattr(value, "item"):
        try:
            return _json(value.item())
        except (ValueError, TypeError):
            pass
    return str(value)


def _rol(usuario):
    nombre = _normalizar(usuario)
    if nombre in ("productor", "productores"):
        return "Productor"
    if nombre in ("comite de cuenca", "comites de cuenca"):
        return "Comité de cuenca"
    if nombre in ("municipio", "defensa civil", "municipio / defensa civil"):
        return "Municipio / Defensa Civil"
    raise ValueError("Usuario no reconocido: elegí Productor, Comité de cuenca o Municipio / Defensa Civil.")


def _acciones(rol, plazo, lluvia, critico):
    if rol == "Productor":
        tareas = [
            ("Hacienda", "Relevar potreros y categorías; preparar traslado a un destino alto con agua y forraje, acordado con su responsable.",
             "Destino y acceso confirmados, capacidad real, identificación, documentación SENASA y plan veterinario."),
            ("Cultivos", "Revisar siembra y cosecha por lote y cultivo; priorizar tareas con el asesor según piso y madurez. " + lluvia,
             "Portancia local, humedad del grano/semilla, pronóstico actualizado y acceso; no ingresar por el color del modelo."),
            ("Drenajes", "Inspeccionar desagües, alcantarillas y canales internos desde lugares accesibles; organizar mantenimiento autorizado antes de la lluvia.",
             "Obstrucciones, permiso y efecto aguas abajo; no trabajar en corrientes, tormentas ni instalaciones energizadas."),
            ("Vialidad", "Acordar recorrido y alternativa para transporte de hacienda e insumos antes de movilizar vehículos.",
             "Parte fechado del tramo, puentes, restricciones de peso y confirmación del acceso rural; un trazado no habilita el paso."),
            ("Salud y familias", "Preparar comunicación, agua y documentos; identificar la atención sanitaria y el punto de encuentro indicado por la autoridad.",
             "Guardia y acceso actuales; si hace falta alojamiento, refugio habilitado y cupo confirmado. No asumir stock de antivenenos."),
        ]
    elif rol == "Comité de cuenca":
        tareas = [
            ("Hacienda", "Compartir con productores la estación, el plazo y los sectores expuestos; relevar necesidades de traslado y destinos propuestos.",
             "Cada productor y autoridad deben validar destino, capacidad ganadera, acceso y logística antes de mover animales."),
            ("Cultivos", "Organizar con extensionistas una revisión por ambientes de las labores próximas. " + lluvia,
             "Mediciones locales, estado del cultivo y piso; no anunciar una ventana común para toda la cuenca."),
            ("Drenajes", "Priorizar un relevamiento de obstrucciones y coordinar mantenimiento entre jurisdicciones y predios.",
             "Permisos, descarga aguas abajo y responsables; evitar aperturas o bombeos que perjudiquen a vecinos."),
            ("Vialidad", "Cruzar solicitudes de productores con partes de cortes y elevar a Vialidad los tramos sin confirmación.",
             "Ruta, tramo/km, fecha, restricción, autoridad y alternativa expresamente indicada; no calcular un desvío seguro por cercanía."),
            ("Salud y refugios", "Consolidar requerimientos de familias y derivarlos al municipio y al sistema sanitario para coordinar recursos.",
             "Confirmación de apertura, acceso, plazas y atención; un directorio sirve para contactar, no asignar capacidad."),
        ]
    else:
        tareas = [
            ("Hacienda", "Coordinar con productores, SENASA y transporte las necesidades de movimiento de animales y los conflictos con la evacuación de personas.",
             "Destino habilitado por su responsable, capacidad, agua/forraje, sanidad y recorrido validado; evitar congestionar accesos."),
            ("Cultivos", "Difundir la evidencia por localidad a INTA y al área productiva; acordar asistencia a establecimientos expuestos. " + lluvia,
             "La decisión de siembra/cosecha requiere diagnóstico agronómico por lote; no es una habilitación municipal por pronóstico."),
            ("Drenajes", "Asignar relevamiento y mantenimiento preventivo de desagües públicos, alcantarillas y puntos reportados como obstruidos.",
             "Responsable, autorización, condiciones del personal y efecto aguas abajo; señalizar y registrar lo intervenido."),
            ("Vialidad", "Validar con Vialidad y tránsito los accesos, cierres y desvíos oficiales antes de asignar recorridos de asistencia.",
             "Tramo exacto, hora del parte, restricciones y próxima revisión; ausencia de corte conocido no significa acceso seguro."),
            ("Refugios y salud", "Contactar responsables de refugios y salud para verificar recursos y preparar asistencia, puntos de encuentro y comunicación a la población.",
             "Apertura, plazas efectivas, accesibilidad, guardia, derivación y stock pertinente confirmados; la autoridad decide activaciones y evacuaciones."),
        ]
    salida = [{"area": area, "accion": accion, "plazo": plazo, "verificar": verificar}
              for area, accion, verificar in tareas]
    if critico:
        salida.insert(0, {"area": "Coordinación prioritaria",
            "accion": "Contactar ahora a la autoridad local, contrastar el boletín y preparar las acciones del rol; coordinar un traslado sólo con destino y acceso confirmados.",
            "plazo": "Ahora; antes de cualquier traslado. Revisar cada nuevo boletín oficial.",
            "verificar": "Fuente original, estación, umbral y plazo local. El plan no emite una orden de evacuación."})
    return salida


def construir_plan(row, usuario, evidencias=None, referencia=None):
    """Devuelve decisiones, pendientes y datos auditables, sin cambiar el riesgo."""
    ref = _fecha(referencia) if referencia is not None else datetime.now(ART)
    if ref is None:
        raise ValueError("La fecha de referencia no es válida.")
    rol = _rol(usuario)
    row = dict(row)
    nivel = str(row.get("nivel", "SIN DATOS")).upper()
    if nivel not in ("VERDE", "AMARILLO", "ROJO", "SIN DATOS"):
        nivel = "SIN DATOS"
    registros = [_json(dict(e)) for e in (evidencias or [])]
    pendientes = []
    for e in registros:
        estado = _normalizar(e.get("estado"))
        nombre = str(e.get("variable") or e.get("id") or "Evidencia sin identificar")
        if not estado or estado == "sin" or any(s in estado for s in ("sin ", "historico", "incomplet", "vencid", "confirmacion", "comprobar")):
            pendientes.append(nombre + ": " + str(e.get("estado") or "estado no informado") + ".")
        elif not e.get("fuente") or not _fecha(e.get("fecha")):
            pendientes.append(nombre + ": completar fuente y fecha de la evidencia; una consulta no actualiza el dato.")
        else:
            horas = {"lluvia_modelada": 12, "humedad_modelada": 12, "lluvia_observada": 72,
                     "agua_util": 240, "agua_superficial": 72, "rio_observado": 48}.get(e.get("id"))
            if horas and not _vigente(e.get("fecha"), horas, ref):
                pendientes.append(nombre + ": fecha vencida o futura; renovar evidencia antes de decidir.")
    if not registros:
        pendientes.append("Completar el inventario de lluvia observada, suelo, agua superficial, relieve, río y recursos territoriales.")
    eval_fecha = row.get("fecha_evaluacion_integrada") or row.get("actualizado")
    if not _vigente(eval_fecha, 12, ref):
        pendientes.append("Actualizar o verificar la fecha del semáforo antes de usarlo para una decisión actual.")
    met_vigente = bool(row.get("cobertura_meteorologica") is True and _vigente(row.get("actualizado"), 12, ref))
    lluvia = {}
    for campo, clave in (("lluvia_futura_24", "24_h"), ("lluvia_futura_72", "72_h"),
                          ("lluvia_futura_7d", "7_dias"), ("lluvia_dias_8_14", "dias_8_14")):
        n = _numero(row.get(campo))
        cobertura = (campo != "lluvia_futura_7d" or row.get("cobertura_pronostico_7d") is True)
        cobertura &= campo != "lluvia_dias_8_14" or row.get("cobertura_dias_8_14") is True
        lluvia[clave] = n if met_vigente and cobertura and n is not None and n >= 0 else None
    if lluvia["24_h"] is None or lluvia["72_h"] is None:
        pendientes.append("Obtener lluvia prevista vigente de 24 y 72 horas; no sustituir faltantes por cero.")
    resumen_lluvia = "; ".join(f"{k.replace('_', ' ')}: {v:g} mm" for k, v in lluvia.items() if v is not None)
    resumen_lluvia = ("Planificar con acumulados de grilla (" + resumen_lluvia + ").") if resumen_lluvia else "Actualizar primero los horizontes de lluvia disponibles."
    critico = nivel == "ROJO"
    hidro_critico = _numero(row.get("hidrologia_rango")) == 2
    if hidro_critico and (not row.get("hidrologia_fuentes") or not _vigente(eval_fecha, 12, ref) or
            (not _fecha(row.get("hidrologia_fecha")) and not (row.get("hidrologia_pronostico_vigente") and row.get("hidrologia_ventana")))):
        pendientes.append("La señal fluvial crítica requiere corroborar fuente, estación, fecha de emisión y plazo; no constituye una orden oficial.")
    if row.get("hidrologia_aplica") and (not row.get("hidrologia_vigente") or not row.get("hidrologia_pronostico_vigente")):
        pendientes.append("Completar observación y previsión fluvial locales; la cobertura parcial no acredita ausencia de crecida.")
    pendientes.append("Confirmar destino y recorrido antes de trasladar hacienda; verificar apertura, acceso y recursos sanitarios antes de asignar asistencia.")
    fases = {"ROJO": "Preparar y coordinar respuesta prioritaria", "AMARILLO": "Preparación preventiva",
             "VERDE": "Preparación y vigilancia", "SIN DATOS": "Completar evidencia y mantener precaución"}
    plazo = {"ROJO": "Ahora y antes de iniciar cada tarea; revisar cada nuevo aviso.",
             "AMARILLO": "En las próximas 24 horas y antes de las lluvias previstas.",
             "VERDE": "Antes de la próxima jornada; volver a revisar ante nuevos avisos.",
             "SIN DATOS": "Ahora, antes de autorizar ingresos o traslados."}[nivel]
    decisiones = {"Productor": "Preparar el manejo del rodeo y las labores por lote, con destino, piso y acceso verificados.",
        "Comité de cuenca": "Coordinar productores y jurisdicciones con estación y plazo de referencia; priorizar drenajes y accesos por verificar.",
        "Municipio / Defensa Civil": "Coordinar la evaluación con Vialidad, salud y responsables de refugios antes de activar operativos."}
    fuentes = [{"organismo": organismo, "url": url} for organismo, url in FUENTES]
    for e in registros:
        for url in re.findall(r"https://[^\s|<>]+", str(e.get("fuente") or "")):
            p = urlparse(url)
            if p.hostname and not p.username and not p.password and url not in [f["url"] for f in fuentes]:
                fuentes.append({"organismo": str(e.get("variable") or "Evidencia aportada"), "url": url})
    return {"usuario": rol, "localidad": str(row.get("localidad") or "Sin localidad"), "nivel": nivel,
        "fase": fases[nivel], "decision_prioritaria": ("Contrastar ahora la señal crítica con la autoridad local. " if critico else "") + decisiones[rol],
        "acciones": _acciones(rol, plazo, resumen_lluvia, critico), "pendientes": list(dict.fromkeys(pendientes)),
        "fuentes": fuentes, "limitaciones": list(LIMITACIONES), "generado": ref.isoformat(),
        "datos": {"fecha_evaluacion": _json(eval_fecha), "lluvia_modelada_mm": lluvia,
            "indice_meteorologico": _numero(row.get("indice")), "hidrologia_rango": _numero(row.get("hidrologia_rango")),
            "rio_observado": _json(row.get("nivel_hidrologico_observado")), "rio_previsto": _json(row.get("nivel_hidrologico_previsto")),
            "plazo_fluvial_publicado": _json(row.get("hidrologia_ventana")), "fuentes_fluviales": _json(row.get("hidrologia_fuentes")),
            "fecha_observacion_fluvial": _json(row.get("hidrologia_fecha")), "evidencias": registros}}
