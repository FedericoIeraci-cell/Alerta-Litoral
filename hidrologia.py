"""Vinculación conservadora de niveles, previsiones oficiales y señales aguas arriba.

No calcula tiempos de viaje ni cotas de inundación a partir de caudales de represas.
Los umbrales corresponden a la escala de cada estación, nunca al terreno urbano.
Una marca de evacuación no equivale a una orden emitida por Defensa Civil.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from html.parser import HTMLParser
import json
import math
import re
import unicodedata
from urllib.parse import urljoin, urlparse, urlencode
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Argentina/Buenos_Aires")
INA_URL = "https://alerta.ina.gob.ar/a5/diario/reporte_diario"
EBY_URL = "https://www.eby.gov.py/"
EBY_CATEGORIA_URL = "https://www.eby.gov.py/category/estado-del-rio-parana/"
MAX_EDAD_HORAS = 48
TIMEOUT = (20, 30)


def normalizar(value):
    value = unicodedata.normalize("NFKD", str(value or ""))
    value = "".join(c for c in value if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", value.lower()).strip()


def _ahora(referencia=None):
    if referencia is None:
        return datetime.now(TZ)
    if isinstance(referencia, str):
        referencia = datetime.fromisoformat(referencia.replace("Z", "+00:00"))
    if isinstance(referencia, date) and not isinstance(referencia, datetime):
        referencia = datetime.combine(referencia, time.min, TZ)
    if referencia.tzinfo is None:
        referencia = referencia.replace(tzinfo=TZ)
    return referencia.astimezone(TZ)


def _fecha(value):
    if not value:
        return None
    try:
        return _ahora(value)
    except (ValueError, TypeError, AttributeError):
        pass
    match = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})(?:\s+(\d{1,2}):(\d{2}))?", str(value))
    if match:
        try:
            return datetime(int(match[3]), int(match[2]), int(match[1]),
                            int(match[4] or 0), int(match[5] or 0), tzinfo=TZ)
        except ValueError:
            pass
    return None


def _iso(value):
    return value.isoformat() if value else None


def _vigente(fecha, ahora, hasta=None):
    dt = _fecha(fecha)
    if dt is None or dt > ahora + timedelta(minutes=15):
        return False
    if ahora - dt > timedelta(hours=MAX_EDAD_HORAS):
        return False
    fin = _fecha(hasta)
    return fin is None or ahora <= fin


def decimal_es(value):
    """Admite coma decimal y miles españoles; NaN/inf no son datos."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(float(value)) else None
    s = str(value or "").strip().replace("\xa0", "")
    match = re.fullmatch(r"[-+]?\d+(?:[.,]\d+)*(?:\s*(?:m|m³/s|m3/s))?", s)
    if not match:
        return None
    s = re.sub(r"\s*(?:m³/s|m3/s|m)$", "", s)
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    elif s.count(".") > 1:
        return None
    try:
        n = float(s)
        return n if math.isfinite(n) else None
    except ValueError:
        return None


class _Node:
    def __init__(self, tag="root", attrs=None):
        self.tag = tag
        self.attrs = attrs or {}
        self.children = []

    def text(self):
        return re.sub(r"\s+", " ", " ".join(
            c.text() if isinstance(c, _Node) else c for c in self.children)).strip()

    def iter(self, tag=None):
        if tag is None or self.tag == tag:
            yield self
        for child in self.children:
            if isinstance(child, _Node):
                yield from child.iter(tag)


class _DOM(HTMLParser):
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self, contenido):
        super().__init__(convert_charrefs=True)
        self.root = _Node()
        self.stack = [self.root]
        self.feed(contenido)

    def handle_starttag(self, tag, attrs):
        node = _Node(tag, dict(attrs))
        self.stack[-1].children.append(node)
        if tag not in self.VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.stack[-1].children.append(_Node(tag, dict(attrs)))

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        if not any(n.tag in ("script", "style") for n in self.stack):
            self.stack[-1].children.append(data)


# Vinculaciones explícitas por localidad y tramo. Sin búsqueda por proximidad.
# Los nodos urbanos no poseen un mapa de inundabilidad calibrado por barrio.
LOCALIDADES = {
    "reconquista": ("Paraná medio", "Reconquista", "parana", "yacyreta"),
    "avellaneda": ("Arroyo del Rey / Paraná", None, "parana", "yacyreta"),
    "santa fe": ("Paraná medio", "Santa Fe", "parana", "yacyreta"),
    "rosario": ("Paraná inferior", "Rosario", "parana", "yacyreta"),
    "corrientes": ("Paraná medio", "Corrientes", "parana", "yacyreta"),
    "goya": ("Paraná medio", "Goya", "parana", "yacyreta"),
    "paso de los libres": ("Uruguay medio", "Paso de los Libres", "uruguay_superior", None),
    "santo tome": ("Uruguay medio", "Santo Tomé", "uruguay_superior", None),
    "parana": ("Paraná medio", "Paraná", "parana", "yacyreta"),
    "la paz": ("Paraná medio", "La Paz", "parana", "yacyreta"),
    "concordia": ("Uruguay inferior", "Concordia", "uruguay", "salto_grande"),
    "resistencia": ("Paraná: tramo Corrientes-Barranqueras", "Barranqueras", "parana", "yacyreta"),
    "barranqueras": ("Paraná medio", "Barranqueras", "parana", "yacyreta"),
    "formosa": ("Paraguay inferior", "Puerto Formosa", "paraguay", None),
    "clorinda": ("Paraguay / desembocadura Pilcomayo", "Puerto Pilcomayo", "paraguay", None),
    "el colorado": ("Bermejo", None, "bermejo", None),
    "posadas": ("Paraná: embalse Yacyretá", None, "parana_superior", "yacyreta_embalse"),
    "eldorado": ("Alto Paraná", None, "parana_superior", "itaipu"),
    "puerto iguazu": ("Confluencia Iguazú-Paraná", "Puerto Iguazú", "parana_superior", "itaipu"),
    "el soberbio": ("Uruguay superior", None, "uruguay_superior", None),
    "gualeguaychu": ("Boca Gualeguaychú / Uruguay", "Boca Gualeguaychú", "uruguay", "salto_grande"),
    "gualeguay": ("Gualeguay / Delta", None, "gualeguay", None),
    "victoria": ("Delta del Paraná", "Victoria", "parana", "yacyreta"),
    "chajari": ("Afluentes al embalse Salto Grande", None, "uruguay_embalse", "salto_grande_embalse"),
    "ituzaingo": ("Paraná: aguas abajo de Yacyretá", "Puerto Ituzaingó", "parana", "yacyreta"),
    "colon": ("Uruguay inferior", "Colón", "uruguay", "salto_grande"),
    "concepcion del uruguay": ("Uruguay inferior", "Concepción del Uruguay", "uruguay", "salto_grande"),
    "san javier": ("Uruguay superior", "San Javier", "uruguay_superior", None),
    "san javier (misiones)": ("Uruguay superior", "San Javier", "uruguay_superior", None),
    "andresito": ("Iguazú antes de la confluencia", "Andresito", "iguazu", None),
}

# Series públicas de altura hidrométrica; se verifican los metadatos devueltos.
SERIES_ALTURA = {
    "andresito": 8, "puerto iguazu": 9, "corrientes": 19, "barranqueras": 20, "goya": 23,
    "reconquista": 24, "la paz": 26, "parana": 29, "santa fe": 30,
    "victoria": 32, "rosario": 34, "san fernando": 52, "puerto pilcomayo": 55,
    "puerto formosa": 57, "san javier": 65, "santo tome": 68,
    "paso de los libres": 72, "concordia": 79, "colon": 80,
    "concepcion del uruguay": 81, "boca gualeguaychu": 84, "puerto ituzaingo": 15,
}

