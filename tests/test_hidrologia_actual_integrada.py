"""Últimas alturas alimentan el motor, con respuestas congeladas y sin envíos."""
from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import hidro_actual
import hidrologia as h


FIX = Path(__file__).parent / "fixtures"
WFS = json.loads((FIX / "ina_ultimas_20261018.json").read_text())
SERIE = json.loads((FIX / "ina_serie19_20261014.json").read_text())
NOW = h._ahora("2026-10-18T17:45:00-03:00")


class UltimasAlturasIntegradasTests(unittest.TestCase):
    def setUp(self):
        guard = patch("requests.sessions.Session.request", side_effect=AssertionError("HTTP real prohibido"))
        guard.start()
        self.addCleanup(guard.stop)

    def panorama(self, payload=None, serie=None, fallo_wfs=False, sin_serie=False):
        payload = deepcopy(WFS if payload is None else payload)
        serie = deepcopy(SERIE if serie is None else serie)

        def descarga(_session, url):
            if not sin_serie and "getSeriesBySiteAndVar?series_id=19&" in url:
                return json.dumps(serie)
            raise OSError("Fuente aislada en prueba; sin red")

        with patch.object(h, "_descargar", side_effect=descarga), \
             patch.object(hidro_actual, "_descargar", return_value=json.dumps(payload).encode(),
                          side_effect=TimeoutError("error privado") if fallo_wfs else None), \
             patch("ctm.obtener_ctm", return_value={}), \
             patch.object(hidro_actual, "consultar_salto_grande", side_effect=OSError("Sin CTM en esta prueba")):
            return h.obtener_panorama(NOW, session=object())

    def test_ultima_altura_reemplaza_la_vieja_y_cambia_la_evaluacion(self):
        antes = h.parsear_serie_ina(SERIE, 19, NOW)
        self.assertFalse(h.evaluar_localidad("Corrientes", antes, NOW)["hidrologia_vigente"])
        panorama = self.panorama()
        estacion = panorama["estaciones"]["corrientes"]
        self.assertEqual(estacion["altura_m"], 6.89)
        self.assertEqual(estacion["fecha"], "2026-10-18T03:00:00+00:00")
        self.assertEqual((estacion["alerta_m"], estacion["evacuacion_m"]), (6.5, 7))
        evaluada = h.evaluar_localidad("Corrientes", panorama, NOW)
        self.assertEqual(evaluada["nivel_hidrologico_observado"], "ALERTA")
        self.assertEqual(evaluada["hidrologia_rango"], 1)
        evidencia = next(e for e in evaluada["hidrologia_evidencias"] if e["tipo"] == "observacion_fluvial")
        self.assertEqual(evidencia["altura_m"], 6.89)
        self.assertEqual(evidencia["fuente"], hidro_actual.url_rios())
        self.assertEqual(evidencia["fuente_umbrales"], hidro_actual.url_rios())
        self.assertEqual(evidencia["precision_fecha"], "fecha_registro_fuente")
        self.assertFalse(evidencia["automatica"])
        self.assertEqual(evidencia["fecha_registro"], "2026-10-18T03:00:00Z")

    def test_wfs_identifica_escala_y_umbrales_aunque_falle_api_de_serie(self):
        panorama = self.panorama(sin_serie=True)
        resultado = h.evaluar_localidad("Corrientes", panorama, NOW)
        self.assertEqual(resultado["nivel_hidrologico_observado"], "ALERTA")
        self.assertIsNone(panorama["estaciones"]["corrientes"]["automatica"])
        self.assertNotIn("zarate", panorama["estaciones"])
        self.assertNotIn("campana", panorama["estaciones"])

    def test_datos_manuales_no_se_rejuvenecen_ni_se_presentan_como_telemetria(self):
        panorama = self.panorama()
        e = panorama["estaciones"]["corrientes"]
        self.assertNotEqual(h._fecha(e["fecha"]), h._fecha(e["consultado"]))
        self.assertEqual(e["estado_lectura"], "LECTURA ATRASADA")
        self.assertEqual(e["edad_horas"], 17.75)
        self.assertIn("no está confirmada", e["nota_fecha"])
        parsed = h.parsear_serie_ina(SERIE, 19, NOW)["estaciones"]["corrientes"]
        self.assertEqual(parsed["precision_fecha"], "fecha_registro_fuente")
        self.assertEqual(parsed["propietario"], "PNA")
        self.assertEqual(parsed["tabla"], "alturas_prefe")

    def test_pronosticos_y_su_origen_no_cambian_al_actualizar_observacion(self):
        serie = deepcopy(SERIE)
        serie["pronosticos"][0]["corrida"]["forecast_date"] = (NOW - timedelta(hours=1)).isoformat()
        antes = h.parsear_serie_ina(serie, 19, NOW)
        panorama = self.panorama(serie=serie)
        estacion = panorama["estaciones"]["corrientes"]
        self.assertTrue(antes["curvas"])
        self.assertEqual(panorama["pronosticos"], antes["pronosticos"])
        self.assertEqual(panorama["curvas"], antes["curvas"])
        self.assertEqual(estacion["pronostico_numerico"], antes["estaciones"]["corrientes"]["pronostico_numerico"])
        self.assertEqual(estacion["fuente_pronostico"], h.url_serie_ina(19, NOW))
        self.assertNotEqual(estacion["fuente"], estacion["fuente_pronostico"])

    def test_lectura_anterior_o_repetida_no_reemplaza_una_mas_reciente(self):
        for fecha in ("2026-10-18T03:00:00Z", "2026-10-18T20:00:00Z"):
            serie = deepcopy(SERIE)
            serie["observaciones"].append([fecha, "", 7.1])
            panorama = self.panorama(serie=serie)
            self.assertEqual(panorama["estaciones"]["corrientes"]["altura_m"], 7.1)
            self.assertEqual(h.evaluar_localidad("Corrientes", panorama, NOW)["nivel_hidrologico_observado"], "EVACUACION")

    def test_observacion_futura_de_serie_no_bloquea_la_ultima_wfs_valida(self):
        for delta in (timedelta(seconds=1), timedelta(minutes=10), timedelta(hours=1)):
            with self.subTest(delta=delta):
                serie = deepcopy(SERIE)
                serie["observaciones"].append([(NOW + delta).isoformat(), "", 8.5])
                panorama = self.panorama(serie=serie)
                estacion = panorama["estaciones"]["corrientes"]
                self.assertEqual(estacion["altura_m"], 6.89)
                self.assertLessEqual(h._fecha(estacion["fecha"]), NOW)
                self.assertEqual(h.evaluar_localidad("Corrientes", panorama, NOW)["nivel_hidrologico_observado"], "ALERTA")

    def test_fuente_incompleta_futura_o_otra_escala_no_pisa_lectura(self):
        for campo, valor in (("nombre", "Zárate"), ("tabla", "sat2"), ("var_id", 1),
                             ("series_id", 29437), ("valor", None), ("valor", True),
                             ("fecha", "2026-10-18T23:00:00Z")):
            with self.subTest(campo=campo, valor=valor):
                payload = deepcopy(WFS)
                payload["features"][0]["properties"][campo] = valor
                panorama = self.panorama(payload)
                self.assertEqual(panorama["estaciones"]["corrientes"]["altura_m"], 5.5)
                self.assertFalse(h.evaluar_localidad("Corrientes", panorama, NOW)["hidrologia_vigente"])

    def test_sin_umbral_no_inventa_cero_ni_riesgo_bajo(self):
        payload = deepcopy(WFS)
        p = payload["features"][0]["properties"]
        del p["nivel_de_alerta"]
        del p["nivel_de_evacuacion"]
        panorama = self.panorama(payload, sin_serie=True)
        e = panorama["estaciones"]["corrientes"]
        self.assertEqual(e["altura_m"], 6.89)
        self.assertIsNone(e["alerta_m"])
        self.assertIsNone(e["evacuacion_m"])
        self.assertEqual(h.evaluar_localidad("Corrientes", panorama, NOW)["nivel_hidrologico_observado"], "SIN_DATOS")
        # Sólo la misma serie y tabla verificadas pueden completar los umbrales.
        panorama = self.panorama(payload)
        self.assertEqual(panorama["estaciones"]["corrientes"]["fuente_umbrales"], h.url_serie_ina(19, NOW))
        self.assertEqual(h.evaluar_localidad("Corrientes", panorama, NOW)["nivel_hidrologico_observado"], "ALERTA")

    def test_umbral_inconsistente_no_se_completa_con_otro_origen(self):
        for valor in (True, float("nan"), 5):
            payload = deepcopy(WFS)
            payload["features"][0]["properties"]["nivel_de_evacuacion"] = valor
            panorama = self.panorama(payload)
            self.assertTrue(panorama["estaciones"]["corrientes"]["umbral_invalido"])
            self.assertEqual(h.evaluar_localidad("Corrientes", panorama, NOW)["nivel_hidrologico_observado"], "SIN_DATOS")
            aguas_abajo = h.evaluar_localidad("Goya", panorama, NOW)
            self.assertEqual(aguas_abajo["hidrologia_alertas_aguas_arriba"], [])

    def test_fallback_de_umbrales_rechaza_booleanos_y_no_finitos(self):
        payload = deepcopy(WFS)
        for campo in ("nivel_de_alerta", "nivel_de_evacuacion"):
            del payload["features"][0]["properties"][campo]
        for valor in (True, False, float("nan"), float("inf"), -float("inf")):
            with self.subTest(valor=valor):
                serie = deepcopy(SERIE)
                serie["estacion"].update(nivel_alerta=valor, nivel_evacuacion=valor)
                panorama = self.panorama(payload, serie=serie)
                estacion = panorama["estaciones"]["corrientes"]
                self.assertIsNone(estacion["alerta_m"])
                self.assertIsNone(estacion["evacuacion_m"])
                self.assertEqual(h.evaluar_localidad("Corrientes", panorama, NOW)["nivel_hidrologico_observado"], "SIN_DATOS")
                self.assertEqual(h.evaluar_localidad("Goya", panorama, NOW)["hidrologia_alertas_aguas_arriba"], [])

    def test_un_cero_medido_es_valido_y_no_se_confunde_con_ausencia(self):
        payload = deepcopy(WFS)
        payload["features"][0]["properties"]["valor"] = 0
        panorama = self.panorama(payload)
        self.assertEqual(panorama["estaciones"]["corrientes"]["altura_m"], 0)
        self.assertEqual(h.evaluar_localidad("Corrientes", panorama, NOW)["nivel_hidrologico_observado"], "BAJO")

    def test_caida_wfs_conserva_fuentes_y_registra_fallo_sin_secretos(self):
        panorama = self.panorama(fallo_wfs=True)
        self.assertEqual(panorama["estaciones"]["corrientes"]["altura_m"], 5.5)
        fuente = next(f for f in panorama["fuentes"] if f["id"] == "ina_ultimas_alturas")
        self.assertEqual(fuente["estado"], "ERROR")
        self.assertIn("TimeoutError", fuente["error"])
        self.assertNotIn("privado", json.dumps(panorama))

    def test_modulo_retenido_se_recarga_y_conserva_consulta_causal(self):
        anterior = ModuleType("hidro_actual")
        consulta = (NOW - timedelta(minutes=5)).isoformat()
        lecturas = hidro_actual.parsear_rios(WFS, NOW)
        for lectura in lecturas:
            lectura["consulta"] = consulta
        nuevo = SimpleNamespace(url_rios=hidro_actual.url_rios,
                                consultar_rios=Mock(return_value=lecturas))
        panorama = {"estaciones": {}, "fuentes": []}
        sesion = object()
        with patch.dict(sys.modules, {"hidro_actual": anterior}), \
             patch("importlib.reload", return_value=nuevo) as recargar:
            h._integrar_lecturas_actuales_ina(panorama, NOW, sesion, {})
        recargar.assert_called_once_with(anterior)
        nuevo.consultar_rios.assert_called_once_with(NOW, sesion)
        self.assertEqual(panorama["estaciones"]["corrientes"]["altura_m"], 6.89)
        evidencia = next(e for e in h.evaluar_localidad("Corrientes", panorama, NOW)["hidrologia_evidencias"]
                         if e["tipo"] == "observacion_fluvial")
        self.assertEqual(evidencia["consultado"], consulta)
        senal = next(e for e in h.evaluar_localidad("Goya", panorama, NOW)["hidrologia_evidencias"]
                     if e["tipo"] == "aguas_arriba")
        self.assertEqual(senal["consultado"], consulta)

    def test_fallo_de_recarga_no_interrumpe_el_panorama(self):
        anterior = ModuleType("hidro_actual")
        original = {"altura_m": 5.5, "fecha": "2026-10-14T03:00:00Z"}
        panorama = {"estaciones": {"corrientes": deepcopy(original)}, "fuentes": []}
        with patch.dict(sys.modules, {"hidro_actual": anterior}), \
             patch("importlib.reload", side_effect=ImportError("detalle privado")):
            h._integrar_lecturas_actuales_ina(panorama, NOW, object(), {})
        self.assertEqual(panorama["estaciones"]["corrientes"], original)
        self.assertEqual(panorama["fuentes"][0]["estado"], "ERROR")
        self.assertNotIn("privado", panorama["fuentes"][0]["error"])

    def test_evidencia_aguas_arriba_conserva_precision_sin_trasladar_altura(self):
        panorama = self.panorama()
        resultado = h.evaluar_localidad("Goya", panorama, NOW)
        self.assertIsNone(resultado["hidrologia_altura_m"])
        senal = next(e for e in resultado["hidrologia_evidencias"] if e["tipo"] == "aguas_arriba")
        self.assertEqual(senal["estacion"], "Corrientes")
        self.assertEqual(senal["precision_fecha"], "fecha_registro_fuente")
        self.assertEqual(senal["fuente_umbrales"], hidro_actual.url_rios())


if __name__ == "__main__":
    unittest.main()
