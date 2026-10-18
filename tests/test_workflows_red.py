"""Contrato de seguridad entre publicación frecuente y vigilancia con envíos."""
from pathlib import Path
import shlex
import unittest

import yaml


WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"


def workflow(name):
    # BaseLoader conserva la clave YAML `on` y evita conversiones YAML 1.1.
    return yaml.load((WORKFLOWS / name).read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


class PublicationWorkflowSafetyTests(unittest.TestCase):
    def test_publicador_frecuente_solo_puede_ejecutar_publicacion_sin_envios(self):
        document = workflow("ala-red-integrada.yml")
        self.assertEqual(set(document["on"]), {"schedule", "workflow_dispatch"})
        self.assertFalse(document["on"]["workflow_dispatch"])
        self.assertEqual(document["permissions"], {"contents": "write"})
        self.assertEqual(set(document["jobs"]), {"publicar"})
        steps = document["jobs"]["publicar"]["steps"]
        commands = [shlex.split(step["run"]) for step in steps if "run" in step]
        self.assertEqual(commands, [
            ["python", "-m", "pip", "install", "-r", "requirements_monitor.txt"],
            ["python", "monitor.py", "--publish-red-only"],
        ])
        text = yaml.dump(document)
        self.assertNotIn("secrets.", text)
        self.assertNotIn("TELEGRAM", text)
        self.assertNotIn("continue-on-error", text)
        environment = steps[-1]["env"]
        self.assertEqual(environment["GITHUB_TOKEN"], "${{ github.token }}")
        self.assertNotIn("ALA_MONITOR_FREQUENCY_MINUTES", environment)
        checkout = next(step for step in steps if step.get("uses", "").startswith("actions/checkout@"))
        self.assertEqual(checkout["with"]["ref"], "${{ github.event.repository.default_branch }}")
        self.assertEqual(checkout["with"]["persist-credentials"], "false")

    def test_escritores_serializados_sin_cancelar_vigilancia_pendiente(self):
        monitor = workflow("ala-monitor.yml")
        publisher = workflow("ala-red-integrada.yml")
        validation = workflow("ala-validacion.yml")
        expected = {
            "group": "ala-monitor-${{ github.repository }}",
            "cancel-in-progress": "false",
            "queue": "max",
        }
        for actual in [monitor["concurrency"], publisher["concurrency"],
                       validation["jobs"]["publicar-red"]["concurrency"]]:
            self.assertEqual(actual, expected)
        for event in ("push", "pull_request"):
            self.assertIn(".github/workflows/*.yml", validation["on"][event]["paths"])

    def test_cadencias_separadas_no_atribuyen_envios_al_publicador(self):
        publication = workflow("ala-red-integrada.yml")["on"]["schedule"]
        self.assertEqual(len(publication), 1)
        minutes, *remaining = publication[0]["cron"].split()
        self.assertEqual(remaining, ["*"] * 4)
        values = [int(value) for value in minutes.split(",")]
        self.assertEqual(values, list(range(3, 60, 10)))
        monitor = workflow("ala-monitor.yml")
        self.assertEqual(monitor["on"]["schedule"], [{"cron": "7 * * * *"}])
        send = monitor["jobs"]["vigilar"]["steps"][-1]
        self.assertEqual(send["env"]["ALA_MONITOR_FREQUENCY_MINUTES"], "60")


if __name__ == "__main__":
    unittest.main()