RED_CUENCAS = {
    "conexiones": [
        ["Alto Paraná / Itaipú", "Confluencia Iguazú-Paraná"],
        ["Iguazú", "Confluencia Iguazú-Paraná"],
        ["Confluencia Iguazú-Paraná", "Yacyretá"],
        ["Yacyretá", "Ituzaingó"], ["Ituzaingó", "Corrientes-Barranqueras"],
        ["Paraguay / Bermejo", "Corrientes-Barranqueras"],
        ["Corrientes-Barranqueras", "Goya-Reconquista"],
        ["Goya-Reconquista", "La Paz"], ["La Paz", "Paraná-Santa Fe"],
        ["Paraná-Santa Fe", "Rosario-Delta"], ["Rosario-Delta", "Río de la Plata"],
        ["Uruguay superior", "Santo Tomé-Paso de los Libres"],
        ["Santo Tomé-Paso de los Libres", "Salto Grande"],
        ["Salto Grande", "Concordia-Colón-Concepción del Uruguay"],
        ["Concordia-Colón-Concepción del Uruguay", "Desembocadura Uruguay"],
        ["Desembocadura Uruguay", "Río de la Plata"],
    ],
    "nota": "Las conexiones indican causalidad aguas abajo; no son tiempos de llegada ni alturas previstas. El Paraná, el Paraguay y el Uruguay son sistemas distintos antes de sus confluencias; marea y viento también condicionan el Delta y el estuario.",
}

_PARANA_ORDEN = ["puerto iguazu", "puerto ituzaingo", "corrientes", "barranqueras", "goya", "reconquista", "la paz", "parana", "santa fe", "victoria", "rosario"]
_URUGUAY_ORDEN = ["san javier", "santo tome", "paso de los libres", "concordia", "colon", "concepcion del uruguay", "boca gualeguaychu"]


def _estado_fuente(nombre, url, fecha, ahora, hasta=None, error=None):
    estado = "ERROR" if error else ("VIGENTE" if _vigente(fecha, ahora, hasta) else "VENCIDO")
    if not error and not _fecha(fecha):
        estado = "SIN_FECHA"
    if _fecha(fecha) and _fecha(fecha) > ahora + timedelta(minutes=15):
        estado = "FECHA_FUTURA"
    return {"id": nombre, "nombre": nombre.replace("_", " ").upper(), "url": url,
            "estado": estado, "fecha_documento": fecha, "consultado": ahora.isoformat(),
            "valido_hasta": hasta, "error": error}


def _nivel(altura, alerta, evacuacion):
    altura, alerta, evacuacion = map(decimal_es, (altura, alerta, evacuacion))
    # Un dato de altura sin umbrales no demuestra riesgo bajo.
    if altura is None or alerta is None or (evacuacion is not None and evacuacion < alerta):
        return "SIN_DATOS"
    if evacuacion is not None and altura >= evacuacion:
        return "EVACUACION"
    return "ALERTA" if altura >= alerta else "BAJO"


def _fecha_ddmm(texto, emision):
    match = re.search(r"(?<!\d)(\d{1,2})/(\d{1,2})(?:/(\d{4}))?(?!\d)", texto)
    if not match or not emision:
        return None
    try:
        year = int(match[3] or emision.year)
        dt = datetime(year, int(match[2]), int(match[1]), tzinfo=TZ)
        # Pronósticos de diciembre pueden citar enero sin repetir el año.
        if not match[3] and emision.month == 12 and dt.month == 1:
            dt = dt.replace(year=year + 1)
        return dt
    except ValueError:
        return None


def _plazo(texto, emision):
    dt = _fecha_ddmm(texto, emision)
    if dt:
        return dt, dt.replace(hour=23, minute=59, second=59), re.search(r"\d{1,2}/\d{1,2}(?:/\d{4})?", texto)[0]
    n = normalizar(texto)
    rango = re.search(r"(?:proximos|plazo de|plazo|en)\s+(\d+)\s*(?:-|a)\s*(\d+)\s*dias", n)
    if rango:
        lo, hi = int(rango[1]), int(rango[2])
        if 0 <= lo <= hi <= 30:
            return emision + timedelta(days=lo), emision + timedelta(days=hi, hours=23, minutes=59), rango[0]
    plazo = re.search(r"(?:proximos|plazo de|en)\s+(\d+)\s*dias", n)
    if plazo and int(plazo[1]) <= 30:
        return emision, emision + timedelta(days=int(plazo[1]), hours=23, minutes=59), plazo[0]
    if "ultima decada de octubre" in n:
        inicio = datetime(emision.year, 10, 21, tzinfo=TZ)
        return inicio, inicio.replace(day=31, hour=23, minute=59, second=59), "última década de octubre"
    if "corto plazo" in n or "proximos dias" in n:
        # Sin duración publicada no se convierte una posibilidad en alerta roja.
        return None, emision + timedelta(hours=48), "corto plazo sin fecha de cruce cuantificada"
    return None, None, "plazo no identificado en el texto oficial"


def _menciones(texto, nombres):
    norm = normalizar(texto)
    found = []
    for key in nombres:
        if not re.search(r"(?<!\w)" + re.escape(key) + r"(?!\w)", norm):
            continue
        # El río Paraná no es automáticamente la estación/localidad Paraná.
        if key == "parana" and not re.search(r"santa fe\s*[/\-]\s*parana|parana\s*/\s*santa fe|en\s+parana(?:/|,|\s|$)", norm):
            continue
        found.append(key)
    return found


def _pronosticos_texto(texto, emision, nombres, fuente):
    if not emision:
        return []
    resultados = []
    # Cada oración se asocia sólo a nombres explicitados. La anáfora no se
    # extrapola a todo el párrafo ni a todos los municipios de una provincia.
    sentences = re.split(r"(?<=[.!?])\s+(?=[A-ZÁÉÍÓÚ-])", texto)
    for sentence in sentences:
        norm = normalizar(sentence)
        if not re.search(r"preve|prevista|pronostic|se espera|probabilidad|chances|continuaria|se observaria", norm):
            continue
        targets = _menciones(sentence, nombres)
        if not targets:
            continue
        negative = bool(re.search(r"no\s+(?:se\s+)?(?:preve|espera)|sin\s+(?:probabilidad|chances|exceder|superar)|no\s+hay\s+probabilidad", norm))
        crosses = list(re.finditer(r"(?:nivel(?:es)?(?:\s+de)?\s+|marca(?:\s+de)?\s+)(alerta|evacuacion)", norm))
        if crosses and not negative:
            for i, match in enumerate(crosses):
                after = sentence[match.start(): crosses[i + 1].start() if i + 1 < len(crosses) else len(sentence)]
                segment_norm = norm[max(0, match.start() - 85): match.end() + 85]
                if not re.search(r"exced|super|sobre\s+la\s+marca|aproximacion|proximidad", segment_norm):
                    continue
                # Se conserva incertidumbre del autor; una aproximación es prealerta.
                nivel = "EVACUACION" if match[1] == "evacuacion" else "ALERTA"
                if "aproximacion" in segment_norm or "proximidad" in segment_norm:
                    nivel = "ALERTA"
                desde, hasta, ventana = _plazo(after, emision)
                if hasta is None:
                    desde, hasta, ventana = _plazo(sentence, emision)
                resultados.append({"localidades": targets, "nivel": nivel,
                    "fecha_emision": emision.isoformat(), "desde": _iso(desde), "hasta": _iso(hasta),
                    "ventana": ventana, "texto": sentence.strip(), "fuente": fuente,
                    "precision_fecha_emision": "fecha_sin_hora", "precision_desde": "fecha_sin_hora",
                    "tipo": "prevision_oficial_textual", "probabilidad": "probabilística según el texto oficial" if re.search(r"probabilidad|chances|probable", norm) else "según previsión oficial"})
        if not any(r["texto"] == sentence.strip() for r in resultados):
            desde, hasta, ventana = _plazo(sentence, emision)
            resultados.append({"localidades": targets, "nivel": "SIN_DATOS", "fecha_emision": emision.isoformat(),
                "desde": _iso(desde), "hasta": _iso(hasta), "ventana": ventana,
                "texto": sentence.strip(), "fuente": fuente, "tipo": "perspectiva_oficial_cualitativa",
                "probabilidad": "sin cruce de umbral confirmado"})
    return resultados


