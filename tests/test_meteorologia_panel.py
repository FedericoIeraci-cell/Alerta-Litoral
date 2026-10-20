"""La portada usa la captura vigente y conserva su consulta original."""
from datetime import timedelta
from unittest.mock import patch
import unittest

import app
from test_horizonte_7d import REF, datos_horarios
from meteorologia_compartida import crear_captura


class MeteorologiaPanelTests(unittest.TestCase):
    def setUp(self):
        self.now = REF.replace(tzinfo=app.TZ)
        self.nodes = [app.NODOS[0]]
        self.clock = patch.object(app, 'ahora', return_value=self.now)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        guard = patch('requests.sessions.Session.request', side_effect=AssertionError('HTTP prohibido'))
        guard.start()
        self.addCleanup(guard.stop)

    def test_captura_compartida_no_consulta_modelo_y_no_renueva_fecha(self):
        captura = crear_captura(self.nodes, [datos_horarios(168)], (self.now-timedelta(minutes=40)).isoformat())
        with patch.object(app, 'NODOS', self.nodes), \
             patch.object(app, 'obtener_captura_meteorologica_publica', return_value=captura), \
             patch.object(app, 'obtener_datos_ecmwf', side_effect=AssertionError('Consulta duplicada')):
            datos, info = app.obtener_meteorologia_publica()
            fila = app.procesar_nodo(self.nodes[0], datos[0], referencia_historica=REF)
            self.assertEqual(info['origen'], 'compartida')
            self.assertEqual(fila['consulta_meteorologica'], captura['consultado'])
            self.assertEqual(fila['lluvia_futura_72'], 72)
            self.assertEqual(fila['lluvia_futura_7d'], 168)
            self.assertEqual(fila['actualizado'], self.now.strftime('%d/%m/%Y %H:%M'))
            fila.update(nivel_meteorologico=fila['nivel'])
            detalle = app.construir_explicacion(fila, {'semaforo': fila['nivel']}, 35, 55)
            evidencia = next(e for e in detalle['evidencias_alerta'] if e['tipo'] == 'meteorologia')
            self.assertEqual(evidencia['fecha_consulta'], captura['consultado'])

    def test_captura_cacheada_vencida_o_invalida_no_se_reutiliza(self):
        captura = crear_captura(self.nodes, [datos_horarios(168)], self.now.isoformat())
        with patch.object(app, 'NODOS', self.nodes), \
             patch.object(app, 'obtener_captura_meteorologica_publica', return_value=captura), \
             patch.object(app, 'ahora', return_value=self.now+timedelta(hours=4)), \
             patch.object(app, 'obtener_datos_ecmwf', return_value=[{'error': 'HTTP 429'}]) as directa:
            datos, info = app.obtener_meteorologia_publica()
            self.assertEqual(datos, [{'error': 'HTTP 429'}])
            self.assertIsNone(info['consultado'])
            directa.assert_called_once_with()

    def test_raw_vencido_futuro_o_sin_zona_no_produce_indice(self):
        for consulta in (self.now-timedelta(hours=4), self.now+timedelta(minutes=6), REF):
            with self.subTest(consulta=consulta):
                raw = dict(datos_horarios(168), _ala_consultado=consulta.isoformat())
                fila = app.procesar_nodo(self.nodes[0], raw, referencia_historica=REF)
                self.assertFalse(fila['cobertura_meteorologica'])
                self.assertEqual(fila['nivel'], 'SIN DATOS')
                self.assertTrue(app.np.isnan(fila['lluvia_futura_72']))


if __name__ == '__main__':
    unittest.main()
