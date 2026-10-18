"""Regresiones offline: documentos y series oficiales capturados el 14/10/2026."""
import copy
from datetime import datetime, timedelta
from itertools import permutations
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

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

    def test_yacyreta_descenso_conserva_texto_y_ordena_extremos_sin_probabilidad(self):
        p = h.parsear_boletin_yacyreta(
            (FIX / "eby_20261016.html").read_text(), "2026-10-17T20:10:00-03:00",
            "https://www.eby.gov.py/alerta-hidrologico-n07-octubre/",
        )
        represa = p["represa"]
        self.assertEqual(represa["afluente_m3s"], 39500)
        self.assertEqual(represa["afluente_tipo"], "prevision_del_dia")
        self.assertEqual(represa["afluente_previsto_extremos_publicados_m3s"], [39000, 33000])
        self.assertEqual(represa["afluente_previsto_min_m3s"], 33000)
        self.assertEqual(represa["afluente_previsto_max_m3s"], 39000)
        self.assertIn("tendencia gradual descendente", represa["texto"])
        self.assertIn("sin probabilidad cuantificada", represa["afluente_previsto_interpretacion"])
        self.assertFalse(represa["señal_aguas_arriba"])
        self.assertEqual(p["estaciones"]["puerto ituzaingo"]["altura_m"], 4.81)
        self.assertEqual(p["estaciones"]["puerto ituzaingo"]["fecha"], "2026-10-16T12:30:00-03:00")
        self.assertEqual(p["estaciones"]["puerto ituzaingo"]["alerta_m"], 3.5)
        self.assertEqual(p["estaciones"]["puerto ituzaingo"]["evacuacion_m"], 4.0)
        self.assertEqual(p["estaciones"]["puerto ituzaingo"]["precision_fecha"], "fecha_hora_publicada")
        self.assertEqual(p["estaciones"]["puerto ituzaingo"]["precision_fecha_documento"], "fecha_sin_hora")
        self.assertEqual(len(p["pronosticos"]), 1)
        previsto = p["pronosticos"][0]
        self.assertEqual(previsto["localidades"], ["ituzaingo"])
        self.assertEqual(previsto["extremos_publicados_m"], [5, 4.4])
        self.assertEqual((previsto["minimo_m"], previsto["maximo_m"]), (4.4, 5))
        self.assertEqual(previsto["hasta"], "2026-10-19T23:59:59-03:00")
        self.assertIsNone(previsto["desde"])
        for campo in ("precision_fecha_emision", "precision_desde", "precision_hasta"):
            self.assertEqual(previsto[campo], "fecha_sin_hora")
        self.assertIn("sin fecha de cruce publicada", previsto["ventana"])
        self.assertEqual(previsto["nivel"], "ALERTA")
        self.assertNotIn("Ayolas", previsto["texto"])
        self.assertIn("sin probabilidad cuantificada", previsto["probabilidad"])
        self.assertNotIn("central_m", previsto)
        self.assertNotIn("erogado_m3s", represa)
        self.assertEqual(represa["precision_fecha"], "fecha_sin_hora")
        self.assertEqual(represa["precision_hasta"], "fecha_sin_hora")
        self.assertEqual(p["fuente"]["precision_fecha_documento"], "fecha_sin_hora")
        self.assertEqual(p["fuente"]["precision_valido_hasta"], "fecha_sin_hora")
        self.assertEqual(p["fuente"]["consultado"], "2026-10-17T20:10:00-03:00")

    def test_yacyreta_umbral_ausente_no_se_toma_del_puerto_siguiente(self):
        contenido = (FIX / "eby_20261016.html").read_text()
        texto = next(h._DOM(contenido).root.iter("article")).text()
        self.assertIn("Nivel de Evacuación 4.00m.", texto)
        texto = texto.replace("Nivel de Evacuación 4.00m.", "")
        texto = texto.replace("Puerto de Ayolas", "Puerto de Ayolas: Nivel de Evacuación 4.50m. Puerto de Ayolas", 1)
        p = h.parsear_boletin_yacyreta("<article>" + texto + "</article>", "2026-10-17T20:10:00-03:00")
        estacion = p["estaciones"]["puerto ituzaingo"]
        self.assertEqual(estacion["altura_m"], 4.81)
        self.assertEqual(estacion["alerta_m"], 3.5)
        self.assertIsNone(estacion["evacuacion_m"])
        self.assertNotIn("Ayolas", estacion["perspectiva"])

    def test_yacyreta_rango_sobre_umbral_no_inventa_cruce_ni_ventana_roja(self):
        contenido = (FIX / "eby_20261016.html").read_text()
        url = "https://www.eby.gov.py/alerta-hidrologico-n07-octubre/"
        referencia = "2026-10-17T20:10:00-03:00"

        class Respuesta:
            def __init__(self, texto):
                self.text = texto

            def raise_for_status(self):
                pass

        class SesionCongelada:
            def get(self, destino, **kwargs):
                if destino == h.EBY_URL:
                    return Respuesta('<a href="' + url + '">Boletín n07</a>')
                if destino == url:
                    return Respuesta(contenido)
                raise OSError("Fuente omitida de la comparación EBY aislada; sin red")

        with patch("ctm.obtener_ctm", return_value={}):
            panorama = h.obtener_panorama(referencia, session=SesionCongelada())
        previsto = panorama["pronosticos"][0]
        self.assertEqual(previsto["nivel"], "EVACUACION")
        self.assertEqual(previsto["fuente_umbrales"], url)
        self.assertNotIn("de INA", previsto["probabilidad"])
        self.assertIsNone(previsto["desde"])
        resultado = h.evaluar_localidad("Ituzaingó", panorama, referencia)
        self.assertEqual(resultado["hidrologia_rango"], 2)
        self.assertEqual(resultado["nivel_hidrologico_observado"], "EVACUACION")
        self.assertEqual(resultado["hidrologia_altura_m"], 4.81)
        self.assertEqual(resultado["hidrologia_evacuacion_m"], 4.0)
        self.assertEqual(resultado["nivel_hidrologico_previsto"], "EVACUACION")
        self.assertFalse(resultado["hidrologia_preparacion_evacuacion_72h"])
        evidencia = next(e for e in resultado["hidrologia_evidencias"] if e["tipo"] == "pronostico_fluvial")
        self.assertEqual(evidencia["rango"], 1)
        self.assertEqual(evidencia["precision_fecha_emision"], "fecha_sin_hora")
        from explicaciones_alerta import detalle_evidencia, resumen_evidencia
        resumen = resumen_evidencia(evidencia)
        self.assertNotIn("00:00", resumen)
        self.assertNotIn("Entra en la ventana", resumen)
        self.assertIn("sin fecha de cruce publicada", resumen)
        detalle = detalle_evidencia(evidencia)
        self.assertIn("Emisión: 16/10/2026 · hora no publicada", detalle)
        self.assertIn("Fin del horizonte interpretado: 19/10/2026 · hora no publicada", detalle)

    def test_yacyreta_ascenso_o_ausencia_no_inventa_extremos(self):
        original = (FIX / "eby_20261013.html").read_text()
        p = h.parsear_boletin_yacyreta(original, NOW)
        self.assertEqual(p["represa"]["afluente_previsto_extremos_publicados_m3s"], [35000, 39000])
        self.assertEqual(p["pronosticos"][0]["extremos_publicados_m"], [4.8, 5])
        self.assertTrue(p["represa"]["señal_aguas_arriba"])
        p = h.parsear_boletin_yacyreta(original.replace("35.000 a 39.000", "sin valores publicados"), NOW)
        self.assertIsNone(p["represa"]["afluente_previsto_min_m3s"])
        self.assertIsNone(p["represa"]["afluente_previsto_max_m3s"])
        self.assertEqual(p["represa"]["afluente_previsto_extremos_publicados_m3s"], [])

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

    def test_firma_combina_fechas_numericas_y_plazos_textuales_incompletos(self):
        raw = json.loads((FIX / "ina_serie19_20261014.json").read_text())
        p = h.parsear_serie_ina(raw, 19, NOW)
        for texto in (
            "En Corrientes se prevé exceder el nivel de Evacuación a corto plazo.",
            "En Corrientes se prevé exceder el nivel de Evacuación.",
        ):
            p["pronosticos"].extend(h._pronosticos_texto(texto, NOW, {"corrientes"}, h.INA_URL))

        # Las fuentes pueden completarse en distinto orden. No debe fallar ni
        # cambiar la firma si coinciden niveles con desde/hasta ISO y None.
        firmas = set()
        for orden in permutations(p["pronosticos"]):
            x = h.evaluar_localidad("Corrientes", dict(p, pronosticos=list(orden)), NOW)
            firmas.add(x["hidrologia_firma"])
            self.assertEqual(x["nivel_hidrologico_previsto"], "EVACUACION")
            self.assertTrue(x["hidrologia_pronostico_vigente"])
            self.assertEqual(x["hidrologia_rango"], 1)
            self.assertFalse(x["hidrologia_preparacion_evacuacion_72h"])
        self.assertEqual(len(firmas), 1)
        textos = [e for e in x["hidrologia_evidencias"] if e.get("clase") == "prevision_oficial_textual"]
        self.assertEqual(len(textos), 2)
        self.assertTrue(all(e["desde"] is None for e in textos))
        self.assertEqual(sum(e["hasta"] is None for e in textos), 1)

    def test_firmas_compatibles_evitan_reavisos_por_la_correccion(self):
        # Firmas capturadas antes de la corrección: datos que ya funcionaban
        # deben conservar la identidad usada para deduplicar notificaciones.
        p = panorama_diario()
        for lugar, firma in (
            ("Corrientes", "5dfa4a2e158a646a3a0f"),
            ("Paso de los Libres", "710556a173bc174dac6e"),
            ("Formosa", "0771d812adcc77256c96"),
        ):
            with self.subTest(localidad=lugar):
                self.assertEqual(h.evaluar_localidad(lugar, p, NOW)["hidrologia_firma"], firma)
        raw = json.loads((FIX / "ina_serie19_20261014.json").read_text())
        p = h.parsear_serie_ina(raw, 19, NOW)
        self.assertEqual(h.evaluar_localidad("Corrientes", p, NOW)["hidrologia_firma"], "586aaae33b5834dc38a4")

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
