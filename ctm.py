"""Consulta limitada y trazable de los documentos oficiales de Salto Grande.

Las cotas de embalse se conservan separadas de las escalas de puertos. Un
máximo de puerto publicado no prueba que se alcanzará ese valor ni constituye
una orden de evacuación. Los umbrales se enriquecen fuera de este módulo con
metadatos de la misma estación y escala.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from html.parser import HTMLParser
import math
import re
import unicodedata
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Argentina/Buenos_Aires")
INICIO = "https://www.saltogrande.org/"
MAX_BYTES = 3 * 1024 * 1024
TIMEOUT = 20
MAX_EDAD = timedelta(hours=48)
UA = "Mozilla/5.0 (compatible; ALA consulta de documentos oficiales)"


def _normalizar(texto):
    s = unicodedata.normalize("NFKD", str(texto or ""))
    return "".join(c for c in s if not unicodedata.combining(c)).lower()


def _ahora(value=None):
    if value is None:
        return datetime.now(TZ)
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if isinstance(value, date) and not isinstance(value, datetime):
        value = datetime.combine(value, time.min, TZ)
    if value.tzinfo is None:
        value = value.replace(tzinfo=TZ)
    return value.astimezone(TZ)


def _fecha(texto, hora=0, minuto=0):
    match = re.search(r"(?<!\d)(\d{1,2})/(\d{1,2})/(\d{4})(?!\d)", texto)
    if not match:
        return None
    try:
        return datetime(int(match[3]), int(match[2]), int(match[1]), hora, minuto, tzinfo=TZ)
    except ValueError:
        return None


def _numero(s, miles=False):
    s = str(s or "").strip()
    if not re.fullmatch(r"[-+]?\d+(?:[.,]\d+)*", s):
        return None
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    elif miles and re.fullmatch(r"\d{1,3}(?:\.\d{3})+", s):
        s = s.replace(".", "")
    try:
        n = float(s)
        return n if math.isfinite(n) else None
    except ValueError:
        return None


def _estado(clave, url, fecha, ahora, hasta=None, error=None):
    estado = "ERROR" if error else "SIN_FECHA"
    if not error and fecha:
        estado = "VIGENTE"
        if fecha > ahora + timedelta(minutes=15):
            estado = "FECHA_FUTURA"
        elif ahora - fecha > MAX_EDAD or (hasta and ahora > hasta):
            estado = "VENCIDO"
    return {"id": clave, "nombre": "CTM Salto Grande", "url": url,
            "estado": estado, "fecha_documento": fecha.isoformat() if fecha else None,
            "consultado": ahora.isoformat(), "valido_hasta": hasta.isoformat() if hasta else None,
            "error": error}


def _url_segura(url):
    parsed = urlparse(url)
    return (parsed.scheme == "https" and parsed.hostname in {"www.saltogrande.org", "saltogrande.org"}
            and parsed.port in {None, 443} and not parsed.username and not parsed.password)


class _Enlaces(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.urls = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "a":
            href = dict(attrs).get("href")
            if href:
                self.urls.append(href)


def resolver_documentos(html, base=INICIO):
    """Preserva queries del sitio; acepta únicamente sus dos PDF conocidos."""
    urls = {}
    for href in _Enlaces(html).urls:
        url = urljoin(base, href)
        if not _url_segura(url):
            continue
        path = urlparse(url).path.lower()
        if path == "/docs/hidrologia/caudalesniveles.pdf":
            urls["niveles"] = url
        elif path == "/docs/hidrologia/comunicado.pdf":
            urls["comunicado"] = url
    return urls


def _descargar(session, url, pdf=False):
    if not _url_segura(url):
        raise ValueError("URL de CTM fuera del dominio oficial HTTPS")
    # No seguir automáticamente una redirección a otro host ni cambiar TLS.
    for _ in range(4):
        response = session.get(url, timeout=TIMEOUT, headers={"User-Agent": UA},
                               stream=True, allow_redirects=False)
        if response.status_code in {301, 302, 303, 307, 308}:
            nueva = urljoin(url, response.headers.get("Location", ""))
            if hasattr(response, "close"):
                response.close()
            if not _url_segura(nueva) or nueva == url:
                raise ValueError("Redirección CTM no verificable")
            url = nueva
            continue
        try:
            response.raise_for_status()
            size = response.headers.get("Content-Length")
            if size and int(size) > MAX_BYTES:
                raise ValueError("Documento CTM supera 3 MB")
            data = bytearray()
            for chunk in response.iter_content(chunk_size=65536):
                if chunk:
                    data.extend(chunk)
                    if len(data) > MAX_BYTES:
                        raise ValueError("Documento CTM supera 3 MB")
            if pdf and not bytes(data).startswith(b"%PDF-"):
                raise ValueError("La respuesta CTM no es un PDF")
            return bytes(data), url
        finally:
            if hasattr(response, "close"):
                response.close()
    raise ValueError("Demasiadas redirecciones CTM")


def _texto_pdf(data):
    if len(data) > MAX_BYTES or not data.startswith(b"%PDF-"):
        raise ValueError("PDF CTM inválido o demasiado grande")
    try:
        import fitz
    except ImportError as exc:
        raise RuntimeError("PyMuPDF no está disponible para leer CTM") from exc
    with fitz.open(stream=data, filetype="pdf") as doc:
        if doc.is_encrypted or not 1 <= len(doc) <= 10:
            raise ValueError("PDF CTM protegido o con extensión inesperada")
        return "\n".join(page.get_text("text") for page in doc)


def _fechas_tabla(texto):
    raw = re.findall(r"\b\d{1,2}/\d{1,2}/\d{4}\b", texto)
    fechas = [_fecha(x) for x in raw]
    if not fechas or any(x is None for x in fechas) or any(a >= b for a, b in zip(fechas, fechas[1:])):
        raise ValueError("Fechas de columnas CTM no reconocidas")
    return fechas


def _valores_fila(texto, nombre, cantidad, miles=False):
    # PyMuPDF extrae las celdas del PDF oficial como líneas individuales.
    lines = [x.strip() for x in texto.splitlines() if x.strip()]
    for i, line in enumerate(lines):
        if _normalizar(line) == _normalizar(nombre):
            values = []
            for celda in lines[i + 1:]:
                n = _numero(celda, miles=miles)
                if n is None:
                    break
                values.append(n)
            return values if len(values) == cantidad else None
    return None


def parsear_niveles(texto, referencia=None, url=INICIO):
    ahora = _ahora(referencia)
    limpio = _normalizar(texto)
    cabecera = re.search(r"observados a las\s+(\d{1,2}):(\d{2})\s*hs\s+de\s+dia\s+(\d{1,2}/\d{1,2}/\d{4})", limpio)
    if not cabecera:
        raise ValueError("No se reconoció la fecha del reporte de niveles CTM")
    publicado = _fecha(cabecera[3], int(cabecera[1]), int(cabecera[2]))
    seccion = re.search(r"Niveles\s+Hora\s+(\d{1,2}):(\d{2})\s*\(m\)([\s\S]*?)(?:Powered by|$)", texto, re.I)
    if not seccion or not publicado:
        raise ValueError("No se reconoció la tabla de niveles CTM")
    bloque = seccion[3]
    primero = re.search(r"(?m)^\s*(?:El Soberbio|San Javier|Garruchos|Santo Tom[eé]|Alvear|Paso de los Libres|Concordia)\s*$", bloque)
    if not primero:
        raise ValueError("No hay estaciones reconocibles en el reporte CTM")
    fechas = _fechas_tabla(bloque[:primero.start()])
    if fechas[-1].date() != publicado.date():
        raise ValueError("Fecha de columna CTM no coincide con la fecha del documento")
    observado = fechas[-1].replace(hour=int(seccion[1]), minute=int(seccion[2]))
    estaciones = {}
    ids = {"San Javier": 65, "Santo Tomé": 68, "Paso de los Libres": 72, "Concordia": 79}
    for nombre in ("El Soberbio", "San Javier", "Garruchos", "Santo Tomé", "Alvear", "Paso de los Libres", "Concordia"):
        valores = _valores_fila(bloque, nombre, len(fechas))
        if valores is not None and -5 <= valores[-1] <= 50:
            clave = _normalizar(nombre)
            estaciones[clave] = {"nombre": nombre, "rio": "Uruguay", "altura_m": valores[-1],
                "fecha": observado.isoformat(), "fecha_documento": publicado.isoformat(), "fuente": url,
                "alerta_m": None, "evacuacion_m": None, "series_id": ids.get(nombre),
                "representatividad": "Escala CTM de la estación; requiere umbrales compatibles, no cota del terreno."}
    if not estaciones:
        raise ValueError("El reporte CTM no aporta niveles locales válidos")
    represa = {"nombre": "Salto Grande", "fecha": observado.isoformat(), "hasta": None,
               "fuente": url, "texto": texto, "señal_aguas_arriba": False}
    # Federación y Salto Grande son cotas de embalse, nunca alturas de Concordia.
    for nombre, key in (("Salto Grande", "embalse_m"), ("Federación", "embalse_federacion_m")):
        valores = _valores_fila(bloque, nombre, len(fechas))
        if valores is not None:
            represa[key] = valores[-1]
    caudales = re.search(r"Salto Grande\s*-\s*Erogado Medio Diario\s*\(m3/s\)([\s\S]*?)Niveles\s+Hora", texto, re.I)
    if caudales:
        valores = _valores_fila(caudales[1], "7 a 7", 3 * len(fechas), miles=True)
        if valores is not None:
            turb, vert, total = valores[-3:]
            if min(turb, vert, total) >= 0 and abs(turb + vert - total) <= 2:
                represa.update({"turbinas_m3s": turb, "vertedero_m3s": vert, "erogado_m3s": total,
                                "caudal_intervalo": "media diaria 07:00 a 07:00",
                                "vertedero_abierto": vert > 0, "señal_aguas_arriba": vert > 0})
    return {"fuente": _estado("ctm_niveles", url, publicado, ahora), "estaciones": estaciones,
            "observaciones": list(estaciones.values()), "represa": represa, "pronosticos": [], "avisos": []}


def parsear_comunicado(texto, referencia=None, url=INICIO):
    ahora = _ahora(referencia)
    flat = re.sub(r"\s+", " ", texto)
    n = _normalizar(flat)
    match = re.search(r"comunicado\s+fecha\s*:\s*(\d{1,2}/\d{1,2}/\d{4})", n)
    publicado = _fecha(match[1]) if match else None
    if not publicado:
        raise ValueError("Fecha del comunicado CTM no reconocida")
    horizonte = re.search(r"hasta la hora\s+(\d{1,2}):(\d{2})\s+de manana", n)
    hasta = (publicado + timedelta(days=1)).replace(hour=int(horizonte[1]), minute=int(horizonte[2])) if horizonte else None
    represa = {"nombre": "Salto Grande", "fecha": publicado.isoformat(),
        "hasta": hasta.isoformat() if hasta else None, "fuente": url, "texto": texto,
        "vertedero_abierto": bool(re.search(r"\bvertedero abierto\b", n)),
        "señal_aguas_arriba": bool(re.search(r"\bvertedero abierto\b", n))}
    aporte = re.search(r"aporte ultimas\s+24\s*hs\s*\(m(?:3|³)/s\)\s*([\d.,]+)", n)
    evacuado = re.search(r"evacuado a la hora\s+(\d{1,2}):(\d{2})\s*\(m(?:3|³)/s\)\s*([\d.,]+)", n)
    if aporte:
        represa["afluente_m3s"] = _numero(aporte[1], miles=True)
    if evacuado:
        represa["erogado_instantaneo_m3s"] = _numero(evacuado[3], miles=True)
        represa["fecha_erogado"] = publicado.replace(hour=int(evacuado[1]), minute=int(evacuado[2])).isoformat()
    rango = re.search(r"caudal medio diario evacuado variara entre\s+([\d.,]+)\s+y\s+([\d.,]+)\s*m(?:3|³)/s", n)
    if rango:
        valores = [_numero(rango[1], miles=True), _numero(rango[2], miles=True)]
        if all(v is not None for v in valores):
            represa["erogado_previsto_min_m3s"], represa["erogado_previsto_max_m3s"] = sorted(valores)
    pronosticos = []
    limite = re.search(r"cotas maximas en los puertos de concordia y salto no superaran los valores de\s+([\d.,]+)\s+y\s+([\d.,]+)\s+metros", n)
    if limite and hasta:
        altura = _numero(limite[1])
        if altura is not None:
            pronosticos.append({"localidades": ["concordia"], "nivel": "SIN_DATOS",
                "fecha_emision": publicado.isoformat(), "desde": publicado.isoformat(), "hasta": hasta.isoformat(),
                "ventana": f"hasta {hasta.strftime('%d/%m/%Y %H:%M')} ART según CTM",
                "texto": flat, "fuente": url, "tipo": "prevision_oficial_ctm", "cota_tipo": "limite_superior",
                "maximo_m": altura, "minimo_m": None,
                "probabilidad": "Límite superior publicado; no garantiza que se alcance ni determina una orden de evacuación."})
    return {"fuente": _estado("ctm_comunicado", url, publicado, ahora, hasta), "represa": represa,
            "estaciones": {}, "observaciones": [], "pronosticos": pronosticos, "avisos": []}


def obtener_ctm(referencia=None, session=None):
    ahora = _ahora(referencia)
    if session is None:
        import requests
        session = requests.Session()
    resultado = {"fuente": None, "fuentes": [], "represa": None, "estaciones": {},
                 "observaciones": [], "pronosticos": [], "avisos": []}
    try:
        html, base = _descargar(session, INICIO)
        urls = resolver_documentos(html.decode("utf-8", errors="replace"), base)
    except Exception as exc:
        resultado["fuente"] = _estado("ctm_salto_grande", INICIO, None, ahora,
                                     error=f"No se obtuvieron enlaces oficiales CTM ({type(exc).__name__}).")
        resultado["fuentes"] = [resultado["fuente"]]
        return resultado
    for tipo, parser in (("niveles", parsear_niveles), ("comunicado", parsear_comunicado)):
        url = urls.get(tipo)
        try:
            if not url:
                raise ValueError("No hay enlace al documento en el sitio oficial")
            data, url = _descargar(session, url, pdf=True)
            parsed = parser(_texto_pdf(data), ahora, url)
            resultado["fuentes"].append(parsed["fuente"])
            resultado["estaciones"].update(parsed["estaciones"])
            resultado["pronosticos"].extend(parsed["pronosticos"])
            # Conserva el documento de operación y la fecha de cada magnitud.
            if resultado["represa"] is None:
                resultado["represa"] = dict(parsed["represa"])
            else:
                comunicado = parsed["represa"]
                resultado["represa"]["operacion_comunicado"] = comunicado
                if parsed["fuente"]["estado"] == "VIGENTE":
                    resultado["represa"]["señal_aguas_arriba"] |= comunicado["señal_aguas_arriba"]
        except Exception as exc:
            resultado["fuentes"].append(_estado("ctm_" + tipo, url or INICIO, None, ahora,
                 error=f"No se obtuvo un documento CTM verificable ({type(exc).__name__})."))
    resultado["observaciones"] = list(resultado["estaciones"].values())
    # Es un resumen de acceso; las vigencias propias se conservan en fuentes.
    vigentes = [f for f in resultado["fuentes"] if f["estado"] == "VIGENTE"]
    fecha = max((_ahora(f["fecha_documento"]) for f in vigentes), default=None)
    error = None if vigentes else "No hay documentos CTM vigentes y verificables."
    resultado["fuente"] = _estado("ctm_salto_grande", INICIO, fecha, ahora, error=error)
    if any(f["estado"] != "VIGENTE" for f in resultado["fuentes"]):
        resultado["avisos"].append("Consulta CTM incompleta: revisar la fecha y el estado de cada documento.")
    return resultado
