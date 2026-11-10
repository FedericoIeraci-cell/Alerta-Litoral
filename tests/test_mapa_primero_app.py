"""Mapa primero y render completo aunque fallen las dos fuentes externas."""
import importlib.util
import os
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from streamlit.testing.v1 import AppTest
from test_mapa_litoral import datos_html

ROOT = Path(__file__).resolve().parents[1]


def renderizar_con_fuentes_que_fallan():
    import streamlit as st
    import streamlit.components.v1 as components

    spec = importlib.util.spec_from_file_location('_app_mapa_primero', ROOT / 'app.py')
    app = importlib.util.module_from_spec(spec)
    with patch.dict(os.environ, {'ALA_MONITOR_HEADLESS': '1'}):
        spec.loader.exec_module(app)
    app.st, app.components = st, components
    app.obtener_estado_monitor_publico = lambda: {}

    class FuenteNoDisponible(RuntimeError):
        pass

    with patch.object(components, 'html', wraps=components.html) as render_html:
        def falla_meteorologia():
            st.session_state['mapas_antes_de_consultar'] = sum(
                bool(c.args and 'id="mapa-litoral-datos"' in c.args[0])
                for c in render_html.call_args_list)
            raise FuenteNoDisponible('Fuente meteorológica indisponible')

        meteo = Mock(side_effect=falla_meteorologia)
        hidro = Mock(side_effect=FuenteNoDisponible('Fuente hidrológica indisponible'))
        app.obtener_meteorologia_publica = meteo
        app.obtener_panorama_hidrologico = hidro
        envios = {nombre: Mock(side_effect=AssertionError('No enviar Telegram'))
                  for nombre in ('enviar_telegram', 'enviar_telegram_detallado', 'procesar_alertas_telegram')}
        for nombre, control in envios.items():
            setattr(app, nombre, control)
        app.render_app()
        st.session_state['render_completo_tras_falla'] = True
        st.session_state['alturas_mapa'] = [c.kwargs.get('height') for c in render_html.call_args_list
            if c.args and 'id="mapa-litoral-datos"' in c.args[0]]
    st.session_state['cantidad_consultas'] = {'meteorologia': meteo.call_count, 'hidrologia': hidro.call_count}
    st.session_state['envios_telegram'] = sum(c.call_count for c in envios.values())
    st.session_state['catalogo_mapa'] = [n['localidad'] for n in app.NODOS]


class MapaPrimeroAppTests(unittest.TestCase):
    def test_catalogo_real_y_navegacion_siguen_visibles_si_las_fuentes_fallan(self):
        codigo = (f'import sys\nsys.path.insert(0,{str(ROOT)!r})\n'
                  f'sys.path.insert(0,{str(ROOT / "tests")!r})\n'
                  'from test_mapa_primero_app import renderizar_con_fuentes_que_fallan\n'
                  'renderizar_con_fuentes_que_fallan()\n')
        with patch('requests.sessions.Session.request', side_effect=AssertionError('Red prohibida')) as http, \
                patch('urllib.request.urlopen', side_effect=AssertionError('Red prohibida')) as urlopen:
            at = AppTest.from_string(codigo, default_timeout=20).run()
        self.assertEqual([e.message for e in at.exception], [])
        self.assertTrue(at.session_state['render_completo_tras_falla'])
        self.assertEqual(at.session_state['mapas_antes_de_consultar'], 1)
        self.assertEqual(at.session_state['cantidad_consultas'], {'meteorologia': 1, 'hidrologia': 1})
        self.assertEqual(at.session_state['envios_telegram'], 0)
        http.assert_not_called()
        urlopen.assert_not_called()
        elementos = list(at.main)
        mapas = [(i, e) for i, e in enumerate(elementos)
                 if e.type == 'iframe' and 'id="mapa-litoral-datos"' in e.proto.srcdoc]
        self.assertEqual(len(mapas), 1)
        posicion, mapa = mapas[0]
        self.assertEqual(at.session_state['alturas_mapa'], [720, 720])
        datos = datos_html(mapa.proto.srcdoc)
        esperados = at.session_state['catalogo_mapa']
        self.assertEqual(len(esperados), 48)
        self.assertEqual(len(datos['puntos']), 48)
        self.assertEqual({p['nombre'] for p in datos['puntos']}, set(esperados))
        self.assertEqual(datos['omitidos'], 0)
        self.assertTrue(all(p['nivel'] == 'SIN DATOS' and p['color'] == '#64748b'
                            for p in datos['puntos']))
        self.assertFalse(datos['capa_viirs'])
        self.assertEqual(datos['leyenda'], 'Disponibilidad de datos')
        for i, e in enumerate(elementos):
            if e.type in ('tab_container', 'selectbox'):
                self.assertLess(posicion, i)
        self.assertEqual(len(at.get('tab_container')), 1)
        self.assertEqual([t.label for t in at.get('tab_container')[0].children.values()],
                         ['Mi localidad', 'Fuentes y límites'])
        self.assertTrue(any('No hay valores actuales' in i.value for i in at.info))
        self.assertTrue(any('No se pudo confirmar el canal completo' in w.value for w in at.warning))
        self.assertFalse(at.metric)
        self.assertFalse(at.get('file_uploader'))
        self.assertEqual(len(at.get('download_button')), 1)


if __name__ == '__main__':
    unittest.main()