def parsear_reporte_ina(contenido, referencia=None, url=INA_URL):
    ahora = _ahora(referencia)
    dom = _DOM(contenido).root
    textos = dom.text()
    match = re.search(r"Fecha de actualizaci[oó]n\s*:\s*(\d{1,2}/\d{1,2}/\d{4})", textos, re.I)
    emision = _fecha(match[1]) if match else None
    estaciones = {}
    avisos = []
    for table in dom.iter("table"):
        if table.attrs.get("id") not in ("tabla-alturas", "tabla-caudales"):
            continue
        es_altura = table.attrs.get("id") == "tabla-alturas"
        for tr in table.iter("tr"):
            cells = [c for c in tr.children if isinstance(c, _Node) and c.tag == "td"]
            if len(cells) < 5 or not es_altura:
                continue
            nombre = cells[0].text()
            titulo_fecha = next((n.attrs.get("title", "") for n in cells[2].iter() if "fecha:" in n.attrs.get("title", "")), "")
            fecha = _fecha(titulo_fecha)
            estacion = {"nombre": nombre, "rio": cells[1].text(), "altura_m": decimal_es(cells[2].text()),
                "alerta_m": decimal_es(cells[3].text()), "evacuacion_m": decimal_es(cells[4].text()),
                "fecha": _iso(fecha), "precision_fecha": "fecha_sin_hora", "fecha_documento": _iso(emision), "fuente": url, "series_id": tr.attrs.get("data-series_id"),
                "tendencia": tr.attrs.get("data-tendencia"), "perspectiva": cells[5].text() if len(cells) > 5 else "",
                "representatividad": "Escala de la estación; no representa la cota de todas las viviendas de la localidad."}
            # El número de la altura no debe incluir la diferencia del tooltip.
            valor = next((n.text() for n in cells[2].iter("a") if "fecha:" in n.attrs.get("title", "")), None)
            if valor:
                estacion["altura_m"] = decimal_es(valor)
            if estacion["altura_m"] is not None and estacion["altura_m"] < -10:
                estacion["altura_m"] = None
            if estacion["alerta_m"] is not None and estacion["evacuacion_m"] is not None and estacion["alerta_m"] > estacion["evacuacion_m"]:
                avisos.append(f"Umbrales contradictorios en {nombre}; estación sin clasificar.")
                estacion["umbral_invalido"] = True
            estaciones[normalizar(nombre)] = estacion
    if not estaciones:
        avisos.append("El reporte INA no contiene una tabla de alturas reconocida.")
    hydro_textos = [n.text() for n in dom.iter("div") if "paragraph" in n.attrs.get("class", "").split() and "SISTEMA URUGUAY" in n.text()]
    hidrologico = " ".join(hydro_textos)
    nombres = set(estaciones) | set(LOCALIDADES) | {"colon", "ituzaingo"}
    pronosticos = _pronosticos_texto(hidrologico, emision, nombres, url)
    represas = {}
    # La descripción INA permite seguir aportes/erogación sin inventar un caudal.
    for clave, nombre in (("yacyreta", "Yacyretá"), ("itaipu", "Itaipú"), ("salto_grande", "Salto Grande")):
        bloques = re.split(r"\s*- (?=[A-ZÁÉÍÓÚ])", hidrologico)
        bloque = next((b for b in bloques if normalizar(nombre) in normalizar(b) and re.search(r"afluencia|erogacion|derrame|vertido", normalizar(b))), None)
        if bloque:
            texto = bloque[:4500]
            represas[clave] = {"nombre": nombre, "fecha": _iso(emision), "hasta": None,
                "texto": texto, "fuente": url, "señal_aguas_arriba": bool(re.search(r"aguas altas|ascenso|incremento|vertedero abierto", normalizar(texto))),
                "tipo": "descripción de operación publicada por INA; sin extrapolación local"}
    return {"fuente": _estado_fuente("ina_diario", url, _iso(emision), ahora), "estaciones": estaciones,
            "pronosticos": pronosticos, "represas": represas, "avisos": avisos, "texto_hidrologico": hidrologico}


_MESES = {"enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6, "julio": 7, "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12}


def _fecha_es(texto, year=None):
    match = re.search(r"(\d{1,2})\s+(?:de\s+)?(" + "|".join(_MESES) + r")(?:\s*,?\s*(?:de\s+)?(\d{4}))?", normalizar(texto))
    if not match or not (match[3] or year):
        return None
    try:
        return datetime(int(match[3] or year), _MESES[match[2]], int(match[1]), tzinfo=TZ)
    except ValueError:
        return None


def parsear_boletin_yacyreta(contenido, referencia=None, url=EBY_URL):
    ahora = _ahora(referencia)
    dom = _DOM(contenido).root
    article = next(dom.iter("article"), None)
    texto = article.text() if article else ""
    fecha = _fecha_es(texto)
    if not fecha:
        # WordPress publica la fecha inequívoca en un time; no usar fechas del sidebar.
        fecha = next((_fecha(t.attrs.get("datetime")) for t in (article.iter("time") if article else []) if _fecha(t.attrs.get("datetime"))), None)
    fin_match = re.search(r"abarcan hasta([^.!]{0,90})", texto, re.I)
    fin = _fecha_es(fin_match[1], fecha.year) if fin_match and fecha else None
    if fin:
        fin = fin.replace(hour=23, minute=59, second=59)
    # Un mismo parte puede repartir el puerto, la lectura y los umbrales en
    # varios párrafos. Acotar por el próximo puerto mantiene las escalas separadas.
    bloque_ituzaingo = re.search(r"\bPuerto Ituzaing[oó]\b.*?(?=\bPuerto (?:de )?Ayolas\b|$)", texto, re.I)
    nivel_texto = bloque_ituzaingo[0] if bloque_ituzaingo else ""
    altura_m = alerta_m = evacuacion_m = None
    fecha_lectura = fecha
    lectura = re.search(r"lectura.*?hora\s+(\d{1,2}):(\d{2}).*?fue de\s*([\d.,]+)\s*m", nivel_texto, re.I)
    umbral = re.search(r"Nivel de alerta en Puerto Ituzaing[oó]\s*:\s*([\d.,]+)\s*m", nivel_texto, re.I)
    umbral_evacuacion = re.search(r"Nivel de Evacuaci[oó]n(?:\s+en Puerto Ituzaing[oó])?\s*:?\s*([\d.,]+)\s*m", nivel_texto, re.I)
    if lectura:
        altura_m = decimal_es(lectura[3])
        if fecha:
            fecha_lectura = fecha.replace(hour=int(lectura[1]), minute=int(lectura[2]))
    if umbral:
        alerta_m = decimal_es(umbral[1])
    if umbral_evacuacion:
        # Sólo la referencia explícita del bloque de este puerto; no trasladar
        # la inundabilidad de Ayolas ni completar un umbral ausente.
        evacuacion_m = decimal_es(umbral_evacuacion[1])
    estaciones = {}
    if altura_m is not None and alerta_m is not None:
        estaciones["puerto ituzaingo"] = {"nombre": "Puerto Ituzaingó", "rio": "Paraná", "altura_m": altura_m,
            "alerta_m": alerta_m, "evacuacion_m": evacuacion_m, "fecha": _iso(fecha_lectura),
            "precision_fecha": "fecha_hora_publicada", "fecha_documento": _iso(fecha),
            "precision_fecha_documento": "fecha_sin_hora", "fuente": url,
            "perspectiva": nivel_texto, "representatividad": "Puerto Ituzaingó; lectura de escala, no cota de cada vivienda."}
    pronosticos = []
    rango = re.search(r"proximos d[ií]as.*?entre\s*([\d.,]+)\s*/\s*([\d.,]+)\s*m", normalizar(nivel_texto))
    if rango and fecha and fin:
        extremos = [decimal_es(rango[1]), decimal_es(rango[2])]
        if all(v is not None for v in extremos):
            # EBY también publica los extremos en orden descendente (5/4.4 m).
            # Su orden textual se conserva; no se deducen horas ni probabilidades.
            minimo, maximo = min(extremos), max(extremos)
            pronosticos.append({"localidades": ["ituzaingo"], "nivel": "ALERTA" if maximo >= (alerta_m or math.inf) else "SIN_DATOS",
                # El parte delimita un período; no publica un punto de cruce.
                # Una fecha documental a medianoche no activa preparación de 72 h.
                "fecha_emision": _iso(fecha), "desde": None, "hasta": _iso(fin),
                "precision_fecha_emision": "fecha_sin_hora", "precision_desde": "fecha_sin_hora",
                "precision_hasta": "fecha_sin_hora",
                "ventana": f"próximos días, hasta {fin.strftime('%d/%m/%Y')} según EBY; sin fecha de cruce publicada", "texto": nivel_texto,
                "fuente": url, "tipo": "prevision_oficial_rango", "minimo_m": minimo, "maximo_m": maximo,
                "extremos_publicados_m": extremos,
                "probabilidad": "sin probabilidad cuantificada; extremos del período publicados por EBY, sujetos a revisión"})
    entrada = re.search(r"caudal afluente previsto.*?entorno de\s*([\d.,]+)\s*m", texto, re.I)
    aporte_futuro = re.search(r"proximos d[ií]as entre\s*([\d.,]+)\s*a\s*([\d.,]+)\s*m", normalizar(texto))
    def caudal(s):
        # Los boletines EBY usan punto como separador de miles (34.100 m³/s).
        return decimal_es(s.replace(".", "")) if s else None
    aportes = [caudal(aporte_futuro[1]), caudal(aporte_futuro[2])] if aporte_futuro else []
    aportes = aportes if aportes and all(v is not None for v in aportes) else []
    represa = {"nombre": "Yacyretá", "fecha": _iso(fecha), "hasta": _iso(fin), "fuente": url,
        "precision_fecha": "fecha_sin_hora", "precision_hasta": "fecha_sin_hora",
        "texto": texto, "señal_aguas_arriba": bool(re.search(r"ascenso en los niveles|tendencia creciente", normalizar(texto))),
        "afluente_m3s": caudal(entrada[1]) if entrada else None,
        "afluente_tipo": "prevision_del_dia" if entrada else None,
        "afluente_previsto_min_m3s": min(aportes) if aportes else None,
        "afluente_previsto_max_m3s": max(aportes) if aportes else None,
        "afluente_previsto_extremos_publicados_m3s": aportes,
        "afluente_previsto_interpretacion": "Extremos del período en el orden del boletín; sin probabilidad cuantificada ni fechas puntuales asignadas." if aportes else None,
        "nota": "El caudal afluente es agua que ingresa al embalse; el valor del día se publica como previsto. Los extremos futuros conservan el texto y su orden original; no forman una banda probabilística ni una cota prevista para todo el Litoral."}
    fuente = {**_estado_fuente("eby_yacyreta", url, _iso(fecha), ahora, _iso(fin)),
              "precision_fecha_documento": "fecha_sin_hora", "precision_valido_hasta": "fecha_sin_hora"}
    return {"fuente": fuente,
            "estaciones": estaciones, "pronosticos": pronosticos, "represa": represa}


