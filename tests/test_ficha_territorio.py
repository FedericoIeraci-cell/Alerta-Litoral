"""El panel territorial no puede contradecir el riesgo integrado de la localidad."""
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

os.environ['ALA_MONITOR_HEADLESS'] = '1'
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app

NOW = datetime(2026, 10, 15, 12, 0, tzinfo=ZoneInfo('America/Argentina/Buenos_Aires'))


def meteorologia(nodo, lluvia=0):
    return {**nodo, 'indice':27.9, 'estado_datos':'OK', 'cobertura_meteorologica':True,
            'lluvia_futura_72':lluvia, 'nivel':'VERDE', 'actualizado':NOW.strftime('%d/%m/%Y %H:%M')}


def panorama_fluvial():
    return {'estaciones':{'paso de los libres':{
        'nombre':'Paso de los Libres', 'rio':'Uruguay', 'altura_m':9.28,
        'alerta_m':7.5, 'evacuacion_m':8.5, 'fecha':NOW.isoformat(),
        'fuente':'https://alerta.ina.gob.ar/'}},
        'pronosticos':[], 'represas':{}, 'fuentes':[], 'avisos':[], 'smn':{}}


def panorama_smn():
    return {'estaciones':{}, 'pronosticos':[], 'represas':{}, 'fuentes':[], 'avisos':[],
            'smn':{'fecha_feed':NOW, 'error':'', 'fallos':0, 'truncado':False,
                   'filas':[{'id':'lluvia-rafaela-1', 'nodos':['Rafaela'], 'status':'Actual',
                             'tipo_mensaje':'Alert', 'emision':NOW-timedelta(hours=1),
                             'desde':NOW-timedelta(hours=1), 'hasta':NOW+timedelta(hours=12),
                             'fenomeno':'LLUVIA', 'severidad_cap':'Extreme',
                             'url':'https://ssl.smn.gob.ar/CAP/lluvia-rafaela.xml'}]}}


class PantallaTerritorial:
    """Interfaz inerte: captura el color y el informe que descargaría el usuario."""
    def __init__(self, state, nodo):
        self.session_state = state
        self.nodo = nodo
        self.markdowns = []
        self.downloads = {}

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def expander(self, *_, **__):
        return self

    def selectbox(self, label, options, **kw):
        if kw.get('key') == 'ficha_localidad':
            return next(i for i,n in enumerate(app.NODOS) if n['localidad'] == self.nodo['localidad'])
        return list(options)[0]

    def select_slider(self, *_, **kw):
        return kw.get('value', 5)

    def button(self, *_, **__):
        return False

    def file_uploader(self, *_, **__):
        return None

    def download_button(self, label, data=None, file_name=None, *_, **__):
        if file_name:
            self.downloads[file_name] = data

    def markdown(self, value, **_):
        self.markdowns.append(value)

    def columns(self, n):
        return [self]*n

    def __getattr__(self, name):
        if name in {'subheader','write','caption','link_button','info','warning','error','metric','dataframe'}:
            return lambda *args, **kw: None
        raise AttributeError(name)


