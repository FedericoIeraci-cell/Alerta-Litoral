"""Observaciones públicas de SIGA/INTA, con procedencia y cobertura explícitas.

El servicio se descubre desde el JavaScript público de SIGA; no es una API
versionada con garantía de estabilidad. Se consulta por GET, sin registros ni
credenciales. Las estaciones cercanas son contexto meteorológico: no representan
la lluvia de cada lote ni miden saturación del perfil del suelo.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
import json
import math
import re
import unicodedata
from urllib.parse import parse_qs, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

INICIO = "https://siga.inta.gob.ar/"
JS_SERVICIO = urljoin(INICIO, "js/urlserver.js")
JS_DATOS = urljoin(INICIO, "js/data.js")
JS_UNIDADES = urljoin(INICIO, "js/global.js")
TZ = ZoneInfo("America/Argentina/Buenos_Aires")
TIMEOUT = (15, 25)
MAX_BYTES = 2_000_000
UA = "Alerta-Litoral-Agro/consulta-publica-SIGA"


def _ahora(ref=None):
    if ref is None:
        return datetime.now(TZ)
    if isinstance(ref, str):
        ref = datetime.fromisoformat(ref.replace("Z", "+00:00"))
    if isinstance(ref, date) and not isinstance(ref, datetime):
        ref = datetime.combine(ref, datetime.min.time(), TZ)
    return (ref.replace(tzinfo=TZ) if ref.tzinfo is None else ref).astimezone(TZ)


def _norm(s):
    s = unicodedata.normalize("NFKD", str(s or ""))
    return " ".join("".join(x for x in s if not unicodedata.combining(x)).lower().split())


def _dia(value):
    """Conserva el día informado; 00:00Z no se desplaza al día anterior en ART."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        texto = str(value).strip()
        if len(texto) > 10:
            # Java JSON de SIGA agrega [UTC] a la representación ISO.
            texto_iso = texto.removesuffix("[UTC]").replace("Z", "+00:00")
            return datetime.fromisoformat(texto_iso).date()
        return date.fromisoformat(texto)
    except (ValueError, TypeError):
        return None


def _numero(value, minimo=None, maximo=None):
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        value = value.strip()
        if not re.fullmatch(r"[-+]?\d+(?:[.,]\d+)?", value):
            return None
        value = value.replace(",", ".")
    try:
        n = float(value)
    except (ValueError, TypeError, OverflowError):
        return None
    if not math.isfinite(n) or (minimo is not None and n < minimo) or (maximo is not None and n > maximo):
        return None
    return n


def _id(value):
    if isinstance(value, bool) or not re.fullmatch(r"[1-9]\d{0,6}", str(value)):
        return None
    return int(value)


def _url_segura(url):
    p = urlparse(url)
    return p.scheme == "https" and p.hostname == "siga.inta.gob.ar" and not p.username and not p.password and p.port in (None, 443)


def _session(session):
    if session is not None:
        return session
    import requests
    return requests.Session()


def _leer(session, url):
    if not _url_segura(url):
        raise ValueError("Servicio fuera del origen oficial SIGA")
    r = session.get(url, headers={"User-Agent": UA}, timeout=TIMEOUT,
                    stream=True, allow_redirects=False)
    try:
        # No se siguen cambios de origen ni redirecciones no verificadas.
        if 300 <= r.status_code < 400:
            raise ValueError("Redirección inesperada del servicio SIGA")
        r.raise_for_status()
        size = r.headers.get("Content-Length")
        if size and int(size) > MAX_BYTES:
            raise ValueError("Respuesta SIGA demasiado grande")
        contenido = bytearray()
        for parte in r.iter_content(chunk_size=65536):
            contenido.extend(parte)
            if len(contenido) > MAX_BYTES:
                raise ValueError("Respuesta SIGA demasiado grande")
        return bytes(contenido)
    finally:
        if hasattr(r, "close"):
            r.close()


def descubrir_servicio(contenido):
    """Lee la constante activa; ignora bloques comentados con servidores antiguos."""
    # Los comentarios de bloque contienen ejemplos obsoletos; no son configuración.
    texto = re.sub(r"/\*[\s\S]*?\*/", "", contenido)
    match = re.search(r"^\s*var\s+URL_MYSQL_ESTACION\s*=\s*(['\"])([^'\"]+)\1", texto, re.M)
    if not match:
        raise ValueError("El servicio público de estaciones no se reconoce en SIGA")
    url = urljoin(INICIO, match[2])
    p = urlparse(url)
    query = parse_qs(p.query, keep_blank_values=True)
    if not _url_segura(url) or not p.path.endswith(".php") or query != {"param_type": ["estacion"], "param_value": [""]}:
        raise ValueError("Configuración del servicio SIGA fuera del contrato verificado")
    return url


