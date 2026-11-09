"""No fabricar cobertura, lluvia nula ni actualidad para la ficha del TP."""
from datetime import datetime, timedelta
import unittest
from ficha_proyecto import ART, construir_inventario, horizontes_lluvia

NOW = datetime(2026, 10, 15, 10, tzinfo=ART)


class FichaProyectoTests(unittest.TestCase):
    def row(self, **cambios):
        row = {"actualizado": "15/10/2026 10:00", "cobertura_meteorologica": True,
               "lluvia_futura_24": 0, "lluvia_futura_72": 2, "lluvia_futura_7d": 10,
               "humedad_suelo_promedio": .34}
        return dict(row, **cambios)

    def test_extendida_necesita_siete_dias_no_suma_parcial(self):
        dias = [(NOW.date() + timedelta(days=i)).isoformat() for i in range(8, 15)]
        raw = {"daily": {"time": dias, "precipitation_sum": [1] * 6 + [None]}}
        self.assertIsNone(horizontes_lluvia(self.row(), raw, NOW)[-1]["precipitacion_mm"])
        raw["daily"]["precipitation_sum"][-1] = 0
        self.assertEqual(horizontes_lluvia(self.row(), raw, NOW)[-1]["precipitacion_mm"], 6)

    def test_fechas_duplicadas_no_acreditan_cobertura(self):
        dias = [(NOW.date() + timedelta(days=i)).isoformat() for i in range(8, 15)]
        raw = {"daily": {"time": dias + [dias[0]], "precipitation_sum": [1] * 8}}
        self.assertIsNone(horizontes_lluvia(self.row(), raw, NOW)[-1]["precipitacion_mm"])

    def test_consulta_antigua_no_se_renueva_por_abrir_panel(self):
        row = self.row(actualizado="14/10/2026 10:00")
        self.assertTrue(all(r["precipitacion_mm"] is None for r in horizontes_lluvia(row, referencia=NOW)))

    def test_cobertura_72h_no_acredita_siete_dias(self):
        parcial = horizontes_lluvia(self.row(lluvia_futura_7d=72), referencia=NOW)
        self.assertEqual(parcial[1]["precipitacion_mm"], 2)
        self.assertIsNone(parcial[2]["precipitacion_mm"])
        completa = horizontes_lluvia(self.row(lluvia_futura_7d=168, cobertura_pronostico_7d=True), referencia=NOW)
        self.assertEqual(completa[2]["precipitacion_mm"], 168)

    def test_respuesta_fresca_pero_incompleta_no_acredita_cero_lluvia(self):
        row = self.row(cobertura_meteorologica=False, lluvia_futura_24=0, lluvia_futura_72=0)
        self.assertTrue(all(r["precipitacion_mm"] is None for r in horizontes_lluvia(row, referencia=NOW)))

    def test_lluvia_completa_independiente_se_conserva_sin_indice(self):
        row = self.row(cobertura_meteorologica=False,
                       cobertura_lluvia_futura_24=True, cobertura_lluvia_futura_72=False,
                       cobertura_pronostico_7d=True,
                       consulta_meteorologica=NOW.isoformat())
        horizontes = horizontes_lluvia(row, referencia=NOW)
        self.assertEqual(horizontes[0]["precipitacion_mm"], 0)
        self.assertIsNone(horizontes[1]["precipitacion_mm"])
        self.assertEqual(horizontes[2]["precipitacion_mm"], 10)

    def test_calculo_reciente_no_renueva_consulta_modelada_vencida(self):
        consulta = (NOW - timedelta(hours=13)).isoformat()
        row = self.row(consulta_meteorologica=consulta, cobertura_pronostico_7d=True)
        self.assertTrue(all(r["precipitacion_mm"] is None for r in horizontes_lluvia(row, referencia=NOW)))
        inventario = {r["id"]: r for r in construir_inventario(row, referencia=NOW)}
        self.assertEqual(inventario["lluvia_modelada"]["estado"], "Incompleto o vencido")
        self.assertEqual(inventario["humedad_modelada"]["estado"], "Sin dato vigente")
        self.assertEqual(inventario["lluvia_modelada"]["fecha"], consulta)
        self.assertEqual(inventario["humedad_modelada"]["fecha"], consulta)

    def test_inventario_lluvia_independiente_requiere_valor_y_cobertura(self):
        for valor, cobertura, estado in ((0, True, "Disponible"),
                                         (2, False, "Incompleto o vencido"),
                                         (None, True, "Incompleto o vencido"),
                                         (-1, True, "Incompleto o vencido")):
            with self.subTest(valor=valor, cobertura=cobertura):
                row = self.row(cobertura_meteorologica=False, lluvia_futura_72=valor,
                               cobertura_lluvia_futura_72=cobertura)
                inventario = {r["id"]: r for r in construir_inventario(row, referencia=NOW)}
                self.assertEqual(inventario["lluvia_modelada"]["estado"], estado)
                self.assertEqual(inventario["humedad_modelada"]["estado"], "Sin dato vigente")

    def test_cero_observado_y_ausencia_son_diferentes(self):
        a = construir_inventario(self.row(), siga={"fecha": NOW.isoformat(), "precipitacion_mm": 0}, referencia=NOW)
        b = construir_inventario(self.row(), siga={"fecha": NOW.isoformat(), "precipitacion_mm": None}, referencia=NOW)
        self.assertEqual(next(r for r in a if r["id"] == "lluvia_observada")["valor"], "0 mm / día")
        self.assertEqual(next(r for r in b if r["id"] == "lluvia_observada")["valor"], "Sin dato")

    def test_satelite_antiguo_no_es_agua_observada_hoy(self):
        d = construir_inventario(self.row(), agua={"fecha": NOW - timedelta(days=8), "NDWI": .2}, referencia=NOW)
        r = next(r for r in d if r["id"] == "agua_superficial")
        self.assertEqual(r["estado"], "Histórico o vencido")
        self.assertEqual(r["monitor_horario"], "No")

    def test_suelo_sin_carga_no_se_confunde_con_humedad_modelada(self):
        d = {r["id"]: r for r in construir_inventario(self.row(), referencia=NOW)}
        self.assertEqual(d["humedad_modelada"]["valor"], "0.34 m³/m³")
        self.assertEqual(d["agua_util"]["estado"], "Sin carga para esta localidad")
        self.assertIn("no mide saturación", d["humedad_modelada"]["alcance"])
        self.assertEqual(d["relieve"]["valor"], "Sin dato")
        desconocido = {r["id"]: r for r in construir_inventario(self.row(cobertura_meteorologica=float("nan")), referencia=NOW)}
        self.assertEqual(desconocido["lluvia_modelada"]["estado"], "Incompleto o vencido")
        self.assertEqual(desconocido["humedad_modelada"]["estado"], "Sin dato vigente")


if __name__ == "__main__":
    unittest.main()