def _descargar(session, url):
    response = session.get(url, timeout=TIMEOUT, headers={"User-Agent": "ALA-Hidrologia/1.0 (consulta de fuentes oficiales)"})
    response.raise_for_status()
    # Las páginas argentinas/paraguayas publican UTF-8 aunque algunos servidores
    # no anuncian correctamente el charset.
    if hasattr(response, "encoding"):
        response.encoding = "utf-8"
    return response.text


def url_serie_ina(series_id, referencia=None):
    ahora = _ahora(referencia)
    return "https://alerta.ina.gob.ar/a5/getSeriesBySiteAndVar?" + urlencode({
        "series_id": series_id, "tipo": "puntual",
        "timestart": (ahora - timedelta(days=2)).date().isoformat(),
        "timeend": (ahora + timedelta(days=15)).date().isoformat(), "includeProno": "true"})


def parsear_serie_ina(payload, series_id, referencia=None, url=None):
    """Lee observaciones y bandas del visualizador INA, sin interpolar puntos."""
    ahora = _ahora(referencia)
    url = url or url_serie_ina(series_id, ahora)
    if not isinstance(payload, dict) or str(payload.get("id")) != str(series_id):
        raise ValueError("La respuesta no corresponde a la serie solicitada")
    var = payload.get("var") or {}
    unidades = payload.get("unidades") or {}
    est = payload.get("estacion") or {}
    if str(var.get("id")) != "2" or str(unidades.get("id")) != "11" or normalizar(unidades.get("nombre")) not in ("metros", "metro"):
        raise ValueError("Variable o unidad no corresponde a altura hidrométrica en metros")
    if not est.get("nombre"):
        raise ValueError("Falta identificación de estación")
    nombre = est["nombre"]
    station_key = normalizar(nombre)
    if station_key == "ituzaingo":
        station_key, nombre = "puerto ituzaingo", "Puerto Ituzaingó"
    esperado = next((key for key, sid in SERIES_ALTURA.items() if str(sid) == str(series_id)), None)
    if esperado is not None and station_key != esperado:
        raise ValueError("El nombre de estación no coincide con la vinculación de la serie")
    if str(series_id) == "65" and est.get("provincia") and normalizar(est["provincia"]) != "misiones":
        raise ValueError("San Javier no corresponde a la estación de Misiones")
    if est.get("tabla") is not None and est["tabla"] != "alturas_prefe":
        raise ValueError("La serie no pertenece a la escala de alturas de Prefectura vinculada")
    obs_validas = []
    for item in payload.get("observaciones", []):
        if not isinstance(item, (list, tuple)) or len(item) < 3:
            continue
        dt, valor = _fecha(item[0]), decimal_es(item[2])
        if dt is not None and dt <= ahora and valor is not None and valor >= -10:
            obs_validas.append((dt, valor))
    ultimo = max(obs_validas, default=(None, None), key=lambda x: x[0])
    estacion = {"nombre": nombre, "rio": est.get("rio", ""), "altura_m": ultimo[1],
        "alerta_m": decimal_es(est.get("nivel_alerta")), "evacuacion_m": decimal_es(est.get("nivel_evacuacion")),
        "fecha": _iso(ultimo[0]), "fuente": url, "series_id": series_id,
        "representatividad": "Escala oficial de estación, sin cota de inundación por barrio.",
        "observaciones": [{"fecha": dt.isoformat(), "valor": valor} for dt, valor in sorted(obs_validas)]}
    # INA publica también series manuales con registros diarios a medianoche.
    # Conservar ese timestamp no demuestra la hora física de la medición.
    automatica = est.get("automatica") if isinstance(est.get("automatica"), bool) else None
    estacion.update(tabla=est.get("tabla"), var_id=2, unidad="m", automatica=automatica,
        propietario=est.get("propietario"), fecha_registro=_iso(ultimo[0]),
        precision_fecha="fecha_hora_publicada" if automatica is True else "fecha_registro_fuente",
        nota_fecha=("Fecha y hora del registro INA; la hora de medición no está confirmada."
                    if automatica is not True else "Fecha y hora publicadas por la estación automática INA."))
    pronosticos, curvas, candidatos = [], [], []
    for p in payload.get("pronosticos", []):
        corrida = p.get("corrida")
        if not isinstance(corrida, dict) or not isinstance(corrida.get("series"), list):
            continue
        out_id = p.get("out_id", [])
        if not isinstance(out_id, list):
            out_id = [out_id]
        if str(series_id) not in [str(v) for v in out_id]:
            continue
        emision = _fecha(corrida.get("forecast_date"))
        if not emision or not _vigente(emision.isoformat(), ahora):
            continue
        candidatos.append((emision, bool(p.get("selected")), p))
    if candidatos:
        _, _, elegido = max(candidatos, key=lambda x: (x[0], x[1]))
        corrida = elegido["corrida"]
        emision = _fecha(corrida["forecast_date"])
        nombre_modelo = elegido.get("nombre", "Previsión INA")
        bands = {}
        for serie in corrida["series"]:
            qualifier = normalizar(serie.get("qualifier", ""))
            qualifier = {"main": "medio", "media": "medio", "central": "medio"}.get(qualifier, qualifier)
            if qualifier not in ("medio", "inferior", "superior"):
                continue
            for punto in serie.get("pronosticos", []):
                dt = _fecha(punto.get("timestart"))
                valor = decimal_es(punto.get("valor"))
                if not dt or valor is None or valor < -10 or dt < emision:
                    continue
                bands.setdefault(dt.isoformat(), {})[qualifier] = valor
        for fecha_punto in sorted(bands):
            band = bands[fecha_punto]
            lo, mid, hi = (band.get(k) for k in ("inferior", "medio", "superior"))
            if mid is not None and ((lo is not None and lo > mid) or (hi is not None and hi < mid)):
                continue
            dt = _fecha(fecha_punto)
            if dt < ahora.replace(hour=0, minute=0, second=0, microsecond=0):
                continue
            curvas.append({"estacion": nombre, "fecha": fecha_punto, "central_m": mid, "inferior_m": lo, "superior_m": hi,
                "fecha_emision": emision.isoformat(), "modelo": nombre_modelo, "fuente": url})
        if curvas:
            fin = max(_fecha(c["fecha"]) for c in curvas).replace(hour=23, minute=59, second=59)
            for c in curvas:
                nivel = _nivel(c["central_m"], estacion["alerta_m"], estacion["evacuacion_m"])
                nivel_superior = _nivel(c["superior_m"], estacion["alerta_m"], estacion["evacuacion_m"])
                fecha_punto = _fecha(c["fecha"])
                texto = (f"{nombre}: previsión INA {nombre_modelo} emitida {emision.isoformat()}; "
                    f"punto publicado {fecha_punto.strftime('%d/%m/%Y')}: central {c['central_m']} m, "
                    f"banda inferior {c['inferior_m']} m, superior {c['superior_m']} m. "
                    "No se interpola fecha de primer cruce entre puntos; las bandas no son certezas.")
                if nivel == "SIN_DATOS":
                    continue
                pronosticos.append({"localidades": [station_key], "nivel": nivel, "fecha_emision": emision.isoformat(),
                    "desde": fecha_punto.isoformat(), "hasta": fin.isoformat(), "ventana": fecha_punto.strftime('%d/%m/%Y') + " (punto numérico INA)",
                    "texto": texto, "fuente": url, "tipo": "prevision_oficial_numerica", "modelo": nombre_modelo,
                    "central_m": c["central_m"], "inferior_m": c["inferior_m"], "superior_m": c["superior_m"],
                    "probabilidad": "escenario central y bandas oficiales; sin probabilidad inventada"})
                if nivel_superior in ("ALERTA", "EVACUACION") and nivel not in ("ALERTA", "EVACUACION"):
                    pronosticos.append({"localidades": [station_key], "nivel": "ALERTA", "fecha_emision": emision.isoformat(),
                        "desde": fecha_punto.isoformat(), "hasta": fin.isoformat(), "ventana": fecha_punto.strftime('%d/%m/%Y') + " (banda superior)",
                        "texto": texto + " Sólo la banda superior cruza un umbral: vigilancia por incertidumbre, sin certeza de evacuación.",
                        "fuente": url, "tipo": "banda_superior_incierta", "probabilidad": "banda superior; incertidumbre elevada"})
    estacion["pronostico_numerico"] = [{"fecha": c["fecha"], "valor": c["central_m"], "inferior": c["inferior_m"],
            "superior": c["superior_m"], "emision": c["fecha_emision"], "modelo": c["modelo"], "fuente": c["fuente"]} for c in curvas]
    if curvas:
        estacion["fuente_pronostico"] = url
    fuente_fecha = max([_fecha(p["fecha_emision"]) for p in pronosticos] + ([ultimo[0]] if ultimo[0] else []), default=None)
    return {"fuente": _estado_fuente(f"ina_serie_{series_id}", url, _iso(fuente_fecha), ahora),
            "estaciones": {station_key: estacion}, "pronosticos": pronosticos, "curvas": curvas}


