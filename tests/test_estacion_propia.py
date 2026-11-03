"""Fallos de telemetría y aislamiento. Todas las respuestas son simuladas."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from archivo_estacion import StationArchive, main
from estacion_propia import (API_URL, MAX_BYTES, EcowittConnector, StationError,
                             acquire, adapt_ecowitt, assess, advance_reference, configuration)

FIXTURE = Path(__file__).parent / "fixtures/ecowitt_simulada.json"
NOW = datetime.fromtimestamp(1792454400, timezone.utc)


def record(**changes):
    out = adapt_ecowitt(json.loads(FIXTURE.read_text()), "terraza_test", received_at=NOW, simulated=True)
    out.update(changes)
    return out


def config():
    return configuration({"ALA_PWS_ENABLED": True, "ECOWITT_APPLICATION_KEY": "FAKE_APP_KEY",
                          "ECOWITT_API_KEY": "FAKE_SECRET", "ECOWITT_MAC": "00:00:00:00:00:00"})


class Response:
    def __init__(self, payload=None, status=200, body=None):
        self.status_code = status
        self.body = body if body is not None else json.dumps(payload or json.loads(FIXTURE.read_text())).encode()
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass
    def iter_content(self, size):
        for i in range(0, len(self.body), size):
            yield self.body[i:i + size]


class QualityTests(unittest.TestCase):
    def test_units_and_distinct_pressure_and_rain_sensors(self):
        result = assess(record(), now=NOW)
        m = result["measurements"]
        self.assertAlmostEqual(m["temperature"]["value"], 25)
        self.assertAlmostEqual(m["wind_speed"]["value"], 4.4704)
        self.assertAlmostEqual(m["pressure_absolute"]["value"], 1000.001, places=2)
        self.assertEqual(m["pressure_relative"]["value"], 1013.2)
        self.assertEqual(m["rain_rate"]["value"], 0)
        self.assertAlmostEqual(m["rain_daily"]["value"], 2.54)
        self.assertEqual(m["rain_piezo_daily"]["value"], 3.1)
        self.assertTrue(all(mv["usable"] for mv in m.values()))
        self.assertFalse(result["risk_eligible"])

    def test_missing_not_zero_and_no_indoor_fallback(self):
        payload = {"code": 0, "data": {"indoor": {"temperature": {"time": 1792454400, "unit": "°C", "value": 27}}}}
        out = assess(adapt_ecowitt(payload, "test", received_at=NOW), now=NOW)
        self.assertEqual(out["status"], "SIN_DATOS")
        self.assertIsNone(out["measurements"]["temperature"]["value"])

    def test_invalid_values_units_ranges_and_dates_preserved_but_not_usable(self):
        for change, flag in [({"value": "NaN"}, "valor_invalido"),
                             ({"value": float("inf")}, "valor_invalido"),
                             ({"value": True}, "valor_invalido"),
                             ({"value": "--"}, "valor_invalido"),
                             ({"unit": "K"}, "unidad_desconocida"),
                             ({"value": -1}, "fuera_de_rango"),
                             ({"observed_at": "2026-10-20T12:00:00"}, "fecha_invalida"),
                             ({"observed_at": None}, "fecha_invalida"),
                             ({"observed_at": 1792454400000}, "fecha_invalida")]:
            with self.subTest(change=change):
                r = record()
                r["measurements"]["humidity"].update(change)
                m = assess(r, now=NOW)["measurements"]["humidity"]
                self.assertIn(flag, m["flags"])
                self.assertFalse(m["usable"])
                json.dumps(m, allow_nan=False)

    def test_freshness_uses_each_sensor_not_request_or_response_time(self):
        r = record(received_at=(NOW + timedelta(hours=1)).isoformat())
        r["measurements"]["temperature"]["observed_at"] = (NOW - timedelta(hours=2)).isoformat()
        r["measurements"]["humidity"]["observed_at"] = (NOW + timedelta(minutes=3)).isoformat()
        m = assess(r, now=NOW)["measurements"]
        self.assertIn("dato_vencido", m["temperature"]["flags"])
        self.assertIn("fecha_futura", m["humidity"]["flags"])
        self.assertTrue(m["rain_rate"]["usable"])
        self.assertFalse(any(v["usable"] for v in assess(record(), now=NOW + timedelta(hours=1))["measurements"].values()))

    def test_spikes_are_not_silently_corrected_and_stay_flagged_on_repeat(self):
        before = assess(record(), now=NOW)
        r = record()
        r["measurements"]["temperature"].update(value=110, observed_at=(NOW + timedelta(minutes=1)).isoformat())
        after = assess(r, now=NOW + timedelta(minutes=1), previous=before)
        self.assertIn("salto_a_revisar", after["measurements"]["temperature"]["flags"])
        again = assess(r, now=NOW + timedelta(minutes=2), previous=after)
        self.assertFalse(again["measurements"]["temperature"]["usable"])
        self.assertGreater(again["measurements"]["temperature"]["value"], 40)

    def test_reset_and_out_of_order_and_conflict(self):
        before = assess(record(), now=NOW)
        for time, value, flag in [(NOW + timedelta(minutes=1), 0, "reinicio_acumulado"),
                                  (NOW - timedelta(minutes=1), .1, "fuera_de_orden"),
                                  (NOW, .2, "conflicto_misma_fecha")]:
            r = record()
            r["measurements"]["rain_daily"].update(value=value, observed_at=time.isoformat())
            self.assertIn(flag, assess(r, now=NOW, previous=before)["measurements"]["rain_daily"]["flags"])

    def test_other_station_or_simulation_cannot_be_a_reference(self):
        before = assess(record(station_id="elsewhere"), now=NOW)
        r = record()
        r["measurements"]["rain_daily"]["observed_at"] = (NOW - timedelta(minutes=1)).isoformat()
        self.assertNotIn("fuera_de_orden", assess(r, now=NOW, previous=before)["measurements"]["rain_daily"]["flags"])

    def test_partial_or_old_packets_cannot_move_sensor_reference_backwards(self):
        newest = assess(record(), now=NOW)
        old = record()
        old["measurements"]["temperature"]["observed_at"] = (NOW - timedelta(minutes=2)).isoformat()
        del old["measurements"]["humidity"]
        quality = assess(old, now=NOW, previous=newest)
        reference = advance_reference(newest, quality)
        self.assertEqual(reference["measurements"]["temperature"]["observed_at"], NOW.isoformat())
        self.assertEqual(reference["measurements"]["humidity"]["observed_at"], NOW.isoformat())
        old["measurements"]["temperature"]["observed_at"] = (NOW - timedelta(minutes=1)).isoformat()
        self.assertIn("fuera_de_orden", assess(old, now=NOW, previous=reference)["measurements"]["temperature"]["flags"])

    def test_contract_rejects_model_and_official_data_and_ignores_risk_claim(self):
        for change in ({"schema_version": 2}, {"source_kind": "official"}, {"source_kind": "model"}, {"station_id": "<script>"}):
            with self.subTest(change=change), self.assertRaises(StationError):
                assess(record(**change), now=NOW)
        self.assertFalse(assess(record(risk_eligible=True), now=NOW)["risk_eligible"])


class TransportTests(unittest.TestCase):
    def test_disabled_means_no_network_even_with_keys(self):
        session = Mock()
        out = acquire({**config(), "enabled": False}, session=session)
        self.assertEqual(out["status"], "DESACTIVADA")
        session.get.assert_not_called()

    def test_missing_keys_and_unknown_provider_fail_closed(self):
        session = Mock()
        for c in (configuration({"ALA_PWS_ENABLED": True}), {**config(), "provider": "unknown"}):
            self.assertIsNone(acquire(c, session=session)["record"])
        session.get.assert_not_called()

    def test_transport_options_and_no_credentials_in_result(self):
        session = Mock(get=Mock(return_value=Response()))
        out = EcowittConnector(config(), session).fetch(now=NOW)
        args, kw = session.get.call_args
        self.assertEqual(args, (API_URL,))
        self.assertFalse(kw["allow_redirects"])
        self.assertEqual(kw["timeout"], (3, 8))
        self.assertEqual(kw["params"]["rainfall_unitid"], 12)
        self.assertNotIn("FAKE_SECRET", json.dumps(out))

    def test_timeout_429_http_redirect_invalid_json_and_oversize(self):
        import requests
        for response, code in [(Response(status=429), "limite_consultas"),
                               (Response(status=500), "error_http"),
                               (Response(status=302), "error_http"),
                               (Response(body=b"bad"), "respuesta_invalida"),
                               (Response(body=b" " * (MAX_BYTES + 1)), "respuesta_demasiado_grande"),
                               (Response(payload={"code": 400, "msg": "FAKE_SECRET"}), "respuesta_proveedor_invalida")]:
            with self.subTest(code=code):
                session = Mock(get=Mock(return_value=response))
                self.assertEqual(acquire(config(), now=NOW, session=session), {"status": code, "record": None})
                self.assertEqual(session.get.call_count, 1)
        session = Mock(get=Mock(side_effect=requests.Timeout("FAKE_SECRET")))
        self.assertEqual(acquire(config(), session=session)["status"], "sin_conexion")


class ArchiveTests(unittest.TestCase):
    def test_restart_duplicates_conflicts_and_failed_queries_preserve_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "obs.sqlite3"
            archive = StationArchive(path)
            self.assertTrue(archive.save(record(), now=NOW)[1])
            archive = StationArchive(path)
            self.assertFalse(archive.save(record(received_at=(NOW + timedelta(seconds=30)).isoformat()), now=NOW)[1])
            conflict = record()
            conflict["measurements"]["humidity"]["value"] = 70
            quality, inserted = archive.save(conflict, now=NOW)
            self.assertTrue(inserted)
            self.assertIn("conflicto_misma_fecha", quality["measurements"]["humidity"]["flags"])
            archive.attempt("sin_conexion", NOW)
            with archive.connect() as db:
                self.assertEqual(db.execute("SELECT count(*) FROM observations").fetchone()[0], 2)
                self.assertEqual(db.execute("SELECT status FROM attempts").fetchone()[0], "sin_conexion")

    def test_future_does_not_displace_latest_and_unknown_keys_not_archived(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = StationArchive(Path(tmp) / "obs.sqlite3")
            a.save(record(), now=NOW)
            future = record()
            for item in future["measurements"].values():
                item["observed_at"] = (NOW + timedelta(days=1)).isoformat()
            future["secret"] = "FAKE_SECRET"
            a.save(future, now=NOW)
            self.assertEqual(a.previous(record())["measurements"]["humidity"]["observed_at"], NOW.isoformat())
            with a.connect() as db:
                self.assertNotIn("FAKE_SECRET", str(db.execute("SELECT record_json,quality_json FROM observations").fetchall()))

    def test_fixture_cli_never_claims_real_measurements(self):
        with tempfile.TemporaryDirectory() as tmp, patch("builtins.print"):
            path = Path(tmp) / "obs.sqlite3"
            self.assertEqual(main(["--db", str(path), "--simulate", str(FIXTURE)]), 0)
            with StationArchive(path).connect() as db:
                self.assertEqual(db.execute("SELECT source_kind FROM observations").fetchone()[0], "simulated")


class PanelTests(unittest.TestCase):
    def test_panel_disabled_and_failure_and_stale_reading(self):
        from streamlit.testing.v1 import AppTest
        import panel_estacion
        script = "from panel_estacion import mostrar_estacion_propia\nmostrar_estacion_propia()"
        with patch.dict(os.environ, {"ALA_PWS_ENABLED": "false"}), patch("panel_estacion.acquire") as http:
            at = AppTest.from_file(str(Path(__file__).parents[1] / "pages/1_Estacion_propia.py")).run()
            self.assertEqual(list(at.exception), [])
            http.assert_not_called()
            self.assertIn("desactivada", at.info[0].value)
        with patch.dict(os.environ, {"ALA_PWS_ENABLED": "true"}), patch("panel_estacion._read", return_value={"status": "sin_conexion", "record": None}):
            at = AppTest.from_string(script).run()
            self.assertEqual(list(at.exception), [])
            self.assertTrue(at.warning)
        with patch.dict(os.environ, {"ALA_PWS_ENABLED": "true"}), patch("panel_estacion._read", return_value={"status": "RECIBIDA", "record": record()}), patch("panel_estacion.assess", side_effect=lambda r, **kw: assess(r, now=NOW + timedelta(hours=1), **kw)):
            at = AppTest.from_string(script).run()
            self.assertEqual(list(at.exception), [])
            self.assertTrue(all(at.dataframe[0].value["Valor"] == "—"))


if __name__ == "__main__":
    unittest.main()
