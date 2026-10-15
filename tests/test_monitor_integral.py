"""Pruebas sin red ni envíos de la integración del monitor automático."""

import copy
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import monitor


def node(**changes):
    value = {
        "localidad": "Punto de prueba", "provincia": "Corrientes", "nivel": "VERDE",
        "cobertura_meteorologica": True, "hidrologia_aplica": False,
        "hidrologia_vigente": False, "hidrologia_pronostico_vigente": False,
        "represa_aplica": False, "represa_vigente": False,
        "nivel_hidrologico_observado": "SIN DATOS",
        "nivel_hidrologico_previsto": "SIN DATOS", "hidrologia_fuentes": "",
    }
    value.update(changes)
    return value


class MemoryStore:
    """Conserva una fila anterior y registra las reservas que ve el callback."""
    def __init__(self):
        self.restored = False
        self.snapshots = []
        self.status = None

    def restore_db(self, path):
        self.restored = True
        with sqlite3.connect(path) as db:
            db.execute("CREATE TABLE estados_prueba (id TEXT PRIMARY KEY, estado TEXT)")
            db.execute("INSERT INTO estados_prueba VALUES (?, ?)",
                       ("registro-anterior", json.dumps({"nivel": "VERDE", "episodio": 7})))

    def save_db(self, path):
        with sqlite3.connect(path) as db:
            self.snapshots.append(dict(db.execute("SELECT id, estado FROM estados_prueba")))

    def save_status(self, status):
        monitor._validate_status(status)
        self.status = copy.deepcopy(status)

    def save_red_integrada(self, document):
        return {"sha": "a" * 40, "generated_at": document["generated_at"]}


class FakeApp:
    def __init__(self, rows):
        self.frame = pd.DataFrame(rows)
        self.NODOS = rows
        self.calls = 0
        self.processed = None
        self.fake_send_checks = []
        self.simulate_event = False
        self.store = None

    def obtener_secrets_telegram(self):
        return "credencial-de-prueba", "destino-de-prueba"

    def obtener_riesgo_automatico(self):
        self.calls += 1
        return self.frame.copy()

    def integrar_semaforo(self, *args):
        raise AssertionError("El monitor no debe volver al circuito meteorológico anterior")

    def obtener_todos_los_nodos(self):
        raise AssertionError("El monitor debe usar el mismo riesgo automático que el panel")

    def historial_envios_telegram(self, limit):
        return pd.DataFrame()

    def procesar_alertas_telegram(self, frame, guardar_registro):
        self.processed = frame.copy()
        if self.simulate_event:
            with sqlite3.connect(self.TELEGRAM_DB_FILE) as db:
                previous = db.execute("SELECT estado FROM estados_prueba WHERE id=?",
                                      ("registro-anterior",)).fetchone()
                self.fake_send_checks.append(json.loads(previous[0]))
                db.execute("INSERT INTO estados_prueba VALUES (?, ?)",
                           ("evento-nuevo", "reservado"))
            guardar_registro()
            # No se llama a Telegram: se comprueba la reserva que ya vio el
            # almacenamiento durable antes de simular la confirmación.
            assert self.store.snapshots[-1]["evento-nuevo"] == "reservado"
            with sqlite3.connect(self.TELEGRAM_DB_FILE) as db:
                db.execute("UPDATE estados_prueba SET estado=? WHERE id=?",
                           ("enviado", "evento-nuevo"))
            guardar_registro()
        return {"configurado": True, "enviadas": int(self.simulate_event)}


class IntegralMonitorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        # Cualquier intento accidental de red hace fallar estas pruebas.
        self.network = patch("requests.sessions.Session.request",
                             side_effect=AssertionError("Red prohibida en las pruebas"))
        self.network.start()
        self.addCleanup(self.network.stop)

    def run_fake(self, rows, dry_run=False, simulate_event=False):
        app = FakeApp(rows)
        store = MemoryStore()
        app.simulate_event = simulate_event
        app.store = store
        status = monitor.run_monitor(
            "app.py", store=store, dry_run=dry_run, state_dir=self.temp.name,
            app_loader=lambda path: app,
        )
        return status, app, store

    def test_common_automatic_source_and_dry_run_have_no_writes(self):
        status, app, store = self.run_fake([node(nivel="ROJO")], dry_run=True)
        self.assertEqual(app.calls, 1)
        self.assertIsNone(app.processed)
        self.assertFalse(store.restored)
        self.assertEqual(store.snapshots, [])
        self.assertEqual(status["scope"], monitor.MONITOR_SCOPE)
        self.assertEqual(status["nodes_red"], 1)

    def test_coverage_uses_river_applicable_denominator(self):
        status, _, _ = self.run_fake([
            node(hidrologia_aplica=True, hidrologia_vigente=True,
                 hidrologia_pronostico_vigente=True),
            node(localidad="Punto interior"),
        ], dry_run=True)
        self.assertEqual(status["coverage"]["meteorologia"], {"vigentes": 2, "aplicables": 2})
        self.assertEqual(status["coverage"]["hidrologia_observada"], {"vigentes": 1, "aplicables": 1})
        self.assertEqual(status["coverage"]["hidrologia_prevista"], {"vigentes": 1, "aplicables": 1})

    def test_complete_meteorology_does_not_claim_complete_river_coverage(self):
        status, _, store = self.run_fake([node(hidrologia_aplica=True)])
        self.assertEqual(status["nodes_valid"], 1)
        self.assertEqual(status["coverage"]["meteorologia"]["vigentes"], 1)
        self.assertEqual(status["coverage"]["hidrologia_observada"]["vigentes"], 0)
        self.assertEqual(status["health"], "datos_parciales")
        self.assertFalse(status["ok"])
        self.assertIn("observaciones fluviales", " ".join(status["errors"]))
        self.assertEqual(store.status, {k:v for k,v in status.items() if k != "red_integrada"})

    def test_severe_current_hydrology_is_evaluated_despite_missing_meteorology(self):
        status, app, _ = self.run_fake([node(
            nivel="ROJO", cobertura_meteorologica=False, hidrologia_aplica=True,
            hidrologia_vigente=True, hidrologia_pronostico_vigente=True,
            nivel_hidrologico_observado="ROJO",
        )])
        self.assertEqual(status["nodes_red"], 1)
        self.assertEqual(status["nodes_valid"], 1)
        self.assertEqual(status["coverage"]["meteorologia"]["vigentes"], 0)
        self.assertEqual(app.processed.iloc[0]["nivel_hidrologico_observado"], "ROJO")

    def test_nan_is_not_counted_as_current_data(self):
        coverage = monitor._coverage_summary(pd.DataFrame([node(
            cobertura_meteorologica=np.nan, hidrologia_aplica=np.bool_(True),
            hidrologia_vigente=np.nan, hidrologia_pronostico_vigente=None,
        )]))
        self.assertEqual(coverage["meteorologia"]["vigentes"], 0)
        self.assertEqual(coverage["hidrologia_observada"], {"vigentes": 0, "aplicables": 1})

    def test_dam_coverage_has_its_own_applicability(self):
        coverage = monitor._coverage_summary(pd.DataFrame([
            node(represa_aplica=True, represa_vigente=True),
            node(localidad="Sin represa"),
        ]))
        self.assertEqual(coverage["represas"], {"vigentes": 1, "aplicables": 1})

    def test_smn_coverage_is_optional_for_previous_contract(self):
        status, _, _ = self.run_fake([node()], dry_run=True)
        self.assertNotIn("avisos_smn", status["coverage"])
        self.assertEqual(status["errors"], [])

    def test_present_smn_column_reports_missing_coverage_without_claiming_no_alerts(self):
        status, _, _ = self.run_fake([
            node(smn_vigente=True, smn_rango=0),
            node(localidad="Canal incompleto", smn_vigente=False, smn_rango=0),
        ])
        self.assertEqual(status["coverage"]["avisos_smn"], {"vigentes": 1, "aplicables": 2})
        self.assertEqual(status["health"], "datos_parciales")
        self.assertIn("canal de avisos SMN", " ".join(status["errors"]))

    def test_previous_and_new_scope_restore_without_new_schema(self):
        status, _, _ = self.run_fake([node()], dry_run=True)
        for scope in (monitor.LEGACY_SCOPE, monitor.MONITOR_SCOPE):
            value = dict(status, scope=scope)
            if scope == monitor.LEGACY_SCOPE:
                value.pop("coverage")
            self.assertIs(monitor._validate_status(value), value)

    def test_existing_state_and_reservation_callback_are_preserved(self):
        status, app, store = self.run_fake([node(nivel="ROJO")], simulate_event=True)
        self.assertEqual(app.fake_send_checks, [{"nivel": "VERDE", "episodio": 7}])
        self.assertEqual(store.snapshots[0]["evento-nuevo"], "reservado")
        self.assertEqual(store.snapshots[1]["evento-nuevo"], "enviado")
        self.assertIn("registro-anterior", store.snapshots[-1])
        self.assertEqual(status["result"]["enviadas"], 1)

    def test_missing_common_integration_fails_instead_of_silent_weather_only(self):
        app = FakeApp([node()])
        app.obtener_riesgo_automatico = None
        with self.assertRaisesRegex(monitor.MonitorError, "integración automática"):
            monitor.run_monitor("app.py", dry_run=True, state_dir=self.temp.name,
                                app_loader=lambda path: app)


if __name__ == "__main__":
    unittest.main()
