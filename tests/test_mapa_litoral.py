"""Datos del mapa 2D y documento autónomo; no se realizan consultas externas."""
import ast
from base64 import b64encode
import hashlib
from html.parser import HTMLParser
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from unittest.mock import patch

import pandas as pd

from mapa_litoral import construir_datos_mapa, html_mapa_litoral, _SCRIPT_MAPA


ROOT = Path(__file__).resolve().parents[1]


class DocumentoMapa(HTMLParser):
    def __init__(self, documento):
        super().__init__()
        self.en_datos = False
        self.datos = ''
        self.scripts_remotos = []
        self.hojas_remotas = []
        self.csp = ''
        self.feed(documento)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'script':
            self.en_datos = attrs.get('id') == 'mapa-litoral-datos'
            if attrs.get('src'):
                self.scripts_remotos.append(attrs['src'])
        if tag == 'link' and attrs.get('href'):
            self.hojas_remotas.append(attrs['href'])
        if tag == 'meta' and attrs.get('http-equiv') == 'Content-Security-Policy':
            self.csp = attrs.get('content', '')

    def handle_endtag(self, tag):
        if tag == 'script':
            self.en_datos = False

    def handle_data(self, data):
        if self.en_datos:
            self.datos += data


def datos_html(documento):
    return json.loads(DocumentoMapa(documento).datos)


def catalogo_ala():
    """Lee los catálogos literales de producción sin importar ni ejecutar la UI."""
    encontrados = {}
    for nodo in ast.parse((ROOT / 'app.py').read_text(encoding='utf-8')).body:
        if isinstance(nodo, ast.Assign):
            for destino in nodo.targets:
                if isinstance(destino, ast.Name) and destino.id in ('NODOS', 'GEOMETRIAS_LITORAL'):
                    encontrados[destino.id] = ast.literal_eval(nodo.value)
    return encontrados['NODOS'], encontrados['GEOMETRIAS_LITORAL']


