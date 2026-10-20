"""Explicación única del semáforo para panel, mapa, registro y Telegram.

No consulta servicios ni envía mensajes. Las evidencias provienen de los mismos
adaptadores y reglas que evaluaron el nivel; un enlace de contexto no es causa.
"""
from datetime import datetime
from html import escape
import math
import textwrap
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

TZ = ZoneInfo('America/Argentina/Buenos_Aires')
RANGOS = {'VERDE': 0, 'AMARILLO': 1, 'ROJO': 2}
COLORES = {'VERDE': '#15803d', 'AMARILLO': '#a16207', 'ROJO': '#be123c', 'SIN DATOS': '#64748b'}


def numero(valor, decimales=2):
    try:
        n = float(valor)
        return f'{n:.{decimales}f}'.replace('.', ',') if math.isfinite(n) else 'Sin dato'
    except (ValueError, TypeError):
        return 'Sin dato'


def fecha(valor, precision='fecha_hora_publicada'):
    try:
        dt = datetime.fromisoformat(str(valor).replace('Z', '+00:00'))
        if dt.tzinfo is None:
            return dt.strftime('%d/%m/%Y') + ' · hora no publicada'
        dt = dt.astimezone(TZ)
        if precision == 'fecha_registro_fuente':
            return dt.strftime('%d/%m/%Y') + ' · hora de medición no confirmada'
        return (dt.strftime('%d/%m/%Y') + ' · hora no publicada' if precision in ('fecha_sin_hora', 'hora_no_verificada_en_reporte_diario')
                else dt.strftime('%d/%m/%Y %H:%M') + ' ART')
    except (ValueError, TypeError):
        return 'Sin fecha verificada'


def url_oficial(valor):
    if not isinstance(valor, str):
        return ''
    try:
        u = urlparse(valor)
        if (u.scheme == 'https' and not u.username and not u.password and
                u.hostname in {'alerta.ina.gob.ar', 'www.eby.gov.py', 'eby.gov.py',
                               'www.saltogrande.org', 'saltogrande.org',
                               'www.smn.gob.ar', 'smn.gob.ar', 'ws.smn.gob.ar', 'ssl.smn.gob.ar',
                               'ina.gob.ar', 'www.ina.gob.ar', 'www.ina.gov.ar', 'ina.gov.ar',
                               'www.eby.org.ar', 'eby.org.ar'}):
            return valor
    except ValueError:
        pass
    return ''


def evidencia_util(e):
    return (isinstance(e, dict) and e.get('verificada') is True and e.get('vigente') is True
            and (e.get('tipo') in ('meteorologia', 'territorial') or bool(url_oficial(e.get('fuente')))))


