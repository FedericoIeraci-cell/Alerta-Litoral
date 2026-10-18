"""Selector Streamlit real y eventos geográficos sintéticos, sin permisos ni red."""
from pathlib import Path
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]


def codigo(app):
    return f"import sys\nsys.path.insert(0,{str(ROOT)!r})\nAPP={app!r}\n" + '''
import streamlit as st
from unittest.mock import patch
import ubicacion_local as geo
nodos=[dict(n) for n in geo.catalogo_guardado() if n['app']==APP]
opciones=['Rafaela','Corrientes','Rosario'] if APP=='ala' else ['Lima','Zárate centro','Campana centro']
destacada='Corrientes' if APP=='ala' else 'Zárate centro'
target='ala-rosario' if APP=='ala' else 'zc-campana-centro'
def evento(status='ok',anterior=False):
    contexto=st.session_state['test_pending'] if anterior else st.session_state['test_context']
    contador=st.session_state.get('test_count',0)+1
    st.session_state['test_count']=contador
    salida={'event_id':f'{contador:032x}','session_id':contexto['session_id'],
            'revision':contexto['revision'],'status':status}
    if status=='ok':
        salida.update(reference_id=target,distance_km=1)
    if status=='pending':
        st.session_state['test_pending']=dict(contexto)
    st.session_state['test_retained']=salida
def componente(**args):
    st.session_state['test_context']={'session_id':args['session_id'],'revision':args['revision']}
    st.session_state['test_auto_allowed']=args['auto_allowed']
    return st.session_state.get('test_retained')
with patch.object(geo,'_COMPONENTE',componente):
    elegida=geo.seleccionar_localidad(st,app=APP,nodos=nodos,opciones=opciones,destacada=destacada)
st.write('Referencia consultada: '+elegida)
st.button('Simular respuesta de ubicación',on_click=evento)
st.button('Iniciar respuesta pendiente',on_click=evento,args=('pending',))
st.button('Completar respuesta anterior',on_click=evento,args=('ok',True))
'''


class SelectorUbicacionStreamlitTests(unittest.TestCase):
    def setUp(self):
        self.red = patch('requests.sessions.Session.request', side_effect=AssertionError('Prueba sin red ni envíos'))
        self.http = self.red.start()
        self.addCleanup(self.red.stop)

    def revisar(self, at, localidad):
        self.assertFalse(at.exception, [x.message for x in at.exception])
        self.assertFalse(at.warning, [x.value for x in at.warning])
        self.assertEqual(at.selectbox(key='localidad_detalle').value, localidad)
        self.http.assert_not_called()

    def test_evento_selecciona_en_ambas_apps_y_manual_permanece_en_reruns(self):
        for app, inicial, geografica, manual in [('ala', 'Corrientes', 'Rosario', 'Rafaela'),
                                                  ('zc', 'Zárate centro', 'Campana centro', 'Lima')]:
            with self.subTest(app=app):
                at = AppTest.from_string(codigo(app)).run()
                self.revisar(at, inicial)
                at.button[0].click().run()
                self.revisar(at, geografica)
                at.selectbox(key='localidad_detalle').set_value(manual).run()
                self.revisar(at, manual)
                at.run()
                self.revisar(at, manual)
                self.assertFalse(at.session_state['test_auto_allowed'])
                at.button[0].click().run()
                self.revisar(at, geografica)

    def test_respuesta_tardia_no_sobrescribe_eleccion_manual_posterior(self):
        at = AppTest.from_string(codigo('ala')).run()
        at.button[1].click().run()
        at.selectbox(key='localidad_detalle').set_value('Rafaela').run()
        at.button[2].click().run()
        self.revisar(at, 'Rafaela')
        at.button[0].click().run()
        self.revisar(at, 'Rosario')

    def test_enlace_a_referencia_conocida_preselecciona_sin_pedir_ubicacion(self):
        at = AppTest.from_string(codigo('zc'))
        at.query_params['referencia'] = 'zc-campana-centro'
        at.run()
        self.revisar(at, 'Campana centro')
        self.assertFalse(at.session_state['test_auto_allowed'])
        at.selectbox(key='localidad_detalle').set_value('Lima').run()
        self.revisar(at, 'Lima')
        at.run()
        self.revisar(at, 'Lima')


if __name__ == '__main__':
    unittest.main()
