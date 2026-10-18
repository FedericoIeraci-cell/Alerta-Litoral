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
        self.head = "0" * 40
        self.commits = {self.head: None}
        self.blobs = {}
        self.calls = []
        self.writes = []
        self.failure = None
        self.stale_read = False
        self.stale_branch_contents = False
        self.previous_data = None
        self.read_refs = []
        self.head_responses = []
        self.head_cache_headers = []
        self.after_write = None
        self.omit_commit = False
        self.ref_failure = None

    @staticmethod
    def blob_sha(payload):
        raw = payload or b""
        return hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()

    @property
    def sha(self):
        return self.blob_sha(self.data)

    def add_commit(self, payload):
        self.head = hashlib.sha1(f"commit-{len(self.commits)}".encode()).hexdigest()
        self.data = payload
        self.commits[self.head] = payload
        if payload is not None:
            self.blobs[self.blob_sha(payload)] = payload
        return self.head

    def request(self, method, url, **kwargs):
        path = url.split("/repos/owner/repo", 1)[1]
        self.calls.append((method, path))
        if path == "":
            return Response({"default_branch": "main"})
        if path == "/branches/ala-monitor-state":
            return Response({})
        if path == "/git/ref/heads/ala-monitor-state":
            assert method == "GET"
            self.head_cache_headers.append(kwargs["headers"].get("Cache-Control"))
            if self.ref_failure:
                return Response({"message": "private HTTP content"}, self.ref_failure)
            head = self.head_responses.pop(0) if self.head_responses else self.head
            return Response({"object": {"type": "commit", "sha": head}})
        if path.startswith("/git/blobs/"):
            payload = self.blobs.get(path.rsplit("/", 1)[1])
            if payload is None:
                return Response({}, 404)
            return Response({"encoding": "base64", "content": base64.b64encode(payload).decode()})
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
            old, old_head = self.data, self.head
            self.previous_data = old
            written_head = self.add_commit(base64.b64decode(body["content"]))
            value = {"content": {"sha": self.sha}, "commit": {"sha": written_head}}
            if self.omit_commit:
                value.pop("commit")
            result = Response(value)
            if self.stale_read:
                self.data, self.head = old, old_head
            if self.after_write:
                self.after_write(self, old_head, written_head)
            return result
        ref = kwargs["params"]["ref"]
        self.read_refs.append(ref)
        if ref == "ala-monitor-state":
            payload = self.previous_data if self.stale_branch_contents and self.writes else self.data
        else:
            payload = self.commits.get(ref)
        if payload is None:
            return Response({}, 404)
        large = len(payload) > 1_000_000
        return Response({"type": "file", "sha": self.blob_sha(payload), "size": len(payload),
                         "encoding": "none" if large else "base64",
                         "content": "" if large else base64.b64encode(payload).decode()})


