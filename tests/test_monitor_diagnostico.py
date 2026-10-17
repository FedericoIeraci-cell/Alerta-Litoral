"""El fallo de evaluación aporta una causa técnica sin publicar datos externos."""

from contextlib import redirect_stderr, redirect_stdout
import io
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import monitor


class MonitorDiagnosticTests(unittest.TestCase):
    def test_cli_identifies_failed_stage_and_type_without_exception_contents(self):
        secret = "TOKEN_ARTIFICIAL_NO_PUBLICAR"
        private_message = f"https://api.example.invalid/?token={secret} body=respuesta_privada"
        cases = (
            ("obtener_panorama_hidrologico", requests.HTTPError(private_message), "HTTPError"),
            ("obtener_riesgo_automatico", TypeError(private_message), "TypeError"),
        )
        real_run = monitor.run_monitor
        for stage, error, expected_type in cases:
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as state_dir:
                app = SimpleNamespace(
                    obtener_panorama_hidrologico=Mock(return_value={}),
                    obtener_riesgo_automatico=Mock(),
                )
                getattr(app, stage).side_effect = error
                stderr, stdout = io.StringIO(), io.StringIO()

                def run_fake(*args, **kwargs):
                    return real_run("app.py", dry_run=True, state_dir=state_dir,
                                    app_loader=lambda path: app)

                with patch.dict(os.environ), patch.object(monitor, "run_monitor", side_effect=run_fake), \
                        patch("requests.sessions.Session.request", side_effect=AssertionError("Red prohibida")), \
                        redirect_stderr(stderr), redirect_stdout(stdout):
                    code = monitor.main(["--dry-run"])

                self.assertEqual(code, 1)
                self.assertIn(f"Diagnóstico: {expected_type} en {stage}.", stderr.getvalue())
                self.assertEqual(stdout.getvalue(), "")
                for private in (secret, private_message, "api.example.invalid", "respuesta_privada", "Traceback"):
                    self.assertNotIn(private, stderr.getvalue())
                if stage == "obtener_panorama_hidrologico":
                    app.obtener_riesgo_automatico.assert_not_called()
                else:
                    app.obtener_riesgo_automatico.assert_called_once_with(panorama={})

    def test_unknown_exception_name_is_not_copied_to_public_diagnostic(self):
        private_class = type("TOKEN_ARTIFICIAL_NO_PUBLICAR", (TypeError,), {})
        self.assertEqual(monitor._safe_exception_type(private_class("respuesta privada")), "TypeError")


if __name__ == "__main__":
    unittest.main()
