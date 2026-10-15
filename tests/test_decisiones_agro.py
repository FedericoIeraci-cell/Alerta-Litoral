"""Decisiones por rol y trazabilidad, sin red ni acciones externas."""
from datetime import datetime, timedelta
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from decisiones_agro import ART, construir_plan

NOW = datetime(2026, 10, 15, 11, 0, tzinfo=ART)


def fila(**extra):
    row = {"localidad": "Goya", "nivel": "VERDE", "indice": 25.0,
           "actualizado": "15/10/2026 10:00", "fecha_evaluacion_integrada": NOW.isoformat(),
           "cobertura_meteorologica": True, "lluvia_futura_24": 0.0,
           "lluvia_futura_72": 18.0, "lluvia_futura_7d": 45.0,
           "cobertura_pronostico_7d": True}
    row.update(extra)
    return row


class DecisionesAgroTests(unittest.TestCase):
    def plan(self, row=None, usuario="Productor", evidencias=None):
        return construir_plan(fila() if row is None else row, usuario, evidencias, NOW)

    def test_roles_have_different_operational_responsibilities(self):
        productor = self.plan()
        comite = self.plan(usuario="Comité de cuenca")
        municipio = self.plan(usuario="Municipio / Defensa Civil")
        self.assertIn("SENASA", productor["acciones"][0]["verificar"])
        self.assertIn("jurisdicciones", comite["acciones"][2]["accion"])
        self.assertIn("cierres y desvíos oficiales", municipio["acciones"][3]["accion"])
        self.assertEqual(self.plan(usuario="Defensa Civil")["usuario"], municipio["usuario"])
        self.assertEqual(len({p["decision_prioritaria"] for p in (productor, comite, municipio)}), 3)

    def test_critical_fluvial_signal_coordinates_authority_without_order(self):
        p = self.plan(fila(nivel="ROJO", hidrologia_rango=2,
            hidrologia_vigente=True, hidrologia_fecha=NOW.isoformat(),
            hidrologia_fuentes="https://alerta.ina.gob.ar/", nivel_hidrologico_observado="EVACUACION"))
        self.assertEqual(p["nivel"], "ROJO")
        self.assertIn("autoridad local", p["decision_prioritaria"])
        self.assertEqual(p["acciones"][0]["area"], "Coordinación prioritaria")
        self.assertIn("no emite una orden", p["acciones"][0]["verificar"])
        self.assertIn("destino y acceso confirmados", p["acciones"][0]["accion"])

    def test_unverified_critical_row_creates_evidence_pending(self):
        p = self.plan(fila(nivel="ROJO", hidrologia_rango=2))
        self.assertTrue(any("señal fluvial crítica" in t for t in p["pendientes"]))
        self.assertTrue(any("no emite alertas oficiales" in t for t in p["limitaciones"]))

    def test_missing_stale_future_and_partial_forecasts_do_not_become_zero(self):
        p = self.plan(fila(lluvia_futura_24=float("nan"), cobertura_pronostico_7d=False,
                           lluvia_dias_8_14=100.0, cobertura_dias_8_14=False))
        lluvia = p["datos"]["lluvia_modelada_mm"]
        self.assertIsNone(lluvia["24_h"])
        self.assertIsNone(lluvia["7_dias"])
        self.assertIsNone(lluvia["dias_8_14"])
        self.assertEqual(lluvia["72_h"], 18.0)
        for stamp in ((NOW - timedelta(days=2)).isoformat(), (NOW + timedelta(days=1)).isoformat()):
            with self.subTest(stamp=stamp):
                p = self.plan(fila(actualizado=stamp))
                self.assertTrue(all(v is None for v in p["datos"]["lluvia_modelada_mm"].values()))

    def test_zero_rain_is_preserved_without_a_safe_window_claim(self):
        p = self.plan()
        self.assertEqual(p["datos"]["lluvia_modelada_mm"]["24_h"], 0.0)
        self.assertTrue(any("no certifican ventanas seguras" in t for t in p["limitaciones"]))
        self.assertIn("Portancia local", p["acciones"][1]["verificar"])

    def test_evidence_failures_are_pending_and_original_provenance_is_kept(self):
        estados = ["Sin", "Histórico", "Incompleto", "Confirmación requerida"]
        evidence = [{"id": str(i), "variable": "Dato " + str(i), "estado": state,
                     "valor": 5, "fecha": NOW.isoformat(), "fuente": "https://siga.inta.gob.ar/",
                     "alcance": "Estación a 15 km; no mide cada lote.", "monitor_horario": "No"}
                    for i, state in enumerate(estados)]
        p = self.plan(evidencias=evidence)
        self.assertTrue(all(any(e["variable"] in t for t in p["pendientes"]) for e in evidence))
        self.assertEqual(p["datos"]["evidencias"], evidence)
        self.assertTrue(any(f["url"] == "https://siga.inta.gob.ar/" for f in p["fuentes"]))

    def test_checkbox_and_static_directory_cannot_confirm_resource_capacity(self):
        evidence = [{"id": "refugio", "variable": "Directorio de refugios", "valor": True,
                     "estado": "Disponible", "fecha": NOW.isoformat(),
                     "fuente": "https://santafeciudad.gov.ar/", "alcance": "Directorio estático"}]
        p = self.plan(usuario="Municipio", evidencias=evidence)
        self.assertTrue(any("apertura, acceso y recursos sanitarios" in t for t in p["pendientes"]))
        refugio = next(a for a in p["acciones"] if a["area"] == "Refugios y salud")
        self.assertIn("plazas efectivas", refugio["verificar"])
        self.assertNotIn("capacidad_confirmada", json.dumps(p))

    def test_available_label_cannot_refresh_old_measurement(self):
        evidence = [{"id": "lluvia_observada", "variable": "Lluvia del pluviómetro",
                     "estado": "Disponible", "valor": "20 mm", "fecha": "2026-10-01",
                     "fuente": "https://siga.inta.gob.ar/"}]
        p = self.plan(evidencias=evidence)
        self.assertTrue(any("fecha vencida o futura" in t for t in p["pendientes"]))
        self.assertEqual(p["datos"]["evidencias"][0]["fecha"], "2026-10-01")

    def test_soil_and_elevation_values_do_not_become_saturation_or_safe_destination(self):
        p = self.plan(fila(humedad_suelo_promedio=0.45, cota_m=14.0, hidrologia_altura_m=7.0))
        self.assertTrue(any("no determinan saturación física" in t for t in p["limitaciones"]))
        self.assertTrue(any("No restar cotas DEM" in t for t in p["limitaciones"]))
        self.assertNotIn("saturacion_pct", p["datos"])
        self.assertNotIn("altura_sobre_rio", p["datos"])

    def test_plan_is_strict_json_and_does_not_mutate_inputs(self):
        row = fila(indice=float("inf"), nivel="DATO DESCONOCIDO")
        evidence = [{"variable": "Muestra", "valor": float("nan"), "estado": "Sin dato"}]
        p = self.plan(row, evidencias=evidence)
        self.assertEqual(p["nivel"], "SIN DATOS")
        self.assertEqual(row["nivel"], "DATO DESCONOCIDO")
        self.assertEqual(evidence[0]["estado"], "Sin dato")
        self.assertIsNone(json.loads(json.dumps(p, allow_nan=False))["datos"]["indice_meteorologico"])
        self.assertIsNone(p["datos"]["evidencias"][0]["valor"])

    def test_unknown_role_and_invalid_reference_are_rejected_explicitly(self):
        with self.assertRaises(ValueError):
            self.plan(usuario="Visitante")
        with self.assertRaises(ValueError):
            construir_plan(fila(), "Productor", referencia="fecha ilegible")


if __name__ == "__main__":
    unittest.main()
