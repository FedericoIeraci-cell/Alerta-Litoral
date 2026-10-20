"""La curva conserva precisión diaria y el resumen regional mantiene los rojos."""
import os
from copy import deepcopy
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch

os.environ['ALA_MONITOR_HEADLESS'] = '1'
sys.path.insert(0, str(Path(__file__).parents[1]))
import app
import plotly.graph_objects as go
from test_portada_local import datos


class PortadaAppTests(unittest.TestCase):
    def setUp(self):
        red = patch('requests.sessions.Session.request', side_effect=AssertionError('Red y envíos prohibidos'))
        red.start()
        self.addCleanup(red.stop)

    def test_curva_de_parte_diario_no_inventa_hora_y_muestra_lectura_disponible(self):
        row, panorama = datos()
        ui = MagicMock()
        with patch.object(app, 'st', ui), patch.object(app, 'go', go):
            app.mostrar_pronostico_fluvial_local(row, panorama)
        self.assertEqual(ui.plotly_chart.call_count, 1)
        figura = ui.plotly_chart.call_args.args[0]
        self.assertEqual(figura.data[0].mode, 'markers')
        for fecha in figura.data[0].customdata.flatten():
            self.assertIn('hora no publicada', fecha)
            self.assertNotIn('00:00', fecha)
        self.assertTrue(any('Fecha del parte' in llamada.args[0] for llamada in ui.caption.call_args_list))

    def test_curva_distingue_registro_sin_hora_de_medicion_confirmada(self):
        for precision in ('fecha_registro_fuente', 'fecha_hora_publicada'):
            with self.subTest(precision=precision):
                fuente = 'https://alerta.ina.gob.ar/a5/getSeriesBySiteAndVar?series_id=19'
                fecha = '2026-10-20T15:45:00Z'
                row = {'hidrologia_estacion': 'Corrientes', 'hidrologia_evidencias': [
                    {'tipo': 'observacion_fluvial', 'precision_fecha': precision, 'fuente': fuente}]}
                panorama = {'estaciones': {'corrientes': {'nombre': 'Corrientes', 'fecha': fecha,
                    'fuente': fuente, 'precision_fecha': precision,
                    'observaciones': [{'fecha': fecha, 'valor': 7.27}]}}}
                original = deepcopy(panorama)
                ui = MagicMock()
                with patch.object(app, 'st', ui), patch.object(app, 'go', go):
                    app.mostrar_pronostico_fluvial_local(row, panorama)
                figura = ui.plotly_chart.call_args.args[0]
                traza = figura.data[0]
                if precision == 'fecha_registro_fuente':
                    self.assertEqual(traza.mode, 'markers')
                    self.assertEqual(figura.layout.xaxis.tickformat, '%d/%m/%Y')
                    self.assertEqual(traza.customdata[0][0],
                                     '20/10/2026 · hora de medición no confirmada')
                    self.assertEqual(traza.x[0].hour, 0)
                else:
                    self.assertEqual(traza.mode, 'lines+markers')
                    self.assertEqual(traza.customdata[0][0], '20/10/2026 12:45 ART')
                    self.assertEqual(traza.x[0].hour, 12)
                self.assertEqual(panorama, original)

    def test_alerta_regional_incluye_rojo_meteorologico_sin_umbral_fluvial_critico(self):
        row, panorama = datos()
        row.update(hidrologia_rango=0, smn_alertas='', smn_fuentes='')
        ui = MagicMock()
        ui.columns.return_value = [MagicMock() for _ in range(4)]
        with patch.object(app, 'st', ui):
            app.mostrar_vigilancia_inundaciones(app.pd.DataFrame([row]), panorama)
        self.assertTrue(any('Atención prioritaria en Corrientes' in llamada.args[0] for llamada in ui.error.call_args_list))
        self.assertFalse(any('Preparar / coordinar evacuación' in str(llamada) for llamada in ui.mock_calls))


if __name__ == '__main__':
    unittest.main()
