"""Exportar el horizonte real de lluvia sin completarlo ni cambiar el índice."""
from copy import deepcopy
from datetime import timedelta
import unittest
from unittest.mock import patch

from test_horizonte_7d import app, REF, datos_horarios


class HorizonteExtendidoTests(unittest.TestCase):
    def setUp(self):
        red = patch('requests.sessions.Session.request', side_effect=AssertionError('Red prohibida'))
        red.start()
        self.addCleanup(red.stop)

    def evaluar(self, datos):
        return app.procesar_nodo(app.NODOS[0], datos, referencia_historica=REF)

    def test_fin_real_de_12_dias_se_exporta_sin_cambiar_el_indice(self):
        row = self.evaluar(datos_horarios(288))
        corta = self.evaluar(datos_horarios(72))
        self.assertTrue(row['cobertura_pronostico_extendido'])
        self.assertEqual(row['lluvia_futura_extendida'], 288.0)
        self.assertEqual(row['pronostico_extendido_horas'], 288)
        self.assertEqual(row['pronostico_extendido_hasta'], '2026-10-27T12:00:00-03:00')
        self.assertEqual(row['referencia_pronostico'], '2026-10-15T12:00:00-03:00')
        self.assertEqual(row['indice'], corta['indice'])
        self.assertEqual(row['nivel'], corta['nivel'])
        self.assertEqual(row['lluvia_futura_72'], corta['lluvia_futura_72'])

    def test_maximo_de_360_horas_conserva_cero_real(self):
        datos = datos_horarios(384)
        datos['hourly']['precipitation'] = [0.0] * len(datos['hourly']['time'])
        row = self.evaluar(datos)
        self.assertTrue(row['cobertura_pronostico_extendido'])
        self.assertEqual(row['pronostico_extendido_horas'], 360)
        self.assertEqual(row['lluvia_futura_extendida'], 0.0)

    def test_hueco_nan_duplicado_o_marca_fraccionada_no_inventan_total(self):
        original = datos_horarios(288)
        indice = original['hourly']['time'].index((REF+timedelta(hours=240)).isoformat())
        for caso in ('hora_ausente', 'lluvia_ausente', 'duplicado', 'fraccion'):
            datos = deepcopy(original)
            if caso == 'hora_ausente':
                for valores in datos['hourly'].values():
                    del valores[indice]
            elif caso == 'lluvia_ausente':
                datos['hourly']['precipitation'][indice] = None
            elif caso == 'duplicado':
                datos['hourly']['time'][indice] = datos['hourly']['time'][indice-1]
            else:
                datos['hourly']['time'][indice] = (REF+timedelta(hours=240, minutes=30)).isoformat()
            with self.subTest(caso=caso):
                row = self.evaluar(datos)
                self.assertFalse(row['cobertura_pronostico_extendido'])
                self.assertIsNone(row['lluvia_futura_extendida'])

    def test_error_de_fuente_no_produce_cero_ni_fecha_futura(self):
        row = self.evaluar({'error': 'Sin fuente'})
        self.assertFalse(row['cobertura_pronostico_extendido'])
        self.assertIsNone(row['lluvia_futura_extendida'])
        self.assertIsNone(row['pronostico_extendido_hasta'])
        self.assertEqual(row['pronostico_extendido_horas'], 0)


if __name__ == '__main__':
    unittest.main()
