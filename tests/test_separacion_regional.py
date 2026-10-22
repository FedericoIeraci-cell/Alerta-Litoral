"""Contratos regionales: una región ausente no se inventa ni se exige."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import unittest
import red_integrada as red

NOW = datetime.now(timezone.utc)

class SeparacionRegionalTests(unittest.TestCase):
    def test_documentos_independientes_y_compatibilidad_historica(self):
        for region, cantidad in (("Litoral", 48), ("Zárate–Campana", 10), (None, 58)):
            with self.subTest(region=region):
                doc = red.construir_red_integrada([], {}, region=region, referencia=NOW)
                self.assertEqual(len(doc["nodos"]), cantidad)
                self.assertEqual(red.validar_red_integrada(doc, NOW), doc)
                if region:
                    self.assertEqual({n["red"] for n in doc["nodos"]}, {region})
                    self.assertEqual(doc["regiones"], [region])

    def test_no_admite_nodos_extranios_ni_catalogos_incompletos(self):
        doc = red.construir_red_integrada([], {}, region="Litoral", referencia=NOW)
        otro = red.construir_red_integrada([], {}, region="Zárate–Campana", referencia=NOW)
        for error in ("faltante", "otra_region", "duplicado"):
            mal = deepcopy(doc)
            if error == "faltante": mal["nodos"].pop()
            if error == "otra_region": mal["nodos"].append(otro["nodos"][0])
            if error == "duplicado": mal["nodos"].append(mal["nodos"][0])
            with self.subTest(error=error), self.assertRaises(ValueError):
                red.validar_red_integrada(mal, NOW)

    def test_conserva_fuentes_de_cuenca_en_ala_sin_referencias_zc(self):
        fuentes = [{"organismo": "INA diario", "url": "https://alerta.ina.gob.ar/a5/diario/reporte_diario",
                    "fecha_documento": "2026-10-21", "consultado": NOW.isoformat()}]
        ala = red.construir_red_integrada([], {"fuentes": fuentes}, region="Litoral", referencia=NOW,
            zc={"nodos": [], "rios": [], "fuentes": [{"red": "Zárate–Campana", "organismo": "Fallo local"}]})
        self.assertEqual(len(ala["fuentes"]), 1)
        self.assertEqual(ala["fuentes"][0]["organismo"], "INA diario")
        self.assertIsNone(ala["fuentes"][0]["fecha_documento"])
        viejo = red.validar_red_integrada(ala, NOW + timedelta(hours=4))
        self.assertFalse(viejo["snapshot_vigente"])
