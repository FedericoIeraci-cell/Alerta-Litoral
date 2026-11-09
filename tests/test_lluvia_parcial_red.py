"""La cobertura individual de lluvia es independiente de la del índice."""
from copy import deepcopy
import unittest

from test_red_integrada import NOW, ala_frame, build_fixture, node_of


class LluviaParcialRedTests(unittest.TestCase):
    def test_conserva_ventanas_verificadas_sin_completar_indice_ni_otros_campos(self):
        records = ala_frame().to_dict("records")
        records[0].update(cobertura_meteorologica=False, estado_datos="INCOMPLETO",
                          nivel="SIN DATOS", nivel_meteorologico="SIN DATOS",
                          cobertura_lluvia_futura_24=True, cobertura_lluvia_futura_72=True,
                          cobertura_pronostico_7d=False, lluvia_futura_24=0.0,
                          lluvia_futura_72=42.5, lluvia_futura_7d=80.0,
                          error_datos="Faltan capas de humedad para el índice")
        original = deepcopy(records)
        node = node_of(build_fixture(frame=records), "Litoral", records[0]["localidad"])
        self.assertEqual(node["lluvia_prevista_24_mm"], 0.0)
        self.assertEqual(node["lluvia_prevista_72_mm"], 42.5)
        self.assertIsNone(node["lluvia_prevista_7d_mm"])
        self.assertIsNone(node["indice_experimental"])
        self.assertIsNone(node["humedad_0_7_m3_m3"])
        self.assertIsNone(node["escurrimiento_previsto_72_mm"])
        self.assertIn("Faltan capas", node["error"])
        self.assertEqual(records, original)

    def test_bandera_individual_requiere_true_y_numero_valido(self):
        for bandera, valor in ((False, 10.0), (None, 10.0), ("true", 10.0),
                               (True, None), (True, -1.0), (True, float("nan"))):
            with self.subTest(bandera=bandera, valor=valor):
                records = ala_frame().to_dict("records")
                records[0].update(cobertura_meteorologica=False, estado_datos="INCOMPLETO",
                                  cobertura_lluvia_futura_72=bandera, lluvia_futura_72=valor)
                node = node_of(build_fixture(frame=records), "Litoral", records[0]["localidad"])
                self.assertIsNone(node["lluvia_prevista_72_mm"])

    def test_false_explicito_prevalece_sobre_estado_ok_heredado(self):
        records = ala_frame().to_dict("records")
        records[0]["cobertura_meteorologica"] = False
        records[0]["estado_datos"] = "OK"
        node = node_of(build_fixture(frame=records), "Litoral", records[0]["localidad"])
        self.assertIsNone(node["indice_experimental"])
        self.assertIsNone(node["lluvia_prevista_72_mm"])


if __name__ == "__main__":
    unittest.main()
