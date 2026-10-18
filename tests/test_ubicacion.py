"""Contrato sin coordenadas, selección de una vez e importación sin interfaz/red."""
import ast
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import ubicacion_local as geo

ROOT = Path(__file__).resolve().parents[1]


def nodos(app):
    return [dict(n) for n in geo.catalogo_guardado() if n['app'] == app]


class ContratoUbicacionTests(unittest.TestCase):
    def setUp(self):
        self.app = 'ala'
        self.catalogo = geo.preparar_catalogo(self.app, nodos(self.app))
        self.opciones = [n['localidad'] for n in self.catalogo if n['app'] == self.app]
        self.estado = geo.estado_inicial()
        self.evento = {'event_id': 'a' * 32, 'session_id': self.estado['session_id'], 'revision': 0,
                       'status': 'ok', 'reference_id': 'ala-rosario', 'distance_km': 1}

    def test_importacion_no_necesita_streamlit_ni_requests(self):
        spec = importlib.util.spec_from_file_location('_geo_headless', ROOT / 'ubicacion_local.py')
        modulo = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {'streamlit': None, 'requests': None}):
            spec.loader.exec_module(modulo)
        self.assertIsNone(modulo._COMPONENTE)

    def test_catalogo_publico_coincide_con_nodos_de_la_aplicacion(self):
        tree = ast.parse((ROOT / 'app.py').read_text(encoding='utf-8'))
        assignment = next(n for n in tree.body if isinstance(n, ast.Assign)
                          and any(isinstance(t, ast.Name) and t.id == 'NODOS' for t in n.targets))
        actuales = ast.literal_eval(assignment.value)
        app = 'zc' if any('partido' in n for n in actuales) else 'ala'
        guardados = {n['localidad']: n for n in nodos(app)}
        self.assertEqual(set(guardados), {n['localidad'] for n in actuales})
        for n in actuales:
            self.assertEqual((n['lat'], n['lon']), (guardados[n['localidad']]['lat'], guardados[n['localidad']]['lon']))
        self.assertEqual(len(geo.catalogo_guardado()), 58)
        self.assertEqual(len({n['id'] for n in geo.catalogo_guardado()}), 58)

    def test_catalogo_actual_filtra_referencias_que_el_selector_no_ofrece(self):
        limitado = geo.preparar_catalogo('ala', nodos('ala'), ['Rosario'])
        self.assertEqual([n['localidad'] for n in limitado if n['app'] == 'ala'], ['Rosario'])
        self.assertEqual(len([n for n in limitado if n['app'] == 'zc']), 10)

    def test_evento_se_consume_una_vez_y_manual_prevalece_en_reruns(self):
        estado, eleccion = geo.consumir_evento(self.evento, self.estado, self.catalogo, self.app, self.opciones)
        self.assertEqual(eleccion, 'Rosario')
        self.assertEqual(estado['result'], {'status': 'ok', 'reference_id': 'ala-rosario', 'distance_km': 1})
        repetido, eleccion = geo.consumir_evento(self.evento, estado, self.catalogo, self.app, self.opciones)
        self.assertIsNone(eleccion)
        self.assertEqual(repetido, estado)
        manual = geo.registrar_eleccion_manual(estado)
        self.assertEqual(manual['revision'], 1)
        conservado, eleccion = geo.consumir_evento(self.evento, manual, self.catalogo, self.app, self.opciones)
        self.assertIsNone(eleccion)
        self.assertEqual(conservado, manual)

    def test_resultado_tardio_no_pisa_manual_y_nuevo_clic_si_puede(self):
        pending = {k: v for k, v in self.evento.items() if k not in {'reference_id', 'distance_km'}}
        pending['status'] = 'pending'
        estado, eleccion = geo.consumir_evento(pending, self.estado, self.catalogo, self.app, self.opciones)
        self.assertIsNone(eleccion)
        self.assertTrue(estado['auto_checked'])
        manual = geo.registrar_eleccion_manual(estado)
        tardio = {**self.evento, 'event_id': 'b' * 32}
        final, eleccion = geo.consumir_evento(tardio, manual, self.catalogo, self.app, self.opciones)
        self.assertEqual(final, manual)
        self.assertIsNone(eleccion)
        nuevo = {**tardio, 'event_id': 'c' * 32, 'revision': manual['revision']}
        final, eleccion = geo.consumir_evento(nuevo, manual, self.catalogo, self.app, self.opciones)
        self.assertEqual(eleccion, 'Rosario')
        self.assertFalse(final['manual'])

    def test_rechaza_coordenadas_urls_mensajes_y_campos_extra(self):
        for campo, valor in [('latitude', -32.95), ('longitude', -60.63), ('accuracy', 8),
                             ('coords', {'latitude': 1}), ('url', 'https://ejemplo.org'),
                             ('message', 'texto recibido'), ('timestamp', 12345)]:
            with self.subTest(campo=campo):
                invalido = {**self.evento, campo: valor}
                final, eleccion = geo.consumir_evento(invalido, self.estado, self.catalogo, self.app, self.opciones)
                self.assertEqual(final, self.estado)
                self.assertIsNone(eleccion)

    def test_rechaza_identificadores_distancias_estados_y_sesiones_invalidas(self):
        cambios = [{'reference_id': 'zc-campana-centro'}, {'reference_id': 'nodo-inventado'},
                   {'distance_km': True}, {'distance_km': -1}, {'distance_km': 51}, {'distance_km': 1.5},
                   {'distance_km': float('nan')}, {'distance_km': float('inf')}, {'status': 'riesgo_bajo'},
                   {'status': {}}, {'session_id': 'otra-sesion'}, {'revision': True}, {'revision': 9},
                   {'event_id': 'id no permitido'}]
        for cambio in cambios:
            with self.subTest(cambio=cambio):
                self.assertIsNone(geo.validar_evento({**self.evento, **cambio}, self.estado, self.catalogo, self.app))

    def test_errores_no_cambian_eleccion_y_no_conservan_informacion_de_posicion(self):
        for status in geo.ESTADOS - geo.ESTADOS_CON_REFERENCIA:
            with self.subTest(status=status):
                evento = {k: v for k, v in self.evento.items() if k not in {'reference_id', 'distance_km'}}
                evento['status'] = status
                final, eleccion = geo.consumir_evento(evento, self.estado, self.catalogo, self.app, self.opciones)
                self.assertIsNone(eleccion)
                self.assertTrue(final['auto_checked'])
                if final['result'] is not None:
                    self.assertEqual(final['result'], {'status': status})

    def test_enlace_voluntario_utiliza_solo_destino_fijo_e_id_publico(self):
        evento = {**self.evento, 'status': 'other_app', 'reference_id': 'zc-campana-centro', 'distance_km': 0}
        estado, eleccion = geo.consumir_evento(evento, self.estado, self.catalogo, self.app, self.opciones)
        self.assertIsNone(eleccion)
        resultado = geo.describir_resultado(estado['result'], self.catalogo)
        url = urlparse(resultado['url'])
        self.assertEqual(url.netloc, 'alerta-zarate-campana.streamlit.app')
        self.assertEqual(parse_qs(url.query), {'referencia': ['zc-campana-centro']})
        self.assertIn('menos de 1 km', resultado['texto'])
        self.assertIn('línea recta', resultado['texto'])
        self.assertEqual(geo.referencia_desde_enlace('zc-campana-centro', self.catalogo, 'zc', ['Campana centro']), 'Campana centro')
        for invalido in ['ala-rosario', 'https://otro.org', None, ['zc-campana-centro']]:
            self.assertIsNone(geo.referencia_desde_enlace(invalido, self.catalogo, 'zc', ['Campana centro']))

    def test_ubicacion_distante_no_se_presenta_como_local(self):
        evento = {**self.evento, 'status': 'outside', 'distance_km': 840}
        estado, eleccion = geo.consumir_evento(evento, self.estado, self.catalogo, self.app, self.opciones)
        self.assertIsNone(eleccion)
        descripcion = geo.describir_resultado(estado['result'], self.catalogo)
        self.assertIn('demasiado alejada', descripcion['texto'])
        self.assertNotIn('url', descripcion)


if __name__ == '__main__':
    unittest.main()