def _fuente(ahora, estado, detalle, url=INICIO, fecha=None):
    return {"id": "inta_siga", "nombre": "INTA · SIGA", "url": url,
            "estado": estado, "consultado": ahora.isoformat(), "fecha_dato": fecha,
            "detalle": detalle, "documentacion": [INICIO, JS_SERVICIO, JS_DATOS, JS_UNIDADES],
            "limitacion": "Servicio web público usado por SIGA; sin API versionada documentada. Estación cercana no equivale al lote."}


def parsear_catalogo(payload, referencia=None):
    ahora = _ahora(referencia)
    if not isinstance(payload, list) or len(payload) > 10000:
        raise ValueError("Catálogo SIGA no reconocido")
    estaciones, ids = [], set()
    for raw in payload:
        if not isinstance(raw, dict):
            continue
        sid = _id(raw.get("id"))
        lat, lon = _numero(raw.get("latitud"), -90, 90), _numero(raw.get("longitud"), -180, 180)
        visible = raw.get("visible") in (True, 1, "1", "true", "True")
        if not sid or sid in ids or lat is None or lon is None or not raw.get("nombre") or not visible:
            continue
        ids.add(sid)
        ultimo = _dia(raw.get("maximoDiario"))
        edad = (ahora.date() - ultimo).days if ultimo else None
        estado = "SIN_FECHA" if ultimo is None else "FECHA_FUTURA" if edad < 0 else "RECIENTE" if edad <= 2 else "VENCIDO"
        estaciones.append({"id": sid, "nombre": str(raw["nombre"]), "provincia": str(raw.get("provincia") or ""),
            "lat": lat, "lon": lon, "id_tipo": _id(raw.get("idTipo")), "tipo": str(raw.get("tipo") or ""),
            "ultimo_dia_catalogo": ultimo.isoformat() if ultimo else None, "edad_dias_catalogo": edad,
            "estado_catalogo": estado, "fuente": INICIO})
    if not estaciones:
        raise ValueError("Catálogo SIGA sin estaciones públicas válidas")
    return estaciones


def obtener_catalogo_siga(referencia=None, session=None):
    ahora, session = _ahora(referencia), _session(session)
    try:
        endpoint = descubrir_servicio(_leer(session, JS_SERVICIO).decode("utf-8-sig"))
        estaciones = parsear_catalogo(json.loads(_leer(session, endpoint)), ahora)
        return {"fuente": _fuente(ahora, "DISPONIBLE", f"Catálogo público: {len(estaciones)} estaciones válidas; vigencia por estación.", endpoint),
                "estaciones": estaciones, "endpoint": endpoint, "errores": []}
    except Exception as exc:
        detalle = f"No se obtuvo el catálogo público verificable de SIGA ({type(exc).__name__})."
        return {"fuente": _fuente(ahora, "ERROR", detalle), "estaciones": [], "endpoint": None, "errores": [detalle]}


