"""Regresiones offline: documentos y series oficiales capturados el 14/10/2026."""
import copy
from datetime import datetime, timedelta
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).parents[1]))
import hidrologia as h

FIX = Path(__file__).parent / "fixtures"
NOW = datetime.fromisoformat("2026-10-14T12:00:00-03:00")


def panorama_diario():
    p = h.parsear_reporte_ina((FIX / "ina_diario_20261013.html").read_text(), NOW)
    p["fuentes"] = [p["fuente"]]
    return p


class HidrologiaTests(unittest.TestCase):
    def test_decimal_y_valores_invalidos(self):
        self.assertEqual(h.decimal_es("28,65"), 28.65)
        self.assertEqual(h.decimal_es("12.500,5"), 12500.5)
        self.assertEqual(h.decimal_es("3.60m"), 3.6)
        for v in ("NaN", "inf", float("nan"), float("inf"), "sin dato", "3,4 5,6"):
            self.assertIsNone(h.decimal_es(v))

    def test_rojo_local_observado_no_depende_de_lluvia(self):
        p = panorama_diario()
        for lugar, altura, umbral in [("Paso de los Libres", 9.28, 8.5), ("Puerto Iguazú", 28.65, 28)]:
            x = h.evaluar_localidad(lugar, p, NOW)
            self.assertEqual(x["hidrologia_rango"], 2)
            self.assertEqual(x["nivel_hidrologico_observado"], "EVACUACION")
            self.assertEqual(x["hidrologia_altura_m"], altura)
            self.assertEqual(x["hidrologia_evacuacion_m"], umbral)
            self.assertIn("indicaciones oficiales", x["hidrologia_accion"])

    def test_prevision_textual_conserva_diferentes_fechas_de_umbral(self):
        x = h.evaluar_localidad("Corrientes", panorama_diario(), NOW)
        self.assertEqual(x["hidrologia_rango"], 2)
        self.assertEqual(x["nivel_hidrologico_observado"], "BAJO")
        self.assertIn("15/10", x["hidrologia_ventana"])
        self.assertIn("17/10", x["hidrologia_ventana"])
        self.assertIn("probabilística", x["hidrologia_motivo"])
        x = h.evaluar_localidad("La Paz", panorama_diario(), NOW)
        self.assertEqual(x["hidrologia_rango"], 1)
        self.assertIn("19/10", x["hidrologia_ventana"])

    def test_no_extiende_senales_entre_cuencas(self):
        p = {"estaciones": {}, "pronosticos": [], "represas": {"yacyreta": {
            "fecha": NOW.isoformat(), "nombre": "Yacyretá", "señal_aguas_arriba": True, "fuente": h.EBY_URL}}}
        self.assertEqual(h.evaluar_localidad("Corrientes", p, NOW)["hidrologia_rango"], 1)
        for lugar in ("Paso de los Libres", "Santo Tomé", "Formosa", "Rafaela"):
            x = h.evaluar_localidad(lugar, p, NOW)
            self.assertIsNone(x["hidrologia_rango"], lugar)
            self.assertFalse(x["represa_aplica"], lugar)

    def test_hidro_faltante_nunca_es_bajo(self):
        x = h.evaluar_localidad("Corrientes", {}, NOW)
        self.assertIsNone(x["hidrologia_rango"])
        self.assertEqual(x["nivel_hidrologico_observado"], "SIN_DATOS")
        self.assertEqual(x["hidrologia_cobertura"], "SIN_DATOS")

    def test_dato_vencido_futuro_y_umbral_invertido(self):
        for delta in (timedelta(hours=-73), timedelta(hours=1)):
            p = {"estaciones": {"paso de los libres": {"altura_m": 9.3, "alerta_m": 7.5,
                "evacuacion_m": 8.5, "fecha": (NOW + delta).isoformat()}}, "represas": {}, "pronosticos": []}
            x = h.evaluar_localidad("Paso de los Libres", p, NOW)
            self.assertFalse(x["hidrologia_vigente"])
            self.assertIsNone(x["hidrologia_rango"])
        self.assertEqual(h._nivel(9, 8, 7), "SIN_DATOS")

    def test_reporte_con_fecha_futura_no_certifica_nivel(self):
        text = (FIX / "ina_diario_20261013.html").read_text().replace("Fecha de actualización: <b>13/10/2026", "Fecha de actualización: <b>15/10/2026")
        p = h.parsear_reporte_ina(text, NOW)
        self.assertEqual(p["fuente"]["estado"], "FECHA_FUTURA")
        self.assertFalse(h.evaluar_localidad("Paso de los Libres", p, NOW)["hidrologia_vigente"])

    def test_pronostico_qualitativo_no_acredita_cobertura(self):
        x = h.evaluar_localidad("Formosa", panorama_diario(), NOW)
        self.assertFalse(x["hidrologia_pronostico_vigente"])
        self.assertNotEqual(x["hidrologia_cobertura"], "COMPLETA")

    def test_no_detecta_rojo_en_negacion(self):
        emision = NOW.replace(hour=0)
        for text in ("En Goya no se prevé exceder el nivel de Evacuación en los próximos 3 días.",
                     "En Goya se prevé una oscilación sin exceder el nivel de Evacuación en los próximos 3 días."):
            forecasts = h._pronosticos_texto(text, emision, {"goya"}, h.INA_URL)
            self.assertTrue(all(p["nivel"] == "SIN_DATOS" for p in forecasts))

    def test_nombre_del_rio_no_se_asigna_a_ciudad(self):
        forecast = h._pronosticos_texto("En el río Paraná se prevé excedencia del nivel de Evacuación en los próximos 2 días.", NOW, {"parana"}, h.INA_URL)
        self.assertEqual(forecast, [])

    def test_aguas_abajo_no_se_propagan_hacia_arriba(self):
        p = {"estaciones": {"rosario": {"altura_m": 6, "alerta_m": 5, "evacuacion_m": 5.5,
              "fecha": NOW.isoformat(), "nombre": "Rosario"}}, "pronosticos": [], "represas": {}}
        for lugar in ("Avellaneda", "Reconquista", "Corrientes"):
            self.assertEqual(h.evaluar_localidad(lugar, p, NOW)["hidrologia_alertas_aguas_arriba"], [])

    def test_boletion_yacyreta_validez_y_rango_sin_cota_evacuacion_inventada(self):
        p = h.parsear_boletin_yacyreta((FIX / "eby_20261013.html").read_text(), NOW, "https://www.eby.gov.py/alerta-hidrologico-n05-octubre/")
        self.assertEqual(p["fuente"]["estado"], "VIGENTE")
        self.assertEqual(p["represa"]["afluente_m3s"], 34100)
        self.assertEqual(p["represa"]["afluente_previsto_max_m3s"], 39000)
        self.assertEqual(p["estaciones"]["puerto ituzaingo"]["altura_m"], 3.6)
        self.assertIsNone(p["estaciones"]["puerto ituzaingo"]["evacuacion_m"])
        self.assertEqual(p["pronosticos"][0]["nivel"], "ALERTA")
        self.assertEqual(p["pronosticos"][0]["minimo_m"], 4.8)
        expired = h.parsear_boletin_yacyreta((FIX / "eby_20261013.html").read_text(), "2026-10-15T00:01:00-03:00")
        self.assertEqual(expired["fuente"]["estado"], "VENCIDO")

    def test_serie_numerica_respeta_fecha_unidad_bandas(self):
        raw = json.loads((FIX / "ina_serie19_20261014.json").read_text())
        p = h.parsear_serie_ina(raw, 19, NOW)
        self.assertEqual(p["estaciones"]["corrientes"]["altura_m"], 5.5)
        self.assertEqual(p["estaciones"]["corrientes"]["fecha"], "2026-10-14T00:00:00-03:00")
        central_evacuacion = [f for f in p["pronosticos"] if f["nivel"] == "EVACUACION"]
        self.assertEqual(len(central_evacuacion), 1)
        self.assertEqual(central_evacuacion[0]["desde"], "2026-10-20T00:00:00-03:00")
        self.assertEqual(p["curvas"][0]["central_m"], 7.31)
        self.assertEqual(p["curvas"][0]["inferior_m"], 6.75)
        evaluado = h.evaluar_localidad("Corrientes", p, NOW)
        self.assertEqual(evaluado["nivel_hidrologico_previsto"], "EVACUACION")
        self.assertEqual(evaluado["hidrologia_rango"], 1)
        self.assertFalse(evaluado["hidrologia_preparacion_evacuacion_72h"])
        for field in ("id", "var", "unidades"):
            changed = copy.deepcopy(raw)
            changed[field] = 999 if field == "id" else {"id": 999}
            with self.assertRaises(ValueError):
                h.parsear_serie_ina(changed, 19, NOW)

    def test_solo_banda_superior_no_produce_rojo(self):
        raw = json.loads((FIX / "ina_serie19_20261014.json").read_text())
        raw["pronosticos"][0]["corrida"]["forecast_date"] = NOW.isoformat()
        for serie in raw["pronosticos"][0]["corrida"]["series"]:
            serie["pronosticos"] = [{"timestart": (NOW + timedelta(hours=24)).isoformat(),
                "valor": {"inferior": 5.9, "medio": 6.1, "superior": 7.8}[serie["qualifier"]]}]
        p = h.parsear_serie_ina(raw, 19, NOW)
        x = h.evaluar_localidad("Corrientes", p, NOW)
        self.assertEqual(x["hidrologia_rango"], 1)
        self.assertNotEqual(x["nivel_hidrologico_previsto"], "EVACUACION")
        self.assertIn("banda superior", x["hidrologia_pronostico_texto"])

    def test_corrida_futura_vencida_o_de_otra_estacion_no_se_aplica(self):
        base = json.loads((FIX / "ina_serie19_20261014.json").read_text())
        for case in ("futura", "vieja", "otro_sitio"):
            raw = copy.deepcopy(base)
            if case == "otro_sitio":
                raw["pronosticos"][0]["out_id"] = [65]
            else:
                raw["pronosticos"][0]["corrida"]["forecast_date"] = (NOW + timedelta(hours=1) if case == "futura" else NOW - timedelta(hours=73)).isoformat()
            p = h.parsear_serie_ina(raw, 19, NOW)
            self.assertEqual(p["pronosticos"], [], case)

    def test_main_qualifier_y_corrida_vacia(self):
        raw = json.loads((FIX / "ina_serie65_20261014.json").read_text())
        p = h.parsear_serie_ina(raw, 65, NOW)
        self.assertEqual(len(p["curvas"]), 13)
        x = h.evaluar_localidad("San Javier (Misiones)", p, NOW)
        self.assertTrue(x["hidrologia_pronostico_vigente"])
        self.assertEqual(x["nivel_hidrologico_previsto"], "BAJO")

    def test_firma_no_cambia_por_hora_o_altura_sin_cambio_material(self):
        p = panorama_diario()
        a = h.evaluar_localidad("Paso de los Libres", p, NOW)
        p["estaciones"]["paso de los libres"].update(altura_m=9.32, fecha=(NOW + timedelta(minutes=1)).isoformat())
        b = h.evaluar_localidad("Paso de los Libres", p, NOW + timedelta(minutes=2))
        self.assertEqual(a["hidrologia_firma"], b["hidrologia_firma"])
        p["estaciones"]["paso de los libres"].update(altura_m=7.6)
        c = h.evaluar_localidad("Paso de los Libres", p, NOW + timedelta(minutes=2))
        self.assertNotEqual(a["hidrologia_firma"], c["hidrologia_firma"])

    def test_panorama_serializable_y_fallos_no_encubren_ausencia(self):
        class Session:
            def get(self, *args, **kwargs):
                raise TimeoutError("prueba offline")
        p = h.obtener_panorama(NOW, Session())
        self.assertEqual(p["estaciones"], {})
        self.assertTrue(all(f["estado"] == "ERROR" for f in p["fuentes"]))
        json.dumps(p, allow_nan=False)
        self.assertIsNone(h.evaluar_localidad("Corrientes", p, NOW)["hidrologia_rango"])


if __name__ == "__main__":
    unittest.main()
