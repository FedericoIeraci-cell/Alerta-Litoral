"""Historial CSV local: lectura recuperable y escritura atómica con respaldo.

Un byte inválido no se convierte a Latin-1 ni se borra silenciosamente. Se
excluye del gráfico el registro afectado y se conserva el original completo
antes de normalizar el archivo. No consulta servicios ni calcula alertas.
"""
from dataclasses import dataclass
import csv
import hashlib
from io import StringIO
import json
import os
from pathlib import Path
import re
import tempfile
import threading

import pandas as pd

_LOCK = threading.RLock()
MAX_BYTES = 32_000_000
REQUERIDAS = {'actualizado', 'localidad', 'indice'}


@dataclass
class Lectura:
    datos: pd.DataFrame
    original: bytes
    omitidas: int = 0
    cola_ilegible: bool = False
    codificacion: str = 'utf-8'

    @property
    def necesita_respaldo(self):
        return bool(self.omitidas or self.cola_ilegible or self.codificacion != 'utf-8')


def _texto_invalido(campo):
    return any(0xDC80 <= ord(c) <= 0xDCFF or (ord(c) < 32 and c not in '\t\r\n')
               or ord(c) == 127 for c in campo)


def leer_historial(ruta):
    ruta = Path(ruta)
    with ruta.open('rb') as archivo:
        original = archivo.read(MAX_BYTES + 1)
    if len(original) > MAX_BYTES:
        raise ValueError('El historial supera el tamaño admitido; conservá el original para revisarlo.')
    codificacion = 'utf-8'
    if original.startswith((b'\xff\xfe', b'\xfe\xff')):
        texto = original.decode('utf-16')  # sólo con marca explícita, sin adivinar
        codificacion = 'utf-16'
    else:
        texto = original.decode('utf-8-sig', errors='surrogateescape')
    lector = csv.reader(StringIO(texto, newline=''), strict=True)
    try:
        columnas = next(lector)
    except (StopIteration, csv.Error):
        raise ValueError('El archivo no contiene una cabecera CSV legible.') from None
    if (any(_texto_invalido(c) for c in columnas) or len(set(columnas)) != len(columnas)
            or not REQUERIDAS.issubset(columnas)):
        raise ValueError('La cabecera del historial está dañada o no corresponde al formato esperado.')
    filas, omitidas, cola_ilegible = [], 0, False
    try:
        for fila in lector:
            if not fila:
                continue
            if len(fila) != len(columnas) or any(_texto_invalido(c) for c in fila):
                omitidas += 1
                continue
            filas.append(fila)
    except csv.Error:
        # Sin un cierre de comillas fiable no se inventan límites de registros.
        cola_ilegible = True
    return Lectura(pd.DataFrame(filas, columns=columnas), original, omitidas,
                   cola_ilegible, codificacion)


def _incidencias(ruta):
    registro = ruta.with_name(ruta.name + '.recuperacion.json')
    if not registro.exists():
        return []
    datos = json.loads(registro.read_text(encoding='utf-8'))
    if not isinstance(datos, list) or any(not isinstance(x, dict) for x in datos):
        raise ValueError('El registro de recuperación requiere revisión.')
    return datos


def _reemplazar_atomico(ruta, escribir):
    temporal = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', newline='',
                dir=ruta.parent, prefix='.ala-historial-', suffix='.tmp', delete=False) as archivo:
            temporal = Path(archivo.name)
            escribir(archivo)
            archivo.flush()
            os.fsync(archivo.fileno())
        os.replace(temporal, ruta)
    finally:
        if temporal is not None and temporal.exists():
            temporal.unlink()


def guardar_registros(ruta, nuevos):
    """Serializa sesiones concurrentes; deja el original intacto si falla el respaldo."""
    ruta = Path(ruta)
    if nuevos.empty:
        return {'ok': True}
    if not REQUERIDAS.issubset(nuevos.columns):
        return {'ok': False, 'error': 'Faltan columnas obligatorias en los registros nuevos.'}
    with _LOCK:
        try:
            anterior = leer_historial(ruta) if ruta.exists() else None
            if anterior and anterior.necesita_respaldo:
                digest = hashlib.sha256(anterior.original).hexdigest()
                respaldo = ruta.with_name(ruta.name + '.respaldo-' + digest[:16] + '.bin')
                if respaldo.exists():
                    if hashlib.sha256(respaldo.read_bytes()).hexdigest() != digest:
                        raise ValueError('El respaldo existente no coincide con el original; se conserva el historial.')
                else:
                    with respaldo.open('xb') as copia:
                        copia.write(anterior.original)
                        copia.flush()
                        os.fsync(copia.fileno())
                incidencias = _incidencias(ruta)
                if not any(x.get('sha256') == digest for x in incidencias):
                    incidencias.append({'archivo': respaldo.name, 'sha256': digest,
                        'filas_omitidas': anterior.omitidas, 'cola_ilegible': anterior.cola_ilegible,
                        'codificacion': anterior.codificacion})
                    _reemplazar_atomico(ruta.with_name(ruta.name + '.recuperacion.json'),
                        lambda archivo: json.dump(incidencias, archivo, ensure_ascii=False))
            combinado = pd.concat([anterior.datos, nuevos], ignore_index=True) if anterior else nuevos.copy()
            # Se mantiene el límite previo de retención del historial local.
            combinado = combinado.tail(50000).copy()
            _reemplazar_atomico(ruta, lambda archivo: combinado.to_csv(archivo, index=False))
            return {'ok': True}
        except (OSError, ValueError, csv.Error) as error:
            return {'ok': False, 'error': 'No se pudo guardar el historial (' + type(error).__name__
                    + '). Los archivos existentes se conservan; descargá el original desde Historial.'}