def _integrar_lecturas_actuales_ina(panorama, ahora, session, estaciones_series):
    """Actualiza la observación con la misma escala; no renueva su fecha.

    WFS identifica la serie, tabla y variable y publica umbrales de esa misma
    feature. Las previsiones conservan su corrida, fecha y fuente originales.
    """
    from hidro_actual import consultar_rios, url_rios
    url = url_rios()
    try:
        lecturas = consultar_rios(ahora, session)
        panorama["lecturas_actuales_ina"] = lecturas
        validas = [d for d in lecturas if not d.get("error") and _fecha(d.get("fecha"))
                   and d.get("altura_m") is not None]
        if not validas:
            raise ValueError("Ninguna observación INA con identidad y fecha verificadas")
        integradas = 0
        for key, sid in SERIES_ALTURA.items():
            lectura = next((d for d in validas if d.get("serie") == sid), None)
            if not lectura:
                continue
            nombre = normalizar(lectura.get("nombre"))
            nombre = "puerto ituzaingo" if nombre == "ituzaingo" else nombre
            if (nombre != key or lectura.get("tabla") != "alturas_prefe"
                    or lectura.get("var_id") != 2 or lectura.get("unidad") != "m"):
                continue
            vieja = panorama["estaciones"].get(key, {})
            # Una respuesta atrasada o repetida no sustituye la última lectura.
            if _fecha(vieja.get("fecha")) and _fecha(lectura["fecha"]) <= _fecha(vieja["fecha"]):
                continue
            serie = estaciones_series.get(key, {})
            misma_serie = (serie.get("series_id") == sid and serie.get("tabla") == "alturas_prefe"
                           and serie.get("var_id") == 2 and serie.get("unidad") == "m")
            # Una serie de otra red (p. ej. RHN 29437) nunca aporta umbrales.
            base = dict(serie) if misma_serie else {}
            alerta, evacuacion = lectura.get("alerta_m"), lectura.get("evacuacion_m")
            fuente_umbrales = url
            if alerta is None and evacuacion is None and not lectura.get("umbral_invalido") and misma_serie:
                alerta, evacuacion = serie.get("alerta_m"), serie.get("evacuacion_m")
                fuente_umbrales = serie.get("fuente_umbrales", serie.get("fuente"))
            observaciones = list(base.get("observaciones", []))
            observaciones.append({"fecha": lectura["fecha"], "valor": lectura["altura_m"], "fuente": url})
            base.update(nombre="Puerto Ituzaingó" if key == "puerto ituzaingo" else lectura["nombre"],
                series_id=sid, tabla="alturas_prefe", var_id=2, unidad="m", altura_m=lectura["altura_m"],
                alerta_m=alerta, evacuacion_m=evacuacion, umbral_invalido=lectura.get("umbral_invalido", False),
                fecha=lectura["fecha"], fecha_registro=lectura.get("fecha_registro", lectura["fecha"]),
                fuente=url, fuente_umbrales=fuente_umbrales, consultado=lectura["consulta"],
                precision_fecha="fecha_registro_fuente", nota_fecha=lectura.get("nota_fecha"),
                estado_lectura=lectura.get("estado"), edad_horas=lectura.get("edad_horas"),
                automatica=serie.get("automatica") if misma_serie else None,
                observaciones=observaciones,
                representatividad="Escala oficial de estación, sin cota de inundación por barrio.")
            panorama["estaciones"][key] = base
            integradas += 1
        fecha = max(_fecha(d["fecha"]) for d in validas).isoformat()
        panorama["fuentes"].append({**_estado_fuente("ina_ultimas_alturas", url, fecha, ahora),
            "precision_fecha_documento": "fecha_registro_fuente", "lecturas_verificadas": len(validas),
            "lecturas_sin_verificar": len(lecturas) - len(validas), "observaciones_integradas": integradas,
            "nota": "Fecha del registro INA más reciente del conjunto; cada estación conserva su fecha y vigencia propias."})
    except Exception as exc:
        panorama["fuentes"].append(_estado_fuente("ina_ultimas_alturas", url, None, ahora,
            error=f"No se obtuvieron últimas alturas INA verificables ({type(exc).__name__})."))


