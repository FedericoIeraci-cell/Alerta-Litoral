"""Presentación pública de la evaluación existente, sin recalcular riesgos.

No consulta servicios, modifica registros ni envía avisos. Separa las fechas de
las fuentes del momento de cálculo y conserva como ausentes los datos faltantes.
"""
from datetime import date, datetime, time, timedelta
from html import escape
from math import isfinite
from zoneinfo import ZoneInfo

from explicaciones_alerta import causas_verificadas, evidencia_util, fuentes_evidencia, motivo_publico, numero, url_oficial
from hidrologia import LOCALIDADES, normalizar

ART = ZoneInfo("America/Argentina/Buenos_Aires")
FASES = {"ROJO": "Atención prioritaria", "AMARILLO": "Preparación preventiva",
         "VERDE": "Mantener seguimiento", "SIN DATOS": "Información insuficiente"}
COLORES = {"ROJO": "#b4233d", "AMARILLO": "#946200", "VERDE": "#177245", "SIN DATOS": "#52677b"}


def _registros(value):
    return [item for item in value if isinstance(item, dict)] if isinstance(value, (list, tuple)) else []


def _si(value):
    if hasattr(value, "item"):
        try:
            value = value.item()
        except (TypeError, ValueError):
            return False
    return value is True


def _numero(value):
    if hasattr(value, "item"):
        try:
            value = value.item()
        except (TypeError, ValueError):
            return None
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
        return value if isfinite(value) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _fecha(value):
    if isinstance(value, date) and not isinstance(value, datetime):
        value = datetime.combine(value, time.min)
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            try:
                value = datetime.strptime(value, "%d/%m/%Y %H:%M")
            except ValueError:
                return None
    if not isinstance(value, datetime):
        return None
    return value.replace(tzinfo=ART) if value.tzinfo is None else value.astimezone(ART)


def fecha_publica(value, precision="fecha_hora_publicada"):
    """La medianoche usada al importar un parte diario no es una hora medida."""
    dt = _fecha(value)
    if dt is None:
        return "Sin fecha publicada"
    if precision in ("fecha_sin_hora", "hora_no_verificada_en_reporte_diario") or (isinstance(value, str) and len(value.strip()) == 10):
        return dt.strftime("%d/%m/%Y") + " · hora no publicada"
    return dt.strftime("%d/%m/%Y %H:%M") + " ART"


def _fechada(titulo, value, referencia, precision="fecha_hora_publicada"):
    texto = titulo + ": " + fecha_publica(value, precision)
    dt = _fecha(value)
    if dt and precision not in ("fecha_sin_hora", "hora_no_verificada_en_reporte_diario") and not (isinstance(value, str) and len(value.strip()) == 10):
        minutos = (referencia - dt).total_seconds() / 60
        if minutos >= 0:
            edad = "menos de 1 min" if minutos < 1 else f"{int(minutos)} min" if minutos < 60 else f"{minutos / 60:.1f} h".replace(".", ",")
            texto += " · hace " + edad
        else:
            texto += " · fecha posterior a esta evaluación"
    return texto


def precision_observada(row, panorama=None):
    evidencias = _registros(row.get("evidencias_alerta")) + _registros(row.get("hidrologia_evidencias"))
    observado = next((e for e in evidencias if e.get("tipo") == "observacion_fluvial"), {})
    if observado:
        if "/diario/reporte_diario" in str(observado.get("fuente", "")):
            return "fecha_sin_hora"
        return observado.get("precision_fecha") or "fecha_hora_publicada"
    estaciones = (panorama or {}).get("estaciones", {})
    estacion = estaciones.get(normalizar(row.get("hidrologia_estacion", "")), {})
    if "/diario/reporte_diario" in str(estacion.get("fuente", "")):
        return "fecha_sin_hora"
    return estacion.get("precision_fecha") or "fecha_hora_publicada"


