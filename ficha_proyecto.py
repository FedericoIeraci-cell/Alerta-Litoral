"""Inventario y horizontes del TP: evidencia disponible, sin habilitar operativos.

El inventario describe una consulta concreta. No convierte una descarga reciente
en una medición nueva, ni la disponibilidad de un adaptador en cobertura efectiva.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from math import isfinite
from zoneinfo import ZoneInfo

ART = ZoneInfo("America/Argentina/Buenos_Aires")
SMN = "https://www.smn.gob.ar/"
SEPA = "https://sepa.inta.gob.ar/"
SIGA = "https://siga.inta.gob.ar/"

FICHA = {
    "titulo": "Alerta de anegamiento en el Litoral",
    "pregunta": "¿Cómo integrar pronósticos de lluvia, humedad de suelo y capas territoriales en un semáforo de riesgo operativo ante anegamientos?",
    "fenomeno": "Lluvia acumulada, condiciones hídricas del suelo, agua en superficie, relieve y crecidas.",
    "ambito_original": "Entre Ríos, Corrientes y Santa Fe",
    "ambito_actual": "También Chaco, Formosa y Misiones; localidades de referencia, sin cobertura continua de cada lote.",
    "usuarios": "Productores, comités de cuenca, municipios y Defensa Civil.",
    "decision": "Preparar traslado de hacienda a zonas altas; revisar siembra/cosecha y drenajes; coordinar accesos, refugios y atención sanitaria.",
    "metodo": "Análisis histórico y propuesta de aplicación. Los casos observados documentan impactos; la capacidad predictiva requiere validación independiente.",
}


def _numero(valor):
    try:
        if isinstance(valor, bool):
            return None
        n = float(valor)
        return n if isfinite(n) else None
    except (TypeError, ValueError):
        return None


def _fecha(valor):
    if isinstance(valor, datetime):
        return valor.replace(tzinfo=ART) if valor.tzinfo is None else valor
    if isinstance(valor, date):
        return datetime.combine(valor, datetime.min.time(), ART)
    if not isinstance(valor, str) or not valor.strip():
        return None
    try:
        f = datetime.fromisoformat(valor.strip().replace("Z", "+00:00"))
    except ValueError:
        try:
            f = datetime.strptime(valor, "%d/%m/%Y %H:%M")
        except ValueError:
            return None
    return f.replace(tzinfo=ART) if f.tzinfo is None else f


def _vigente(fecha, horas, referencia):
    f = _fecha(fecha)
    return bool(f and -300 <= (referencia - f).total_seconds() <= horas * 3600)


def _valor(numero, unidad):
    n = _numero(numero)
    return "Sin dato" if n is None else f"{n:g} {unidad}".strip()


def horizontes_lluvia(row, datos=None, referencia=None):
    """Los días 8–14 usan siete días calendario completos, nunca suma parcial."""
    ref = _fecha(referencia) or datetime.now(ART)
    reciente = bool(row.get("cobertura_meteorologica") is True and _vigente(row.get("actualizado"), 12, ref))
    salida = []
    for titulo, campo, horas in [
        ("Próximas 24 horas", "lluvia_futura_24", 24),
        ("Próximas 72 horas", "lluvia_futura_72", 72),
        ("Próximos 7 días", "lluvia_futura_7d", 168),
    ]:
        n = _numero(row.get(campo))
        valido = reciente and n is not None and n >= 0
        if campo == "lluvia_futura_7d":
            # La cobertura general del índice sólo exige 72 h futuras.
            # Se requiere el indicador independiente del horizonte de 168 h.
            valido = valido and row.get("cobertura_pronostico_7d") is True
        salida.append({"horizonte": titulo, "precipitacion_mm": n if valido else None,
                       "estado": "Estimación de grilla" if valido else "Sin cobertura vigente suficiente",
                       "horas": horas, "inicio": None, "fin": None})
    daily = (datos or {}).get("daily") or {}
    fechas, valores = daily.get("time") or [], daily.get("precipitation_sum") or []
    por_dia = {}
    duplicadas = set()
    for f, v in zip(fechas, valores):
        try:
            dia = date.fromisoformat(str(f)[:10])
        except (ValueError, TypeError):
            continue
        if dia in por_dia:
            duplicadas.add(dia)
        por_dia[dia] = _numero(v)
    esperados = [ref.astimezone(ART).date() + timedelta(days=i) for i in range(8, 15)]
    completos = reciente and all(d in por_dia and d not in duplicadas and
                                 por_dia[d] is not None and por_dia[d] >= 0 for d in esperados)
    salida.append({"horizonte": "Días calendario 8–14", "precipitacion_mm":
                   sum(por_dia[d] for d in esperados) if completos else None,
                   "estado": "Tendencia; menor confianza a mayor plazo" if completos else
                   "Sin los siete días completos; no se extrapola",
                   "horas": None, "inicio": esperados[0].isoformat(), "fin": esperados[-1].isoformat()})
    return salida


def construir_inventario(row, *, agua=None, suelo=None, relieve=None, siga=None,
                         trimestral=None, referencia=None):
    """Registros comparables para la tabla, el plan y la exportación del TP.

    `siga` es el resumen de una estación: fecha, precipitacion_mm, estacion,
    distancia_km y fuente. La selección de esa estación se conserva en la UI.
    """
    ref = _fecha(referencia) or datetime.now(ART)
    filas = []

    def agregar(clave, variable, valor, estado, fuente, fecha, alcance, monitor=False):
        filas.append({"id": clave, "variable": variable, "valor": valor, "estado": estado,
                      "fuente": str(fuente or ""), "fecha": str(fecha or "Sin fecha"),
                      "alcance": alcance, "monitor_horario": "Sí" if monitor else "No"})

    met = bool(row.get("cobertura_meteorologica")) and _vigente(row.get("actualizado"), 12, ref)
    agregar("lluvia_modelada", "Lluvia y pronóstico de grilla",
            _valor(row.get("lluvia_futura_72"), "mm / próximas 72 h"),
            "Disponible" if met else "Incompleto o vencido", "https://open-meteo.com/en/docs/ecmwf-api",
            row.get("actualizado"), "Celda del modelo; la lluvia antecedente también es modelada, no un pluviómetro del lote.", True)
    humedad = _numero(row.get("humedad_suelo_promedio"))
    agregar("humedad_modelada", "Humedad volumétrica del suelo", _valor(humedad, "m³/m³"),
            "Disponible" if met and humedad is not None else "Sin dato vigente", "https://open-meteo.com/en/docs/ecmwf-api",
            row.get("actualizado"), "Promedio modelado de capas; no mide saturación física ni portancia del lote.", True)
    sig = siga or {}
    pp = _numero(sig.get("precipitacion_mm"))
    agregar("lluvia_observada", "Lluvia observada · INTA SIGA", _valor(pp, "mm / día"),
            ("Disponible" if _vigente(sig.get("fecha"), 72, ref) else "Histórico o vencido") if pp is not None else "Sin consulta o sin medición",
            sig.get("fuente", SIGA), sig.get("fecha"),
            f"Estación {sig.get('estacion') or 'sin seleccionar'}; distancia { _valor(sig.get('distancia_km'), 'km') }. No representa cada lote.")
    s = suelo or {}
    su = _numero(s.get("agua_util_pct"))
    agregar("agua_util", "Agua útil del perfil · INTA/relevamiento", _valor(su, "% de agua útil"),
            ("Disponible" if _vigente(s.get("fecha"), 240, ref) else "Histórico o vencido") if su is not None else "Sin carga para esta localidad",
            s.get("fuente", SEPA), s.get("fecha"), "Píxel/registro aportado con procedencia. Requiere carga autorizada; no es porcentaje de saturación física.")
    a = agua or {}
    ndwi = _numero(a.get("NDWI"))
    agregar("agua_superficial", "Agua superficial · índices satelitales", _valor(ndwi, "NDWI"),
            ("Disponible" if _vigente(a.get("fecha"), 72, ref) else "Histórico o vencido") if ndwi is not None else "Sin escena/muestra válida consultada",
            a.get("fuente", "https://earth-search.aws.element84.com/v1"), a.get("fecha"),
            "Nubes, sombras y NoData limitan la observación. Agua detectada puede ser permanente; no determina profundidad ni daño.")
    dem = relieve or {}
    agregar("relieve", "Cotas del entorno · DEM", _valor(dem.get("cota_m"), "m"),
            "Referencia topográfica disponible" if _numero(dem.get("cota_m")) is not None else "Sin consulta para esta localidad",
            "https://open-meteo.com/en/docs/elevation-api", dem.get("consulta"),
            "Muestreo Copernicus GLO-90; fecha de consulta, no de adquisición. No restar una escala INA ni usar para certificar un destino seguro.")
    agregar("rio_observado", "Río · altura observada INA", _valor(row.get("hidrologia_altura_m"), "m de escala"),
            "Disponible" if row.get("hidrologia_vigente") else "Sin observación vigente" if row.get("hidrologia_aplica") else "Sin estación local aplicable",
            row.get("hidrologia_fuentes"), row.get("hidrologia_fecha"),
            str(row.get("hidrologia_estacion") or "Sin estación vinculada") + "; el umbral corresponde a su escala y tramo.", True)
    agregar("rio_previsto", "Río · previsión oficial", row.get("hidrologia_ventana") or "Sin horizonte verificado",
            "Disponible" if row.get("hidrologia_pronostico_vigente") else "Sin pronóstico local vigente",
            row.get("hidrologia_fuentes"), row.get("fecha_evaluacion_integrada"),
            "Fecha de evaluación; las fechas de emisión/validez originales se conservan en Vigilancia de crecidas. No se calcula llegada desde compuertas.", True)
    agregar("smn", "Avisos oficiales SMN", row.get("smn_alertas") or "Sin aviso local en esta consulta",
            "Consulta vigente" if row.get("smn_vigente") else "Cobertura de avisos incompleta",
            row.get("smn_fuentes") or "https://www.smn.gob.ar/alertas", row.get("fecha_evaluacion_integrada"),
            "Se verifica el polígono y vigencia de cada aviso; no se interpreta un fallo de consulta como ausencia de peligro.", True)
    t = trimestral or {}
    fin, inicio = _fecha(t.get("fin")), _fecha(t.get("inicio"))
    vigente = bool(fin and inicio and inicio <= ref <= fin and not t.get("error"))
    agregar("trimestral", "Perspectiva climática SMN a 90 días", t.get("periodo") or "Sin informe consultado",
            "Período que incluye hoy; comprobar última edición" if vigente else "Histórico o sin consulta",
            t.get("url") or "https://www.smn.gob.ar/pronostico-trimestral", t.get("inicio"),
            "Contexto estacional probabilístico; no es pronóstico diario ni activa evacuaciones.")
    agregar("territorio", "Caminos, refugios y salud", "Revisar registros de Territorio y rutas", "Confirmación local requerida",
            "https://www.argentina.gob.ar/transporte/vialidad-nacional/estado-de-rutas", None,
            "Un directorio no confirma apertura, paso, cupo, guardia o stock. Cada operativo necesita autoridad, evidencia, acceso y vigencia propios.")
    return filas