class MapaLitoralTests(unittest.TestCase):
    def setUp(self):
        self.http = patch('requests.sessions.Session.request', side_effect=AssertionError('Sin HTTP'))
        self.request = self.http.start()
        self.addCleanup(self.http.stop)
        self.urlopen = patch('urllib.request.urlopen', side_effect=AssertionError('Sin HTTP'))
        self.url = self.urlopen.start()
        self.addCleanup(self.urlopen.stop)

    def tearDown(self):
        self.request.assert_not_called()
        self.url.assert_not_called()

    def test_conserva_las_48_referencias_y_seis_geometrias_de_produccion(self):
        nodos, geometrias = catalogo_ala()
        filas = [{**nodo, 'nivel': 'SIN DATOS', 'indice': float('nan')} for nodo in nodos]
        datos = construir_datos_mapa(pd.DataFrame(filas), geometrias)
        self.assertEqual(len(datos['puntos']), 48)
        self.assertEqual({p['nombre'] for p in datos['puntos']}, {n['localidad'] for n in nodos})
        self.assertTrue(all(p['nivel'] == 'SIN DATOS' for p in datos['puntos']))
        self.assertEqual(datos['omitidos'], 0)
        self.assertEqual(datos['geometrias_omitidas'], 0)
        self.assertEqual(len(datos['geometrias']['features']), 6)
        reconstruidas = {f['properties']['nombre']: f['geometry'] for f in datos['geometrias']['features']}
        self.assertEqual(reconstruidas, geometrias, 'Los límites y sus huecos no se aproximan ni reemplazan')

    def test_indices_cero_y_ausente_no_ocultan_puntos_ni_mutan_el_dataframe(self):
        df = pd.DataFrame([
            {'localidad': 'A', 'lat': -27.5, 'lon': -58.8, 'nivel': 'ROJO', 'indice': 0.0},
            {'localidad': 'B', 'lat': -31.3, 'lon': -61.5, 'nivel': 'SIN DATOS', 'indice': float('nan')},
            {'localidad': 'C', 'lat': -30, 'lon': -59, 'nivel': 'AMARILLO', 'indice': 51},
            {'localidad': 'D', 'lat': -29, 'lon': -60, 'nivel': 'VERDE', 'indice': 8},
        ])
        antes = df.copy(deep=True)
        recibidos = []
        def tarjeta(fila):
            recibidos.append(fila['localidad'])
            fila['nivel'] = 'Cambio exclusivo de esta copia'
            return '<b>Motivo y fecha originales</b>'
        datos = construir_datos_mapa(df, {}, tarjeta, ajustar_a='puntos')
        self.assertEqual(recibidos, ['A', 'B', 'C', 'D'])
        self.assertEqual([p['nivel'] for p in datos['puntos']], ['ROJO', 'SIN DATOS', 'AMARILLO', 'VERDE'])
        self.assertEqual(len({p['color'] for p in datos['puntos']}), 4)
        self.assertTrue(all(p['popup'] == '<b>Motivo y fecha originales</b>' for p in datos['puntos']))
        pd.testing.assert_frame_equal(df, antes)

    def test_coordenadas_invalidas_se_declaran_y_no_se_inventan_ubicaciones(self):
        filas = [
            {'localidad': 'Cero válido', 'lat': 0, 'lon': 0, 'nivel': 'VERDE'},
            {'localidad': 'Ausente', 'lat': None, 'lon': -59},
            {'localidad': 'NaN', 'lat': float('nan'), 'lon': -59},
            {'localidad': 'Infinito', 'lat': -29, 'lon': float('inf')},
            {'localidad': 'Fuera de rango', 'lat': -99, 'lon': -59},
            {'localidad': 'Booleano', 'lat': True, 'lon': -59},
        ]
        datos = construir_datos_mapa(filas, {})
        self.assertEqual([p['nombre'] for p in datos['puntos']], ['Cero válido'])
        self.assertEqual(datos['puntos'][0]['lat'], 0)
        self.assertEqual(datos['omitidos'], 5)

    def test_ambas_vistas_y_el_fondo_opcional_conservan_los_mismos_datos(self):
        nodos, geometrias = catalogo_ala()
        filas = [{**n, 'nivel': 'AMARILLO'} for n in nodos]
        sin_fondo = datos_html(html_mapa_litoral(filas, geometrias, fondo_osm=False))
        con_fondo = datos_html(html_mapa_litoral(filas, geometrias))
        self.assertFalse(sin_fondo.pop('fondo_osm'))
        self.assertTrue(con_fondo.pop('fondo_osm'))
        self.assertEqual(sin_fondo, con_fondo)
        zc = datos_html(html_mapa_litoral(filas[:2], {}, ajustar_a='puntos',
            titulo='Referencias Zárate–Campana', leyenda='Índice meteorológico experimental'))
        self.assertEqual(zc['ajustar_a'], 'puntos')
        self.assertEqual(zc['geometrias']['features'], [])
        self.assertEqual(zc['puntos'], con_fondo['puntos'][:2])
        self.assertEqual(zc['leyenda'], 'Índice meteorológico experimental')

    def test_documento_no_descarga_codigo_y_el_json_no_puede_cerrar_su_script(self):
        nombre = '</script><script src="https://invalid.example/inyectar.js">&'
        documento = html_mapa_litoral([{'localidad': nombre, 'lat': -29, 'lon': -59}], {},
                                      titulo=nombre, fondo_osm=False)
        parser = DocumentoMapa(documento)
        self.assertEqual(parser.scripts_remotos, [])
        self.assertEqual(parser.hojas_remotas, [])
        self.assertIn("connect-src 'none'", parser.csp)
        self.assertEqual(json.loads(parser.datos)['puntos'][0]['nombre'], nombre)
        self.assertNotIn(nombre, documento)
        self.assertNotIn('<script', json.loads(parser.datos)['puntos'][0]['popup'])
        self.assertNotIn('url(images/', documento)

    def test_integridad_y_licencia_de_los_assets_oficiales(self):
        esperados = {
            'leaflet.js': '20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=',
            'leaflet.css': 'p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=',
        }
        for nombre, esperado in esperados.items():
            with self.subTest(nombre=nombre):
                dato = (ROOT / 'assets' / 'leaflet' / nombre).read_bytes()
                self.assertEqual(b64encode(hashlib.sha256(dato).digest()).decode('ascii'), esperado)
        licencia = (ROOT / 'assets' / 'leaflet' / 'LICENSE').read_text()
        self.assertIn('Redistribution and use in source and binary forms', licencia)
        self.assertIn('Volodymyr Agafonkin', licencia)
        self.assertEqual(hashlib.sha256((ROOT / 'assets' / 'leaflet' / 'LICENSE').read_bytes()).hexdigest(),
                         '53e8dc25862014e4324741ca18fbe3611e11d42ef69f59f86ea8c5389647d4cb')

    def test_viirs_opcional_no_consulta_red_al_generar_y_limita_origenes(self):
        filas = [{'localidad': 'Referencia', 'lat': -29, 'lon': -59, 'nivel': 'ROJO'}]
        base = datos_html(html_mapa_litoral(filas, {}))
        documento = html_mapa_litoral(filas, {}, capa_viirs=True)
        parser = DocumentoMapa(documento)
        datos = json.loads(parser.datos)
        self.assertEqual(datos['puntos'], base['puntos'])
        self.assertTrue(datos['capa_viirs'])
        self.assertEqual(parser.scripts_remotos, [])
        self.assertIn('connect-src https://floods.ssec.wisc.edu;', parser.csp)
        self.assertNotIn('connect-src *', parser.csp)

    def test_importacion_no_depende_de_streamlit_plotly_ni_red(self):
        spec = importlib.util.spec_from_file_location('_mapa_sin_interfaz', ROOT / 'mapa_litoral.py')
        modulo = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {'streamlit': None, 'plotly': None, 'requests': None}):
            spec.loader.exec_module(modulo)
            datos = modulo.construir_datos_mapa([], {})
        self.assertEqual(datos['puntos'], [])

    def test_script_javascript_es_valido_sin_transpilacion(self):
        node = shutil.which('node')
        if node is None:
            self.skipTest('Node no instalado; la comprobación JS se ejecuta donde está disponible')
        resultado = subprocess.run([node, '--check'], input=_SCRIPT_MAPA, text=True,
                                   capture_output=True, timeout=15, check=False)
        self.assertEqual(resultado.returncode, 0, resultado.stderr)


if __name__ == '__main__':
    unittest.main()