def resumen_evidencia(e):
    tipo, estacion = e.get('tipo'), e.get('estacion', 'Estación no identificada')
    if tipo == 'meteorologia':
        return (f"Índice meteorológico {numero(e.get('indice'), 1)}/100: {e.get('nivel')}; "
                f"umbrales experimentales amarillo ≥ {numero(e.get('amarillo'), 0)}, rojo ≥ {numero(e.get('rojo'), 0)}.")
    if tipo == 'territorial':
        return 'Evidencia territorial de esta sesión: ' + str(e.get('texto', ''))
    if tipo in ('observacion_fluvial', 'aguas_arriba'):
        umbral = e.get('evacuacion_m') if e.get('nivel') == 'EVACUACION' else e.get('alerta_m')
        condicion = 'supera o alcanza' if e.get('nivel') in ('EVACUACION', 'ALERTA') else 'por debajo de'
        prefijo = 'Señal aguas arriba en ' if tipo == 'aguas_arriba' else 'Río observado en '
        return (f"{prefijo}{estacion}: {numero(e.get('altura_m'))} m, {condicion} "
                f"el umbral de {'evacuación' if e.get('nivel') == 'EVACUACION' else 'alerta'} "
                f"({numero(umbral)} m). Lectura {fecha(e.get('fecha_observada'), e.get('precision_fecha'))}."
                + (' No es la altura local.' if tipo == 'aguas_arriba' else ''))
    if tipo == 'pronostico_fluvial':
        nivel = 'evacuación' if e.get('nivel') == 'EVACUACION' else 'alerta' if e.get('nivel') == 'ALERTA' else 'referencia'
        valor = e.get('central_m')
        if valor is not None:
            detalle = f"escenario central {numero(valor)} m; bandas {numero(e.get('inferior_m'))}–{numero(e.get('superior_m'))} m"
        elif e.get('minimo_m') is not None or e.get('maximo_m') is not None:
            detalle = f"rango publicado {numero(e.get('minimo_m'))}–{numero(e.get('maximo_m'))} m"
        else:
            detalle = 'previsión textual, sin altura numérica de cruce publicada'
        umbral = e.get('evacuacion_m') if e.get('nivel') == 'EVACUACION' else e.get('alerta_m')
        umbral_txt = f" ({numero(umbral)} m)" if umbral is not None and url_oficial(e.get('fuente_umbrales')) else ''
        cruce = ('posible superación del umbral de ' + nivel + umbral_txt if e.get('nivel') in ('ALERTA', 'EVACUACION')
                 else 'perspectiva ' + str(e.get('nivel', 'sin clasificar')))
        ventana = fecha(e.get('desde'), e.get('precision_desde')) if e.get('desde') else str(e.get('ventana') or 'sin fecha de cruce')
        preparacion = (' Entra en la ventana de preparación de 72 h según la fecha publicada.' if e.get('rango') == 2
                       else ' Fuera de la ventana roja de 72 h o sin fecha precisa.' if e.get('nivel') == 'EVACUACION' else '')
        enlace_temporal = 'para el punto publicado' if e.get('clase') in ('prevision_oficial_numerica','banda_superior_incierta') else 'a partir de'
        return f'Previsión fluvial en {estacion}: {cruce} {enlace_temporal} {ventana}; {detalle}.{preparacion}'
    if tipo == 'smn':
        return (f"Aviso SMN {e.get('fenomeno')} · {e.get('severidad')}; incluye este nodo. "
                f"Desde {fecha(e.get('desde'))} hasta {fecha(e.get('hasta'))}.")
    if tipo == 'represa':
        return f"{estacion}: {e.get('texto')}."
    return 'Evidencia sin descripción verificable.'


def construir_explicacion(row, fusion, amarillo, rojo):
    evidencias = list(row.get('hidrologia_evidencias') or []) + list(row.get('smn_evidencias') or [])
    met = row.get('nivel_meteorologico', 'SIN DATOS')
    if row.get('cobertura_meteorologica') and met in RANGOS:
        evidencias.append(dict(tipo='meteorologia', rango=RANGOS[met], nivel=met,
                               indice=row.get('indice'), amarillo=amarillo, rojo=rojo,
                               verificada=True, vigente=True,
                               fecha_consulta=row.get('consulta_meteorologica') or row.get('actualizado'),
                               fuente='https://open-meteo.com/en/docs/ecmwf-api'))
    if RANGOS.get(fusion.get('semaforo'), -1) > RANGOS.get(met, -1):
        evidencias.append(dict(tipo='territorial', rango=RANGOS[fusion['semaforo']],
                               texto='; '.join(fusion['motivos'][1:]), verificada=True, vigente=True))
    rango = RANGOS.get(row.get('nivel'))
    causas = [e for e in evidencias if evidencia_util(e) and e.get('rango') == rango and rango is not None]
    orden = {'observacion_fluvial': 0, 'pronostico_fluvial': 1, 'smn': 2, 'territorial': 3, 'meteorologia': 4,
             'aguas_arriba': 5, 'represa': 6}
    causas.sort(key=lambda e: (orden.get(e['tipo'], 9), -{'EVACUACION':2,'ALERTA':1}.get(e.get('nivel'),0), str(e.get('desde') or '')))
    if causas:
        motivo = resumen_evidencia(causas[0])
    elif row.get('nivel') == 'SIN DATOS':
        motivo = 'Cobertura insuficiente para clasificar el riesgo integrado; los faltantes no acreditan seguridad.'
    else:
        motivo = 'Desencadenante no verificable en este registro; revisar la evaluación y sus fuentes.'
    return dict(explicacion_version=1, evidencias_alerta=evidencias, desencadenantes=causas,
                motivo_principal=motivo, umbral_meteorologico_amarillo=amarillo, umbral_meteorologico_rojo=rojo)


