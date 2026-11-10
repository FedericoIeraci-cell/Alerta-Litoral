"""Vista pública mínima: sólo valores fechados, con origen y cobertura explícitos.

El color del mapa representa disponibilidad, nunca riesgo de inundación. Las
reglas experimentales archivadas no participan de esta vista ni de su descarga.
"""
from datetime import datetime, timedelta
from html import escape
import json
from math import isfinite
from zoneinfo import ZoneInfo

from explicaciones_alerta import url_oficial
from hidrologia import LOCALIDADES, normalizar
from mapa_litoral import html_mapa_litoral

ART = ZoneInfo('America/Argentina/Buenos_Aires')
ESTADOS = {'DATOS': ('#2563eb', 'Datos disponibles'),
           'SIN DATOS': ('#64748b', 'Sin datos válidos')}
MODELO = 'ECMWF IFS 0,25° (~25 km)'
METEO_URL = 'https://open-meteo.com/en/docs/ecmwf-api'
CAP_URL = 'https://www.smn.gob.ar/alertas'


def fecha(valor):
    """No inventar hora ni zona para una fecha incompleta."""
    try:
        valor = datetime.fromisoformat(valor.replace('Z', '+00:00')) if isinstance(valor, str) else valor
        return valor.astimezone(ART) if isinstance(valor, datetime) and valor.tzinfo is not None else None
    except (ValueError, TypeError):
        return None


def fechada(valor):
    dt = fecha(valor)
    return dt.strftime('%d/%m/%Y %H:%M ART') if dt else 'Sin fecha y hora confirmadas'


def reciente(valor, ahora, horas, tolerancia_segundos=0):
    dt = fecha(valor)
    return dt is not None and -timedelta(seconds=tolerancia_segundos) <= ahora - dt <= timedelta(hours=horas)


def numero(valor, minimo=0, maximo=None):
    if isinstance(valor, bool):
        return None
    try:
        n = float(valor)
        return n if isfinite(n) and n >= minimo and (maximo is None or n <= maximo) else None
    except (ValueError, TypeError, OverflowError):
        return None


def verdadero(valor):
    return valor is True or (type(valor).__name__ == 'bool' and bool(valor))


def lluvia_valida(fila, horas, ahora):
    campo = 'lluvia_futura_' + str(horas)
    valor = numero(fila.get(campo))
    inicio = fecha(fila.get('referencia_pronostico'))
    if (valor is None or fila.get('modelo_api') != 'ecmwf_ifs025'
            or not verdadero(fila.get('cobertura_' + campo))
            or not reciente(fila.get('consulta_meteorologica'), ahora, 3, 300)
            or inicio is None or not timedelta(0) <= ahora - inicio < timedelta(hours=1)):
        return None
    return {'variable': 'Lluvia prevista · ' + str(horas) + ' h', 'valor': valor, 'unidad': 'mm',
            'desde': inicio.isoformat(), 'hasta': (inicio + timedelta(hours=horas)).isoformat(),
            'consulta': fecha(fila['consulta_meteorologica']).isoformat(),
            'fuente': METEO_URL, 'modelo': MODELO, 'modelo_api': 'ecmwf_ifs025',
            'emision': None, 'tipo': 'pronostico_de_modelo'}


def lectura_valida(estacion, ahora):
    valor = numero(estacion.get('altura_m'), -10, 100)
    if (valor is None or estacion.get('precision_fecha') != 'fecha_hora_publicada'
            or not reciente(estacion.get('fecha'), ahora, 6)
            or estacion.get('error') or not url_oficial(estacion.get('fuente'))):
        return None
    # Los parsers de INA/CTM validan variable y unidad antes de crear altura_m.
    if estacion.get('unidad') != 'm':
        return None
    return {'variable': 'Altura del río · ' + str(estacion.get('nombre') or 'Estación vinculada'),
            'valor': valor, 'unidad': 'm', 'medicion': fecha(estacion['fecha']).isoformat(),
            'fuente': estacion['fuente'], 'tipo': 'observacion',
            'estacion': str(estacion.get('nombre') or '')}


def cobertura_cap(cap, ahora):
    return (not cap.get('error') and not cap.get('fallos') and not cap.get('truncado')
            and reciente(cap.get('consultado'), ahora, 1, 300)
            and reciente(cap.get('fecha_feed'), ahora, 24, 300)
            and isinstance(cap.get('total'), int) and cap.get('leidos') == cap.get('total'))