def _fecha_local(valor):
    # Un ISO año-mes-día no debe convertirse por accidente a año-día-mes.
    if re.match(r'^\d{4}-\d{2}-\d{2}(?:$|[ T])', valor):
        fecha = pd.to_datetime(valor, format='ISO8601', errors='coerce')
    else:
        fecha = pd.to_datetime(valor, dayfirst=True, errors='coerce')
    if pd.isna(fecha):
        return pd.NaT
    if fecha.tzinfo is not None:
        return fecha.tz_convert('America/Argentina/Buenos_Aires').tz_localize(None)
    return fecha


def mostrar_historial(st, px, ruta):
    st.header('📈 Historial')
    st.caption('Desde V4.1 el historial conserva el semáforo integrado y sus motivos. '
               'Los registros anteriores corresponden al índice meteorológico de aquella versión. '
               'El archivo es local al servidor y puede reiniciarse al desplegar; descargá una copia para conservarlo.')
    if not ruta.exists():
        st.info('El historial comenzará a generarse cuando la aplicación procese datos.')
        return
    ruta = Path(ruta)
    try:
        lectura = leer_historial(ruta)
    except (OSError, ValueError, csv.Error):
        st.warning('El archivo del historial no se puede interpretar con seguridad. '
                   'Se conserva el original; no se inventaron registros.')
        try:
            if ruta.stat().st_size <= MAX_BYTES:
                st.download_button('Descargar original para recuperar', ruta.read_bytes(),
                                   file_name='historial_original.bin', mime='application/octet-stream',
                                   key='historial_original_error')
        except OSError:
            st.caption('El servidor tampoco permitió leer el original para descargarlo.')
        return
    if lectura.omitidas or lectura.cola_ilegible:
        st.warning(f'Historial parcial: {lectura.omitidas} registro(s) con bytes o columnas inválidos '
                   'no se grafican.' + (' Hay además un fragmento final que no se pudo interpretar.'
                                       if lectura.cola_ilegible else '') + ' El original se conserva.')
        st.download_button('Descargar original sin modificar', lectura.original,
                           file_name='historial_original.bin', mime='application/octet-stream',
                           key='historial_original_parcial')
    try:
        recuperaciones = _incidencias(ruta)
        if recuperaciones:
            st.warning('Se recuperó la parte legible de un historial con problemas de formato. '
                       'Los registros no interpretables permanecen en los respaldos; el gráfico puede tener huecos.')
            with st.expander('Respaldos originales y detalle de recuperación'):
                for i, recuperacion in enumerate(recuperaciones):
                    nombre = recuperacion.get('archivo', '')
                    if (not isinstance(nombre, str) or Path(nombre).name != nombre
                            or not nombre.startswith(ruta.name + '.respaldo-') or not nombre.endswith('.bin')):
                        continue
                    st.caption(f"Respaldo {i + 1}: {recuperacion.get('filas_omitidas', 0)} registro(s) omitidos; "
                               f"fragmento final ilegible: {'sí' if recuperacion.get('cola_ilegible') else 'no'}.")
                    respaldo = ruta.with_name(nombre)
                    if respaldo.exists() and respaldo.stat().st_size <= MAX_BYTES:
                        st.download_button(f'Descargar respaldo original {i + 1}', respaldo.read_bytes(),
                                           file_name=nombre, mime='application/octet-stream',
                                           key=f'historial_respaldo_{i}')
    except (OSError, ValueError):
        st.warning('No se pudo leer el detalle de los respaldos; el historial legible se muestra debajo.')
    historial = lectura.datos
    localidades = sorted({x for x in historial['localidad'].dropna().astype(str) if x.strip()})
    if not localidades:
        st.info('Todavía no hay registros históricos legibles por localidad.')
        return
    localidad = st.selectbox('Localidad para historial', localidades, key='hist_localidad')
    seleccion = historial[historial['localidad'].eq(localidad)].copy()
    grafico = seleccion.copy()
    # Las fechas locales antiguas no contienen zona; no se interpretan como UTC.
    fechas = {valor: _fecha_local(valor) for valor in grafico['actualizado'].unique()}
    grafico['actualizado'] = grafico['actualizado'].map(fechas)
    grafico['indice'] = pd.to_numeric(grafico['indice'], errors='coerce')
    validas = grafico['actualizado'].notna() & grafico['indice'].between(0, 100)
    if (~validas).any():
        st.caption(f'{int((~validas).sum())} registro(s) sin fecha o índice válido se conservan en el CSV, fuera del gráfico.')
    grafico = grafico[validas].sort_values('actualizado')
    if grafico.empty:
        st.info('No hay fechas e índices válidos para graficar esta localidad.')
    else:
        figura = px.line(grafico, x='actualizado', y='indice', markers=True,
                         title=f'Evolución del índice meteorológico — {localidad}')
        figura.update_yaxes(range=[0, 100])
        st.plotly_chart(figura, use_container_width=True)
    st.download_button('⬇️ Descargar historial CSV', seleccion.to_csv(index=False).encode('utf-8-sig'),
                       file_name='historial_alerta_litoral.csv', mime='text/csv', key='historial_csv')