def causas_verificadas(row):
    return [e for e in (row.get('desencadenantes') or []) if evidencia_util(e)
            and e.get('rango') == RANGOS.get(row.get('nivel'))]


def motivo_publico(row):
    causas = causas_verificadas(row)
    if causas:
        return resumen_evidencia(causas[0])
    if row.get('nivel') == 'SIN DATOS':
        return 'Cobertura insuficiente; no se acredita ausencia de riesgo.'
    return 'Desencadenante no verificable en este registro; revisar fuentes y evaluación.'


def motivo_transicion(row, anterior_nivel, anterior_indice, referencia=None, contexto=''):
    causas = causas_verificadas(row)
    partes = [contexto or f"Riesgo integrado: {anterior_nivel} → {row.get('nivel')}."]
    if causas:
        partes.extend(resumen_evidencia(e) for e in causas[:2])
        previo = referencia or {}
        if (previo.get('previsto') == 'EVACUACION' and previo.get('rango') == 1
                and any(e['tipo'] == 'pronostico_fluvial' and e.get('rango') == 2 for e in causas)):
            partes.append('La previsión antes en prealerta ahora está dentro de la ventana de 72 h; esto no demuestra una subida observada del río.')
    else:
        partes.append(motivo_publico(row))
    partes.append(f"Índice meteorológico: {numero(anterior_indice, 1)} → {numero(row.get('indice'), 1)}/100; se evalúa por separado.")
    return ' '.join(partes)


def fuentes_evidencia(e):
    result = []
    for campo, titulo in [('fuente', 'Documento de la evidencia'), ('fuente_umbrales', 'Umbrales de la estación')]:
        url = url_oficial(e.get(campo))
        if e.get('tipo') == 'meteorologia' and campo == 'fuente':
            url = 'https://open-meteo.com/en/docs/ecmwf-api'
        if url and url not in [u for _, u in result]:
            host = urlparse(url).hostname or ''
            organismo = 'INA' if 'ina.' in host else 'CTM' if 'saltogrande' in host else 'SMN' if 'smn.' in host else 'EBY' if 'eby.' in host else 'Open-Meteo'
            result.append((titulo + ' · ' + organismo, url))
    return result


def detalle_evidencia(e):
    partes = [resumen_evidencia(e)]
    if e.get('fecha_emision'):
        partes.append('Emisión: ' + fecha(e['fecha_emision'], e.get('precision_fecha_emision')))
    if e.get('hasta'):
        partes.append('Fin del horizonte interpretado: ' + fecha(e['hasta'], e.get('precision_desde')))
    if e.get('valida_hasta'):
        partes.append('Límite de uso ALA: ' + fecha(e['valida_hasta']) + ' (se reevalúa en cada control)')
    if e.get('criterio_vigencia'):
        partes.append(str(e['criterio_vigencia']))
    if e.get('probabilidad'):
        partes.append('Incertidumbre: ' + str(e['probabilidad']))
    return ' '.join(partes)


