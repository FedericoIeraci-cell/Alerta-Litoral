"""Un acumulado de siete días requiere la ventana completa de siete días."""
from copy import deepcopy
from datetime import datetime, timedelta
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

os.environ['ALA_MONITOR_HEADLESS'] = '1'
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app

REF = datetime(2026, 10, 15, 12)


def datos_horarios(futuras):
    horas = app.pd.date_range(REF-timedelta(days=8), REF+timedelta(hours=futuras), freq='h')
    h = {'time':[t.isoformat() for t in horas],
         'precipitation':[1.0 if t > REF else 0.0 for t in horas],
         'runoff':[.1]*len(horas)}
    for nombre in ('soil_moisture_0_to_7cm','soil_moisture_7_to_28cm',
                   'soil_moisture_28_to_100cm','soil_moisture_100_to_255cm'):
        h[nombre] = [.3]*len(horas)
    return {'hourly':h}


class Horizonte7DiasTests(unittest.TestCase):
    def setUp(self):
        self.network = patch('requests.sessions.Session.request', side_effect=AssertionError('Red prohibida'))
        self.network.start()
        self.addCleanup(self.network.stop)

    def evaluar(self, datos):
        return app.procesar_nodo(app.NODOS[0], datos, referencia_historica=REF)

    def test_72_horas_completan_indice_pero_no_acumulado_7d(self):
        row = self.evaluar(datos_horarios(72))
        self.assertTrue(row['cobertura_meteorologica'])
        self.assertFalse(row['cobertura_pronostico_7d'])
        self.assertEqual(row['lluvia_futura_72'], 72.0)
        self.assertTrue(app.np.isnan(row['lluvia_futura_7d']))
        self.assertTrue(app.np.isnan(row['runoff_futuro_7d']))

    def test_168_horas_validas_completan_7d_sin_cambiar_indice_72h(self):
        corta = self.evaluar(datos_horarios(72))
        row = self.evaluar(datos_horarios(168))
        self.assertTrue(row['cobertura_pronostico_7d'])
        self.assertEqual(row['lluvia_futura_7d'], 168.0)
        self.assertEqual(row['runoff_futuro_7d'], 16.8)
        self.assertEqual(row['indice'], corta['indice'])
        self.assertEqual(row['lluvia_futura_72'], corta['lluvia_futura_72'])

    def test_hueco_duplicado_o_marca_no_horaria_no_completan_7d(self):
        original = datos_horarios(168)
        h = original['hourly']
        indice = h['time'].index((REF+timedelta(hours=130)).isoformat())
        casos = {}
        hueco = deepcopy(original)
        hueco['hourly']['precipitation'][indice] = None
        casos['valor_ausente'] = hueco
        duplicada = deepcopy(original)
        duplicada['hourly']['time'][indice] = duplicada['hourly']['time'][indice-1]
        casos['duplicado_con_hueco'] = duplicada
        extra = deepcopy(original)
        for nombre, valores in extra['hourly'].items():
            valores.insert(indice, valores[indice])
        casos['168_distintas_pero_169_filas'] = extra
        fraccion = deepcopy(original)
        fraccion['hourly']['time'][indice] = (REF+timedelta(hours=130,minutes=30)).isoformat()
        casos['168_distintas_no_contiguas'] = fraccion
        for nombre, datos in casos.items():
            with self.subTest(nombre=nombre):
                row = self.evaluar(datos)
                if nombre in {'duplicado_con_hueco','168_distintas_pero_169_filas'}:
                    self.assertFalse(row['cobertura_meteorologica'])
                    self.assertIn('duplicadas',row['error_datos'])
                else:
                    self.assertTrue(row['cobertura_meteorologica'])
                self.assertFalse(row['cobertura_pronostico_7d'])
                self.assertTrue(app.np.isnan(row['lluvia_futura_7d']))

    def test_duplicado_en_antecedentes_tambien_rechaza_toda_meteorologia(self):
        datos = datos_horarios(168)
        h = datos['hourly']
        indice = h['time'].index((REF-timedelta(hours=10)).isoformat())
        for valores in h.values():
            valores.insert(indice,valores[indice])
        row = self.evaluar(datos)
        self.assertEqual(row['nivel'],'SIN DATOS')
        self.assertFalse(row['cobertura_meteorologica'])
        self.assertFalse(row['cobertura_pronostico_7d'])
        self.assertTrue(app.np.isnan(row['indice']))
        self.assertTrue(app.np.isnan(row['lluvia_futura_72']))
        self.assertIn('duplicadas',row['error_datos'])

    def test_hidrologia_sigue_elevando_si_meteorologia_se_rechaza_por_duplicados(self):
        datos = datos_horarios(168)
        h=datos['hourly']
        for valores in h.values():
            valores.append(valores[-1])
        nodo = next(n for n in app.NODOS if n['localidad']=='Paso de los Libres')
        met = app.procesar_nodo(nodo,datos,referencia_historica=REF)
        now=app.ahora()
        p={'estaciones':{'paso de los libres':{'nombre':'Paso de los Libres','rio':'Uruguay',
           'altura_m':9.28,'alerta_m':7.5,'evacuacion_m':8.5,'fecha':now.isoformat(),
           'fuente':'https://alerta.ina.gob.ar/'}},'pronosticos':[],'represas':{},'avisos':[],'smn':{}}
        with patch.object(app.st,'session_state',{}):
            row=app.integrar_semaforo(app.pd.DataFrame([met]),panorama=p).iloc[0]
        self.assertEqual(row.nivel,'ROJO')
        self.assertFalse(row.cobertura_meteorologica)
        self.assertEqual(row.hidrologia_rango,2)

    def test_escorrentia_incompleta_no_se_disfraza_de_total_7d(self):
        datos = datos_horarios(168)
        datos['hourly']['runoff'][-1] = None
        row = self.evaluar(datos)
        self.assertTrue(row['cobertura_pronostico_7d'])
        self.assertEqual(row['lluvia_futura_7d'],168.0)
        self.assertTrue(app.np.isnan(row['runoff_futuro_7d']))

    def test_error_meteorologico_declara_7d_sin_cobertura(self):
        row = self.evaluar({'error':'Fuente no disponible'})
        self.assertFalse(row['cobertura_pronostico_7d'])
        self.assertTrue(app.np.isnan(row['lluvia_futura_7d']))


if __name__ == '__main__':
    unittest.main()