def _fuentes(evidencias):
    salida = []
    for e in evidencias:
        for titulo, url in fuentes_evidencia(e):
            if url not in [u for _, u in salida]:
                salida.append((titulo, url))
    return salida


def _bloque(titulo, estado, datos=None, fechas=None, fuentes=None):
    return {"titulo": titulo, "estado": estado, "datos": datos or [], "fechas": fechas or [], "fuentes": fuentes or []}


def construir_portada_local(row, panorama=None, referencia=None):
    row = dict(row)
    panorama = panorama if isinstance(panorama, dict) else {}
    ref = _fecha(referencia) or _fecha(row.get("fecha_evaluacion_integrada")) or datetime.now(ART)
    nivel = str(row.get("nivel", "SIN DATOS"))
    if nivel not in FASES:
        nivel = "SIN DATOS"
    evidencias = _registros(row.get("evidencias_alerta"))
    row["evidencias_alerta"] = evidencias
    row["desencadenantes"] = _registros(row.get("desencadenantes"))
    utiles = [e for e in evidencias if evidencia_util(e)]
    faltantes = []

    met_ok = _si(row.get("cobertura_meteorologica"))
    lluvias = []
    cifras = []
    for campo, plazo in (("lluvia_futura_24", "Próximas 24 h"), ("lluvia_futura_72", "Próximas 72 h"), ("lluvia_futura_7d", "Próximos 7 días")):
        value = _numero(row.get(campo))
        completo = met_ok and (campo != "lluvia_futura_7d" or _si(row.get("cobertura_pronostico_7d")))
        valido = completo and value is not None and value >= 0
        lluvias.append(plazo + ": " + (numero(value, 1) + " mm" if valido else "sin dato completo vigente"))
        if campo != "lluvia_futura_7d":
            cifras.append({"id": "lluvia24" if campo == "lluvia_futura_24" else "lluvia72",
                "titulo": "Lluvia prevista · " + ("24 h" if campo == "lluvia_futura_24" else "72 h"),
                "valor": numero(value, 1) + " mm" if valido else "Sin dato completo vigente",
                "fecha": "Período desde: " + fecha_publica(row.get("referencia_pronostico")),
                "fuentes": [("ECMWF / Open-Meteo", "https://open-meteo.com/en/docs/ecmwf-api")]})
        if not valido:
            faltantes.append("Lluvia prevista de " + plazo.lower().replace("próximas ", "").replace("próximos ", "") + ": sin dato completo vigente.")
    faltantes.append("Hora de emisión del modelo meteorológico: no informada en esta consulta.")
    met = _bloque("Lluvia prevista", "Pronóstico de modelo" if met_ok else "Información meteorológica incompleta", lluvias,
        ["Períodos de lluvia contados desde: " + fecha_publica(row.get("referencia_pronostico")),
         _fechada("Cálculo meteorológico", row.get("actualizado"), ref), "Emisión del modelo: no informada"],
        [("ECMWF vía Open-Meteo", "https://open-meteo.com/en/docs/ecmwf-api")])
    indice = _numero(row.get("indice"))
    met["datos"].insert(0, "Índice meteorológico: " + (numero(indice, 1) + "/100 · " + str(row.get("nivel_meteorologico") or "sin nivel")
                                                     if met_ok and indice is not None else "sin dato completo vigente"))
    extendida = _numero(row.get("lluvia_futura_extendida"))
    if _si(row.get("cobertura_pronostico_extendido")) and extendida is not None and row.get("pronostico_extendido_hasta"):
        met["datos"].append("Horizonte disponible: " + numero(extendida, 1) + " mm hasta " + fecha_publica(row["pronostico_extendido_hasta"]))

    observadas = [e for e in utiles if e.get("tipo") == "observacion_fluvial"]
    previsiones = [e for e in utiles if e.get("tipo") == "pronostico_fluvial" and e.get("nivel") in ("BAJO", "ALERTA", "EVACUACION")]
    previsiones.sort(key=lambda e: (-({"EVACUACION": 2, "ALERTA": 1, "BAJO": 0}.get(e.get("nivel"), -1)), str(e.get("desde") or "")))
    obs = observadas[0] if observadas else {}
    prono = previsiones[0] if previsiones else {}
    aplica = _si(row.get("hidrologia_aplica"))
    rio_datos, rio_fechas = [], []
    if aplica:
        rio_datos.append("Estación: " + str(row.get("hidrologia_estacion") or "sin estación identificada"))
        if _si(row.get("hidrologia_vigente")) and obs:
            rio_estado = "Altura observada: " + numero(obs.get("altura_m")) + " m"
            for campo, titulo in (("alerta_m", "Nivel de referencia para alerta"), ("evacuacion_m", "Nivel de referencia para evacuación")):
                umbral = _numero(obs.get(campo))
                rio_datos.append(titulo + ": " + (numero(umbral) + " m" if umbral is not None else "sin dato publicado"))
            rio_fechas.append(_fechada("Lectura", obs.get("fecha_observada"), ref, precision_observada(row, panorama)))
        else:
            rio_estado = "Sin lectura local vigente verificada"
            faltantes.append("Lectura local del río: sin dato vigente verificado.")
            if row.get("hidrologia_fecha"):
                rio_fechas.append(_fechada("Fecha del dato disponible, sin vigencia confirmada", row["hidrologia_fecha"], ref, precision_observada(row, panorama)))
        if _si(row.get("hidrologia_pronostico_vigente")) and prono:
            previstos = {"BAJO": "nivel bajo según la fuente", "ALERTA": "posible cruce del nivel de referencia para alerta", "EVACUACION": "posible cruce del nivel de referencia para evacuación"}
            rio_datos.append("Previsión: " + previstos[prono["nivel"]])
            rio_datos.append("Plazo publicado: " + str(prono.get("ventana") or "sin fecha precisa"))
            precision = "fecha_sin_hora" if prono.get("clase") == "prevision_oficial_ctm" else prono.get("precision_fecha_emision", "fecha_hora_publicada")
            rio_fechas.append(_fechada("Emisión de la previsión", prono.get("fecha_emision"), ref, precision))
        else:
            rio_datos.append("Previsión fluvial: sin dato vigente verificado")
            faltantes.append("Previsión local del río: sin dato vigente verificado.")
    else:
        rio_estado = "Sin estación local vinculada"
        rio_datos.append("El río principal no se evalúa para esta localidad. Los arroyos y desagües requieren información local.")
    rio = _bloque("Río · observación y previsión", rio_estado, rio_datos, rio_fechas, _fuentes(observadas + previsiones[:1]))
    altura = _numero(obs.get("altura_m")) if aplica and _si(row.get("hidrologia_vigente")) and obs else None
    fecha_altura = obs.get("fecha_observada") if altura is not None else row.get("hidrologia_fecha")
    cifras.append({"id": "rio", "titulo": "Río observado · " + str(row.get("hidrologia_estacion") or "referencia local"),
        "valor": numero(altura) + " m" if altura is not None else "Sin lectura vigente" if aplica else "Sin estación vinculada",
        "fecha": (("Lectura: " if altura is not None else "Último dato disponible: ") +
                  fecha_publica(fecha_altura, precision_observada(row, panorama))) if aplica else
                 "Los arroyos y desagües requieren información local.",
        "fuentes": _fuentes(observadas) if aplica else []})

    mapping = LOCALIDADES.get(normalizar(row.get("localidad", "")))
    represa = (panorama.get("represas") or {}).get(mapping[3], {}) if mapping and mapping[3] else {}
    rep_datos, rep_fechas, rep_fuentes = [], [], []
    if not _si(row.get("represa_aplica")):
        rep_estado = "Sin represa vinculada a esta localidad"
    elif not _si(row.get("represa_vigente")) or not represa:
        rep_estado = "Sin boletín vigente verificado"
        faltantes.append("Represa vinculada: sin boletín vigente verificado.")
        if represa.get("fecha"):
            rep_fechas.append(_fechada("Último boletín disponible", represa["fecha"], ref, "fecha_sin_hora"))
    else:
        rep_estado = str(represa.get("nombre") or "Represa vinculada") + " · boletín vigente"
        aporte = _numero(represa.get("afluente_m3s"))
        salida = _numero(represa.get("erogado_instantaneo_m3s"))
        media = _numero(represa.get("erogado_m3s"))
        if aporte is not None:
            etiqueta = "Aporte previsto para el día" if represa.get("afluente_tipo") == "prevision_del_dia" else "Aporte informado al embalse"
            rep_datos.append(etiqueta + ": " + numero(aporte, 0) + " m³/s")
        if salida is not None:
            rep_datos.append("Salida instantánea informada: " + numero(salida, 0) + " m³/s")
            rep_fechas.append(_fechada("Medición de salida", represa.get("fecha_erogado"), ref))
        elif media is not None:
            rep_datos.append("Salida media diaria: " + numero(media, 0) + " m³/s (" + str(represa.get("caudal_intervalo") or "intervalo no informado") + ")")
        if not rep_datos:
            rep_datos.append("Descripción oficial disponible; caudal numérico no disponible en esta ficha.")
        rep_fechas.append(_fechada("Fecha del boletín", represa.get("fecha"), ref, "fecha_sin_hora"))
        if represa.get("hasta"):
            precision_hasta = "fecha_sin_hora" if mapping[3] == "yacyreta" else "fecha_hora_publicada"
            rep_fechas.append("Horizonte publicado: " + fecha_publica(represa["hasta"], precision_hasta))
    if url_oficial(represa.get("fuente")):
        rep_fuentes.append(("Boletín de la represa", represa["fuente"]))
    rep_datos.append("El caudal no indica la altura ni la hora de llegada del agua a tu localidad.")
    rep = _bloque("Represas y aportes", rep_estado, rep_datos, rep_fechas, rep_fuentes)

    smn_result = panorama.get("smn") or {}
    smn_avisos = [e for e in utiles if e.get("tipo") == "smn"]
    smn_datos, smn_fechas = [], []
    if not _si(row.get("smn_vigente")):
        smn_estado = "Cobertura de avisos sin confirmar"
        smn_datos.append("Consultá el SMN: no se puede afirmar que no haya avisos.")
        faltantes.append("Avisos SMN: la consulta no confirma cobertura completa y vigente.")
    elif smn_avisos:
        smn_estado = f"{len(smn_avisos)} aviso(s) oficial(es) aplicado(s) a este punto"
        severidades = {"Extreme": "extrema", "Severe": "severa", "Moderate": "moderada", "Minor": "menor", "Unknown": "sin clasificar"}
        for aviso in smn_avisos:
            smn_datos.append(str(aviso.get("fenomeno") or "Fenómeno no informado") + " · severidad " + severidades.get(aviso.get("severidad"), str(aviso.get("severidad") or "no informada")))
            smn_datos.append("Desde " + fecha_publica(aviso.get("desde")) + " hasta " + fecha_publica(aviso.get("hasta")))
            smn_fechas.append(_fechada("Emisión del aviso", aviso.get("fecha_emision"), ref))
    else:
        smn_estado = "Sin aviso de lluvia o tormenta aplicado a este punto"
        smn_datos.append("Alcance de esta consulta: avisos vigentes o previstos dentro de 72 h. Revisá otros fenómenos y áreas en el SMN.")
    smn_fechas.append(_fechada("Actualización del listado SMN", smn_result.get("fecha_feed"), ref))
    smn_fechas.append(_fechada("Consulta al SMN", smn_result.get("consultado"), ref))
    smn = _bloque("Avisos oficiales SMN", smn_estado, smn_datos, smn_fechas,
                  _fuentes(smn_avisos) or [("Ver avisos en el SMN", "https://www.smn.gob.ar/")])

    ciudadanos = {
        "ROJO": ["Revisá ahora el aviso local y contactá a Defensa Civil si estás en una zona expuesta.",
                 "Prepará documentos, medicación y asistencia para quienes necesiten ayuda; seguí las indicaciones oficiales de traslado.",
                 "No cruces calles, caminos ni corrientes con agua."],
        "AMARILLO": ["Revisá cada nuevo aviso y verificá los accesos antes de salir.",
                     "Acordá un contacto y un punto de encuentro con tu familia; prepará documentos y medicación."],
        "VERDE": ["Mantené seguimiento de los avisos y revisá desagües desde lugares accesibles.",
                  "Verificá las condiciones locales antes de circular; el color no confirma que todas las calles estén transitables."],
        "SIN DATOS": ["Consultá las fuentes oficiales y la autoridad local para completar la información que falta.",
                      "Si observás agua, daños o peligro, pedí asistencia sin esperar un cambio del semáforo."],
    }[nivel]
    productores = ["Confirmá piso y accesos antes de ingresar maquinaria; revisá siembra y cosecha por lote con tu asesor.",
                   "Tené previsto un destino, agua, forraje y recorrido para la hacienda si fuera necesario trasladarla; coordiná antes de mover animales."]
    if nivel == "ROJO":
        productores.insert(0, "Priorizá la seguridad de las personas y coordiná ahora las medidas con la autoridad y los responsables del establecimiento.")
    elif nivel == "SIN DATOS":
        productores.insert(0, "Completá la información local antes de decidir tareas o movimientos.")
    return {"localidad": str(row.get("localidad") or "Sin localidad"), "provincia": str(row.get("provincia") or ""),
            "nivel": nivel, "fase": FASES[nivel], "motivo": motivo_publico(row).replace("Previsión fluvial", "Previsión del río").replace("escenario central", "valor central estimado").replace("bandas ", "rango de incertidumbre "),
            "evaluacion": fecha_publica(row.get("fecha_evaluacion_integrada")),
            "cifras": cifras, "bloques": [met, rio, rep, smn], "ciudadanos": ciudadanos, "productores": productores,
            "faltantes": list(dict.fromkeys(faltantes)), "fuentes_motivo": _fuentes(causas_verificadas(row))}


