"""El mapa real aparece antes de consultar fuentes externas, aun si fallan."""
import importlib.util
import json
import os
from pathlib import Path
import re
import unittest
from unittest.mock import Mock, patch

from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]


def renderizar_hasta_falla_controlada():
    import streamlit as st
    import streamlit.components.v1 as components

    spec = importlib.util.spec_from_file_location('_app_mapa_primero', ROOT / 'app.py')
    app = importlib.util.module_from_spec(spec)
    with patch.dict(os.environ, {'ALA_MONITOR_HEADLESS': '1'}):
        spec.loader.exec_module(app)
    app.st = st
    app.components = components

    class FuenteNoDisponible(RuntimeError):
        pass

    fuente = Mock(side_effect=FuenteNoDisponible('Fuente externa indisponible'))
    if hasattr(app, 'obtener_meteorologia_publica'):
        app.obtener_meteorologia_publica = fuente
    else:
        app.obtener_datos_ecmwf = fuente
    app.vigilar_actualizacion_meteorologica = Mock()
    with patch.object(components, 'html', wraps=components.html) as render_html:
        try:
            app.render_app()
        except FuenteNoDisponible:
            st.session_state['fuente_fallo_controlado'] = True
        st.session_state['alturas_mapa'] = [c.kwargs.get('height') for c in render_html.call_args_list
            if c.args and 'id="mapa-litoral-datos"' in c.args[0]]
    st.session_state['cantidad_consultas'] = fuente.call_count
    st.session_state['catalogo_mapa'] = [n['localidad'] for n in app.NODOS]


class MapaPrimeroAppTests(unittest.TestCase):
    def test_catalogo_real_es_visible_antes_de_una_consulta_que_falla(self):
        codigo = (f'import sys\nsys.path.insert(0,{str(ROOT)!r})\n'
                  f'sys.path.insert(0,{str(ROOT / "tests")!r})\n'
                  'from test_mapa_primero_app import renderizar_hasta_falla_controlada\n'
                  'renderizar_hasta_falla_controlada()\n')
        with patch('requests.sessions.Session.request', side_effect=AssertionError('Red prohibida')), \
                patch('urllib.request.urlopen', side_effect=AssertionError('Red prohibida')):
            at = AppTest.from_string(codigo, default_timeout=20).run()
        self.assertFalse(at.exception)
        self.assertTrue(at.session_state['fuente_fallo_controlado'])
        self.assertEqual(at.session_state['cantidad_consultas'], 1)
        elementos = list(at.main)
        mapas = [(i, e) for i, e in enumerate(elementos)
                 if e.type == 'iframe' and 'id="mapa-litoral-datos"' in e.proto.srcdoc]
        self.assertEqual(len(mapas), 1)
        posicion, mapa = mapas[0]
        self.assertEqual(at.session_state['alturas_mapa'], [440])
        bloque = re.search(r'<script id="mapa-litoral-datos" type="application/json">(.*?)</script>',
                           mapa.proto.srcdoc, re.S)
        self.assertIsNotNone(bloque)
        datos = json.loads(bloque.group(1))
        esperados = at.session_state['catalogo_mapa']
        self.assertEqual(len(esperados), 48)
        self.assertEqual(len(datos['puntos']), 48)
        self.assertEqual({p['nombre'] for p in datos['puntos']}, set(esperados))
        self.assertEqual(datos['omitidos'], 0)
        self.assertTrue(all(p['nivel'] == 'SIN DATOS' and p['color'] == '#64748b'
                            for p in datos['puntos']))
        for i, e in enumerate(elementos):
            if e.type == 'tab_container' or (e.type == 'markdown' and 'class="hero"' in e.value):
                self.assertLess(posicion, i)
        self.assertFalse(any(e.type == 'markdown' and 'class="hero"' in e.value for e in elementos))

        # La navegación sigue disponible aunque la primera fuente falle:
        # cuatro entradas principales, con las fuentes y el proyecto agrupados.
        principal, fuentes, proyecto = at.get('tab_container')
        self.assertEqual([t.label for t in principal.children.values()],
                         ['📍 Mi localidad', '🌦️ Fuentes y mapas', '🗺️ Territorio', '📘 Proyecto'])
        self.assertEqual([t.label for t in fuentes.children.values()],
                         ['Datos del Litoral', 'Avisos y datos SMN', 'Radar', 'Suelo y ríos', 'El Niño / La Niña'])
        self.assertEqual([t.label for t in proyecto.children.values()],
                         ['Plan y ficha', 'Casos históricos y validación'])
        guia = next(e for e in at.expander if e.label == 'Cómo leer el mapa y el agua satelital')
        textos = ' '.join(e.value for e in guia if e.type in ('markdown', 'caption'))
        self.assertIn('no es una vista en vivo', textos)
        self.assertIn('La ausencia de color no confirma suelo seco', textos)
        self.assertIn('no cambia automáticamente el color de los nodos', textos)


if __name__ == '__main__':
    unittest.main()

