"""Producción completa ZC fuera de Streamlit, sin avisos ni fechas renovadas."""
from datetime import datetime, timedelta, timezone
import ast
from collections import Counter
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import meteorologia_compartida as met
import monitor
from red_integrada import NODOS_ZC

NOW = datetime.now(timezone.utc).replace(microsecond=0)


class Response:
    def __init__(self, data): self.data = data; self.closed = False
    def raise_for_status(self): pass
    def json(self): return self.data
    def close(self): self.closed = True


class Session:
    def __init__(self, fail=False): self.calls = []; self.responses = []; self.fail = fail
    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.fail: raise TimeoutError("No mostrar datos privados del transporte")
        nodes = list(zip(kwargs["params"]["latitude"].split(","), kwargs["params"]["longitude"].split(",")))
        first = NOW.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=8)
        times = [(first + timedelta(hours=i)).replace(tzinfo=None).isoformat() for i in range(552)]
        daily = [(first + timedelta(days=i)).date().isoformat() for i in range(23)]
        data = [{"latitude": float(lat), "longitude": float(lon),
                 "timezone": "America/Argentina/Buenos_Aires", "utc_offset_seconds": -10800,
                 "hourly_units": {"time": "iso8601", "temperature_2m": "°C", "precipitation": "mm", "wind_speed_10m": "km/h"},
                 "hourly": {"time": times, "temperature_2m": [20.0] * 552,
                            "precipitation": [0.0] * 552, "wind_speed_10m": [15.0] * 552},
                 "daily_units": {"time": "iso8601", "temperature_2m_max": "°C"},
                 "daily": {"time": daily, "temperature_2m_max": [24.0] * 23}}
                for lat, lon in nodes]
        response = Response(data); self.responses.append(response); return response