def html_portada_local(datos):
    def esc(value):
        return escape(str(value), quote=True)
    def links(fuentes):
        return " · ".join(f'<a href="{esc(url)}" target="_blank" rel="noopener noreferrer">{esc(titulo)}</a>' for titulo, url in fuentes)
    def lista(items):
        return "<ul>" + "".join("<li>" + esc(item) + "</li>" for item in items) + "</ul>"
    color = COLORES.get(datos["nivel"], COLORES["SIN DATOS"])
    cifras = ''.join('<article class="ala-local-cifra'+(' ala-local-cifra-rio' if c['id']=='rio' else '')+'" data-cifra="'+esc(c['id'])+'">'
        '<h3>'+esc(c['titulo'])+'</h3><p class="ala-local-valor">'+esc(c['valor'])+'</p>'
        '<p class="ala-local-fecha">'+esc(c['fecha'])+'</p><p class="ala-local-links">'+links(c['fuentes'])+'</p></article>'
        for c in datos.get('cifras',[]))
    bloques = ''.join('<details class="ala-local-dato"><summary><span>'+esc(b['titulo'])+'</span><strong>'+esc(b['estado'])+'</strong></summary>'
        + "".join("<p>" + esc(t) + "</p>" for t in b["datos"]) + '<div class="ala-local-fechas">'
        + "".join("<p>" + esc(t) + "</p>" for t in b["fechas"]) + '</div><p class="ala-local-links">' + links(b["fuentes"]) + "</p></details>" for b in datos["bloques"])
    roles = ''.join('<article><h3>'+esc(titulo)+'</h3><p>'+esc(acciones[0])+'</p>'
        +('<details><summary>Más acciones de preparación</summary>'+lista(acciones[1:])+'</details>' if len(acciones)>1 else '')+'</article>'
        for titulo,acciones in (("Qué hacer · hogar y comunidad",datos['ciudadanos']),("Qué hacer · trabajo rural",datos['productores'])) if acciones)
    faltantes = '<details class="ala-local-faltantes"><summary><strong>Información por confirmar · '+str(len(datos['faltantes']))+' puntos</strong></summary>' + lista(datos["faltantes"]) + '</details>' if datos["faltantes"] else ''
    return f'''<style>
.ala-local{{font-family:system-ui,sans-serif;background:#fff;color:#183448;border:1px solid #d9e3eb;border-top:4px solid {color};border-radius:12px;padding:16px 18px;margin:2px 0 16px}}
.ala-local *{{box-sizing:border-box}}.ala-local h2{{font-size:24px;margin:0 0 3px;padding:0;line-height:1.2}}.ala-local h3{{font-size:14px;margin:0 0 5px;line-height:1.35;padding:0}}.ala-local p,.ala-local li{{font-size:14px;line-height:1.45;overflow-wrap:anywhere}}
.ala-local p{{margin:5px 0}}.ala-local header{{display:flex;justify-content:space-between;align-items:flex-start;gap:12px}}.ala-local header small{{color:#536879;font-size:12px}}
.ala-local-badge{{border:1px solid {color};border-radius:7px;padding:6px 9px;color:{color};font-weight:800;font-size:12px;white-space:nowrap}}.ala-local-motivo{{margin:10px 0 12px!important;font-size:15px!important}}
.ala-local-cifras{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:9px;margin-top:9px}}.ala-local-cifra{{border:1px solid #dfe7ef;border-radius:9px;padding:11px 12px;background:#f4f8fb;min-width:0}}.ala-local .ala-local-valor{{font-size:28px;font-weight:800;line-height:1.15;margin:6px 0 8px;letter-spacing:-.02em}}
.ala-local-grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:9px}}.ala-local-dato{{border:1px solid #dfe7ef;border-radius:9px;padding:11px 12px;background:#f8fafc;align-self:start}}.ala-local summary{{cursor:pointer;font-size:14px;line-height:1.4}}.ala-local summary::marker{{color:{color}}}.ala-local summary span{{font-weight:700}}.ala-local summary strong{{display:block;font-weight:500;margin:4px 0 0;color:#405a70}}.ala-local details[open]>summary{{margin-bottom:9px}}
.ala-local .ala-local-fechas p,.ala-local .ala-local-fecha,.ala-local .ala-local-links,.ala-local .ala-local-meta{{font-size:12px;color:#516779}}.ala-local-fechas{{border-top:1px solid #dfe7ef;padding-top:6px;margin-top:9px}}
.ala-local a{{color:#075985;text-decoration:underline}}.ala-local-roles{{display:grid;grid-template-columns:1fr 1fr;gap:15px;margin:13px 0 15px;padding:11px 0;border-top:1px solid #dfe7ef;border-bottom:1px solid #dfe7ef}}.ala-local-roles details{{margin-top:8px}}.ala-local-roles summary{{font-size:12px;color:#31566f}}.ala-local ul{{margin:7px 0;padding-left:18px}}.ala-local li{{margin:5px 0}}
.ala-local-faltantes{{background:#fff7e6;border:1px solid #eed6a3;border-radius:9px;padding:10px 12px;margin:10px 0;font-size:14px}}.ala-local-faltantes ul{{columns:2;column-gap:24px}}.ala-local-faltantes li{{break-inside:avoid}}
@media(max-width:620px){{.ala-local{{padding:12px;border-radius:10px}}.ala-local h2{{font-size:21px}}.ala-local header{{flex-wrap:wrap;gap:7px}}.ala-local-cifras{{grid-template-columns:repeat(2,minmax(0,1fr));gap:7px}}.ala-local-cifra{{padding:9px}}.ala-local .ala-local-valor{{font-size:25px}}.ala-local-cifra-rio{{grid-column:1/-1}}.ala-local-grid,.ala-local-roles{{grid-template-columns:1fr}}.ala-local-roles{{gap:12px}}.ala-local-faltantes ul{{columns:1}}}}
</style><section class="ala-local" aria-label="Situación y acciones de {esc(datos['localidad'])}"><header><div><h2>{esc(datos['localidad'])}</h2><small>{esc(datos['provincia'])} · {esc(datos['fase'])}</small></div><span class="ala-local-badge">{esc(datos['nivel'])} · ALA experimental</span></header>
<p class="ala-local-meta">Evaluación: {esc(datos['evaluacion'])}</p><p class="ala-local-motivo">{esc(datos['motivo'])}</p><p class="ala-local-links">{links(datos['fuentes_motivo'])}</p>
<div class="ala-local-cifras">{cifras}</div><p class="ala-local-meta">Lluvia estimada por un modelo; emisión de la corrida no informada. La altura del río pertenece a la escala de su estación, no a una calle o lote.</p>
<div class="ala-local-roles">{roles}</div><div class="ala-local-grid">{bloques}</div>{faltantes}
<p class="ala-local-meta">Cada fuente conserva su fecha: una consulta nueva no renueva la medición. El color integra las evidencias disponibles; un nivel de referencia del río no es una orden de evacuación. La autoridad local indica traslados y confirma caminos, refugios y recursos.</p>
<p class="ala-local-links"><a href="https://www.argentina.gob.ar/sinagir/riesgos-frecuentes/inundacion/prevencion" target="_blank" rel="noopener noreferrer">Prevención · SINAGIR</a> · <a href="https://www.argentina.gob.ar/noticias/recomendaciones-para-prevenir-riesgos-y-reducir-el-impacto-del-fenomeno-del-nino-0" target="_blank" rel="noopener noreferrer">Preparación rural · INTA</a></p></section>'''