def obtener_panorama(referencia=None, session=None):
    """Obtiene documentos oficiales; cada error conserva cobertura desconocida."""
    ahora = _ahora(referencia)
    if session is None:
        import requests
        session = requests.Session()
    panorama = {"version": "1.0", "consultado": ahora.isoformat(), "fuentes": [], "estaciones": {},
                "pronosticos": [], "represas": {}, "curvas": [], "red_cuencas": RED_CUENCAS, "avisos": []}
    try:
        parsed = parsear_reporte_ina(_descargar(session, INA_URL), ahora)
        panorama["fuentes"].append(parsed["fuente"])
        for key in ("estaciones", "represas"):
            panorama[key].update(parsed[key])
        panorama["pronosticos"].extend(parsed["pronosticos"])
        panorama["avisos"].extend(parsed["avisos"])
    except Exception as exc:
        panorama["fuentes"].append(_estado_fuente("ina_diario", INA_URL, None, ahora, error=f"No se obtuvo el reporte ({type(exc).__name__})."))
    try:
        homepage = _DOM(_descargar(session, EBY_URL)).root
        links = []
        for a in homepage.iter("a"):
            href = urljoin(EBY_URL, a.attrs.get("href", ""))
            if "alerta-hidrologico" in href and urlparse(href).hostname == "www.eby.gov.py" and href not in links:
                links.append(href)
        if not links:
            # La portada institucional no siempre incluye los boletines; la
            # categoría oficial del río Paraná es un índice independiente.
            categoria = _DOM(_descargar(session, EBY_CATEGORIA_URL)).root
            for a in categoria.iter("a"):
                href = urljoin(EBY_URL, a.attrs.get("href", ""))
                if "alerta-hidrologico" in href and urlparse(href).hostname == "www.eby.gov.py" and href not in links:
                    links.append(href)
        if not links:
            raise ValueError("No hay enlace reconocible a boletín hidrológico EBY")
        candidatos = []
        for url in links[:3]:
            try:
                parsed = parsear_boletin_yacyreta(_descargar(session, url), ahora, url)
                if _fecha(parsed["fuente"]["fecha_documento"]):
                    candidatos.append(parsed)
            except Exception:
                continue
        if not candidatos:
            raise ValueError("No se pudo verificar la fecha de ningún boletín enlazado")
        parsed = max(candidatos, key=lambda p: _fecha(p["fuente"]["fecha_documento"]) or datetime.min.replace(tzinfo=TZ))
        panorama["fuentes"].append(parsed["fuente"])
        panorama["estaciones"].update(parsed["estaciones"])
        panorama["pronosticos"].extend(parsed["pronosticos"])
        # Si EBY está vencido, se conserva la señal vigente más reciente del INA.
        actual = panorama["represas"].get("yacyreta")
        if parsed["fuente"]["estado"] == "VIGENTE" or not actual:
            panorama["represas"]["yacyreta"] = parsed["represa"]
        panorama["represas"]["yacyreta_boletin"] = parsed["represa"]
    except Exception as exc:
        panorama["fuentes"].append(_estado_fuente("eby_yacyreta", EBY_URL, None, ahora, error=f"No se obtuvo un boletín verificable ({type(exc).__name__})."))
    def leer_serie(item):
        key, series_id = item
        url = url_serie_ina(series_id, ahora)
        try:
            return parsear_serie_ina(json.loads(_descargar(session, url)), series_id, ahora, url)
        except Exception as exc:
            return {"fuente": _estado_fuente(f"ina_serie_{series_id}", url, None, ahora,
                    error=f"Serie {key}: no se obtuvo altura/previsión verificable ({type(exc).__name__})."),
                    "estaciones": {}, "pronosticos": [], "curvas": []}
    estaciones_series = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        for parsed in pool.map(leer_serie, SERIES_ALTURA.items()):
            panorama["fuentes"].append(parsed["fuente"])
            panorama["pronosticos"].extend(parsed["pronosticos"])
            panorama["curvas"].extend(parsed["curvas"])
            for key, nueva in parsed["estaciones"].items():
                estaciones_series[key] = nueva
                vieja = panorama["estaciones"].get(key)
                if _fecha(nueva.get("fecha")) and (not vieja or not _fecha(vieja.get("fecha")) or _fecha(nueva["fecha"]) >= _fecha(vieja["fecha"])):
                    panorama["estaciones"][key] = nueva
                elif vieja and nueva.get("pronostico_numerico"):
                    # Una lectura más antigua no reemplaza la altura; puede
                    # aportar una corrida pronosticada válida de esa estación.
                    vieja["pronostico_numerico"] = nueva["pronostico_numerico"]
                    vieja["fuente_pronostico"] = nueva.get("fuente_pronostico", nueva.get("fuente"))
    _integrar_lecturas_actuales_ina(panorama, ahora, session, estaciones_series)
    try:
        from ctm import obtener_ctm
        ctm = obtener_ctm(ahora, session)
        panorama["fuentes"].extend(ctm.get("fuentes") or ([ctm["fuente"]] if ctm.get("fuente") else []))
        panorama["avisos"].extend(ctm.get("avisos", []))
        if ctm.get("represa"):
            panorama["represas"]["salto_grande"] = ctm["represa"]
        panorama["pronosticos"].extend(ctm.get("pronosticos", []))
        for key, nueva in ctm.get("estaciones", {}).items():
            vieja = panorama["estaciones"].get(key)
            if not vieja or not _fecha(nueva.get("fecha")):
                continue
            # La escala CTM/INA se vincula sólo por el mismo puerto y río.
            # El PDF CTM no declara umbrales; los conserva de la estación INA.
            if "uruguay" not in normalizar(nueva.get("rio")) or "uruguay" not in normalizar(vieja.get("rio")):
                continue
            if _fecha(vieja.get("fecha")) and _fecha(nueva["fecha"]) <= _fecha(vieja["fecha"]):
                continue
            combinada = dict(vieja)
            combinada.update({k: v for k, v in nueva.items() if v is not None})
            combinada["fuente_umbrales"] = vieja.get("fuente")
            combinada["observaciones"] = list(vieja.get("observaciones", [])) + [{"fecha": nueva["fecha"], "valor": nueva.get("altura_m")}]
            panorama["estaciones"][key] = combinada
    except Exception as exc:
        panorama["fuentes"].append(_estado_fuente("ctm_salto_grande", "https://www.saltogrande.org/", None, ahora,
            error=f"No se obtuvo un documento CTM verificable ({type(exc).__name__})."))
    # La observación horaria se publica por separado del parte diario y sus
    # previsiones. No reemplaza señales usadas por el motor de avisos.
    from hidro_actual import consultar_salto_grande, CTM_HORARIOS
    try:
        horario = consultar_salto_grande(ahora, session)
        panorama["represas"]["salto_grande_horario"] = horario
        panorama["fuentes"].append({**_estado_fuente("ctm_horarios", CTM_HORARIOS,
            horario["fecha"], ahora, horario["hasta"]), "precision_fecha_documento": "fecha_hora_publicada"})
    except Exception as exc:
        panorama["fuentes"].append(_estado_fuente("ctm_horarios", CTM_HORARIOS, None, ahora,
            error=f"No se obtuvo la observación horaria CTM ({type(exc).__name__})."))
    # Enriquecer rangos oficiales sólo con el umbral de la escala local.
    # Un límite superior aislado nunca demuestra que se alcanzará evacuación.
    for p in panorama["pronosticos"]:
        if p.get("tipo") not in ("prevision_oficial_rango", "prevision_oficial_ctm"):
            continue
        key = next(iter(p.get("localidades", [])), "")
        key = "puerto ituzaingo" if key == "ituzaingo" else key
        estacion = panorama["estaciones"].get(key)
        if not estacion or not _vigente(estacion.get("fecha"), ahora):
            continue
        alerta = decimal_es(estacion.get("alerta_m"))
        evacuacion = decimal_es(estacion.get("evacuacion_m"))
        minimo, maximo = decimal_es(p.get("minimo_m")), decimal_es(p.get("maximo_m"))
        if minimo is not None and evacuacion is not None and minimo >= evacuacion:
            p["nivel"] = "EVACUACION"
            p["probabilidad"] = p.get("probabilidad", "rango oficial") + "; el rango completo supera el umbral local de evacuación de la estación"
        elif maximo is not None and alerta is not None and maximo >= alerta:
            p["nivel"] = "ALERTA"
            if minimo is None or minimo < alerta:
                p["probabilidad"] = p.get("probabilidad", "rango oficial") + "; sólo rango/límite superior: cruce de umbral incierto"
        p["fuente_umbrales"] = estacion.get("fuente_umbrales", estacion.get("fuente"))
    return panorama


VERSION_EVIDENCIAS = 1
VERSION_MOTOR = "2026-10-18.1"


def _fuente_verificable(url, panorama):
    """Una URL sola no anula un fallo registrado por el adaptador."""
    from explicaciones_alerta import url_oficial
    if not url_oficial(url):
        return False
    registros = [f for f in panorama.get("fuentes", []) if f.get("url") == url]
    return not any(f.get("error") or f.get("estado") == "ERROR" for f in registros)


def _evidencia(tipo, estacion, fuente, fecha, ahora, rango, **campos):
    limite = _fecha(fecha) + timedelta(hours=MAX_EDAD_HORAS)
    hasta = _fecha(campos.get("hasta"))
    if hasta:
        limite = min(limite, hasta)
    documento = _fecha(campos.get("fecha_documento"))
    if documento:
        limite = min(limite, documento + timedelta(hours=MAX_EDAD_HORAS))
    return {"tipo": tipo, "estacion": estacion, "fuente": fuente,
            "rango": rango, "verificada": True, "vigente": True,
            "evaluada_en": ahora.isoformat(), "valida_hasta": limite.isoformat(),
            "criterio_vigencia": "Máximo 48 h desde la fecha de la fuente; límite de uso de ALA, no vigencia oficial adicional",
            **campos}


