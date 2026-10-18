"""Integración real de la portada y el selector Streamlit, con fuentes congeladas.

Se ejecuta render_app hasta su final. La integración de riesgo, la selección,
la ficha HTML, el mapa y los detalles meteorológicos son los de producción.
Sólo se sustituyen consultas/paneles externos, historial y funciones de envío.
"""
from contextlib import ExitStack
from copy import deepcopy
import importlib.util
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from streamlit.testing.v1 import AppTest
from test_mapa_litoral import datos_html

ROOT = Path(__file__).resolve().parents[1]


def ejecutar_app_con_fuentes_congeladas():
    """Entrypoint del ScriptRunner: conserva el estado de widgets entre reruns."""
    import plotly.express as px
    import plotly.graph_objects as go
    import streamlit as st
    import streamlit.components.v1 as components
    from test_portada_local import NOW, datos

    spec=importlib.util.spec_from_file_location('_ala_app_portada_streamlit',ROOT/'app.py')
    app=importlib.util.module_from_spec(spec)
    with patch.dict(os.environ,{'ALA_MONITOR_HEADLESS':'1'}):
        spec.loader.exec_module(app)
    app.st=st
    app.px=px
    app.go=go
    app.components=components
    app.ahora=lambda: NOW
    app.NODOS=[dict(n) for n in app.NODOS if n['localidad'] in ('Corrientes','Rafaela')]
    completo,panorama=datos()
    filas=[]
    for nodo in app.NODOS:
        fila=app.resultado_sin_datos(nodo,'Fuente meteorológica ausente en el escenario de prueba')
        if nodo['localidad']=='Corrientes':
            fila.update(completo,estado_datos='OK',error_datos='',humedad_suelo_promedio=.3,
                        lluvia_24=0.,lluvia_72=1.,lluvia_7d=2.,runoff_72=0.)
        filas.append(fila)
    # La firma impide que la primera entrada invalide el fixture como si
    # hubiera cambiado la red de localidades. Los controles siguen siendo reales.
    st.session_state['firma_cobertura']=app.firma_cobertura()
    if 'df_alerta' not in st.session_state:
        st.session_state['df_alerta']=app.pd.DataFrame(filas)
        st.session_state['raw_ecmwf']=[{},{}]
        st.session_state['meteorologia_consultada']=NOW.isoformat()

    app.obtener_panorama_hidrologico=Mock(side_effect=lambda:deepcopy(panorama))
    app.obtener_datos_ecmwf=Mock(side_effect=AssertionError('No se debe renovar un fixture vigente'))
    app.obtener_secrets_telegram=lambda: ('','')
    app.HISTORY_FILE=SimpleNamespace(exists=lambda:False)
    app.guardar_historial=Mock()
    secundarios=('mostrar_proyecto_ficha','mostrar_radar_smn','mostrar_datos_smn',
                 'mostrar_paneles_ficha','mostrar_windy','mostrar_goes19',
                 'mostrar_recomendaciones_alerta','mostrar_pronostico_fluvial_local',
                 'estado_telegram_ui')
    for nombre in secundarios:
        setattr(app,nombre,Mock())
    app.cargar_panel_red=lambda: SimpleNamespace(mostrar_estado_conexion=Mock(),mostrar_red_integrada=Mock())
    envios={nombre:Mock(side_effect=AssertionError('Telegram prohibido en AppTest'))
            for nombre in ('enviar_telegram','enviar_telegram_detallado','procesar_alertas_telegram')}
    for nombre,control in envios.items():
        setattr(app,nombre,control)
    app.render_app()
    st.session_state['prueba_render_completo']=True
    st.session_state['prueba_consultas_meteo']=app.obtener_datos_ecmwf.call_count
    st.session_state['prueba_envios']=sum(control.call_count for control in envios.values())


