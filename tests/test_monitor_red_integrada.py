"""Comprueba que compartir datos no cambia destinatarios, eventos ni persistencia."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

import pandas as pd


PATH = Path(__file__).resolve().parents[1] / "monitor.py"
spec = importlib.util.spec_from_file_location("monitor_red_test", PATH)
monitor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(monitor)


class TestRedMonitor(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.calls = []
        self.panorama = {"estaciones": {"rosario": {"altura_m": 2.1}}}
        self.frame = pd.DataFrame([{
            "localidad": "Rosario", "provincia": "Santa Fe", "nivel": "AMARILLO",
            "cobertura_meteorologica": True, "hidrologia_aplica": True,
            "hidrologia_vigente": True, "hidrologia_pronostico_vigente": True,
            "represa_aplica": True, "represa_vigente": True, "smn_vigente": True,
        }])
        self.app = types.SimpleNamespace(
            VERSION="V4.6.0", NODOS=[{"localidad": "Rosario"}],
            obtener_secrets_telegram=lambda: ("secret", "existing-channel"),
            obtener_panorama_hidrologico=self.get_panorama,
            obtener_riesgo_automatico=self.risk,
            procesar_alertas_telegram=self.process,
            historial_envios_telegram=lambda limit: pd.DataFrame(),
        )
        self.store = types.SimpleNamespace(
            restore_db=lambda path: self.calls.append("restore"),
            save_db=lambda path: self.calls.append("db"),
            save_status=lambda status: self.calls.append(("status", copy.deepcopy(status))),
            save_red_integrada=lambda value: self.calls.append("save_red"),
        )
        self.module = types.ModuleType("red_integrada")
        self.module.consultar_zc_red = lambda **kwargs: self.calls.append("query_zc") or {"nodes": "ZC"}
        self.module.construir_red_integrada = self.build
        self.module.validar_red_integrada = lambda value, **kwargs: copy.deepcopy(value)
        self.addCleanup(patch.stopall)
        patch.dict(sys.modules, {"red_integrada": self.module}).start()

    def get_panorama(self):
        self.calls.append("panorama")
        return self.panorama

    def risk(self, panorama):
        self.assertIs(panorama, self.panorama)
        self.calls.append("risk")
        return self.frame

    def process(self, frame, guardar_registro):
        self.assertIs(frame, self.frame)
        self.assertEqual(frame.localidad.tolist(), ["Rosario"])
        self.calls.append("telegram")
        guardar_registro()
        return {"configurado": True, "enviadas": 1}

    def build(self, frame, panorama, **kwargs):
        self.assertIs(frame, self.frame)
        self.assertEqual(panorama, self.panorama)
        self.calls.append("build_red")
        return {"schema_version": 1, "generated_at": kwargs["referencia"].isoformat(), "public": True}

    def run_monitor(self, **kwargs):
        return monitor.run_monitor(store=self.store, state_dir=self.temp.name,
                                   app_loader=lambda path: self.app, **kwargs)

    def test_un_panorama_y_publicacion_despues_estado(self):
        status = self.run_monitor()
        self.assertEqual(self.calls.count("panorama"), 1)
        self.assertEqual(self.calls.count("risk"), 1)
        self.assertEqual(self.calls.count("telegram"), 1)
        self.assertEqual(self.calls.count("query_zc"), 1)
        names = [call[0] if isinstance(call, tuple) else call for call in self.calls]
        self.assertLess(names.index("status"), names.index("query_zc"))
        self.assertLess(names.index("status"), names.index("save_red"))
        saved = next(call[1] for call in self.calls if isinstance(call, tuple))
        self.assertNotIn("red_integrada", saved)
        self.assertTrue(status["red_integrada_publicada"])
        self.assertTrue(status["ok"])
        self.assertEqual(status["nodes_total"], 1)
        self.assertEqual(status["result"]["enviadas"], 1)
        self.assertEqual(self.app.NODOS, [{"localidad": "Rosario"}])

    def test_dry_run_no_store_ni_telegram_ni_secrets(self):
        self.app.obtener_secrets_telegram = lambda: self.fail("No debe leer secretos")
        status = self.run_monitor(dry_run=True)
        self.assertEqual(self.calls, ["panorama", "risk", "query_zc", "build_red"])
        self.assertIn("red_integrada", status)
        self.assertEqual(status["health"], "dry_run")
        self.assertFalse(status["ok"])
        self.assertFalse(Path(self.app.TELEGRAM_DB_FILE).exists())

    def test_export_local_dry_run(self):
        status = self.run_monitor(dry_run=True)
        destination = Path(self.temp.name, "export.json")
        monitor._exportar_red_local(status["red_integrada"], destination)
        self.assertEqual(json.loads(destination.read_text()), status["red_integrada"])
        self.assertNotIn("save_red", self.calls)

    def test_compat_sin_getter_automatico_sin_kwargs(self):
        del self.app.obtener_panorama_hidrologico
        self.app.obtener_riesgo_automatico = lambda: self.calls.append("risk") or self.frame
        self.module.construir_red_integrada = lambda *args, **kwargs: {"schema_version": 1}
        status = self.run_monitor()
        self.assertNotIn("panorama", self.calls)
        self.assertEqual(status["result"]["enviadas"], 1)

    def test_consulta_red_fallida_no_cambia_canal(self):
        self.module.consultar_zc_red = lambda **kwargs: (_ for _ in ()).throw(TimeoutError("HTTP token secret"))
        status = self.run_monitor()
        self.assertTrue(status["ok"])
        self.assertEqual(status["result"]["enviadas"], 1)
        self.assertNotIn("save_red", self.calls)
        self.assertNotIn("secret", status["red_integrada_error"])
        self.assertEqual(self.calls.count("telegram"), 1)

    def test_publicacion_red_fallida_no_cambia_canal(self):
        self.store.save_red_integrada = lambda value: (_ for _ in ()).throw(monitor.StateStoreError("conflict"))
        status = self.run_monitor()
        self.assertTrue(status["ok"])
        self.assertEqual(status["result"]["enviadas"], 1)
        self.assertFalse(status["red_integrada_publicada"])
        self.assertIn("red_integrada_error", status)

    def test_rechazo_telegram_conserva_error_original_y_snapshot(self):
        self.app.procesar_alertas_telegram = lambda *args, **kwargs: {"configurado": True, "fallidas": 1}
        with self.assertRaisesRegex(monitor.MonitorError, "problema de envío"):
            self.run_monitor()
        saved = next(call[1] for call in self.calls if isinstance(call, tuple))
        self.assertEqual(saved["health"], "envio_rechazado")
        self.assertEqual(self.calls.count("save_red"), 1)

    def test_sin_datos_no_envios_snapshot_despues_estado(self):
        self.frame["nivel"] = "SIN DATOS"
        with self.assertRaisesRegex(monitor.MonitorError, "Ninguna localidad"):
            self.run_monitor()
        self.assertNotIn("telegram", self.calls)
        saved = next(call[1] for call in self.calls if isinstance(call, tuple))
        self.assertEqual(saved["health"], "sin_datos")
        self.assertEqual(self.calls.count("save_red"), 1)

    def test_status_original_contrato_sin_variaciones(self):
        status = self.run_monitor()
        saved = next(call[1] for call in self.calls if isinstance(call, tuple))
        self.assertIs(monitor._validate_status(saved), saved)
        self.assertEqual(saved["coverage"], status["coverage"])
        self.assertEqual(saved["history"], [])

    def test_store_leer_sha_red_independiente_antes_escribir(self):
        store = monitor.GitHubStateStore("secret", "owner/repo")
        order = []
        store._read_file = lambda path, maximum: order.append(("read", path, maximum))
        store._write_file = lambda path, payload, message: order.append(("write", path, json.loads(payload)))
        document = {"schema_version": 1, "public": True}
        store.save_red_integrada(document)
        self.assertEqual(order[0], ("read", "red_integrada.json", 500_000))
        self.assertEqual(order[1], ("write", "red_integrada.json", document))

    def test_store_no_escritura_cuando_validador_rechaza(self):
        self.module.validar_red_integrada = lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("private"))
        store = monitor.GitHubStateStore("secret", "owner/repo")
        store._read_file = lambda *args: self.fail("No debe contactar GitHub")
        with self.assertRaisesRegex(monitor.StateStoreError, "contrato público"):
            store.save_red_integrada({"token": "secret"})

    def test_store_limite_tamano(self):
        store = monitor.GitHubStateStore("secret", "owner/repo")
        store._read_file = lambda *args: self.fail("No debe contactar GitHub")
        with self.assertRaisesRegex(monitor.StateStoreError, "tamaño"):
            store.save_red_integrada({"public": "x" * 500_000})


if __name__ == "__main__":
    unittest.main(verbosity=2)