def avisos_validos(cap, localidad, ahora):
    # Un canal parcial no permite confirmar que no hubo una cancelación.
    if not cobertura_cap(cap, ahora):
        return []
    salida = []
    for aviso in cap.get('filas', []):
        emision, desde, hasta = (fecha(aviso.get(c)) for c in ('emision', 'desde', 'hasta'))
        desde = desde or emision
        if (localidad not in aviso.get('nodos', []) or aviso.get('status') != 'Actual'
                or aviso.get('tipo_mensaje') not in ('Alert', 'Update')
                or not url_oficial(aviso.get('url')) or emision is None or desde is None
                or hasta is None or desde >= hasta or emision > ahora + timedelta(minutes=5)
                or hasta <= ahora or desde > ahora + timedelta(hours=72)):
            continue
        salida.append({'titulo': str(aviso.get('titulo') or aviso.get('fenomeno') or 'Aviso SMN'),
                       'fenomeno': str(aviso.get('fenomeno') or ''),
                       'severidad_original_cap': str(aviso.get('severidad_cap') or ''),
                       'desde': desde.isoformat(), 'hasta': hasta.isoformat(),
                       'emision': emision.isoformat(), 'fuente': aviso['url'],
                       'estado': 'Vigente' if desde <= ahora else 'Previsto'})
    return salida


def construir_filas(catalogo, meteorologia, panorama, ahora):
    indices = {}
    for fila in meteorologia:
        clave = (fila.get('localidad'), fila.get('provincia'))
        indices.setdefault(clave, []).append(fila)
    estaciones = panorama.get('estaciones') or {}
    cap = panorama.get('smn') or {}
    salida = []
    for nodo in catalogo:
        candidatos = indices.get((nodo['localidad'], nodo['provincia']), [])
        modelo = candidatos[0] if len(candidatos) == 1 else {}
        valores = [v for h in (24, 72) if (v := lluvia_valida(modelo, h, ahora)) is not None]
        vinculo = LOCALIDADES.get(normalizar(nodo['localidad']))
        estacion = estaciones.get(normalizar(vinculo[1]), {}) if vinculo and vinculo[1] else {}
        rio = (lectura_valida(estacion, ahora) if vinculo and
               normalizar(estacion.get('nombre')) == normalizar(vinculo[1]) else None)
        if rio:
            valores.append(rio)
        avisos = avisos_validos(cap, nodo['localidad'], ahora)
        salida.append({**nodo, 'nivel': 'DATOS' if valores or avisos else 'SIN DATOS',
                       'valores': valores, 'avisos': avisos,
                       'rio_disponible': rio is not None,
                       'canal_smn_completo': cobertura_cap(cap, ahora)})
    return salida


def tarjeta_html(fila):
    partes = ['<h3>' + escape(fila['localidad']) + ' · ' + escape(fila.get('provincia', '')) + '</h3>']
    for valor in fila['valores']:
        cifra = f"{valor['valor']:.1f}".replace('.', ',') + ' ' + valor['unidad']
        partes.append('<p><b>' + escape(valor['variable']) + ':</b> ' + escape(cifra) + '<br>')
        if valor['tipo'] == 'pronostico_de_modelo':
            partes.append('<small>' + escape(MODELO) + '<br>Desde ' + escape(fechada(valor['desde']))
                          + '<br>Hasta ' + escape(fechada(valor['hasta']))
                          + '<br>Consulta: ' + escape(fechada(valor['consulta'])) + '</small>')
        else:
            partes.append('<small>Medición: ' + escape(fechada(valor['medicion'])) + '</small>')
        partes.append('<br><a href="' + escape(valor['fuente'], quote=True)
                      + '" target="_blank" rel="noopener noreferrer">Fuente</a></p>')
    if not fila['valores']:
        partes.append('<p>No hay valores actuales que superen los controles de validez.</p>')
    if not fila['rio_disponible']:
        partes.append('<p>Río: sin lectura actual con hora de medición confirmada.</p>')
    for aviso in fila['avisos']:
        partes.append('<p><b>SMN · ' + escape(aviso['estado']) + ':</b> ' + escape(aviso['titulo'])
                      + '<br>' + escape(fechada(aviso['desde'])) + ' a ' + escape(fechada(aviso['hasta']))
                      + '<br><a target="_blank" rel="noopener noreferrer" href="'
                      + escape(aviso['fuente'], quote=True) + '">Documento oficial</a></p>')
    partes.append('<p>Azul indica datos disponibles. No es un nivel de riesgo ni una confirmación de seguridad.</p>')
    return ''.join(partes)