def parsear_diarios(payload, estacion, desde, hasta, referencia=None):
    ahora = _ahora(referencia)
    inicio, fin = _dia(desde), _dia(hasta)
    sid = _id(estacion.get("id"))
    if not sid or not inicio or not fin or fin < inicio or (fin - inicio).days > 90:
        raise ValueError("Estación o intervalo diario inválido")
    if not isinstance(payload, list) or len(payload) > 10000:
        raise ValueError("Serie diaria SIGA no reconocida")
    registros, avisos, vistos, ambiguos = [], [], set(), set()
    for raw in payload:
        if not isinstance(raw, dict):
            avisos.append("Registro diario con formato inválido.")
            continue
        if _id(raw.get("idEstacion")) != sid:
            avisos.append("Se descartó una observación de otra estación.")
            continue
        dia = _dia(raw.get("fechaHora"))
        if not dia or not inicio <= dia <= fin or dia >= ahora.date():
            # No mezclar el día actual parcial con días completos ni admitir futuro.
            continue
        if dia in vistos:
            ambiguos.add(dia.isoformat())
            avisos.append("Hay fechas duplicadas: ese día no se usa para acumulados.")
            continue
        vistos.add(dia)
        pluv = _numero(raw.get("precDiaPlub"), 0)
        crono = _numero(raw.get("precDiaCrono"), 0)
        # La UI oficial usa Pluv. en convencionales, Crono. en automáticas.
        tipo = estacion.get("id_tipo")
        campo = "precDiaPlub" if tipo == 1 else "precDiaCrono" if isinstance(tipo, int) and tipo > 1 else None
        lluvia = pluv if campo == "precDiaPlub" else crono if campo == "precDiaCrono" else None
        registros.append({"estacion_id": sid, "fecha_diaria": dia.isoformat(),
            "fecha_original": str(raw.get("fechaHora")), "precipitacion_mm": lluvia,
            "precipitacion_campo": campo, "precipitacion_pluviometro_mm": pluv,
            "precipitacion_cronologica_mm": crono, "temperatura_media_c": _numero(raw.get("tempAbrigo150"), -90, 60),
            "temperatura_minima_c": _numero(raw.get("tempAbrigo150Min"), -90, 60),
            "temperatura_maxima_c": _numero(raw.get("tempAbrigo150Max"), -90, 60),
            "humedad_relativa_media_pct": _numero(raw.get("HMedia"), 0, 100),
            "naturaleza": "observación meteorológica de estación; no humedad ni saturación del suelo"})
    registros = sorted((r for r in registros if r["fecha_diaria"] not in ambiguos), key=lambda r: r["fecha_diaria"])
    esperados = [inicio + timedelta(days=i) for i in range((fin - inicio).days + 1) if inicio + timedelta(days=i) < ahora.date()]
    lluvias = {r["fecha_diaria"]: r["precipitacion_mm"] for r in registros if r["precipitacion_mm"] is not None}
    faltantes = [d.isoformat() for d in esperados if d.isoformat() not in lluvias]
    ultimo = _dia(registros[-1]["fecha_diaria"]) if registros else None
    edad = (ahora.date() - ultimo).days if ultimo else None
    estado = "SIN_DATOS" if ultimo is None else "VENCIDO" if edad > 2 else "PARCIAL" if faltantes else "VIGENTE"
    completa = bool(esperados) and not faltantes
    return {"estacion": estacion, "estado": estado, "registros": registros,
            "ultimo_dia": ultimo.isoformat() if ultimo else None, "edad_dias": edad,
            "desde": inicio.isoformat(), "hasta": fin.isoformat(), "dias_esperados": len(esperados),
            "dias_con_lluvia_valida": len(lluvias), "dias_faltantes": faltantes,
            "acumulado_completo_mm": sum(lluvias.values()) if completa else None,
            "suma_dias_disponibles_mm": sum(lluvias.values()) if lluvias else None,
            "cobertura_completa": completa, "avisos": list(dict.fromkeys(avisos)),
            "periodo": "Días informados por SIGA; no se ha confirmado una hora de corte común entre tipos de estación."}


def obtener_diarios_siga(estacion, desde, hasta, referencia=None, session=None, endpoint=None):
    ahora, session = _ahora(referencia), _session(session)
    inicio, fin = _dia(desde), _dia(hasta)
    if not isinstance(estacion, dict) or not _id(estacion.get("id")) or not inicio or not fin or fin < inicio or (fin - inicio).days > 90:
        raise ValueError("Estación o intervalo inválido")
    try:
        if endpoint is None:
            endpoint = descubrir_servicio(_leer(session, JS_SERVICIO).decode("utf-8-sig"))
        p = urlparse(endpoint)
        if not _url_segura(endpoint) or not p.path.endswith(".php") or parse_qs(p.query, keep_blank_values=True) != {"param_type": ["estacion"], "param_value": [""]}:
            raise ValueError("Endpoint SIGA no reconocido")
        # Se solicita un día adicional y se filtra el intervalo inclusivo localmente;
        # el servicio no documenta la inclusividad del límite superior.
        valor = f'{estacion["id"]}/{inicio.day}-{inicio.month}-{inicio.year}/{(fin + timedelta(days=1)).day}-{(fin + timedelta(days=1)).month}-{(fin + timedelta(days=1)).year}'
        url = p._replace(query=urlencode({"param_type": "diario", "param_value": valor})).geturl()
        resultado = parsear_diarios(json.loads(_leer(session, url)), estacion, inicio, fin, ahora)
        resultado["fuente"] = _fuente(ahora, resultado["estado"], "Serie diaria observada; cobertura y fecha verificadas por registro.", url, resultado["ultimo_dia"])
        return resultado
    except Exception as exc:
        detalle = f"No se obtuvo la serie diaria verificable de SIGA ({type(exc).__name__})."
        return {"estacion": estacion, "estado": "ERROR", "registros": [], "ultimo_dia": None, "edad_dias": None,
                "desde": inicio.isoformat(), "hasta": fin.isoformat(), "cobertura_completa": False,
                "acumulado_completo_mm": None, "suma_dias_disponibles_mm": None,
                "dias_esperados": (fin - inicio).days + 1, "dias_con_lluvia_valida": 0,
                "dias_faltantes": [], "avisos": [detalle], "fuente": _fuente(ahora, "ERROR", detalle)}


