"""La vista mínima retira cifras inciertas y no fabrica seguridad ni ceros."""
from copy import deepcopy
from datetime import datetime, timedelta
import unittest
from zoneinfo import ZoneInfo

from pagina_confiable import construir_filas, tarjeta_html, avisos_validos

NOW = datetime(2026, 11, 9, 21, 20, tzinfo=ZoneInfo('America/Argentina/Buenos_Aires'))
NODOS = [dict(localidad='Goya', provincia='Corrientes', lat=-29.14, lon=-59.26)]
INA = 'https://alerta.ina.gob.ar/a5/getSeriesBySiteAndVar?series_id=23'
CAP = 'https://ssl.smn.gob.ar/CAP/alerta_prueba.xml'


def modelo():
    return {**NODOS[0], 'lluvia_futura_24': 0., 'lluvia_futura_72': 2.,
            'cobertura_lluvia_futura_24': True, 'cobertura_lluvia_futura_72': True,
            'modelo_api': 'ecmwf_ifs025',
            'consulta_meteorologica': (NOW-timedelta(minutes=10)).isoformat(),
            'referencia_pronostico': NOW.replace(minute=0).isoformat(),
            'nivel': 'ROJO', 'indice': 99, 'accion': 'EVACUAR'}


def panorama():
    return {'estaciones': {'goya': {'nombre': 'Goya', 'altura_m': 5.98,
        'fecha': (NOW-timedelta(hours=1)).isoformat(), 'precision_fecha': 'fecha_hora_publicada',
        'fuente': INA, 'unidad': 'm'}},
        'smn': {'consultado': NOW, 'fecha_feed': NOW-timedelta(minutes=2), 'total': 0,
                'leidos': 0, 'fallos': 0, 'truncado': False, 'filas': [], 'error': ''}}


class PaginaConfiableTests(unittest.TestCase):
    def test_solo_valores_validos_y_cero_real_sin_indice_ni_acciones(self):
        filas = construir_filas(NODOS, [modelo()], panorama(), NOW)
        self.assertEqual(filas[0]['nivel'], 'DATOS')
        self.assertEqual([v['valor'] for v in filas[0]['valores']], [0., 2., 5.98])
        html = tarjeta_html(filas[0])
        self.assertIn('0,0 mm', html)
        self.assertIn('5,98 m', html)
        self.assertIn('21:00 ART', html)
        self.assertNotIn('99', html)
        self.assertNotIn('EVACUAR', html)
        self.assertNotIn('ROJO', html)
        self.assertNotIn('indice', filas[0])

    def test_cada_horizonte_exige_cobertura_propia(self):
        fila = modelo()
        del fila['cobertura_lluvia_futura_24']
        fila['cobertura_meteorologica'] = True
        resultado = construir_filas(NODOS, [fila], {}, NOW)[0]
        self.assertEqual([v['valor'] for v in resultado['valores']], [2.])

    def test_consulta_vencida_sin_zona_o_ausente_no_usa_actualizado(self):
        for consulta in (None, NOW.replace(tzinfo=None).isoformat(), (NOW-timedelta(hours=4)).isoformat()):
            with self.subTest(consulta=consulta):
                fila = modelo()
                fila.update(consulta_meteorologica=consulta, actualizado=NOW.strftime('%d/%m/%Y %H:%M'))
                resultado = construir_filas(NODOS, [fila], {}, NOW)[0]
                self.assertEqual(resultado['valores'], [])
                self.assertEqual(resultado['nivel'], 'SIN DATOS')
                self.assertNotIn('0,0 mm', tarjeta_html(resultado))

    def test_ventana_de_hora_anterior_no_se_publica_como_proximas_horas(self):
        fila = modelo()
        fila['referencia_pronostico'] = (NOW-timedelta(hours=1)).replace(minute=0).isoformat()
        self.assertEqual(construir_filas(NODOS, [fila], {}, NOW)[0]['valores'], [])

    def test_rio_sin_hora_futuro_o_antiguo_se_omite(self):
        for cambios in ({'precision_fecha': 'fecha_registro_fuente'}, {'precision_fecha': None},
                        {'fecha': NOW.date().isoformat()}, {'fecha': (NOW+timedelta(seconds=1)).isoformat()},
                        {'fecha': (NOW-timedelta(hours=7)).isoformat()}, {'unidad': 'cm'},
                        {'fuente': 'https://example.com/'}):
            with self.subTest(cambios=cambios):
                p = panorama()
                p['estaciones']['goya'].update(cambios)
                resultado = construir_filas(NODOS, [], p, NOW)[0]
                self.assertFalse(resultado['rio_disponible'])
                self.assertEqual(resultado['valores'], [])
                self.assertNotIn('5,98', tarjeta_html(resultado))

    def test_filas_duplicadas_no_acreditan_datos(self):
        self.assertEqual(construir_filas(NODOS, [modelo(), modelo()], {}, NOW)[0]['valores'], [])

    def test_cap_parcial_o_vencido_no_republica_aviso_posiblemente_cancelado(self):
        cap = panorama()['smn']
        cap.update(total=1, leidos=1, filas=[{'nodos': ['Goya'], 'status': 'Actual',
            'tipo_mensaje': 'Alert', 'url': CAP, 'titulo': 'Lluvia', 'emision': NOW-timedelta(minutes=30),
            'desde': NOW-timedelta(minutes=10), 'hasta': NOW+timedelta(hours=3)}])
        self.assertEqual(len(avisos_validos(cap, 'Goya', NOW)), 1)
        for cambio in ({'fallos': 1}, {'truncado': True}, {'leidos': 0},
                       {'consultado': NOW-timedelta(hours=2)}):
            parcial = deepcopy(cap)
            parcial.update(cambio)
            self.assertEqual(avisos_validos(parcial, 'Goya', NOW), [])
        cap['filas'][0]['hasta'] = NOW-timedelta(seconds=1)
        self.assertEqual(avisos_validos(cap, 'Goya', NOW), [])


if __name__ == '__main__':
    unittest.main()
