"""El ensamble histórico conserva el contrato de unidades del motor actual."""
from copy import deepcopy
import unittest
from unittest.mock import patch
from test_horizonte_7d import app, REF, datos_horarios


class HistoricosUnidadesTests(unittest.TestCase):
    def fuente(self):
        return dict(datos_horarios(72), timezone='America/Argentina/Buenos_Aires', utc_offset_seconds=-10800)

    def test_ensamble_valido_preserva_unidades_y_puede_evaluarse(self):
        raw = self.fuente()
        original = deepcopy(raw)
        ensamblada = app.ensamblar_corrida_historica(raw, raw, REF.replace(tzinfo=app.TZ))
        self.assertEqual(ensamblada['hourly_units'], raw['hourly_units'])
        self.assertEqual(ensamblada['timezone'], raw['timezone'])
        self.assertEqual(ensamblada['utc_offset_seconds'], -10800)
        fila = app.procesar_nodo(app.NODOS[0], ensamblada, referencia_historica=REF)
        self.assertTrue(fila['cobertura_meteorologica'])
        self.assertEqual(fila['lluvia_futura_72'], 72.)
        self.assertEqual(raw, original)

    def test_unidad_ausente_o_incompatible_no_se_inventa(self):
        for cambio in ('ausente', 'incompatible', 'zona'):
            anterior, futura = self.fuente(), self.fuente()
            if cambio == 'ausente':
                del futura['hourly_units']['soil_moisture_0_to_7cm']
            elif cambio == 'incompatible':
                futura['hourly_units']['soil_moisture_0_to_7cm'] = '%'
            else:
                futura['utc_offset_seconds'] = 0
            with self.subTest(cambio=cambio), self.assertRaises(ValueError):
                app.ensamblar_corrida_historica(anterior, futura, REF.replace(tzinfo=app.TZ))

    def test_diagnostico_vera_conserva_lluvia_aunque_runoff_no_este_disponible(self):
        raw = dict(datos_horarios(168), timezone='America/Argentina/Buenos_Aires', utc_offset_seconds=-10800)
        desplazamiento = app.pd.Timestamp('2025-05-24T03:00:00') - app.pd.Timestamp(REF)
        raw['hourly']['time'] = [(app.pd.Timestamp(t) + desplazamiento).isoformat() for t in raw['hourly']['time']]
        raw['hourly']['runoff'] = [None] * len(raw['hourly']['time'])
        with patch.object(app, 'copia_ficha', return_value=raw):
            resultado = app.reconstruir_vera()
        self.assertEqual(len(resultado), 3)
        self.assertTrue(resultado['indice'].isna().all())
        self.assertEqual(resultado['lluvia_prevista_72_mm'].tolist(), [72., 72., 72.])
        self.assertTrue(resultado['escorrentia_previa_72_mm'].isna().all())


if __name__ == '__main__':
    unittest.main()
