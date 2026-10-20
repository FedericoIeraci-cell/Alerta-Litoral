"""Controles responsive y conservación de datos. Sin red ni avisos."""
import ast
from html.parser import HTMLParser
from pathlib import Path
import unittest
import shutil
import subprocess

from interfaz_movil import accesos_principales, estilos_responsive
from mapa_litoral import construir_datos_mapa, html_mapa_litoral, _SCRIPT_MAPA


class Elementos(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.tags = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


class InterfazMovilTests(unittest.TestCase):
    def test_interacciones_javascript_sin_red(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node requerido para comprobar interacciones')
        run = subprocess.run([node, str(Path(__file__).with_name('test_mapa_movil_js.cjs'))],
                             input=_SCRIPT_MAPA, text=True, capture_output=True, timeout=15)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)

    def test_busqueda_filtros_lista_y_detalle_son_controles_accesibles(self):
        doc = Elementos(html_mapa_litoral([], {}, fondo_osm=False))
        por_id = {a['id']: (tag, a) for tag, a in doc.tags if 'id' in a}
        self.assertEqual(por_id['mapa-buscar'][1]['type'], 'search')
        self.assertEqual(por_id['mapa-nivel'][1]['aria-label'], 'Filtrar por nivel')
        self.assertEqual(por_id['mapa-vista-mapa'][1]['aria-pressed'], 'true')
        self.assertEqual(por_id['mapa-vista-lista'][1]['aria-pressed'], 'false')
        self.assertEqual(por_id['mapa-detalle'][0], 'dialog')
        self.assertEqual(por_id['mapa-detalle'][1]['aria-labelledby'], 'mapa-detalle-titulo')
        self.assertEqual(por_id['mapa-cuenta'][1]['aria-live'], 'polite')

    def test_la_lista_conserva_niveles_y_provincias_sin_inventar_datos(self):
        filas = [{'localidad': 'Goya', 'provincia': 'Corrientes', 'lat': -29.14,
                  'lon': -59.26, 'nivel': 'ROJO', 'indice': 0},
                 {'localidad': 'Sin cobertura', 'lat': -30, 'lon': -59, 'nivel': 'SIN DATOS'}]
        datos = construir_datos_mapa(filas, {})
        self.assertEqual([p['nivel'] for p in datos['puntos']], ['ROJO', 'SIN DATOS'])
        self.assertEqual(datos['puntos'][0]['provincia'], 'Corrientes')
        self.assertEqual(datos['puntos'][1]['provincia'], '')
        self.assertEqual(filas[0]['indice'], 0)

    def test_los_accesos_tienen_destino_real_sin_navegacion_a_terceros(self):
        enlaces = [a['href'] for tag, a in Elementos(accesos_principales()).tags if tag == 'a']
        self.assertEqual(enlaces, ['#ala-mapa', '#ala-localidad', '#ala-alertas', '#ala-estado'])
        fuente = (Path(__file__).parents[1] / 'app.py').read_text()
        for enlace in enlaces:
            self.assertIn(enlace[1:], fuente)
        arbol = ast.parse(fuente)
        llamadas = [n for n in ast.walk(arbol) if isinstance(n, ast.Call)
                    and isinstance(n.func, ast.Name) and n.func.id == 'html_mapa_litoral']
        callback = next(k.value.id for k in llamadas[0].keywords if k.arg == 'tarjeta_nodo')
        self.assertEqual(callback, 'tarjeta_nodo_html', 'La ficha móvil no recorta las evidencias')

    def test_estilos_contemplan_area_segura_y_tamano_tactil(self):
        css = estilos_responsive()
        self.assertIn('safe-area-inset-bottom', css)
        self.assertIn('min-height:48px', css)
        self.assertIn('font-size:16px', css)
        self.assertIn('prefers-reduced-motion', css)


if __name__ == '__main__':
    unittest.main()