def _upstream(localidad, station_key, sistema, panorama, ahora):
    # Sólo para orden causal del tramo, no para asignar a Avellaneda una
    # altura/umbral medido en la ciudad vecina.
    if localidad == "avellaneda":
        station_key = "reconquista"
    if sistema == "parana":
        indice = _PARANA_ORDEN.index(station_key) if station_key in _PARANA_ORDEN else 0
        anteriores = _PARANA_ORDEN[:indice] + ["puerto pilcomayo", "puerto formosa"]
    elif sistema == "uruguay":
        indice = _URUGUAY_ORDEN.index(station_key) if station_key in _URUGUAY_ORDEN else len(_URUGUAY_ORDEN)
        anteriores = _URUGUAY_ORDEN[:indice]
    elif sistema == "uruguay_superior":
        indice = _URUGUAY_ORDEN.index(station_key) if station_key in _URUGUAY_ORDEN else 0
        anteriores = _URUGUAY_ORDEN[:indice]
    else:
        anteriores = []
    result = []
    for key in anteriores:
        obs = panorama.get("estaciones", {}).get(key)
        if (not obs or not _vigente(obs.get("fecha"), ahora) or obs.get("umbral_invalido")
                or not _fuente_verificable(obs.get("fuente"), panorama)
                or not _fuente_verificable(obs.get("fuente_umbrales", obs.get("fuente")), panorama)):
            continue
        if obs.get("fecha_documento") and not _vigente(obs["fecha_documento"], ahora):
            continue
        nivel = _nivel(obs.get("altura_m"), obs.get("alerta_m"), obs.get("evacuacion_m"))
        if nivel in ("ALERTA", "EVACUACION"):
            result.append({"estacion": obs.get("nombre", key), "nivel": nivel, "fecha": obs.get("fecha"), "fuente": obs.get("fuente"),
                "altura_m": obs.get("altura_m"), "alerta_m": obs.get("alerta_m"), "evacuacion_m": obs.get("evacuacion_m"),
                "precision_fecha": obs.get("precision_fecha", "fecha_hora_publicada"),
                "fecha_registro": obs.get("fecha_registro"), "fuente_umbrales": obs.get("fuente_umbrales", obs.get("fuente")),
                "nota": "Señal aguas arriba para vigilancia; no demuestra ese nivel ni un tiempo de llegada en la localidad."})
    return result


