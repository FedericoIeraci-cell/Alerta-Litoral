"""Publicación realista mediante API simulada; toda red externa está prohibida."""
import base64
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import unittest
from unittest.mock import patch

import requests
import monitor
import red_integrada as red


def document(size_per_node=0):
    value = red.construir_red_integrada([], {}, referencia=datetime.now(timezone.utc))
    for node in value["nodos"]:
        node["evidencias_alerta"] = [{"tipo": "pronostico_fluvial", "vigente": False,
                                    "texto": "e" * size_per_node}]
    return value


class Response:
    def __init__(self, value, status=200):
        self.value, self.status_code = value, status

    def json(self):
        return copy.deepcopy(self.value)


class GitHubMemory:
    def __init__(self):
        self.data = None
        self.calls = []
        self.writes = []
        self.failure = None
        self.stale_read = False

    @property
    def sha(self):
        raw = self.data or b""
        return hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()

    def request(self, method, url, **kwargs):
        path = url.split("/repos/owner/repo", 1)[1]
        self.calls.append((method, path))
        if path == "":
            return Response({"default_branch": "main"})
        if path == "/branches/ala-monitor-state":
            return Response({})
        if path.startswith("/git/blobs/"):
            return Response({"encoding": "base64", "content": base64.b64encode(self.data).decode()})
        if path != "/contents/red_integrada.json":
            raise AssertionError("Acceso inesperado al registro de Telegram: " + path)
        if method == "PUT":
            if self.failure == "timeout":
                raise requests.Timeout("private credential must never appear")
            if self.failure:
                return Response({"message": "private HTTP content"}, self.failure)
            body = kwargs["json"]
            if self.data is None:
                assert "sha" not in body
            else:
                assert body["sha"] == self.sha
            self.writes.append(copy.deepcopy(body))
            old = self.data
            self.data = base64.b64decode(body["content"])
            result = Response({"content": {"sha": self.sha}})
            if self.stale_read:
                self.data = old
            return result
        if self.data is None:
            return Response({}, 404)
        large = len(self.data) > 1_000_000
        return Response({"type": "file", "sha": self.sha, "size": len(self.data),
                         "encoding": "none" if large else "base64",
                         "content": "" if large else base64.b64encode(self.data).decode()})


class PublicacionTests(unittest.TestCase):
    def setUp(self):
        guard = patch("requests.sessions.Session.request", side_effect=AssertionError("Red/Telegram prohibidos"))
        guard.start()
        self.addCleanup(guard.stop)
        self.api = GitHubMemory()
        self.store = monitor.GitHubStateStore("test-secret", "owner/repo", session=self.api)

    def test_regresion_500kb_y_dos_ejecuciones_con_sha_y_relectura(self):
        value = document(9_000)
        original = copy.deepcopy(value)
        receipt = self.store.save_red_integrada(value)
        self.assertGreater(receipt["bytes"], 500_000)
        self.assertLessEqual(receipt["bytes"], red.MAX_BYTES)
        self.assertEqual(monitor.MAX_RED_BYTES, red.MAX_BYTES)
        self.assertEqual(value, original, "La serialización no debe mutar evidencias")
        self.assertEqual(json.loads(self.api.data)["nodos"][0]["evidencias_alerta"][0]["texto"], "e" * 9_000)
        self.assertEqual(receipt["sha"], self.api.sha)
        first_sha = receipt["sha"]
        value["generated_at"] = (datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat()
        second = self.store.save_red_integrada(value)
        self.assertEqual(self.api.writes[1]["sha"], first_sha)
        self.assertNotEqual(first_sha, second["sha"])
        self.assertEqual(json.loads(self.api.data)["generated_at"], value["generated_at"])
        self.assertEqual(self.api.calls[-1], ("GET", "/contents/red_integrada.json"))
        self.assertTrue(red.validar_red_integrada(json.loads(self.api.data))["snapshot_vigente"])

    def test_informe_mayor_a_1mb_restaura_y_verifica_blob(self):
        value = document(18_000)
        first = self.store.save_red_integrada(value)
        self.assertGreater(first["bytes"], 1_000_000)
        self.store.save_red_integrada(value)
        self.assertGreaterEqual(sum(path.startswith("/git/blobs/") for _, path in self.api.calls), 3)

    def test_limite_2mb_explicito_sin_contactar_github(self):
        with self.assertRaises(monitor.RedPublicationError) as caught:
            self.store.save_red_integrada(document(36_000))
        self.assertEqual(caught.exception.code, "tamano_excedido")
        self.assertGreater(caught.exception.size, red.MAX_BYTES)
        self.assertEqual(self.api.calls, [])

    def test_relectura_antigua_no_acredita_publicacion(self):
        self.api.stale_read = True
        with self.assertRaises(monitor.RedPublicationError) as caught:
            self.store.save_red_integrada(document())
        self.assertEqual(caught.exception.stage, "verificacion")
        self.assertEqual(caught.exception.code, "contenido_no_confirmado")

    def test_conflicto_permisos_y_timeout_no_filtran_credenciales_ni_reintentan_envios(self):
        for failure, code in [(409, "http_409"), (403, "http_403"), (503, "http_503"), ("timeout", "conexion")]:
            with self.subTest(failure=failure):
                self.api.failure = failure
                with self.assertRaises(monitor.RedPublicationError) as caught:
                    self.store.save_red_integrada(document())
                self.assertEqual(caught.exception.stage, "escritura")
                self.assertEqual(caught.exception.code, code)
                self.assertNotIn("private", str(caught.exception))
                self.assertNotIn("test-secret", str(caught.exception))
                self.assertEqual(self.api.writes, [])

    def test_no_republica_informe_vencido_con_fecha_aparentemente_nueva(self):
        value = document()
        value["generated_at"] = (datetime.now(timezone.utc) - timedelta(hours=4)).isoformat()
        with self.assertRaisesRegex(monitor.RedPublicationError, "informe_vencido"):
            self.store.save_red_integrada(value)
        self.assertEqual(self.api.calls, [])

    def test_campos_privados_rechazados_antes_de_publicar(self):
        value = document()
        value["nodos"][0]["token"] = "private"
        with self.assertRaisesRegex(monitor.RedPublicationError, "contrato_invalido"):
            self.store.save_red_integrada(value)
        self.assertEqual(self.api.calls, [])


if __name__ == "__main__":
    unittest.main()
