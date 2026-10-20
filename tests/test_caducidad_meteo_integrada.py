"""La fila cacheada vence por consulta original y conserva alertas fluviales."""
from copy import deepcopy
from datetime import timedelta
import os
import unittest
from unittest.mock import patch

os.environ['ALA_MONITOR_HEADLESS'] = '1'
import app
from test_horizonte_7d import REF, datos_horarios


class CaducidadMeteorologiaIntegradaTests(unittest.TestCase):
    def setUp(self):
        self.now = REF.replace(tzinfo=app.TZ)
        self.clock = patch.object(app, 'ahora', return_value=self.now).start()
        patch.object(app.st, 'session_state', {}).start()
        patch.object(app, 'componentes_localidad', return_value=(None, None, None, None)).start()
        patch('requests.sessions.Session.request', side_effect=AssertionError('HTTP prohibido')).start()
        self.addCleanup(patch.stopall)

    def fila(self, localidad):
        nodo = next(n for n in app.NODOS if n['localidad'] == localidad)
        consulta = (self.now - timedelta(hours=2, minutes=59)).isoformat()
        raw = dict(datos_horarios(168), _ala_consultado=consulta, _ala_origen='compartida')
        row = app.procesar_nodo(nodo, raw, referencia_historica=REF)
        self.assertTrue(row['cobertura_meteorologica'])
        return row

    def evaluar(self, row, panorama=None):
        return app.integrar_semaforo(app.pd.DataFrame([row]), panorama=panorama or {}).iloc[0]

    def test_fila_cacheada_cruza_tres_horas_sin_rejuvenecer_ni_acreditar_verde(self):
        row = self.fila('Rafaela')
        original = deepcopy(row)
        self.assertNotEqual(app.fusion_territorial(row)['semaforo'], 'SIN DATOS')
        self.clock.return_value = self.now + timedelta(minutes=2)
        self.assertEqual(app.fusion_territorial(row)['semaforo'], 'SIN DATOS')
        evaluada = self.evaluar(row)
        self.assertFalse(evaluada.cobertura_meteorologica)
        self.assertEqual(evaluada.nivel_meteorologico, 'SIN DATOS')
        self.assertEqual(evaluada.nivel, 'SIN DATOS')
        self.assertFalse(any(e['tipo'] == 'meteorologia' for e in evaluada.evidencias_alerta))
        self.assertEqual(evaluada.consulta_meteorologica, original['consulta_meteorologica'])
        self.assertEqual(evaluada.actualizado, original['actualizado'])

    def test_meteorologia_vencida_no_borra_rojo_fluvial_vigente(self):
        row = self.fila('Paso de los Libres')
        self.clock.return_value = self.now + timedelta(minutes=2)
        fuente = 'https://www.ina.gov.ar/alerta/index.php?seccion=6'
        panorama = {'fuentes': [{'id': 'ina_diario', 'url': fuente, 'estado': 'VIGENTE',
                                  'fecha_documento': self.now.isoformat()}],
                    'estaciones': {'paso de los libres': {
                        'nombre': 'Paso de los Libres', 'rio': 'Uruguay', 'altura_m': 9.28,
                        'alerta_m': 7.5, 'evacuacion_m': 8.5,
                        'fecha': self.now.isoformat(), 'fuente': fuente}},
                    'pronosticos': [], 'represas': {}, 'avisos': []}
        evaluada = self.evaluar(row, panorama)
        self.assertFalse(evaluada.cobertura_meteorologica)
        self.assertEqual(evaluada.nivel_meteorologico, 'SIN DATOS')
        self.assertEqual(evaluada.nivel, 'ROJO')
        self.assertEqual(evaluada.hidrologia_rango, 2)
        self.assertTrue(any(e['tipo'] == 'observacion_fluvial' for e in evaluada.evidencias_alerta))
        self.assertFalse(any(e['tipo'] == 'meteorologia' for e in evaluada.evidencias_alerta))

    def test_fila_legada_conserva_control_de_calculo_sin_inventar_consulta(self):
        row = self.fila('Rafaela')
        row.pop('consulta_meteorologica')
        self.clock.return_value = self.now + timedelta(hours=4)
        self.assertNotEqual(app.fusion_territorial(row)['semaforo'], 'SIN DATOS')
        self.clock.return_value = self.now + timedelta(hours=13)
        self.assertEqual(app.fusion_territorial(row)['semaforo'], 'SIN DATOS')


if __name__ == '__main__':
    unittest.main()