def _distancia(lat1, lon1, lat2, lon2):
    r1, r2 = math.radians(lat1), math.radians(lat2)
    dlat, dlon = r2 - r1, math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(r1) * math.cos(r2) * math.sin(dlon / 2) ** 2
    return 6371.0088 * 2 * math.asin(min(1, math.sqrt(a)))


def seleccionar_estaciones_cercanas(nodos, estaciones, radio_max_km=50):
    radio = _numero(radio_max_km, 0, 200)
    if radio is None:
        raise ValueError("Radio de búsqueda fuera de 0–200 km")
    resultado = []
    for n in nodos:
        lat, lon = _numero(n.get("lat"), -90, 90), _numero(n.get("lon"), -180, 180)
        candidatos = []
        if lat is not None and lon is not None:
            for e in estaciones:
                distancia = _distancia(lat, lon, e["lat"], e["lon"])
                if distancia <= radio and e["estado_catalogo"] != "FECHA_FUTURA":
                    candidatos.append((e["estado_catalogo"] != "RECIENTE", distancia, e["id"], e))
        mejor = min(candidatos, default=None, key=lambda x: x[:3])
        resultado.append({"localidad": str(n.get("localidad") or n.get("nombre") or ""),
            "estacion": mejor[3] if mejor else None, "distancia_km": round(mejor[1], 2) if mejor else None,
            "radio_max_km": radio, "representatividad": "Estación cercana de referencia; la lluvia y el suelo pueden diferir dentro del lote.",
            "motivo": "Se prioriza catálogo reciente y luego distancia; datos diarios sujetos a verificación." if mejor else "Sin estación pública en el radio o coordenadas válidas."})
    return resultado


def obtener_panorama_siga(nodos, referencia=None, session=None, max_estaciones=12, radio_max_km=50, dias=7):
    ahora, session = _ahora(referencia), _session(session)
    if isinstance(dias, bool) or not isinstance(dias, int) or not 1 <= dias <= 30:
        raise ValueError("Ventana SIGA fuera de 1–30 días")
    if isinstance(max_estaciones, bool) or not isinstance(max_estaciones, int) or not 1 <= max_estaciones <= 24:
        raise ValueError("Cantidad de estaciones fuera de 1–24")
    catalogo = obtener_catalogo_siga(ahora, session)
    asociaciones = seleccionar_estaciones_cercanas(nodos, catalogo["estaciones"], radio_max_km)
    seleccion = {}
    for a in asociaciones:
        if a["estacion"] and len(seleccion) < max_estaciones:
            seleccion[a["estacion"]["id"]] = a["estacion"]
    desde, hasta = ahora.date() - timedelta(days=dias), ahora.date() - timedelta(days=1)
    def consultar(e):
        return e["id"], obtener_diarios_siga(e, desde, hasta, ahora, session, catalogo["endpoint"])
    with ThreadPoolExecutor(max_workers=min(3, len(seleccion) or 1)) as pool:
        consultas = dict(pool.map(consultar, seleccion.values()))
    for a in asociaciones:
        sid = a["estacion"]["id"] if a["estacion"] else None
        a["consulta"] = consultas.get(sid)
        a["consulta_estado"] = consultas[sid]["estado"] if sid in consultas else "OMITIDA_LIMITE" if sid else "SIN_ESTACION"
    estados = [a["consulta_estado"] for a in asociaciones]
    estado = ("ERROR" if catalogo["fuente"]["estado"] == "ERROR" or (estados and all(e == "ERROR" for e in estados))
              else "VIGENTE" if estados and all(e == "VIGENTE" for e in estados) else "PARCIAL")
    return {"fuente": _fuente(ahora, estado, "Observaciones diarias de estaciones; no se sustituyen variables del suelo ni pronósticos."),
            "catalogo_fuente": catalogo["fuente"], "estaciones": catalogo["estaciones"],
            "consultas_por_id": {str(k): v for k, v in consultas.items()}, "asociaciones": asociaciones,
            "desde": desde.isoformat(), "hasta": hasta.isoformat(), "errores": catalogo["errores"],
            "limitacion": "Las mediciones no acreditan saturación, agua superficial ni lluvia uniforme sobre la localidad."}