def tarjeta_nodo_html(row):
    """Detalle persistente para mouse, teclado y selección en pantallas táctiles."""
    nivel = str(row.get('nivel', 'SIN DATOS'))
    color = COLORES.get(nivel, COLORES['SIN DATOS'])
    causas = causas_verificadas(row)
    detalles = []
    for e in causas:
        links = ' · '.join(f'<a href="{escape(u, quote=True)}" target="_blank" rel="noopener noreferrer">{escape(t)}</a>'
                           for t, u in fuentes_evidencia(e))
        detalles.append(f'<li><p>{escape(detalle_evidencia(e))}</p>{links}</li>')
    obs = next((e for e in (row.get('evidencias_alerta') or []) if e.get('tipo') == 'observacion_fluvial' and evidencia_util(e)), None)
    observacion = f'<p class="ala-evi-observacion">{escape(resumen_evidencia(obs))}</p>' if obs else '<p class="ala-evi-observacion">Lectura fluvial local: sin evidencia vigente verificable.</p>'
    return f'''<style>
.ala-evi{{background:#fff;color:#17324d;border:1px solid #dbe4ee;border-left:5px solid {color};border-radius:14px;padding:20px;margin:12px 0;font-family:system-ui,sans-serif;box-shadow:0 4px 16px #1834480d}}
.ala-evi h3{{margin:0 0 8px;font-size:21px}}.ala-evi p{{margin:10px 0;line-height:1.5;overflow-wrap:anywhere}}.ala-evi .ala-evi-meta{{display:flex;gap:8px;flex-wrap:wrap}}
.ala-evi-meta span{{background:#f1f5f9;border-radius:8px;padding:8px 12px;font-size:14px}}.ala-evi-meta strong{{color:{color}}}.ala-evi summary{{cursor:pointer;font-weight:650;padding:12px 0}}
.ala-evi a{{color:#075985;text-decoration:underline}}.ala-evi li{{margin-bottom:16px}}.ala-evi small,.ala-evi-observacion{{color:#52677c}}.ala-evi ul{{padding-left:22px}}
</style><section class="ala-evi" aria-label="Explicación del riesgo de {escape(str(row.get('localidad','')))}">
<h3>{escape(str(row.get('localidad','')))} <small>· {escape(str(row.get('provincia','')))}</small></h3>
<div class="ala-evi-meta"><span>Riesgo integrado <strong>{escape(nivel)}</strong></span><span>Índice meteorológico <b>{numero(row.get('indice'),1)}/100</b> · {escape(str(row.get('nivel_meteorologico','SIN DATOS')))}</span></div>
<p><b>Por qué tiene este color:</b> {escape(motivo_publico(row))}</p>{observacion}
<p><b>Primera acción:</b> {escape(str(row.get('accion_prioritaria') or row.get('accion') or 'Consultar las fuentes y la autoridad local.'))}</p>
<details><summary>Evidencias, vigencia y fuentes consultables ({len(causas)})</summary><ul>{''.join(detalles)}</ul></details>
<small>Evaluación: {fecha(row.get('fecha_evaluacion_integrada'))} · Cobertura fluvial: {escape(str(row.get('hidrologia_cobertura','SIN DATOS')))}.<br>El color es experimental. Un umbral de estación no es una orden oficial de evacuación.</small></section>'''


def hover_nodo(row):
    """Plotly admite HTML limitado: líneas cortas, sin URLs ni nombres internos."""
    def lineas(texto, ancho=48, max_lineas=5):
        partes = textwrap.wrap(str(texto), width=ancho) or ['Sin dato']
        if len(partes) > max_lineas:
            partes = partes[:max_lineas]
            partes[-1] += '…'
        return '<br>'.join(escape(p) for p in partes)
    accion = str(row.get('accion_prioritaria') or 'Revisar fuentes y autoridades locales.')
    if accion.startswith(('Previsión oficial de', 'Umbral hidrométrico')) and ': ' in accion:
        accion = accion.split(': ', 1)[1]
    nivel = str(row.get('nivel', 'SIN DATOS'))
    icon = {'ROJO':'🔴', 'AMARILLO':'🟡', 'VERDE':'🟢'}.get(nivel, '⚪')
    causas = causas_verificadas(row)
    tipo = causas[0].get('tipo') if causas else None
    titular = {'pronostico_fluvial':'Previsión de crecida', 'observacion_fluvial':'Nivel del río observado',
               'meteorologia':'Condiciones meteorológicas', 'smn':'Aviso oficial SMN',
               'aguas_arriba':'Vigilancia aguas arriba','represa':'Vigilancia de aportes',
               'territorial':'Evidencia territorial'}.get(tipo, 'Cobertura por revisar')
    return (f"<b>{escape(str(row.get('localidad','')))} · {escape(str(row.get('provincia','')))}</b><br>"
            f"{icon} <b>Riesgo integrado: {escape(nivel)}</b><br>"
            f"Índice meteorológico: <b>{numero(row.get('indice'),1)}/100</b> · {escape(str(row.get('nivel_meteorologico','SIN DATOS')))}<br><br>"
            f"<b>{titular}</b><br>{lineas(motivo_publico(row))}<br><br>"
            f"<b>Primera acción</b><br>{lineas(accion,max_lineas=3)}<br>"
            f"<br>Evaluación: {escape(fecha(row.get('fecha_evaluacion_integrada')))}<br>"
            "<i>Seleccioná la localidad debajo del mapa<br>para ver alturas, plazos y fuentes.</i>")