class PublicacionTests(unittest.TestCase):
    def setUp(self):
        guard = patch("requests.sessions.Session.request", side_effect=AssertionError("Red/Telegram prohibidos"))
        guard.start()
        self.addCleanup(guard.stop)
        sleep = patch("monitor.time.sleep")
        self.sleep = sleep.start()
        self.addCleanup(sleep.stop)
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
        self.assertEqual(self.api.calls[-1], ("GET", "/git/ref/heads/ala-monitor-state"))
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
        self.assertEqual(len(self.api.writes), 1)
        self.assertEqual([call.args[0] for call in self.sleep.call_args_list], [1, 2])

    def test_contents_de_rama_cacheado_no_rechaza_commit_escrito(self):
        self.api.add_commit(json.dumps(document()).encode())
        self.api.stale_branch_contents = True
        receipt = self.store.save_red_integrada(document(2))
        self.assertEqual(receipt["sha"], self.api.sha)
        self.assertEqual(receipt["commit_sha"], self.api.head)
        self.assertEqual(self.api.read_refs, ["ala-monitor-state", receipt["commit_sha"]])
        self.assertEqual(self.api.head_cache_headers, ["no-cache"])
        self.assertEqual(len(self.api.writes), 1)
        self.sleep.assert_not_called()

    def test_referencia_demora_dos_lecturas_sin_repetir_put(self):
        self.api.after_write = lambda api, old, written: api.head_responses.extend([old, old])
        receipt = self.store.save_red_integrada(document())
        self.assertEqual(receipt["sha"], self.api.sha)
        self.assertEqual(self.store._shas[monitor.RED_REMOTE_PATH], receipt["sha"])
        self.assertEqual(len(self.api.writes), 1)
        self.assertEqual([call.args[0] for call in self.sleep.call_args_list], [1, 2])
        self.assertEqual(self.api.head_cache_headers, ["no-cache"] * 3)

    def test_avance_de_rama_que_conserva_red_se_confirma(self):
        self.api.after_write = lambda api, old, written: api.add_commit(api.data)
        receipt = self.store.save_red_integrada(document())
        self.assertNotEqual(receipt["commit_sha"], self.api.head)
        self.assertEqual(receipt["sha"], self.api.sha)
        self.assertEqual(self.api.read_refs[-1], self.api.head)
        self.assertEqual(len(self.api.head_cache_headers), 2)
        self.assertEqual(len(self.api.writes), 1)

    def test_otro_escritor_sustituye_red_y_no_se_oculta(self):
        self.api.after_write = lambda api, old, written: api.add_commit(b'{"otro_informe":true}')
        with self.assertRaises(monitor.RedPublicationError) as caught:
            self.store.save_red_integrada(document())
        self.assertEqual(caught.exception.stage, "verificacion")
        self.assertEqual(caught.exception.code, "contenido_no_confirmado")
        self.assertEqual(self.api.data, b'{"otro_informe":true}')
        self.assertEqual(len(self.api.writes), 1)
        self.assertEqual(self.sleep.call_count, 2)

    def test_rama_cambia_durante_relectura_del_commit_y_no_se_confirma(self):
        def replace_during_check(api, old, written):
            same_content = api.add_commit(api.data)
            replacement = api.add_commit(b'{"otro_informe":true}')
            api.head_responses.extend([same_content, replacement])

        self.api.after_write = replace_during_check
        with self.assertRaises(monitor.RedPublicationError) as caught:
            self.store.save_red_integrada(document())
        self.assertEqual(caught.exception.code, "contenido_no_confirmado")
        self.assertEqual(self.api.data, b'{"otro_informe":true}')
        self.assertEqual(len(self.api.writes), 1)
        self.assertEqual(self.sleep.call_count, 2)

    def test_commit_inmutable_con_contenido_distinto_no_se_confirma(self):
        self.api.after_write = lambda api, old, written: api.commits.__setitem__(written, b'{}')
        with self.assertRaises(monitor.RedPublicationError) as caught:
            self.store.save_red_integrada(document())
        self.assertEqual(caught.exception.code, "contenido_no_confirmado")
        self.assertEqual(len(self.api.writes), 1)
        self.assertEqual(self.api.head_cache_headers, [])
        self.sleep.assert_not_called()

    def test_commit_no_identificado_falla_sin_otra_escritura(self):
        self.api.omit_commit = True
        with self.assertRaises(monitor.RedPublicationError) as caught:
            self.store.save_red_integrada(document())
        self.assertEqual(caught.exception.stage, "verificacion")
        self.assertEqual(caught.exception.code, "estado_no_confirmado")
        self.assertEqual(len(self.api.writes), 1)
        self.sleep.assert_not_called()

    def test_no_poder_confirmar_rama_falla_sin_filtrar_respuesta(self):
        self.api.ref_failure = 503
        with self.assertRaises(monitor.RedPublicationError) as caught:
            self.store.save_red_integrada(document())
        self.assertEqual(caught.exception.stage, "verificacion")
        self.assertEqual(caught.exception.code, "http_503")
        self.assertNotIn("private", str(caught.exception))
        self.assertEqual(len(self.api.writes), 1)
        self.sleep.assert_not_called()

    def test_documento_caducado_durante_verificacion_no_se_rejuvenece(self):
        now = datetime.now(timezone.utc)
        clock = [now]
        value = document()
        self.api.after_write = lambda api, old, written: clock.__setitem__(0, now + timedelta(hours=4))
        with patch("red_integrada._ahora", side_effect=lambda reference=None: reference if reference is not None else clock[0]):
            with self.assertRaises(monitor.RedPublicationError) as caught:
                self.store.save_red_integrada(value)
        self.assertEqual(caught.exception.code, "informe_vencido")
        self.assertEqual(json.loads(self.api.data)["generated_at"], value["generated_at"])
        self.assertEqual(len(self.api.writes), 1)

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
