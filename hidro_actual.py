"""Últimas lecturas oficiales, consultadas sin meteorología, secretos ni envíos.

La consulta no renueva la fecha de la medición. Las escalas se conservan por
estación y este módulo no calcula alertas ni traslada umbrales entre escalas.
"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
import json
import math
import re
import threading
import time
import unicodedata
from urllib.parse import urlencode, urlsplit
from zoneinfo import ZoneInfo

ART = ZoneInfo('America/Argentina/Buenos_Aires')
CTM_HORARIOS = 'https://www.saltogrande.org/datos_horarios.php'
INA_WFS = 'https://alerta.ina.gob.ar/geoserver/ows'
CACHE_SEGUNDOS = 300
MAX_BYTES = 2_000_000
# Identidades del catálogo INA de alturas de Prefectura, variable H en metros.
ESTACIONES = {8:'Andresito',9:'Puerto Iguazú',15:'Ituzaingó',19:'Corrientes',20:'Barranqueras',
    23:'Goya',24:'Reconquista',26:'La Paz',29:'Paraná',30:'Santa Fe',32:'Victoria',34:'Rosario',
    40:'Zárate',41:'Campana',52:'San Fernando',55:'Puerto Pilcomayo',57:'Puerto Formosa',
    65:'San Javier',68:'Santo Tomé',72:'Paso de los Libres',79:'Concordia',80:'Colón',
    81:'Concepción del Uruguay',84:'Boca Gualeguaychú'}
_CACHE = {}
_LOCKS = {key: threading.Lock() for key in ('rios','salto','zarate')}


def _fecha(value):
    try:
        dt = datetime.fromisoformat(value.replace('Z','+00:00')) if isinstance(value,str) else value
        return dt.astimezone(timezone.utc) if isinstance(dt,datetime) and dt.tzinfo is not None else None
    except ValueError:
        return None


def _ahora(value=None):
    dt = datetime.now(timezone.utc) if value is None else _fecha(value)
    if dt is None:
        raise ValueError('La referencia requiere zona horaria.')
    return dt


def _normal(value):
    return ''.join(c for c in unicodedata.normalize('NFKD',str(value)) if not unicodedata.combining(c)).casefold().strip()


def _numero(value, minimo=-10, maximo=100):
    if isinstance(value,bool):
        return None
    try:
        n = float(value)
        return n if math.isfinite(n) and minimo <= n <= maximo else None
    except (ValueError, TypeError, OverflowError):
        return None


def estado_lectura(item, referencia=None):
    out = deepcopy(item)
    ref, fecha = _ahora(referencia), _fecha(out.get('fecha'))
    edad = (ref-fecha).total_seconds()/3600 if fecha else None
    limite = 2 if out.get('tipo') == 'observacion_horaria_ctm' else 6
    estado = ('SIN FECHA VERIFICADA' if fecha is None else 'FECHA FUTURA' if edad < 0
              else 'LECTURA ATRASADA' if edad > limite else 'LECTURA RECIENTE')
    if out.get('error'):
        estado = 'CONSULTA FALLIDA · última lectura conservada' if fecha else 'SIN DATOS · consulta fallida'
    out.update(edad_horas=edad, estado=estado, vigente=estado == 'LECTURA RECIENTE', limite_horas=limite)
    return out


def _descargar(url, session=None):
    import requests
    parts = urlsplit(url)
    if (parts.scheme != 'https' or parts.netloc not in ('www.saltogrande.org','alerta.ina.gob.ar')
            or parts.path not in ('/datos_horarios.php','/geoserver/ows')):
        raise ValueError('Origen no autorizado.')
    response = (session or requests).get(url, timeout=(5,15), allow_redirects=False,
        stream=True, headers={'User-Agent':'Mozilla/5.0 (ALA-ZC hidrologia)', 'Cache-Control':'no-cache'})
    try:
        if response.status_code != 200:
            raise ValueError('Respuesta HTTP no utilizable.')
        data = bytearray()
        for chunk in response.iter_content(chunk_size=65536):
            data.extend(chunk)
            if len(data)>MAX_BYTES:
                raise ValueError('La respuesta supera el límite de tamaño.')
        return bytes(data)
    finally:
        response.close()


class _Tabla(HTMLParser):
    """Extrae sólo el encabezado fechado y las celdas del cuadro operativo."""
    def __init__(self, contenido):
        super().__init__(convert_charrefs=True)
        self.encabezados, self.filas = [], []
        self.h3, self.celda, self.fila = None, None, None
        self.feed(contenido)

    def handle_starttag(self, tag, attrs):
        if tag == 'h3': self.h3 = []
        if tag == 'tr': self.fila = []
        if tag == 'td': self.celda = []

    def handle_data(self, data):
        if self.h3 is not None: self.h3.append(data)
        if self.celda is not None: self.celda.append(data)

    def handle_endtag(self, tag):
        if tag == 'h3' and self.h3 is not None:
            self.encabezados.append(' '.join(self.h3)); self.h3 = None
        if tag == 'td' and self.celda is not None:
            if self.fila is not None: self.fila.append(''.join(self.celda).strip())
            self.celda = None
        if tag == 'tr' and self.fila is not None:
            self.filas.append(self.fila); self.fila = None


def parsear_horarios(contenido, referencia=None):
    tabla = _Tabla(contenido)
    fechas = [re.fullmatch(r'\s*Fecha:\s*(\d{2})/(\d{2})/(\d{4})\s+Hora:\s*(\d{2}):(\d{2})\s*', x) for x in tabla.encabezados]
    fechas = [f for f in fechas if f]
    if len(fechas) != 1:
        raise ValueError('Falta una única fecha y hora del cuadro operativo CTM.')
    d,m,y,h,mi = map(int,fechas[0].groups())
    fecha = datetime(y,m,d,h,mi,tzinfo=ART)
    campos = {'caudal turbinado':('turbinas_m3s','m3/s',200000),
              'caudal vertido':('vertedero_m3s','m3/s',200000),
              'cota del embalse (promedio)':('cota_embalse_m','m',100),
              'cota de restitucion (promedio)':('cota_restitucion_m','m',100)}
    valores = {}
    for fila in tabla.filas:
        if len(fila)!=2 or _normal(fila[0]) not in campos:
            continue
        key,unidad,limite = campos[_normal(fila[0])]
        match = re.fullmatch(r'([\d.,]+)\s*'+re.escape(unidad), fila[1].replace('³','3'))
        if not match or key in valores:
            raise ValueError('Magnitud CTM repetida o con unidad no verificada.')
        numero = match[1].replace('.','').replace(',','.')
        value = _numero(numero,0,limite)
        if value is None: raise ValueError('Magnitud CTM fuera de rango.')
        valores[key] = value
    if not {'turbinas_m3s','vertedero_m3s','cota_embalse_m'} <= valores.keys():
        raise ValueError('Cuadro horario CTM incompleto.')
    return estado_lectura({'nombre':'Salto Grande', 'tipo':'observacion_horaria_ctm',
        'fecha':fecha.isoformat(), 'consulta':_ahora(referencia).isoformat(), 'fuente':CTM_HORARIOS,
        'precision_fecha':'fecha_hora_publicada', 'erogado_m3s':valores['turbinas_m3s']+valores['vertedero_m3s'],
        'hasta':(fecha+timedelta(hours=2)).isoformat(), 'error':'', **valores},referencia)


def consultar_salto_grande(referencia=None, session=None):
    return parsear_horarios(_descargar(CTM_HORARIOS,session).decode('utf-8'),referencia)


def url_rios():
    return INA_WFS+'?'+urlencode({'service':'WFS','version':'1.0.0','request':'GetFeature',
        'typeName':'public2:ultimas_alturas_con_timeseries','outputFormat':'application/json',
        'CQL_FILTER':'series_id IN ('+','.join(map(str,ESTACIONES))+')',
        'propertyName':'series_id,nombre,var_id,tabla,fecha,valor,timeseries,nivel_de_alerta,nivel_de_evacuacion'})


def parsear_rios(payload, referencia=None):
    ref = _ahora(referencia)
    if not isinstance(payload,dict) or payload.get('type')!='FeatureCollection' or not isinstance(payload.get('features'),list):
        raise ValueError('Respuesta INA incompatible.')
    salida = []
    for sid,nombre in ESTACIONES.items():
        item = {'nombre':nombre,'serie':sid,'fecha':None,'altura_m':None,'fuente':url_rios(),
                'tipo':'lectura_ina','consulta':ref.isoformat(),'error':'Serie ausente o identidad no verificada.',
                'precision_fecha':'fecha_registro_fuente', 'automatica':None,
                'nota_fecha':'Fecha y hora del registro INA; la hora de medición no está confirmada.'}
        filas = [f.get('properties',{}) for f in payload['features'] if isinstance(f,dict)
                 and isinstance(f.get('properties'),dict) and f['properties'].get('series_id')==sid]
        if (len(filas)==1 and filas[0].get('tabla')=='alturas_prefe' and filas[0].get('var_id')==2
                and _normal(filas[0].get('nombre'))==_normal(nombre)):
            p = filas[0]
            # Se conserva la fecha registrada por INA; puede corresponder a un dato diario.
            fecha,valor = _fecha(p.get('fecha')),_numero(p.get('valor'))
            if fecha and fecha<=ref and valor is not None:
                alerta,evacuacion = _numero(p.get('nivel_de_alerta')),_numero(p.get('nivel_de_evacuacion'))
                umbral_invalido = (any(p.get(c) is not None and _numero(p[c]) is None
                    for c in ('nivel_de_alerta','nivel_de_evacuacion'))
                    or (alerta is not None and evacuacion is not None and evacuacion<alerta))
                item.update(fecha=fecha.isoformat(),fecha_registro=p['fecha'],altura_m=valor,error='',
                    tabla='alturas_prefe',var_id=2,unidad='m',alerta_m=alerta,evacuacion_m=evacuacion,
                    umbral_invalido=umbral_invalido,fuente_umbrales=url_rios())
        salida.append(estado_lectura(item,ref))
    return salida


def consultar_rios(referencia=None, session=None):
    """Alturas y umbrales de la misma escala INA; no consulta la serie RHN."""
    return parsear_rios(json.loads(_descargar(url_rios(),session)),referencia)


def _consulta(nombre, referencia):
    if nombre=='salto': return [consultar_salto_grande(referencia)]
    if nombre=='rios': return consultar_rios(referencia)
    from hidrometria_local import consultar_altura_zarate
    dato = consultar_altura_zarate(referencia)
    return [{'nombre':'Zárate · automática RHN', 'serie':29437,'tipo':'automatica_rhn',
             'fecha':dato.get('fecha_observada'),'altura_m':dato.get('altura_m'),
             'consulta':dato.get('consulta'),'fuente':dato.get('fuente'),'error':dato.get('error','')}]


def _con_cache(nombre, referencia):
    with _LOCKS[nombre]:
        anterior = _CACHE.get(nombre)
        if anterior and time.monotonic()-anterior['tick']<CACHE_SEGUNDOS:
            return deepcopy(anterior['datos'])
        try:
            datos = _consulta(nombre,referencia)
        except Exception as exc:
            datos = deepcopy(anterior['datos']) if anterior else [{'nombre':{'salto':'Salto Grande','rios':'Ríos INA','zarate':'Zárate · automática RHN'}[nombre]}]
            for dato in datos:
                dato.update(error='Consulta oficial no confirmada ('+type(exc).__name__+').',
                            consulta=referencia.isoformat())
        if anterior:
            previos = {d.get('nombre'):d for d in anterior['datos']}
            for i,dato in enumerate(datos):
                previo = previos.get(dato.get('nombre'),{})
                antes, nuevo = _fecha(previo.get('fecha')), _fecha(dato.get('fecha'))
                if antes and (dato.get('error') or (nuevo and nuevo<antes)):
                    datos[i] = {**previo, 'consulta':dato.get('consulta'),
                        'error':dato.get('error') or 'La fuente devolvió una lectura anterior; se conserva la última verificada.'}
        _CACHE[nombre] = {'tick':time.monotonic(),'datos':deepcopy(datos)}
        return datos


def consultar_hidrologia_actual(referencia=None):
    ref = _ahora(referencia)
    with ThreadPoolExecutor(max_workers=3) as pool:
        grupos = list(pool.map(lambda key:_con_cache(key,ref),('rios','salto','zarate')))
    # Revalidar incluso si la respuesta se recuperó de caché.
    return [estado_lectura(item,ref) for grupo in grupos for item in grupo]
