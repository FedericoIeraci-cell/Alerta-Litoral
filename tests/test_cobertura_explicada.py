"""Los faltantes fluviales no ocultan la meteorología ni se convierten en verde."""
from datetime import datetime
import unittest

import explicaciones_alerta as e
import hidrologia as h


class CoberturaExplicadaTests(unittest.TestCase):
    def test_localidades_sin_estacion_conservan_limitacion_y_meteorologia(self):
        ahora = datetime.fromisoformat('2026-10-26T19:34:25-03:00')
        for nombre in ('El Colorado', 'Posadas', 'Gualeguay', 'Chajarí'):
            with self.subTest(localidad=nombre):
                hidro = h.evaluar_localidad(nombre, {}, referencia=ahora)
                self.assertEqual(hidro['hidrologia_estacion'], 'Sin estación local vinculada')
                self.assertFalse(hidro['hidrologia_vigente'])
                self.assertFalse(hidro['hidrologia_pronostico_vigente'])
                row = dict(hidro, localidad=nombre, nivel='SIN DATOS',
                           nivel_meteorologico='VERDE', cobertura_meteorologica=True,
                           indice=20)
                detalle = e.construir_explicacion(row, {'semaforo': 'VERDE', 'motivos': []}, 35, 55)
                self.assertIn(nombre, detalle['motivo_principal'])
                self.assertIn('meteorología está disponible', detalle['motivo_principal'])
                self.assertIn('No hay estación local vinculada', detalle['motivo_principal'])
                self.assertIn('No hay pronóstico fluvial local', detalle['motivo_principal'])
                self.assertIn('no acreditan ausencia de riesgo', detalle['motivo_principal'])
                self.assertEqual(detalle['desencadenantes'], [])
                self.assertEqual(e.motivo_publico(dict(row, **detalle)), detalle['motivo_principal'])
                self.assertEqual(row['nivel'], 'SIN DATOS')

    def test_estacion_vinculada_con_lectura_no_verificada_no_se_presenta_como_ausente(self):
        row = dict(nivel='SIN DATOS', localidad='Corrientes', hidrologia_aplica=True,
                   hidrologia_estacion='Corrientes', hidrologia_vigente=False,
                   hidrologia_pronostico_vigente=True, cobertura_meteorologica=True,
                   nivel_meteorologico='VERDE')
        texto = e.motivo_publico(row)
        self.assertIn('lectura fluvial local está ausente, incompleta o sin vigencia verificada', texto)
        self.assertNotIn('No hay estación local vinculada', texto)
        self.assertNotIn('No hay pronóstico', texto)

    def test_meteorologia_incompleta_no_se_anuncia_disponible(self):
        texto = e.motivo_publico(dict(nivel='SIN DATOS', cobertura_meteorologica=False,
                                     nivel_meteorologico='VERDE'))
        self.assertIn('meteorología no tiene cobertura completa', texto)
        self.assertNotIn('meteorología está disponible', texto)


if __name__ == '__main__':
    unittest.main()