class FichaTerritorioTests(unittest.TestCase):
    def setUp(self):
        self.state = {}
        self.patches = [
            patch.object(app, 'ahora', return_value=NOW),
            patch.object(app.st, 'session_state', self.state),
            patch('requests.sessions.Session.request', side_effect=AssertionError('Red prohibida en la prueba')),
        ]
        for p in self.patches:
            p.start()
        self.addCleanup(patch.stopall)

    def nodo(self, nombre):
        return next(n.copy() for n in app.NODOS if n['localidad'] == nombre)

    def verificar_pantalla_e_informe(self, nombre, panorama):
        nodo = self.nodo(nombre)
        self.state['df_alerta'] = app.pd.DataFrame([meteorologia(nodo)])
        principal = app.integrar_semaforo(self.state['df_alerta'], panorama=panorama).iloc[0].to_dict()
        pantalla = PantallaTerritorial(self.state, nodo)
        recomendaciones = []
        salud_vacia = app.pd.DataFrame(columns=['lat','lon','nombre','provincia','estado'])
        with (
             patch.object(app, 'st', pantalla),
             patch.object(app, 'obtener_panorama_hidrologico', return_value=panorama),
             patch.object(app, 'mostrar_registros_operativos'),
             patch.object(app, 'copia_salud_litoral', return_value=(salud_vacia, 'Catálogo de prueba')),
             patch.object(app, 'catalogos_salud_conservados', return_value=({},{})),
             patch.object(app, 'copia_vialidad_documentada', return_value=None),
             patch.object(app, 'copia_ficha', return_value=[{'nombre':'Antecedente', 'tipo':'refugio',
                                                          'estado':'histórico','fuente':'https://www.santafe.gob.ar/'}]),
             patch.object(app, 'mostrar_revision_recorrido'),
             patch.object(app, 'documento_territorio', return_value='<div>Mapa de prueba</div>'),
             patch.object(app, 'components', SimpleNamespace(html=lambda *args, **kw: None)),
             patch.object(app, 'mostrar_recomendaciones_alerta', side_effect=lambda row, **kw: recomendaciones.append(dict(row))),
        ):
            app.mostrar_territorio_ficha()
        informe = json.loads(pantalla.downloads[f'planificacion_{nombre}.json'])
        self.assertEqual(principal['nivel'], 'ROJO')
        self.assertIn('### 🔴 ROJO', pantalla.markdowns)
        self.assertNotIn('### 🟢 VERDE', pantalla.markdowns)
        self.assertEqual(informe['semaforo']['semaforo'], principal['nivel'])
        self.assertEqual(informe['evaluacion_integrada']['nivel'], principal['nivel'])
        self.assertEqual(recomendaciones[-1]['nivel'], principal['nivel'])
        self.assertEqual(informe['recomendaciones'], app.recomendaciones_para_alerta(recomendaciones[-1]))
        return informe

    def test_territorio_y_exportacion_conservan_rojo_fluvial_con_indice_bajo(self):
        informe = self.verificar_pantalla_e_informe('Paso de los Libres', panorama_fluvial())
        self.assertEqual(informe['evaluacion_integrada']['indice'], 27.9)
        self.assertEqual(informe['hidrologia']['hidrologia_rango'], 2)
        self.assertEqual(informe['hidrologia']['hidrologia_altura_m'], 9.28)
        self.assertIn('alerta.ina.gob.ar', informe['hidrologia']['hidrologia_fuentes'])

    def test_territorio_y_exportacion_conservan_rojo_por_smn(self):
        informe = self.verificar_pantalla_e_informe('Rafaela', panorama_smn())
        self.assertEqual(informe['avisos_smn']['smn_rango'], 2)
        self.assertIn('ssl.smn.gob.ar', informe['avisos_smn']['smn_fuentes'])

    def test_ausencia_meteorologica_no_borra_crecida_fluvial_actual(self):
        row = app.fila_territorial_integrada(self.nodo('Paso de los Libres'), panorama=panorama_fluvial())
        self.assertEqual(row['nivel'], 'ROJO')
        self.assertFalse(row['cobertura_meteorologica'])
        self.assertEqual(row['hidrologia_rango'], 2)

    def test_una_nueva_muestra_local_si_puede_elevar_la_sesion(self):
        nodo = self.nodo('Rafaela')
        self.state['df_alerta'] = app.pd.DataFrame([meteorologia(nodo, lluvia=35)])
        sin_agua = app.fila_territorial_integrada(nodo, panorama={})
        self.assertEqual(sin_agua['nivel'], 'VERDE')
        self.state['ficha_indices'] = app.pd.DataFrame([{
            'localidad':nodo['localidad'], 'lat':nodo['lat'], 'lon':nodo['lon'],
            'fecha':NOW, 'fuente':'https://example.org/observacion-controlada', 'NDWI':.2}])
        con_agua = app.fila_territorial_integrada(nodo, panorama={})
        self.assertEqual(con_agua['nivel'], 'ROJO')
        self.assertEqual(con_agua['indice'], sin_agua['indice'])
        self.assertIn('NDWI', con_agua['motivos_semaforo'])


if __name__ == '__main__':
    unittest.main()
