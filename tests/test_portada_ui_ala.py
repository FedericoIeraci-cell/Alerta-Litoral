"""Render público completo: datos por variable y selección, sin red ni envíos.

El fixture contiene lluvia real cero, lluvia no cero, un río sin hora que debe
omitirse y una localidad sin datos. No se ejecuta la vista experimental archivada.
"""
from contextlib import ExitStack
from datetime import datetime, timedelta
import importlib.util
import os
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from streamlit.testing.v1 import AppTest
from test_mapa_litoral import datos_html

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime.fromisoformat('2026-11-10T12:30:00-03:00')


def ejecutar_app_con_fuentes_congeladas():
    """Entrypoint del ScriptRunner; conserva los widgets entre reruns."""
    import streamlit as st
    import streamlit.components.v1 as components

    spec = importlib.util.spec_from_file_location('_ala_app_portada_streamlit', ROOT / 'app.py')
    app = importlib.util.module_from_spec(spec)
    with patch.dict(os.environ, {'ALA_MONITOR_HEADLESS': '1'}):
        spec.loader.exec_module(app)
    app.st, app.components, app.ahora = st, components, lambda: NOW
    app.obtener_estado_monitor_publico = lambda: {}
    app.NODOS = [dict(n) for n in app.NODOS if n['localidad'] in ('Goya', 'Rafaela')]
    if 'consulta_confiable' not in st.session_state:
        st.session_state['consulta_confiable'] = {
            'fecha': NOW.isoformat(),
            'meteorologia': [{
                'localidad': 'Goya', 'provincia': 'Corrientes',
                'modelo_api': 'ecmwf_ifs025',
                'lluvia_futura_24': 0.0, 'lluvia_futura_72': 12.5,
                'cobertura_lluvia_futura_24': True,
                'cobertura_lluvia_futura_72': True,
                'consulta_meteorologica': (NOW - timedelta(minutes=10)).isoformat(),
                'referencia_pronostico': NOW.replace(minute=0).isoformat(),
                'nivel': 'ROJO', 'indice': 99.9, 'accion': 'ACCION_EXPERIMENTAL_PROHIBIDA',
            }],
            'panorama': {
                'estaciones': {'goya': {
                    'nombre': 'Goya', 'altura_m': 6.66, 'unidad': 'm',
                    'fecha': (NOW - timedelta(minutes=10)).isoformat(),
                    'precision_fecha': 'fecha_sin_hora',
                    'fuente': 'https://www.ina.gob.ar/alerta/diario/reporte_diario/',
                }},
                'smn': {'fecha_feed': NOW.isoformat(), 'consultado': NOW.isoformat(),
                        'total': 0, 'leidos': 0, 'filas': [], 'fallos': 0, 'truncado': False},
            },
            'origen': {'consultado': (NOW - timedelta(minutes=10)).isoformat()},
        }
    externos = ('obtener_meteorologia_publica', 'obtener_datos_ecmwf',
                'obtener_panorama_hidrologico', 'guardar_historial',
                '_render_app_experimental_archivada', 'enviar_telegram',
                'enviar_telegram_detallado', 'procesar_alertas_telegram')
    controles = {nombre: Mock(side_effect=AssertionError('Consulta o flujo retirado: ' + nombre))
                 for nombre in externos}
    for nombre, control in controles.items():
        setattr(app, nombre, control)
    app.render_app()
    st.session_state['prueba_render_completo'] = True
    st.session_state['prueba_llamadas_externas'] = sum(c.call_count for c in controles.values())


