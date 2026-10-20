"""La publicación ECMWF confirma contenido y nunca cambia envíos de Telegram."""
import copy
from datetime import datetime, timedelta, timezone
import json
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import pandas as pd

import monitor
import meteorologia_compartida as meteo
from test_publicacion_red import GitHubMemory


NODOS = [{"localidad": "Rosario", "provincia": "Santa Fe", "lat": -32.95, "lon": -60.65}]


def captura(consultado=None):
    return meteo.crear_captura(NODOS, [{
        "latitude": -32.95, "longitude": -60.65,
        "hourly": {"time": ["2026-10-19T21:00"], "precipitation": [1.0]},
    }], consultado or datetime.now(timezone.utc))


class MeteorologiaMemory(GitHubMemory):
    """Reutiliza la API de commits simulados con una única ruta autorizada."""

    def request(self, method, url, **kwargs):
        if "/contents/" in url:
            if not url.endswith("/contents/meteorologia_ecmwf.json"):
                raise AssertionError("No debe acceder a red, estado ni SQLite")
            url = url.replace("/contents/meteorologia_ecmwf.json", "/contents/red_integrada.json")
        return super().request(method, url, **kwargs)


class PublicacionMeteorologiaTests(unittest.TestCase):
    def setUp(self):
        self.api = MeteorologiaMemory()
        self.store = monitor.GitHubStateStore("test-secret", "owner/repo", session=self.api)
        guard = patch("requests.sessions.Session.request", side_effect=AssertionError("Red prohibida"))
        guard.start()
        self.addCleanup(guard.stop)
        sleep = patch("monitor.time.sleep")
        self.sleep = sleep.start()
        self.addCleanup(sleep.stop)

    def test_publicacion_lectura_sha_commit_y_head_sin_rejuvenecer_consulta(self):
        value = captura(datetime.now(timezone.utc) - timedelta(minutes=15))
        original = copy.deepcopy(value)
        first = self.store.save_meteorologia(value)
        second = self.store.save_meteorologia(value)
        self.assertEqual(value, original)
        self.assertEqual(first["consultado"], original["consultado"])
        self.assertEqual(second["sha"], first["sha"])
        self.assertEqual(self.api.writes[1]["sha"], first["sha"])
        self.assertEqual(self.api.read_refs, ["ala-monitor-state", first["commit_sha"],
                                              "ala-monitor-state", second["commit_sha"]])
        self.assertEqual(meteo.deserializar_captura(self.api.data, NODOS), original)
        self.assertEqual(self.api.head_cache_headers, ["no-cache", "no-cache"])
        self.assertEqual(set(self.store._shas), {monitor.METEOROLOGIA_REMOTE_PATH})

    def test_rama_sustituida_no_acredita_publicacion_ni_reintenta_put(self):
        self.api.after_write = lambda api, old, written: api.add_commit(b'{"otro":true}')
        with self.assertRaises(monitor.MeteorologiaPublicationError) as caught:
            self.store.save_meteorologia(captura())
        self.assertEqual(caught.exception.code, "contenido_no_confirmado")
        self.assertEqual(len(self.api.writes), 1)
        self.assertEqual(self.sleep.call_count, 2)

    def test_commit_no_confirmado_no_acredita_publicacion(self):
        self.api.omit_commit = True
        with self.assertRaises(monitor.MeteorologiaPublicationError) as caught:
            self.store.save_meteorologia(captura())
        self.assertEqual(caught.exception.stage, "verificacion")
        self.assertEqual(len(self.api.writes), 1)

    def test_rama_avanza_con_misma_captura_y_se_confirma(self):
        self.api.after_write = lambda api, old, written: api.add_commit(api.data)
        receipt = self.store.save_meteorologia(captura())
        self.assertEqual(receipt["sha"], self.api.sha)
        self.assertNotEqual(receipt["commit_sha"], self.api.head)
        self.assertEqual(len(self.api.head_cache_headers), 2)

    def test_fallos_http_son_seguros_sin_reintentar_escrituras(self):
        for failure, code in [(403, "http_403"), (409, "http_409"), ("timeout", "conexion")]:
            with self.subTest(failure=failure):
                self.api.failure = failure
                with self.assertRaises(monitor.MeteorologiaPublicationError) as caught:
                    self.store.save_meteorologia(captura())
                self.assertEqual(caught.exception.code, code)
                self.assertNotIn("private", str(caught.exception))
                self.assertNotIn("test-secret", str(caught.exception))
                self.assertEqual(self.api.writes, [])

    def test_captura_vencida_no_contacta_github(self):
        value = captura(datetime.now(timezone.utc) - timedelta(hours=4))
        with self.assertRaisesRegex(monitor.MeteorologiaPublicationError, "captura_invalida_o_vencida"):
            self.store.save_meteorologia(value)
        self.assertEqual(self.api.calls, [])


class MonitorMeteorologiaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.calls = []
        self.statuses = []
        self.frame = pd.DataFrame([{
            "localidad": "Rosario", "provincia": "Santa Fe", "nivel": "VERDE",
            "cobertura_meteorologica": True,
        }])
        self.app = SimpleNamespace(
            VERSION="test", NODOS=NODOS,
            _captura_meteorologica_monitor=captura(),
            _captura_meteorologica_error=None,
            obtener_secrets_telegram=lambda: ("secret", "channel"),
            obtener_panorama_hidrologico=lambda: {},
            obtener_riesgo_automatico=lambda **kwargs: self.frame,
            procesar_alertas_telegram=self.telegram,
            historial_envios_telegram=lambda limit: pd.DataFrame(),
        )
        self.store = SimpleNamespace(
            restore_db=lambda path: self.calls.append("restore"),
            save_db=lambda path: self.calls.append("db"),
            save_status=lambda status: self.statuses.append(copy.deepcopy(status)),
            save_red_integrada=lambda value: self.calls.append("red") or {},
            save_meteorologia=lambda value: self.calls.append("meteorologia") or {"consultado": value["consultado"]},
        )
        prepare = patch.object(monitor, "_preparar_red_integrada", return_value={"public": True})
        prepare.start()
        self.addCleanup(prepare.stop)
        guard = patch("requests.sessions.Session.request", side_effect=AssertionError("Red prohibida"))
        guard.start()
        self.addCleanup(guard.stop)

    def telegram(self, frame, guardar_registro):
        self.calls.append("telegram")
        guardar_registro()
        return {"configurado": True, "enviadas": 1}

    def run_monitor(self, **kwargs):
        return monitor.run_monitor(store=self.store, state_dir=self.temp.name,
                                   app_loader=lambda path: self.app, **kwargs)

    def test_publica_despues_red_y_nunca_guarda_datos_en_status(self):
        result = self.run_monitor()
        self.assertTrue(result["meteorologia_publicada"])
        self.assertEqual(self.calls[-2:], ["red", "meteorologia"])
        self.assertEqual(self.calls.count("telegram"), 1)
        self.assertEqual(result["result"]["enviadas"], 1)
        for status in self.statuses:
            self.assertNotIn("hourly", json.dumps(status))
            self.assertNotIn("datos", status)
            monitor._validate_status(status)

    def test_fallo_meteorologia_no_repite_telegram_y_deja_diagnostico(self):
        self.store.save_meteorologia = lambda value: (_ for _ in ()).throw(ValueError("private credential"))
        with self.assertRaisesRegex(monitor.MonitorError, "Meteorología compartida"):
            self.run_monitor()
        status = self.statuses[-1]
        self.assertTrue(status["red_integrada_publicada"])
        self.assertFalse(status["meteorologia_publicada"])
        self.assertFalse(status["ok"])
        self.assertEqual(status["health"], "error_meteorologia")
        self.assertEqual(status["health_evaluacion"], "ok")
        self.assertNotIn("private", json.dumps(status))
        self.assertEqual(self.calls.count("telegram"), 1)

    def test_publicacion_sola_falla_sin_telegram_ni_monitor_status(self):
        self.store.save_meteorologia = lambda value: (_ for _ in ()).throw(RuntimeError("secret"))
        with self.assertRaisesRegex(monitor.MonitorError, "Meteorología compartida"):
            self.run_monitor(publish_red_only=True)
        self.assertEqual(self.calls, ["red"])
        self.assertEqual(self.statuses, [])

    def test_publicacion_sola_confirma_captura_sin_telegram_ni_status(self):
        status = self.run_monitor(publish_red_only=True)
        self.assertTrue(status["meteorologia_publicada"])
        self.assertEqual(self.calls, ["red", "meteorologia"])
        self.assertEqual(self.statuses, [])

    def test_fallo_zc_queda_guardado_y_no_repite_envios(self):
        self.store.save_meteorologia_zc = lambda value: self.calls.append("zc")
        with patch.object(meteo, "crear_captura_zc", side_effect=meteo.CapturaMeteorologicaError("ZC timeout_lectura")):
            with self.assertRaisesRegex(monitor.MonitorError, "ZC timeout_lectura"):
                self.run_monitor()
        status = self.statuses[-1]
        self.assertEqual(status["health"], "error_meteorologia_zc")
        self.assertEqual(status["health_evaluacion"], "ok")
        self.assertFalse(status["ok"])
        self.assertIn("ZC timeout_lectura", status["errors"])
        self.assertTrue(status["red_integrada_publicada"])
        self.assertTrue(status["meteorologia_publicada"])
        self.assertFalse(status["meteorologia_zc_publicada"])
        self.assertNotIn("zc", self.calls)
        self.assertEqual(self.calls.count("telegram"), 1)

    def test_fallo_zc_detiene_publicacion_sola_sin_telegram_ni_status(self):
        self.store.save_meteorologia_zc = lambda value: self.calls.append("zc")
        with patch.object(meteo, "crear_captura_zc", side_effect=RuntimeError("private credential")):
            with self.assertRaisesRegex(monitor.MonitorError, "Zárate–Campana") as caught:
                self.run_monitor(publish_red_only=True)
        self.assertNotIn("private", str(caught.exception))
        self.assertEqual(self.calls, ["red", "meteorologia"])
        self.assertEqual(self.statuses, [])

    def test_recuperacion_zc_confirma_sin_telegram_ni_status(self):
        self.store.save_meteorologia_zc = lambda value: self.calls.append("zc") or {"sha": "confirmed"}
        with patch.object(meteo, "crear_captura_zc", return_value=captura()):
            status = self.run_monitor(publish_red_only=True)
        self.assertTrue(status["meteorologia_zc_publicada"])
        self.assertEqual(status["meteorologia_zc_confirmacion"], {"sha": "confirmed"})
        self.assertNotIn("meteorologia_zc_error", status)
        self.assertEqual(self.calls, ["red", "meteorologia", "zc"])
        self.assertEqual(self.statuses, [])

    def test_error_zc_no_oculta_error_del_canal(self):
        status = {"health": "envio_rechazado", "ok": False, "errors": ["Canal"],
                  "red_integrada_publicada": True, "meteorologia_zc_error": "ZC timeout_lectura"}
        monitor._guardar_resultado_publicacion(status, self.store)
        self.assertEqual(self.statuses[-1]["health"], "envio_rechazado")
        self.assertEqual(self.statuses[-1]["errors"], ["Canal", "ZC timeout_lectura"])

    def test_dry_run_no_publica_captura_disponible(self):
        status = self.run_monitor(dry_run=True)
        self.assertEqual(self.calls, [])
        self.assertFalse(status["meteorologia_publicada"])
        self.assertEqual(status["meteorologia_estado"], "no_solicitada")

    def test_app_sin_captura_conserva_compatibilidad_sin_acreditar_publicacion(self):
        del self.app._captura_meteorologica_monitor
        del self.app._captura_meteorologica_error
        del self.store.save_meteorologia
        status = self.run_monitor()
        self.assertTrue(status["ok"])
        self.assertFalse(status["meteorologia_publicada"])
        self.assertEqual(status["meteorologia_estado"], "no_disponible")

    def test_preparacion_fallida_no_se_confunde_con_captura_no_disponible(self):
        self.app._captura_meteorologica_monitor = None
        self.app._captura_meteorologica_error = "captura_no_valida"
        with self.assertRaisesRegex(monitor.MonitorError, "preparacion.*captura_no_valida"):
            self.run_monitor()
        status = self.statuses[-1]
        self.assertTrue(status["red_integrada_publicada"])
        self.assertFalse(status["meteorologia_publicada"])
        self.assertEqual(status["meteorologia_estado"], "error")
        self.assertEqual(status["meteorologia_error_codigo"], "captura_no_valida")
        self.assertEqual(status["health"], "error_meteorologia")
        self.assertFalse(status["ok"])
        self.assertNotIn("meteorologia", self.calls)
        self.assertEqual(self.calls.count("telegram"), 1)

    def test_preparacion_fallida_detiene_publicacion_sola(self):
        self.app._captura_meteorologica_monitor = None
        self.app._captura_meteorologica_error = "captura_no_valida"
        with self.assertRaisesRegex(monitor.MonitorError, "preparacion.*captura_no_valida"):
            self.run_monitor(publish_red_only=True)
        self.assertEqual(self.calls, ["red"])
        self.assertEqual(self.statuses, [])

    def test_dry_run_rechaza_preparacion_fallida_sin_filtrar_detalles(self):
        self.app._captura_meteorologica_error = "private credential"
        with self.assertRaises(monitor.MeteorologiaPublicationError) as caught:
            self.run_monitor(dry_run=True)
        self.assertEqual(caught.exception.stage, "preparacion")
        self.assertEqual(caught.exception.code, "captura_no_valida")
        self.assertNotIn("private", str(caught.exception))
        self.assertEqual(self.calls, [])
        self.assertEqual(self.statuses, [])
        monitor._preparar_red_integrada.assert_not_called()

    def test_red_no_confirmada_no_publica_captura(self):
        self.store.save_red_integrada = lambda value: (_ for _ in ()).throw(ValueError("private"))
        with self.assertRaises(monitor.MonitorError):
            self.run_monitor()
        self.assertNotIn("meteorologia", self.calls)
        self.assertFalse(self.statuses[-1]["meteorologia_publicada"])
        self.assertEqual(self.statuses[-1]["health"], "error_red_integrada")

    def test_captura_de_otro_catalogo_no_publica(self):
        self.app.NODOS = [{**NODOS[0], "localidad": "Otra localidad"}]
        with self.assertRaisesRegex(monitor.MonitorError, "captura_invalida_o_vencida"):
            self.run_monitor()
        self.assertNotIn("meteorologia", self.calls)
        self.assertEqual(self.statuses[-1]["health"], "error_meteorologia")


if __name__ == "__main__":
    unittest.main()
