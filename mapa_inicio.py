"""Primera vista de nodos, disponible antes de completar las consultas.

No consulta servicios ni calcula niveles. El catálogo fija la ubicación de cada
referencia; una fila ausente permanece gris y no recibe valores inventados.
"""
from html import escape
from datetime import datetime, timedelta

from mapa_litoral import html_mapa_litoral


def crear_tarjeta_mapa(tarjeta_nodo=None, referencia=None, panorama=None):
    """Añade cifras fechadas de ALA al detalle existente, sin reclasificar nodos.

    Reutiliza la portada para las ventanas independientes de lluvia y para la
    precisión de las fechas. Un antecedente fluvial se identifica como tal y
    nunca se convierte en lectura actual por volver a abrir el mapa.
    """
    from portada_local import (
        ART, _fecha, _numero, _si, construir_portada_local, fecha_publica,
        normalizar, precision_observada, url_oficial,
    )
    contexto = panorama if isinstance(panorama, dict) else {}
    ref = _fecha(referencia) or datetime.now(ART)

    def tarjeta(row):
        original = dict(row)
        lectura = {}
        evidencias = original.get('evidencias_alerta')
        evidencias = evidencias if isinstance(evidencias, (list, tuple)) else []
        for evidencia in evidencias:
            if (isinstance(evidencia, dict) and evidencia.get('tipo') == 'observacion_fluvial'
                    and evidencia.get('verificada') is True and url_oficial(evidencia.get('fuente'))):
                lectura = evidencia
                break
        estaciones = contexto.get('estaciones')
        estaciones = estaciones if isinstance(estaciones, dict) else {}
        estacion = estaciones.get(normalizar(original.get('hidrologia_estacion', '')), {})
        if not lectura and isinstance(estacion, dict) and url_oficial(estacion.get('fuente')):
            lectura = {'altura_m': estacion.get('altura_m'), 'fecha_observada': estacion.get('fecha'),
                       'fuente': estacion.get('fuente'), 'estacion': estacion.get('nombre')}
        fecha_lectura = lectura.get('fecha_observada') or original.get('hidrologia_fecha')
        dt_lectura = _fecha(fecha_lectura)
        temporal = (dt_lectura is not None and
                    -timedelta(minutes=5) <= ref - dt_lectura <= timedelta(hours=48))
        fila = dict(original)
        if not temporal:
            fila['hidrologia_vigente'] = False
        resumen = construir_portada_local(fila, contexto, ref)
        cifras = {c['id']: c for c in resumen['cifras']}
        partes = []
        for campo in ('lluvia24', 'lluvia72'):
            cifra = cifras[campo]
            partes.append('<b>' + escape(cifra['titulo']) + ':</b> ' + escape(cifra['valor'])
                          + '<br><small>' + escape(cifra['fecha']) + '</small>')
        cifra_rio = cifras['rio']
        altura = _numero(lectura.get('altura_m'))
        precision = precision_observada(original, contexto)
        hora_verificada = (precision == 'fecha_hora_publicada' and
                           not (isinstance(fecha_lectura, str) and len(fecha_lectura.strip()) == 10))
        actual = (_si(fila.get('hidrologia_vigente')) and temporal and hora_verificada
                  and cifra_rio['valor'].endswith(' m'))
        if actual:
            titulo_rio = cifra_rio['titulo']
            texto_rio = cifra_rio['valor']
            fecha_rio = cifra_rio['fecha']
        elif altura is not None and dt_lectura is not None and dt_lectura <= ref + timedelta(minutes=5):
            nombre_estacion = original.get('hidrologia_estacion') or lectura.get('estacion') or 'estación local'
            titulo_rio = 'Río · antecedente de ' + str(nombre_estacion)
            texto_rio = f'{altura:.2f}'.replace('.', ',') + ' m · sin vigencia actual confirmada'
            fecha_rio = 'Registro publicado: ' + fecha_publica(fecha_lectura, precision)
        else:
            titulo_rio = cifra_rio['titulo']
            texto_rio = cifra_rio['valor']
            fecha_rio = cifra_rio['fecha']
        partes.append('<b>' + escape(titulo_rio) + ':</b> ' + escape(texto_rio)
                      + '<br><small>' + escape(fecha_rio) + '</small>')
        datos = '<br>'.join(partes)
        detalle = (str(tarjeta_nodo(dict(original))) if tarjeta_nodo is not None else
                   '<b>' + escape(str(original.get('localidad') or 'Referencia')) + '</b>')
        # El nombre sigue primero; las cifras preceden al motivo y las acciones.
        cabecera, separador, resto = detalle.partition('<br>')
        if separador:
            return cabecera + '<br>' + datos + '<hr>' + resto
        return detalle + '<br>' + datos

    return tarjeta