class PortadaPrincipalStreamlitTests(unittest.TestCase):
    def setUp(self):
        self.red=ExitStack()
        self.addCleanup(self.red.close)
        self.http=self.red.enter_context(patch('requests.sessions.Session.request',
            side_effect=AssertionError('La prueba no permite consultas ni envíos HTTP')))
        self.urlopen=self.red.enter_context(patch('urllib.request.urlopen',
            side_effect=AssertionError('La prueba no permite consultas externas')))
        self.excepciones_por_paso={}

    def revisar_paso(self,at,nombre):
        self.excepciones_por_paso[nombre]=[e.message for e in at.exception]
        self.assertEqual(self.excepciones_por_paso[nombre],[],self.excepciones_por_paso)
        self.assertTrue(at.session_state['prueba_render_completo'])
        self.assertEqual(at.session_state['prueba_consultas_meteo'],0)
        self.assertEqual(at.session_state['prueba_envios'],0)
        self.http.assert_not_called()
        self.urlopen.assert_not_called()
        fichas=[m.value for m in at.markdown if '<section class="ala-local"' in m.value]
        self.assertEqual(len(fichas),1,'Debe haber una única ficha de la localidad seleccionada')
        elementos=list(at.main)
        mapas=[]
        for posicion,elemento in enumerate(elementos):
            if elemento.type=='iframe' and 'id="mapa-litoral-datos"' in elemento.proto.srcdoc:
                mapas.append((posicion,datos_html(elemento.proto.srcdoc)))
        self.assertEqual(len(mapas),1,'El mapa integrado debe aparecer una sola vez')
        posicion_mapa,datos_mapa=mapas[0]
        posicion_selector=next(i for i,e in enumerate(elementos) if e.type=='selectbox' and e.key=='localidad_detalle')
        posicion_ficha=next(i for i,e in enumerate(elementos) if e.type=='markdown' and '<section class="ala-local"' in e.value)
        self.assertLess(posicion_mapa,posicion_selector,'El mapa debe ser visible antes de elegir o localizar un punto')
        self.assertLess(posicion_selector,posicion_ficha)
        niveles={punto['nivel'] for punto in datos_mapa['puntos']}
        self.assertTrue({'ROJO','SIN DATOS'}.issubset(niveles),'El mapa inicial conserva el rojo fluvial y los puntos sin datos')
        self.assertEqual({p['nombre'] for p in datos_mapa['puntos']},{'Corrientes','Rafaela'})
        self.assertEqual(len(datos_mapa['geometrias']['features']),6)
        self.assertEqual(datos_mapa['omitidos'],0)
        return fichas[0]

    def test_render_y_cambio_de_localidad_con_riesgo_fluvial_y_datos_ausentes(self):
        codigo=(f'import sys\nsys.path.insert(0,{str(ROOT)!r})\n'
                f'sys.path.insert(0,{str(ROOT/"tests")!r})\n'
                'from test_portada_ui_ala import ejecutar_app_con_fuentes_congeladas\n'
                'ejecutar_app_con_fuentes_congeladas()\n')
        at=AppTest.from_string(codigo,default_timeout=20).run()
        primera=self.revisar_paso(at,'Corrientes inicial')
        self.assertEqual(at.selectbox(key='localidad_detalle').value,'Corrientes')
        self.assertIn('<h2>Corrientes</h2>',primera)
        self.assertIn('ROJO · ALA experimental',primera)
        self.assertIn('Próximas 24 h: 0,0 mm',primera)
        self.assertIn('Altura observada: 5,32 m',primera)
        self.assertIn('13/10/2026 · hora no publicada',primera)
        self.assertIn('Previsión',primera)

        at.selectbox(key='localidad_detalle').set_value('Rafaela').run()
        sin_datos=self.revisar_paso(at,'Rafaela sin datos')
        self.assertIn('<h2>Rafaela</h2>',sin_datos)
        self.assertNotIn('<h2>Corrientes</h2>',sin_datos)
        self.assertIn('SIN DATOS · ALA experimental',sin_datos)
        self.assertIn('Próximas 24 h: sin dato completo vigente',sin_datos)
        self.assertNotIn('Próximas 24 h: 0,0 mm',sin_datos)
        self.assertNotIn('Altura observada: 5,32 m',sin_datos)
        self.assertIn('Sin estación local vinculada',sin_datos)

        at.selectbox(key='localidad_detalle').set_value('Corrientes').run()
        regreso=self.revisar_paso(at,'Regreso a Corrientes')
        self.assertEqual(regreso,primera,'El cambio de punto no debe alterar la evidencia congelada')


if __name__ == '__main__':
    unittest.main()
