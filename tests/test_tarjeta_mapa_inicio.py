"""Cifras originales visibles en el nodo sin rejuvenecer fuentes."""
from copy import deepcopy
from datetime import datetime, timedelta
import unittest
from zoneinfo import ZoneInfo

from mapa_inicio import crear_tarjeta_mapa


AHORA = datetime(2026, 10, 20, 13, 33, tzinfo=ZoneInfo('America/Argentina/Buenos_Aires'))
FUENTE = 'https://alerta.ina.gob.ar/pub/datos/series/30'


def fila():
    return {'localidad': 'Santa Fe', 'provincia': 'Santa Fe', 'nivel': 'SIN DATOS',
            'indice': None, 'cobertura_meteorologica': False,
            'cobertura_lluvia_futura_24': True, 'cobertura_lluvia_futura_72': True,
            'cobertura_pronostico_7d': False, 'lluvia_futura_24': 0.0,
            'lluvia_futura_72': 12.3, 'consulta_meteorologica': '2026-10-20T13:29:00-03:00',
            'actualizado': '20/10/2026 13:30', 'referencia_pronostico': '2026-10-20T13:00:00-03:00',
            'fecha_evaluacion_integrada': AHORA.isoformat(), 'hidrologia_aplica': True,
            'hidrologia_estacion': 'Santa Fe', 'hidrologia_vigente': True,
            'hidrologia_fecha': '2026-10-20T13:00:00-03:00',
            'evidencias_alerta': [{'tipo': 'observacion_fluvial', 'verificada': True, 'vigente': True,
                                   'altura_m': 0.0, 'fecha_observada': '2026-10-20T13:00:00-03:00',
                                   'precision_fecha': 'fecha_hora_publicada', 'fuente': FUENTE,
                                   'estacion': 'Santa Fe'}], 'desencadenantes': []}


def tarjeta(row, panorama=None):
    return crear_tarjeta_mapa(lambda x: '<b>Santa Fe</b><br>Detalle original', AHORA, panorama)(row)


class TarjetaMapaInicioTest(unittest.TestCase):
    def test_ficha_completa_conserva_evidencias_y_ubica_cifras_despues_del_titulo(self):
        from explicaciones_alerta import tarjeta_nodo_html
        row = fila()
        original = tarjeta_nodo_html(row)
        result = crear_tarjeta_mapa(tarjeta_nodo_html, AHORA)(row)
        self.assertIn('Evidencias, vigencia y fuentes consultables', result)
        self.assertLess(result.index('</h3>'), result.index('12,3 mm'))
        self.assertLess(result.index('12,3 mm'), result.index('Por qué tiene este color'))
        # Quitar sólo el bloque nuevo debe recuperar la ficha completa byte a byte.
        import re
        self.assertEqual(re.sub(r'<p class="ala-mapa-cifras">.*?</p>', '', result, count=1), original)

    def test_lluvia_independiente_y_altura_cero_validas_visibles_antes_del_detalle(self):
        row = fila()
        original = deepcopy(row)
        result = tarjeta(row)
        self.assertIn('Lluvia prevista · 24 h:</b> 0,0 mm', result)
        self.assertIn('Lluvia prevista · 72 h:</b> 12,3 mm', result)
        self.assertIn('Río observado · Santa Fe:</b> 0,00 m', result)
        self.assertIn('20/10/2026 13:00 ART', result)
        self.assertLess(result.index('12,3 mm'), result.index('Detalle original'))
        self.assertEqual(row, original)

    def test_ventana_parcial_no_se_muestra_como_acumulado_completo(self):
        row = fila()
        row['cobertura_lluvia_futura_72'] = False
        result = tarjeta(row)
        self.assertIn('Lluvia prevista · 72 h:</b> Sin dato completo vigente', result)
        self.assertNotIn('12,3 mm', result)

    def test_calculo_nuevo_no_renueva_lluvia_antigua(self):
        row = fila()
        row['consulta_meteorologica'] = (AHORA-timedelta(hours=13)).isoformat()
        result = tarjeta(row)
        self.assertIn('Lluvia prevista · 24 h:</b> Sin dato completo vigente', result)
        self.assertNotIn('12,3 mm', result)

    def test_lectura_antigua_con_bandera_vigente_no_se_presenta_como_actual(self):
        row = fila()
        vieja = (AHORA-timedelta(hours=49)).isoformat()
        row['hidrologia_fecha'] = vieja
        row['evidencias_alerta'][0]['fecha_observada'] = vieja
        result = tarjeta(row)
        self.assertIn('antecedente de Santa Fe', result)
        self.assertIn('0,00 m · sin vigencia actual confirmada', result)
        self.assertNotIn('Río observado · Santa Fe:</b> 0,00 m', result)

    def test_fuente_diaria_no_inventa_hora_publicada(self):
        row = fila()
        row['hidrologia_fecha'] = '2026-10-19T00:00:00-03:00'
        row['evidencias_alerta'][0].update(fecha_observada=row['hidrologia_fecha'],
            fuente='https://alerta.ina.gob.ar/a5/diario/reporte_diario')
        result = tarjeta(row)
        self.assertIn('19/10/2026 · hora no publicada', result)
        self.assertNotIn('19/10/2026 00:00', result)
        self.assertIn('sin vigencia actual confirmada', result)

    def test_antecedente_del_panorama_conserva_valor_y_fuente_original(self):
        row = fila()
        row['evidencias_alerta'] = []
        row['hidrologia_vigente'] = False
        panorama = {'estaciones': {'santa fe': {'nombre': 'Santa Fe', 'altura_m': 2.35,
                        'fecha': '2026-10-18T10:00:00-03:00', 'fuente': FUENTE}}}
        result = tarjeta(row, panorama)
        self.assertIn('2,35 m · sin vigencia actual confirmada', result)
        self.assertIn('18/10/2026 10:00 ART', result)

    def test_lectura_futura_no_se_publica_como_lectura_actual(self):
        row = fila()
        futura = (AHORA+timedelta(hours=1)).isoformat()
        row['hidrologia_fecha'] = futura
        row['evidencias_alerta'][0]['fecha_observada'] = futura
        result = tarjeta(row)
        self.assertIn('Sin lectura vigente', result)
        self.assertNotIn('0,00 m', result)


if __name__ == '__main__':
    unittest.main()