def completar_catalogo_mapa(catalogo, frame=None):
    """Conserva una fila por referencia y no modifica el catálogo ni el resultado."""
    filas = frame.to_dict('records') if hasattr(frame, 'to_dict') else list(frame or [])
    indice = {}
    for fila in filas:
        if isinstance(fila, dict):
            identidad = (str(fila.get('localidad') or ''), str(fila.get('provincia') or ''))
            indice.setdefault(identidad, []).append(fila)
    salida = []
    for nodo in catalogo:
        identidad = (str(nodo.get('localidad') or ''), str(nodo.get('provincia') or ''))
        candidatas = indice.get(identidad, [])
        if len(candidatas) == 1:
            fila = {**nodo, **candidatas[0]}
            # Una respuesta meteorológica no desplaza el punto del catálogo.
            fila['lat'], fila['lon'] = nodo.get('lat'), nodo.get('lon')
        else:
            fila = {**nodo, 'nivel': 'SIN DATOS', 'indice': None,
                    'mapa_sin_evaluacion': True,
                    'error_datos': ('La consulta devolvió referencias repetidas; requiere verificación.'
                                    if candidatas else 'No se obtuvo una evaluación de este punto.')}
        salida.append(fila)
    return salida


def mostrar_mapa_principal(st, catalogo, frame=None, *, componentes=None,
                          geometrias=None, tarjeta_nodo=None, altura=440,
                          nombre='Mapa de riesgo del Litoral', ajustar_a='geometrias',
                          leyenda='Evaluación integrada', consultando=None):
    """Dibuja el mapa antes de sus textos; admite un ``st.empty().container()``.

    El primer dibujo usa ``frame=None``. El mismo contenedor puede reemplazarse
    después con las filas verificadas, sin esperar a radar, boletines o fichas.
    ``consultando`` sólo describe la carga de la página: no renueva ninguna fecha.
    """
    if componentes is None:
        import streamlit.components.v1 as componentes
    if consultando is None:
        consultando = frame is None
    filas = completar_catalogo_mapa(catalogo, frame)

    def tarjeta(fila):
        if fila.get('mapa_sin_evaluacion'):
            estado = 'Consultando fuentes' if consultando else 'Información insuficiente'
            return ('<b>' + escape(str(fila.get('localidad') or 'Referencia'))
                    + '</b><br>' + estado + '<br>' + escape(str(fila.get('error_datos') or '')))
        if tarjeta_nodo is not None:
            return tarjeta_nodo(fila)
        return ('<b>' + escape(str(fila.get('localidad') or 'Referencia'))
                + '</b><br>' + escape(str(fila.get('nivel') or 'SIN DATOS')))

    documento = html_mapa_litoral(filas, geometrias or {}, tarjeta_nodo=tarjeta,
                                 altura=altura, ajustar_a=ajustar_a, titulo=nombre,
                                 leyenda=leyenda)
    componentes.html(documento, height=altura, scrolling=False)
    if consultando:
        st.caption('Consultando las fuentes. Los puntos grises todavía no tienen una evaluación disponible.')
    else:
        conteo = {nivel: sum(str(f.get('nivel') or 'SIN DATOS') == nivel for f in filas)
                  for nivel in ('ROJO', 'AMARILLO', 'VERDE', 'SIN DATOS')}
        st.markdown('**🔴 Atención prioritaria: ' + str(conteo['ROJO'])
                    + '** · **🟡 Prepararse: ' + str(conteo['AMARILLO'])
                    + '** · 🟢 Seguimiento: ' + str(conteo['VERDE'])
                    + ' · ⚪ Información insuficiente: ' + str(conteo['SIN DATOS']))
        con_lluvia = sum(f.get('cobertura_lluvia_futura_24') is True or f.get('cobertura_lluvia_futura_72') is True for f in filas)
        st.caption('Lluvia prevista disponible en ' + str(con_lluvia) + '/' + str(len(filas))
                   + ' puntos. Tocá un nodo para ver sus valores y fechas. Un índice pendiente no elimina los datos de lluvia disponibles.')
        st.caption('Gris significa que falta información para completar la evaluación. El color es experimental; los avisos oficiales y las condiciones locales se revisan debajo.')
    return filas
