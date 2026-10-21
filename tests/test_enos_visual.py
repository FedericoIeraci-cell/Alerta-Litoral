"""El color muestra el signo del RONI sin declarar fases ni rellenar huecos."""
import unittest
import pandas as pd

from enos_visual import tarjeta_roni, grafico_roni


class EnosVisualTests(unittest.TestCase):
    def test_valores_invalidos_no_se_presentan_como_anomalia_negativa_o_cero(self):
        for value in [None, float('nan'), float('inf'), True, '<script>']:
            with self.subTest(value=value):
                html = tarjeta_roni(value)
                self.assertIn('Sin dato', html)
                self.assertNotIn('<script>', html)
                self.assertNotIn('Anomalía negativa', html)
                self.assertNotIn('Anomalía cero', html)

    def test_grafico_conserva_signo_valores_y_trimestre_aun_dentro_de_zona_neutra(self):
        frame = pd.DataFrame({'fecha': pd.to_datetime(['2026-01-15', '2026-03-15', '2026-04-15']),
                              'RONI': [-.2, .1, 0], 'trimestre': ['DJF', 'FMA', 'MAM'],
                              'año': [2026, 2026, 2026]})
        fig = grafico_roni(frame)
        self.assertEqual(list(fig.data[0].y), [-.2, .1, 0])
        self.assertEqual(list(fig.data[0].marker.color), ['#2563eb', '#dc2626', '#64748b'])
        self.assertEqual(len(fig.data[0].x), 3, 'El mes ausente no se rellena con cero')
        self.assertEqual(fig.data[0].customdata.tolist(), [['DJF', 2026], ['FMA', 2026], ['MAM', 2026]])
        self.assertIn('Anomalía positiva', tarjeta_roni(.1))
        self.assertNotIn('El Niño', tarjeta_roni(.1))


if __name__ == '__main__':
    unittest.main()
