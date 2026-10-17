"""El pronóstico diario parcial conserva sus días sin abortar la interfaz."""
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

os.environ['ALA_MONITOR_HEADLESS'] = '1'
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app


class PronosticoParcialUITests(unittest.TestCase):
    def test_de_uno_a_cinco_dias_se_muestran_sin_slider_invalido(self):
        for dias in (1, 2, 3, 4, 5):
            with self.subTest(dias=dias):
                ui = Mock(spec=['caption', 'slider'])
                ui.slider.side_effect = AssertionError('Un rango corto no necesita selector')
                with patch.object(app, 'st', ui):
                    horizonte = app.seleccionar_horizonte_pronostico(dias)
                self.assertEqual(horizonte, dias)
                self.assertIn(str(dias), ui.caption.call_args.args[0])

    def test_horizonte_largo_permite_elegir_sin_superar_datos_disponibles(self):
        for dias in (6, 15):
            with self.subTest(dias=dias):
                ui = Mock(spec=['caption', 'slider'])
                def seleccionar(label, *, min_value, max_value, value, key):
                    self.assertLess(min_value, max_value)
                    self.assertEqual(max_value, dias)
                    self.assertEqual(value, dias)
                    return 5
                ui.slider.side_effect = seleccionar
                with patch.object(app, 'st', ui):
                    self.assertEqual(app.seleccionar_horizonte_pronostico(dias), 5)


if __name__ == '__main__':
    unittest.main()