def evaluar_localidad(localidad, panorama, referencia=None):
    ahora = _ahora(referencia)
    nombre = localidad.get("localidad", "") if isinstance(localidad, dict) else str(localidad)
    key = normalizar(nombre)
    mapping = LOCALIDADES.get(key)
    result = {"hidrologia_aplica": bool(mapping), "hidrologia_vigente": False,
        "hidrologia_pronostico_vigente": False, "represa_aplica": bool(mapping and mapping[3]), "represa_vigente": False,
        "nivel_hidrologico_observado": "SIN_DATOS" if mapping else "NO_APLICA", "nivel_hidrologico_previsto": "SIN_DATOS",
        "hidrologia_rango": None, "hidrologia_fuentes": "", "hidrologia_motivo": "",
        "hidrologia_accion": "", "hidrologia_estacion": mapping[1] if mapping and mapping[1] else "Sin estación local vinculada",
        "hidrologia_altura_m": None, "hidrologia_alerta_m": None, "hidrologia_evacuacion_m": None,
        "hidrologia_fecha": None, "hidrologia_ventana": "Sin horizonte fluvial verificado",
        "hidrologia_firma": "", "hidrologia_cobertura": "NO_APLICA" if not mapping else "SIN_DATOS",
        "hidrologia_pronostico_texto": "", "hidrologia_alertas_aguas_arriba": [], "hidrologia_incertidumbre": ""}
    result["hidrologia_preparacion_evacuacion_72h"] = False
    result["hidrologia_evidencias"] = []
    if not mapping:
        result["hidrologia_motivo"] = "Sin vínculo directo evaluado con una estación de los grandes ríos. El anegamiento local por lluvias se evalúa por separado; no se descarta inundación urbana o de arroyos."
        result["hidrologia_accion"] = "Consultar alertas meteorológicas y autoridades locales para arroyos, drenajes y anegamientos."
        result["hidrologia_firma"] = sha256(f"{key}|NO_APLICA".encode()).hexdigest()[:20]
        return result
    tramo, estacion_nombre, sistema, represa_key = mapping
    station_key = normalizar(estacion_nombre)
    estacion = panorama.get("estaciones", {}).get(station_key)
    motivos, fuentes = [], set()
    observado_rango = previsto_rango = None
    if estacion:
        result.update({"hidrologia_altura_m": estacion.get("altura_m"), "hidrologia_alerta_m": estacion.get("alerta_m"),
            "hidrologia_evacuacion_m": estacion.get("evacuacion_m"), "hidrologia_fecha": estacion.get("fecha")})
        if estacion.get("fuente"):
            fuentes.add(estacion["fuente"])
        vigente = (_vigente(estacion.get("fecha"), ahora) and not estacion.get("umbral_invalido", False)
                   and _fuente_verificable(estacion.get("fuente"), panorama)
                   and _fuente_verificable(estacion.get("fuente_umbrales", estacion.get("fuente")), panorama))
        if estacion.get("fecha_documento"):
            vigente = vigente and _vigente(estacion["fecha_documento"], ahora)
        nivel = _nivel(estacion.get("altura_m"), estacion.get("alerta_m"), estacion.get("evacuacion_m")) if vigente else "SIN_DATOS"
        result["nivel_hidrologico_observado"] = nivel
        result["hidrologia_vigente"] = vigente and nivel != "SIN_DATOS"
        observado_rango = {"BAJO": 0, "ALERTA": 1, "EVACUACION": 2}.get(nivel)
        if nivel != "SIN_DATOS":
            result["hidrologia_evidencias"].append(_evidencia(
                "observacion_fluvial", estacion_nombre, estacion.get("fuente"), estacion["fecha"], ahora, observado_rango,
                nivel=nivel, altura_m=estacion.get("altura_m"), alerta_m=estacion.get("alerta_m"),
                evacuacion_m=estacion.get("evacuacion_m"), fecha_observada=estacion["fecha"],
                precision_fecha=estacion.get("precision_fecha", "fecha_hora_publicada"),
                fecha_registro=estacion.get("fecha_registro"), nota_fecha=estacion.get("nota_fecha"),
                automatica=estacion.get("automatica"), series_id=estacion.get("series_id"),
                tabla=estacion.get("tabla"), consultado=estacion.get("consultado"),
                fecha_documento=estacion.get("fecha_documento"),
                fuente_umbrales=estacion.get("fuente_umbrales", estacion.get("fuente"))))
            motivos.append(f"{estacion_nombre}: {estacion.get('altura_m')} m; nivel {nivel}, lectura {estacion.get('fecha')}.")
        else:
            motivos.append("Lectura local vencida, futura, incompleta, sin umbrales válidos o sin fuente verificable; riesgo observado sin evaluar.")
    else:
        motivos.append(f"{tramo}: no hay estación local con altura y umbrales automatizados.")
    candidatos = [p for p in panorama.get("pronosticos", []) if key in p.get("localidades", []) or station_key in p.get("localidades", [])]
    validos = [p for p in candidatos if _vigente(p.get("fecha_emision"), ahora, p.get("hasta"))
        and _fuente_verificable(p.get("fuente"), panorama)
        and not (p.get("tipo") in ("prevision_oficial_numerica", "banda_superior_incierta")
                 and _fecha(p.get("desde")) and _fecha(p["desde"]).date() < ahora.date())]
    textos, ventanas = [], []
    firma_previsiones = []
    clase_prevista = None
    for p in validos:
        if p.get("nivel") in ("BAJO", "ALERTA", "EVACUACION"):
            result["hidrologia_pronostico_vigente"] = True
        textos.append(p.get("texto", ""))
        ventanas.append(p.get("ventana", "horizonte publicado"))
        if p.get("fuente"):
            fuentes.add(p["fuente"])
        nivel = p.get("nivel", "SIN_DATOS")
        clase = {"BAJO": 0, "ALERTA": 1, "EVACUACION": 2}.get(nivel)
        if clase is not None and (clase_prevista is None or clase > clase_prevista):
            clase_prevista = clase
            result["nivel_hidrologico_previsto"] = {0: "BAJO", 1: "ALERTA", 2: "EVACUACION"}[clase]
        desde = _fecha(p.get("desde"))
        # La evacuación prevista sólo se presenta como roja dentro de las
        # próximas 72 h; el resto mantiene prealerta y plazo exacto del autor.
        valor = {"BAJO": 0, "ALERTA": 1}.get(nivel)
        if nivel == "EVACUACION":
            valor = 2 if desde is not None and desde <= ahora + timedelta(hours=72) else 1
            if valor == 2:
                result["hidrologia_preparacion_evacuacion_72h"] = True
        if valor is not None and (previsto_rango is None or valor > previsto_rango):
            previsto_rango = valor
        if nivel in ("ALERTA", "EVACUACION"):
            motivos.append(f"Previsión oficial {nivel}, emitida {p.get('fecha_emision')} ({p.get('ventana')}): {p.get('probabilidad', 'según el autor')}." + (" Preparación anticipada; cruce previsto más allá de 72 h o sin fecha precisa." if nivel == "EVACUACION" and valor == 1 else ""))
        result["hidrologia_evidencias"].append(_evidencia(
            "pronostico_fluvial", estacion_nombre or nombre, p.get("fuente"), p["fecha_emision"], ahora, valor,
            nivel=nivel, clase=p.get("tipo"), fecha_emision=p["fecha_emision"], desde=p.get("desde"), hasta=p.get("hasta"),
            precision_fecha_emision=p.get("precision_fecha_emision", "fecha_hora_publicada"),
            precision_desde=p.get("precision_desde", "fecha_hora_publicada"),
            ventana=p.get("ventana"), probabilidad=p.get("probabilidad"), texto=p.get("texto"),
            central_m=p.get("central_m"), inferior_m=p.get("inferior_m"), superior_m=p.get("superior_m"),
            minimo_m=p.get("minimo_m"), maximo_m=p.get("maximo_m"),
            alerta_m=estacion.get("alerta_m") if estacion else None,
            evacuacion_m=estacion.get("evacuacion_m") if estacion else None,
            fuente_umbrales=p.get("fuente_umbrales") or (estacion.get("fuente_umbrales", estacion.get("fuente")) if estacion else None)))
        firma_previsiones.append((nivel, p.get("desde"), p.get("hasta")))
    if not validos:
        motivos.append("Pronóstico fluvial local vigente no disponible; no se puede afirmar ausencia de crecida futura.")
    elif previsto_rango is None:
        motivos.append("Perspectiva oficial cualitativa disponible, sin un cruce local de umbral cuantificado.")
    result["hidrologia_pronostico_texto"] = "\n\n".join(dict.fromkeys(t for t in textos if t))
    # Resumen de plazos, sin inundar la ficha con todos los puntos diarios.
    # La serie completa se conserva en estaciones.pronostico_numerico.
    resumen = []
    numericos = [p for p in validos if p.get("tipo") == "prevision_oficial_numerica"]
    for nivel in ("ALERTA", "EVACUACION"):
        grupo = [p for p in numericos if p.get("nivel") == nivel]
        if grupo:
            primero = min(grupo, key=lambda p: _fecha(p.get("desde")) or ahora)
            resumen.append(f"{nivel}: primer punto publicado {primero.get('ventana')}")
    if numericos:
        ultima = max((_fecha(p.get("desde")) for p in numericos if _fecha(p.get("desde"))), default=None)
        if ultima:
            resumen.append("Horizonte numérico hasta " + ultima.strftime("%d/%m/%Y"))
    resumen.extend(p.get("ventana", "") for p in validos if p.get("tipo") != "prevision_oficial_numerica")
    result["hidrologia_ventana"] = "; ".join(dict.fromkeys(v for v in resumen if v)) or result["hidrologia_ventana"]
    represa = panorama.get("represas", {}).get(represa_key or "")
    señal_represa = False
    if represa:
        result["represa_vigente"] = (_vigente(represa.get("fecha"), ahora, represa.get("hasta"))
                                       and _fuente_verificable(represa.get("fuente"), panorama))
        if represa.get("fuente"):
            fuentes.add(represa["fuente"])
        señal_represa = result["represa_vigente"] and bool(represa.get("señal_aguas_arriba"))
        if señal_represa:
            result["hidrologia_evidencias"].append(_evidencia(
                "represa", represa.get("nombre", represa_key), represa.get("fuente"), represa["fecha"], ahora, 1,
                fecha_emision=represa["fecha"], hasta=represa.get("hasta"), texto="Aportes/erogación elevados: vigilancia aguas abajo; sin altura local deducida"))
            motivos.append(f"{represa.get('nombre', represa_key)} publica aportes/erogación elevados: señal de vigilancia aguas abajo, sin convertir ese caudal en cota local.")
    elif result["represa_aplica"]:
        motivos.append("No hay información de operación de la represa vinculada con vigencia verificable.")
    upstream = _upstream(key, station_key, sistema, panorama, ahora)
    result["hidrologia_alertas_aguas_arriba"] = upstream
    for senal in upstream:
        result["hidrologia_evidencias"].append(_evidencia(
            "aguas_arriba", senal["estacion"], senal["fuente"], senal["fecha"], ahora, 1,
            fecha_observada=senal["fecha"], nivel=senal["nivel"], altura_m=senal.get("altura_m"),
            precision_fecha=senal.get("precision_fecha"), fecha_registro=senal.get("fecha_registro"),
            fuente_umbrales=senal.get("fuente_umbrales"),
            alerta_m=senal.get("alerta_m"), evacuacion_m=senal.get("evacuacion_m"), texto=senal["nota"]))
    if upstream:
        motivos.append("Estaciones aguas arriba en alerta o sobre umbral de evacuación: " + ", ".join(s["estacion"] for s in upstream) + ". Reforzar vigilancia; sin tiempo de llegada calculado.")
    rangos = [r for r in (observado_rango, previsto_rango) if r is not None]
    if upstream or señal_represa:
        rangos.append(1)
    result["hidrologia_rango"] = max(rangos) if rangos else None
    result["hidrologia_cobertura"] = "COMPLETA" if result["hidrologia_vigente"] and result["hidrologia_pronostico_vigente"] and (not result["represa_aplica"] or result["represa_vigente"]) else ("PARCIAL" if result["hidrologia_vigente"] or result["hidrologia_pronostico_vigente"] or señal_represa else "SIN_DATOS")
    result["hidrologia_incertidumbre"] = "No existe en este módulo una modelación calibrada de propagación ni de inundabilidad por barrio. Los pronósticos conservan la probabilidad y el horizonte publicados; los caudales de represas no se extrapolan a alturas locales."
    if key in ("resistencia", "clorinda", "gualeguaychu"):
        motivos.append(f"Referencia por tramo: {estacion_nombre}; no representa cada calle o barrio de {nombre}.")
    if result["nivel_hidrologico_observado"] == "EVACUACION":
        accion = "Umbral hidrométrico de evacuación superado: contactar de inmediato a Defensa Civil/municipio, preparar el traslado preventivo de personas, animales y bienes esenciales y seguir las indicaciones oficiales de evacuación. No circular por caminos inundados."
    elif result["hidrologia_preparacion_evacuacion_72h"]:
        accion = "Previsión oficial de posible cruce del umbral de evacuación en la ventana de preparación de 72 h (si sólo se publica el día, la hora no está determinada): preparar evacuación preventiva y coordinar con Defensa Civil/municipio puntos de encuentro, refugios y rutas transitables. Priorizar personas con movilidad reducida y traslado de hacienda antes de que se corten accesos."
    elif result["hidrologia_rango"] == 1:
        accion = "Activar preparación: seguir niveles y pronósticos oficiales, verificar accesos y lugares elevados, preparar documentación/medicación y plan de traslado de personas y animales; coordinar con autoridades locales. Revisar cada actualización y anticipar cortes antes del plazo indicado."
    else:
        accion = "Mantener seguimiento oficial. Cuando falta la lectura o el pronóstico fluvial, el riesgo por crecida queda sin evaluar; verificar con Defensa Civil/municipio antes de concluir que el lugar es seguro."
    result["hidrologia_accion"] = accion
    result["hidrologia_motivo"] = " ".join(motivos)
    result["hidrologia_fuentes"] = " | ".join(sorted(fuentes))
    # Un plazo textual puede omitir desde/hasta (None), mientras una serie
    # numérica aporta fechas ISO. Ordenar nulos primero evita comparar None
    # con str y conserva los valores y el orden de las firmas ya válidas.
    firma_previsiones.sort(key=lambda p: tuple((v is not None, v) for v in p))
    firma = [key, result["nivel_hidrologico_observado"], result["nivel_hidrologico_previsto"],
             firma_previsiones, señal_represa, sorted((s["estacion"], s["nivel"]) for s in upstream), result["hidrologia_cobertura"]]
    result["hidrologia_firma"] = sha256(json.dumps(firma, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:20]
    return result