def mensaje_estado_monitor(estado, referencia=None):
    """Cobertura parcial, antigüedad y fallos operativos tienen textos distintos."""
    ref = _fecha(referencia) or datetime.now(ART)
    control = _fecha(estado.get("checked_at"))
    if control is None or control > ref + timedelta(minutes=5):
        return "warning", "La fecha del último control no es válida; no se puede confirmar vigilancia reciente."
    if ref - control > timedelta(hours=3):
        return "warning", "El último control tiene más de tres horas. La vigilancia puede estar interrumpida; consultá las fuentes oficiales."
    if estado.get("health") == "datos_parciales":
        publicacion = " y publicó el informe compartido" if estado.get("red_integrada_publicada") is True else ""
        return "warning", "El monitor completó el control" + publicacion + ", con datos parciales. Algunas fuentes o localidades no tienen información suficiente; revisá la cobertura."
    if estado.get("ok") is True:
        return "success", "Control reciente completado. El monitor está programado cada hora; las ejecuciones pueden demorarse."
    fallos = {"sin_datos": "El control no obtuvo localidades con información suficiente.",
              "error_registro": "El control no pudo confirmar el registro de avisos.",
              "envio_sin_confirmacion": "Hay un aviso sin confirmación de recepción de Telegram.",
              "envio_rechazado": "Telegram rechazó uno o más avisos.",
              "configuracion_invalida": "La configuración impidió evaluar los envíos automáticos."}
    return "warning", fallos.get(estado.get("health"), "El último control registró un problema operativo; revisá su estado antes de asumir vigilancia activa.")
