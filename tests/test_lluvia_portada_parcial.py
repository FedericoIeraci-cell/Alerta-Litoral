"""Una variable ausente no elimina las ventanas completas de lluvia."""
from copy import deepcopy
from datetime import datetime, timedelta
import unittest
from zoneinfo import ZoneInfo

from portada_local import construir_portada_local


AHORA = datetime(2026, 10, 20, 13, 33, tzinfo=ZoneInfo('America/Argentina/Buenos_Aires'))


def fila(**cambios):
    row = {'localidad': 'Vera', 'provincia': 'Santa Fe', 'nivel': 'SIN DATOS',
           'nivel_meteorologico': 'SIN DATOS', 'indice': None,
           'cobertura_meteorologica': False, 'estado_datos': 'INCOMPLETO',
           'error_datos': 'Escurrimiento sin dato', 'actualizado': '20/10/2026 13:30',
           'consulta_meteorologica': '2026-10-20T13:29:00-03:00',
           'referencia_pronostico': '2026-10-20T13:00:00-03:00',
           'fecha_evaluacion_integrada': AHORA.isoformat(),
           'lluvia_futura_24': 12.3, 'lluvia_futura_72': 31.2,
           'lluvia_futura_7d': 45.0, 'cobertura_lluvia_futura_24': True,
           'cobertura_lluvia_futura_72': True, 'cobertura_pronostico_7d': True,
           'evidencias_alerta': [], 'desencadenantes': []}
    row.update(cambios)
    return row


def cifras(row):
    datos = construir_portada_local(row, referencia=AHORA)
    return datos, {x['id']: x['valor'] for x in datos['cifras']}


class LluviaPortadaParcialTest(unittest.TestCase):
    def test_falta_runoff_conserva_lluvia_y_no_completa_indice(self):
        row = fila()
        original = deepcopy(row)
        datos, valores = cifras(row)
        self.assertEqual(valores['lluvia24'], '12,3 mm')
        self.assertEqual(valores['lluvia72'], '31,2 mm')
        self.assertEqual(datos['nivel'], 'SIN DATOS')
        self.assertIn('índice meteorológico incompleto', datos['bloques'][0]['estado'])
        self.assertIn('Próximos 7 días: 45,0 mm', datos['bloques'][0]['datos'])
        self.assertFalse(any(x.startswith('Lluvia prevista') for x in datos['faltantes']))
        self.assertEqual(row, original)

    def test_cero_completo_se_muestra_y_ventana_incompleta_no(self):
        _, valores = cifras(fila(lluvia_futura_24=0.0, cobertura_lluvia_futura_72=False))
        self.assertEqual(valores['lluvia24'], '0,0 mm')
        self.assertEqual(valores['lluvia72'], 'Sin dato completo vigente')

    def test_valor_parcial_o_sin_bandera_no_se_presenta_como_total_completo(self):
        row = fila(cobertura_lluvia_futura_24=False)
        row.pop('cobertura_lluvia_futura_72')
        _, valores = cifras(row)
        self.assertEqual(valores['lluvia24'], 'Sin dato completo vigente')
        self.assertEqual(valores['lluvia72'], 'Sin dato completo vigente')

    def test_calculo_nuevo_no_renueva_consulta_antigua(self):
        datos, valores = cifras(fila(consulta_meteorologica=(AHORA-timedelta(hours=13)).isoformat()))
        self.assertEqual(valores['lluvia24'], 'Sin dato completo vigente')
        self.assertEqual(valores['lluvia72'], 'Sin dato completo vigente')
        self.assertFalse(any('Próximos 7 días: 45,0 mm' == x for x in datos['bloques'][0]['datos']))

    def test_consulta_futura_o_sin_fecha_no_afirma_vigencia(self):
        for fecha in [(AHORA+timedelta(minutes=10)).isoformat(), 'fecha inválida']:
            with self.subTest(fecha=fecha):
                _, valores = cifras(fila(consulta_meteorologica=fecha))
                self.assertEqual(valores['lluvia24'], 'Sin dato completo vigente')

    def test_fila_heredada_completa_conserva_visibilidad(self):
        row = fila(cobertura_meteorologica=True)
        row.pop('cobertura_lluvia_futura_24')
        row.pop('cobertura_lluvia_futura_72')
        _, valores = cifras(row)
        self.assertEqual(valores['lluvia24'], '12,3 mm')
        self.assertEqual(valores['lluvia72'], '31,2 mm')


if __name__ == '__main__':
    unittest.main()