def dibujar_mapa(st, componentes, catalogo, geometrias, filas=None):
    filas = filas if filas is not None else [{**n, 'nivel': 'SIN DATOS', 'valores': [], 'avisos': [],
                                             'rio_disponible': False} for n in catalogo]
    documento = html_mapa_litoral(filas, geometrias, tarjeta_nodo=tarjeta_html, altura=720,
        titulo='Datos por localidad · Litoral', leyenda='Disponibilidad de datos',
        estados=ESTADOS, capa_viirs=False)
    componentes.html(documento, height=720, scrolling=False)


def mostrar_pagina(app):
    st, componentes = app.st, app.components
    from interfaz_movil import estilos_responsive
    from mapa_inicio import mostrar_logotipo_ala
    st.html(estilos_responsive())
    mostrar_logotipo_ala(st, app.cargar_logo_ala())
    mapa = st.empty()
    with mapa.container():
        dibujar_mapa(st, componentes, app.NODOS, app.GEOMETRIAS_LITORAL)
    st.caption('Azul: datos disponibles · Gris: sin datos válidos. Los colores no representan riesgo de inundación.')
    with st.sidebar:
        st.header('A.L.A. · Datos verificables')
        actualizar = st.button('Actualizar datos', use_container_width=True)
        st.caption('Cada fuente conserva su fecha. Una consulta nueva no renueva una medición antigua.')
    if actualizar:
        st.cache_data.clear()
        st.session_state.pop('consulta_confiable', None)
    ahora = app.ahora()
    consulta = st.session_state.get('consulta_confiable')
    if not consulta or not reciente(consulta.get('fecha'), ahora, 5 / 60):
        try:
            raw, origen = app.obtener_meteorologia_publica()
            raw = raw if isinstance(raw, list) else []
            procesadas = [app.procesar_nodo(nodo, dato) for nodo, dato in zip(app.NODOS, raw)
                if isinstance(dato, dict) and all(k in dato for k in ('latitude', 'longitude', 'timezone', 'utc_offset_seconds'))
                and dato.get('timezone') in {'America/Argentina/Buenos_Aires', 'America/Buenos_Aires'}
                and dato.get('utc_offset_seconds') == -10800]
        except Exception:
            procesadas, origen = [], {}
        try:
            panorama = app.obtener_panorama_hidrologico()
        except Exception:
            panorama = {}
        consulta = {'fecha': ahora.isoformat(), 'meteorologia': procesadas,
                    'panorama': panorama, 'origen': origen}
        st.session_state['consulta_confiable'] = consulta
    ahora = app.ahora()
    panorama = consulta['panorama']
    filas = construir_filas(app.NODOS, consulta['meteorologia'], panorama, ahora)
    with mapa.container():
        dibujar_mapa(st, componentes, app.NODOS, app.GEOMETRIAS_LITORAL, filas)
    disponibles = sum(f['nivel'] == 'DATOS' for f in filas)
    st.caption(f'{disponibles}/{len(filas)} localidades con al menos un dato válido. Cada variable se comprueba por separado.')
    localidad_tab, fuentes_tab = st.tabs(['Mi localidad', 'Fuentes y límites'])
    with localidad_tab:
        from ubicacion_local import seleccionar_localidad
        nombres = sorted(f['localidad'] for f in filas)
        if nombres:
            seleccion = seleccionar_localidad(st, app='ala', nodos=app.NODOS, opciones=nombres,
                destacada='Goya' if 'Goya' in nombres else nombres[0], key='localidad_detalle')
            fila = next(f for f in filas if f['localidad'] == seleccion)
            st.subheader(fila['localidad'] + ' · ' + fila['provincia'])
            for valor in fila['valores']:
                st.metric(valor['variable'], f"{valor['valor']:.1f}".replace('.', ',') + ' ' + valor['unidad'])
                if valor['tipo'] == 'pronostico_de_modelo':
                    st.caption(MODELO + ' · Desde ' + fechada(valor['desde']) + ' hasta ' + fechada(valor['hasta']))
                    st.caption('Consulta a la fuente: ' + fechada(valor['consulta']) + ' · Emisión de la corrida: no informada por la API.')
                else:
                    st.caption('Hora de medición publicada: ' + fechada(valor['medicion']))
                st.markdown('[Fuente del dato](' + valor['fuente'] + ')')
            if not fila['valores']:
                st.info('No hay valores actuales que superen los controles de validez para esta localidad.')
            if not fila['rio_disponible']:
                st.caption('Río: se omiten alturas sin hora de medición confirmada o de más de 6 horas.')
            st.subheader('Avisos oficiales SMN')
            if fila['avisos']:
                for aviso in fila['avisos']:
                    st.warning(aviso['estado'] + ' · ' + aviso['titulo'])
                    st.caption('Emisión: ' + fechada(aviso['emision']) + ' · Desde ' + fechada(aviso['desde']) + ' hasta ' + fechada(aviso['hasta']))
                    st.markdown('[Leer documento oficial](' + aviso['fuente'] + ')')
            elif fila['canal_smn_completo']:
                st.info('No se encontraron documentos CAP aplicables a este punto en el canal consultado. Esto no descarta otros peligros.')
            else:
                st.warning('No se pudo confirmar el canal completo de avisos SMN. No se interpreta como ausencia de alertas.')
            st.markdown('[Consultar alertas del SMN](' + CAP_URL + ')')
            st.caption('La lluvia es una estimación de grilla, no una medición en tu lote. La altura del río corresponde a su estación, no a una calle. Las decisiones de emergencia corresponden a las autoridades locales.')
    with fuentes_tab:
        st.subheader('Controles aplicados')
        st.markdown('Se publican únicamente valores finitos, con unidad y fuente identificadas. La lluvia requiere todas las horas de cada ventana y una consulta de hasta 3 horas. La lectura de río requiere hora de medición publicada y hasta 6 horas de antigüedad. Son límites de uso de A.L.A., no garantías del organismo.')
        st.caption('Meteorología consultada: ' + fechada(consulta.get('origen', {}).get('consultado')))
        cap = panorama.get('smn') or {}
        st.caption('Canal SMN consultado: ' + fechada(cap.get('consultado')) + ' · Fecha del canal: ' + fechada(cap.get('fecha_feed')))
        st.write('Canal SMN: ' + ('completo en la consulta comprobada' if cobertura_cap(cap, ahora) else 'sin cobertura completa confirmada'))
        try:
            control = app.obtener_estado_monitor_publico()
        except Exception:
            control = {}
        st.caption('Último control automático registrado: ' + fechada(control.get('checked_at')))
        if not reciente(control.get('checked_at'), ahora, 3, 300):
            st.warning('La vigilancia automática no tiene un control reciente confirmado. La actualización de esta pantalla no acredita vigilancia continua.')
        st.caption('La pantalla abierta vuelve a consultar cada 5 minutos. La fecha de cada dato se conserva y se comprueba nuevamente.')
        st.markdown('[Modelo y variables · Open-Meteo](' + METEO_URL + ') · [Ríos · INA](https://alerta.ina.gob.ar/) · [Avisos · SMN](' + CAP_URL + ')')
        st.subheader('Alcance')
        st.write('Se retiraron de la vista pública el índice experimental de inundación y sus acciones por color, las cargas manuales, los recursos y recorridos sin confirmación actual, las copias vencidas y las capas complementarias. El mapa muestra disponibilidad de información; no calcula una probabilidad de inundación ni acredita zonas seguras.')
        st.caption('Los datos faltantes se omiten; nunca se completan con cero. Un valor de 0,0 mm sólo se publica cuando la ventana completa contiene ceros válidos.')
        st.download_button('Descargar datos válidos y sus fuentes', json.dumps({'version': app.VERSION,
            'comprobado': ahora.isoformat(), 'localidades': filas}, ensure_ascii=False, allow_nan=False, indent=2),
            'ala_datos_validos.json', 'application/json')
    st.caption('Alerta Litoral Agro · ' + app.VERSION + ' · Poco, preciso y verificable · Revisión de pantalla: ' + fechada(ahora))