class CompleteCaptureTests(unittest.TestCase):
    def test_colector_completo_timeout_catalogo_fecha_y_ceros(self):
        session = Session()
        capture = met.crear_captura_zc(referencia=NOW, session=session)
        self.assertEqual(len(capture["datos"]), 10)
        self.assertEqual(capture["nodos"], met._catalogo(NODOS_ZC))
        self.assertEqual(datetime.fromisoformat(capture["consultado"]), NOW)
        self.assertEqual(len(session.calls), 5)
        for url, kw in session.calls:
            self.assertEqual(url, met.FUENTE)
            self.assertEqual(kw["timeout"], (20, 45))
            self.assertIn("temperature_2m", kw["params"]["hourly"])
            self.assertIn("wind_speed_10m", kw["params"]["hourly"])
            self.assertIn("temperature_2m_max", kw["params"]["daily"])
            self.assertEqual(list(kw["params"])[:2], ["latitude", "longitude"])
            self.assertEqual(len(kw["params"]["latitude"].split(",")), 2)
            self.assertEqual(kw["headers"], {"User-Agent": "Alerta-Litoral-Agro/3.10.2"})
        self.assertTrue(all(r.closed for r in session.responses))
        encoded = met.serializar_captura(capture)
        decoded = met.deserializar_captura(encoded, NODOS_ZC, referencia=NOW)
        self.assertEqual(capture, decoded)
        self.assertEqual(decoded["datos"][0]["hourly"]["precipitation"][0], 0.0)
        self.assertEqual(len(decoded["datos"][0]["daily"]["time"]), 23)

    def test_fallo_total_no_fabrica_captura(self):
        with self.assertRaises(met.CapturaMeteorologicaError):
            met.crear_captura_zc(referencia=NOW, session=Session(fail=True))

    def test_reintenta_una_vez_timeout_o_conexion_y_conserva_fecha(self):
        for error in (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
            with self.subTest(error=error.__name__):
                class TransientSession(Session):
                    def __init__(self): super().__init__(); self.attempts = {}
                    def get(self, url, **kwargs):
                        key = kwargs["params"]["latitude"]
                        self.attempts[key] = self.attempts.get(key, 0) + 1
                        if self.attempts[key] == 1:
                            raise error("Detalle privado")
                        return super().get(url, **kwargs)
                session = TransientSession()
                capture = met.crear_captura_zc(referencia=NOW, session=session)
                self.assertEqual(sorted(session.attempts.values()), [2] * 5)
                self.assertEqual(datetime.fromisoformat(capture["consultado"]), NOW)
                self.assertEqual(len(capture["datos"]), 10)

    def test_no_reintenta_http_429_y_cierra_respuestas(self):
        session = Session()
        error = requests.exceptions.HTTPError("429 privado", response=SimpleNamespace(status_code=429))
        with patch.object(Response, "raise_for_status", side_effect=error):
            with self.assertRaisesRegex(met.CapturaMeteorologicaError, "http_429"):
                met.crear_captura_zc(referencia=NOW, session=session)
        self.assertTrue(1 <= len(session.calls) <= 5)
        self.assertEqual(len({kw["params"]["latitude"] for _, kw in session.calls}), len(session.calls))
        self.assertTrue(all(r.closed for r in session.responses))

    def test_orden_variables_identico_al_getter_ala(self):
        tree = ast.parse((Path(__file__).resolve().parents[1] / "app.py").read_text())
        getter = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "obtener_datos_ecmwf")
        variables = {n.targets[0].id: ast.literal_eval(n.value) for n in getter.body
                     if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name)
                     and n.targets[0].id in {"variables_horarias", "variables_diarias"}}
        self.assertEqual(tuple(variables["variables_horarias"]), met._ORDEN_HORARIAS)
        self.assertEqual(tuple(variables["variables_diarias"]), met._ORDEN_DIARIAS)

    def test_diagnostico_de_transporte_es_acotado_sin_detalles_privados(self):
        for error, expected in [(requests.exceptions.ConnectTimeout, "timeout_conexion"),
                                (requests.exceptions.ReadTimeout, "timeout_lectura"),
                                (requests.exceptions.ConnectionError, "conexion")]:
            with self.subTest(error=error.__name__):
                session = SimpleNamespace(get=Mock(side_effect=error("Detalle privado")))
                with self.assertRaises(met.CapturaMeteorologicaError) as raised:
                    met.crear_captura_zc(referencia=NOW, session=session)
                self.assertIn(expected, str(raised.exception))
                self.assertNotIn("Detalle privado", str(raised.exception))
                attempts = Counter(call.kwargs["params"]["latitude"] for call in session.get.call_args_list)
                self.assertTrue(attempts)
                self.assertTrue(all(count == 2 for count in attempts.values()))

    def test_monitor_conserva_solo_diagnostico_publico(self):
        status = {"red_integrada_publicada": True}
        store = SimpleNamespace(save_meteorologia_zc=Mock())
        with patch.object(met, "crear_captura_zc", side_effect=met.CapturaMeteorologicaError("lote de 8 nodos, timeout_lectura")):
            monitor._publicar_meteorologia_zc(status, store)
        self.assertEqual(status["meteorologia_zc_error"], "lote de 8 nodos, timeout_lectura")
        store.save_meteorologia_zc.assert_not_called()

    def test_lote_fallido_tras_dos_intentos_no_sustituye_captura_completa(self):
        class PartialSession(Session):
            def __init__(self): super().__init__(); self.failed_attempts = 0
            def get(self, url, **kwargs):
                if kwargs["params"]["latitude"].split(",")[0] == f'{NODOS_ZC[8]["lat"]:.6f}':
                    self.failed_attempts += 1
                    raise requests.exceptions.Timeout("Detalle privado")
                return super().get(url, **kwargs)
        session = PartialSession()
        store = SimpleNamespace(save_meteorologia_zc=Mock())
        status = {"red_integrada_publicada": True}
        original = met.crear_captura_zc
        with patch.object(met, "crear_captura_zc", side_effect=lambda: original(referencia=NOW, session=session)):
            monitor._publicar_meteorologia_zc(status, store)
        self.assertEqual(session.failed_attempts, 2)
        store.save_meteorologia_zc.assert_not_called()
        self.assertTrue(status["red_integrada_publicada"])
        self.assertFalse(status["meteorologia_zc_publicada"])
        self.assertNotIn("Detalle privado", status["meteorologia_zc_error"])

    def test_archivo_zc_separado_reutiliza_verificacion_existente(self):
        store = object.__new__(monitor.GitHubStateStore)
        store._save_meteorologia = Mock(return_value={"sha": "confirmed"})
        capture = {"nodos": "fixture"}
        self.assertEqual(store.save_meteorologia_zc(capture), {"sha": "confirmed"})
        args = store._save_meteorologia.call_args.args
        self.assertIs(args[0], capture)
        self.assertEqual(args[1], "meteorologia_zc_ecmwf.json")

    def test_publicacion_solo_datos_no_envia_ni_toca_red(self):
        capture = {"nodos": "fixture"}; store = SimpleNamespace(save_meteorologia_zc=Mock(return_value={"sha": "ok"}))
        status = {"red_integrada_publicada": True, "result": {"enviadas": 0}}
        with patch.object(met, "crear_captura_zc", return_value=capture):
            monitor._publicar_meteorologia_zc(status, store)
        store.save_meteorologia_zc.assert_called_once_with(capture)
        self.assertTrue(status["meteorologia_zc_publicada"])
        self.assertTrue(status["red_integrada_publicada"])
        self.assertEqual(status["result"], {"enviadas": 0})

    def test_fallo_captura_preserva_informe_reducido_y_no_escribe_archivo(self):
        store = SimpleNamespace(save_meteorologia_zc=Mock())
        status = {"red_integrada_publicada": True}
        with patch.object(met, "crear_captura_zc", side_effect=ValueError("No divulgar")):
            monitor._publicar_meteorologia_zc(status, store)
        store.save_meteorologia_zc.assert_not_called()
        self.assertTrue(status["red_integrada_publicada"])
        self.assertFalse(status["meteorologia_zc_publicada"])
        self.assertNotIn("No divulgar", status["meteorologia_zc_error"])

    def test_store_antiguo_sin_metodo_no_dispara_nuevas_consultas(self):
        with patch.object(met, "crear_captura_zc", side_effect=AssertionError("No consultar")):
            monitor._publicar_meteorologia_zc({}, SimpleNamespace())

    def test_resumen_cli_incluye_confirmacion_publica_zc(self):
        status = {"checked_at_art": NOW.isoformat(), "health": "datos_parciales", "nodes_total": 48,
                  "nodes_valid": 48, "nodes_red": 0, "scope": monitor.MONITOR_SCOPE,
                  "coverage": {}, "result": {}, "dry_run": True, "publication_only": True,
                  "meteorologia_zc_publicada": True, "meteorologia_zc_error": None,
                  "meteorologia_zc_confirmacion": {"consultado": NOW.isoformat(), "sha": "confirmed"}}
        with patch.object(monitor, "run_monitor", return_value=status), patch("builtins.print") as output:
            self.assertEqual(monitor.main(["--publish-red-only"]), 0)
        summary = json.loads(output.call_args.args[0])
        for key in ("meteorologia_zc_publicada", "meteorologia_zc_error", "meteorologia_zc_confirmacion"):
            self.assertEqual(summary[key], status[key])


if __name__ == "__main__": unittest.main()
