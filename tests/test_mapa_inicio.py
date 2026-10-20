"""Primera pintura del mapa y conservación de referencias sin resultado."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from mapa_inicio import completar_catalogo_mapa, mostrar_mapa_principal


CATALOGO = [
    {'localidad': 'Punto A', 'provincia': 'Buenos Aires', 'lat': -34.1, 'lon': -59.0},
    {'localidad': 'Punto B', 'provincia': 'Buenos Aires', 'lat': -34.2, 'lon': -59.1},
]


class Vista:
    def __init__(self):
        self.eventos = []
    def html(self, value, **kwargs):
        self.eventos.append(('mapa', value, kwargs))
    def caption(self, value):
        self.eventos.append(('caption', value))
    def markdown(self, value):
        self.eventos.append(('resumen', value))


class MapaInicioTest(unittest.TestCase):
    def test_catalogo_sin_fuentes_permanece_completo_y_gris(self):
        datos = completar_catalogo_mapa(CATALOGO)
        self.assertEqual([x['localidad'] for x in datos], ['Punto A', 'Punto B'])
        self.assertTrue(all(x['nivel'] == 'SIN DATOS' and x['indice'] is None for x in datos))
        self.assertTrue(all('actualizado' not in x for x in datos))

    def test_resultado_parcial_preserva_valores_cero_y_nodos_ausentes(self):
        fila = {**CATALOGO[0], 'nivel': 'ROJO', 'lluvia_futura_24': 0.0, 'actualizado': '20/10/2026 13:30'}
        original = deepcopy(fila)
        datos = completar_catalogo_mapa(CATALOGO, [fila])
        self.assertEqual(datos[0]['nivel'], 'ROJO')
        self.assertEqual(datos[0]['lluvia_futura_24'], 0.0)
        self.assertEqual(datos[1]['nivel'], 'SIN DATOS')
        self.assertNotIn('lluvia_futura_24', datos[1])
        self.assertEqual(fila, original)

    def test_respuesta_no_desplaza_catalogo_ni_duplica_puntos(self):
        fila = {**CATALOGO[0], 'lat': -20.0, 'lon': -40.0, 'nivel': 'AMARILLO'}
        datos = completar_catalogo_mapa(CATALOGO, [fila])
        self.assertEqual((datos[0]['lat'], datos[0]['lon']), (-34.1, -59.0))
        ambiguos = completar_catalogo_mapa(CATALOGO, [fila, fila])
        self.assertEqual(len(ambiguos), 2)
        self.assertEqual(ambiguos[0]['nivel'], 'SIN DATOS')

    def test_mapa_es_primera_salida_sin_esperar_ninguna_fuente(self):
        vista = Vista()
        with patch('mapa_litoral._leer_leaflet', return_value=('', '')):
            datos = mostrar_mapa_principal(vista, CATALOGO, componentes=vista)
        self.assertEqual(vista.eventos[0][0], 'mapa')
        self.assertEqual(vista.eventos[0][2]['height'], 440)
        self.assertIn('Consultando las fuentes', vista.eventos[1][1])
        self.assertEqual(len(datos), 2)
        self.assertIn('Punto A', vista.eventos[0][1])
        self.assertIn('Punto B', vista.eventos[0][1])

    def test_dibujo_final_conserva_detalle_y_resumen_de_niveles(self):
        vista = Vista()
        rows = [{**CATALOGO[0], 'nivel': 'ROJO'}, {**CATALOGO[1], 'nivel': 'SIN DATOS'}]
        with patch('mapa_litoral._leer_leaflet', return_value=('', '')):
            mostrar_mapa_principal(vista, CATALOGO, rows, componentes=vista,
                                   tarjeta_nodo=lambda r: '<b>Detalle ' + r['localidad'] + '</b>')
        self.assertEqual(vista.eventos[0][0], 'mapa')
        self.assertIn('Detalle Punto A', vista.eventos[0][1])
        self.assertIn('Atención prioritaria: 1', vista.eventos[1][1])
        self.assertIn('Información insuficiente: 1', vista.eventos[1][1])


if __name__ == '__main__':
    unittest.main()