class PortadaPrincipalStreamlitTests(unittest.TestCase):
    def setUp(self):
        self.red = ExitStack()
        self.addCleanup(self.red.close)
        self.http = self.red.enter_context(patch('requests.sessions.Session.request',
            side_effect=AssertionError('La prueba no permite consultas ni envíos HTTP')))
        self.urlopen = self.red.enter_context(patch('urllib.request.urlopen',
            side_effect=AssertionError('La prueba no permite consultas externas')))

    def revisar_paso(self, at):
        self.assertEqual([e.message for e in at.exception], [])
        self.assertTrue(at.session_state['prueba_render_completo'])
        self.assertEqual(at.session_state['prueba_llamadas_externas'], 0)
        self.http.assert_not_called()
        self.urlopen.assert_not_called()
        elementos = list(at.main)
        mapas = [(i, e, datos_html(e.proto.srcdoc)) for i, e in enumerate(elementos)
                 if e.type == 'iframe' and 'id="mapa-litoral-datos"' in e.proto.srcdoc]
        self.assertEqual(len(mapas), 1)
        posicion_mapa, mapa, datos = mapas[0]
        posicion_selector = next(i for i, e in enumerate(elementos)
                                 if e.type == 'selectbox' and e.key == 'localidad_detalle')
        self.assertLess(posicion_mapa, posicion_selector)
        self.assertEqual({p['nombre'] for p in datos['puntos']}, {'Goya', 'Rafaela'})
        self.assertEqual({p['nivel'] for p in datos['puntos']}, {'DATOS', 'SIN DATOS'})
        self.assertEqual({p['color'] for p in datos['puntos']}, {'#2563eb', '#64748b'})
        self.assertEqual(len(datos['geometrias']['features']), 6)
        self.assertEqual(datos['omitidos'], 0)
        self.assertFalse(datos['capa_viirs'])
        self.assertEqual(datos['leyenda'], 'Disponibilidad de datos')
        for punto in datos['puntos']:
            self.assertNotIn('6,66', punto['popup'])
            self.assertNotIn('99,9', punto['popup'])
            self.assertNotIn('ACCION_EXPERIMENTAL_PROHIBIDA', punto['popup'])
            self.assertNotIn('Riesgo integrado', punto['popup'])
            self.assertNotIn('Índice meteorológico', punto['popup'])
        self.assertNotIn('Mostrar agua', mapa.proto.srcdoc)
        self.assertEqual(len(at.get('tab_container')), 1)
        principal = at.get('tab_container')[0]
        self.assertEqual([t.label for t in principal.children.values()],
                         ['Mi localidad', 'Fuentes y límites'])
        self.assertFalse(at.get('file_uploader'))
        self.assertFalse(at.get('plotly_chart'))
        self.assertEqual([d.label for d in at.get('download_button')],
                         ['Descargar datos válidos y sus fuentes'])
        self.assertFalse(any('ACCION_EXPERIMENTAL_PROHIBIDA' in str(getattr(e, 'value', ''))
                             for e in elementos))
        return [(m.label, m.value) for m in at.metric]

    def test_render_y_cambio_de_localidad_con_lluvia_valida_y_rio_sin_hora_omitido(self):
        codigo = (f'import sys\nsys.path.insert(0,{str(ROOT)!r})\n'
                  f'sys.path.insert(0,{str(ROOT / "tests")!r})\n'
                  'from test_portada_ui_ala import ejecutar_app_con_fuentes_congeladas\n'
                  'ejecutar_app_con_fuentes_congeladas()\n')
        at = AppTest.from_string(codigo, default_timeout=20).run()
        primera = self.revisar_paso(at)
        self.assertEqual(at.selectbox(key='localidad_detalle').value, 'Goya')
        self.assertEqual(primera, [('Lluvia prevista · 24 h', '0,0 mm'),
                                  ('Lluvia prevista · 72 h', '12,5 mm')])
        self.assertIn('Goya · Corrientes', [s.value for s in at.subheader])
        self.assertTrue(any('se omiten alturas sin hora' in c.value for c in at.caption))
        self.assertTrue(any('ECMWF IFS' in c.value and 'Desde' in c.value and 'hasta' in c.value
                            for c in at.caption))
        self.assertTrue(any('0,0 mm sólo se publica' in c.value for c in at.caption))

        at.selectbox(key='localidad_detalle').set_value('Rafaela').run()
        self.assertEqual(self.revisar_paso(at), [])
        self.assertIn('Rafaela · Santa Fe', [s.value for s in at.subheader])
        self.assertNotIn('Goya · Corrientes', [s.value for s in at.subheader])
        self.assertTrue(any('No hay valores actuales' in i.value for i in at.info))

        at.selectbox(key='localidad_detalle').set_value('Goya').run()
        self.assertEqual(self.revisar_paso(at), primera)


if __name__ == '__main__':
    unittest.main()
