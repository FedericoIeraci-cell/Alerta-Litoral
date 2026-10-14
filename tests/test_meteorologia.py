"""Recuperación de consultas meteorológicas sin cambiar orden ni ocultar errores."""
import logging
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))
logging.getLogger("streamlit").setLevel(logging.ERROR)
import app


class Response:
    def __init__(self, values=None, status=200, invalid=False):
        self.values = values
        self.status_code = status
        self.invalid = invalid

    def raise_for_status(self):
        if self.status_code >= 400:
            raise app.requests.exceptions.HTTPError("Respuesta del servidor", response=self)

    def json(self):
        if self.invalid:
            raise ValueError("JSON inválido con https://example.invalid/secreto")
        return self.values


class MeteorologiaTests(unittest.TestCase):
    def setUp(self):
        app.obtener_datos_ecmwf.clear()

    def tearDown(self):
        app.obtener_datos_ecmwf.clear()

    def _consultar(self, callback, cantidad=9):
        nodos = [{"localidad": str(i), "lat": float(i + 1), "lon": float(-40 - i)} for i in range(cantidad)]
        with patch.object(app, "NODOS", nodos), patch.object(app.requests, "get", side_effect=callback):
            return app.obtener_datos_ecmwf()

    def test_reintento_transitorio_recupera_solo_el_lote_fallido_y_respeta_orden(self):
        counts = {}
        timeouts = []
        def get(url, **kw):
            self.assertEqual(url, app.API_URL)
            clave = kw["params"]["latitude"]
            counts[clave] = counts.get(clave, 0) + 1
            timeouts.append(kw["timeout"])
            if clave.startswith("1.000000") and counts[clave] == 1:
                raise app.requests.exceptions.ReadTimeout("Read timed out en una URL privada")
            return Response([{"id": int(float(lat))} for lat in clave.split(",")])
        result = self._consultar(get)
        self.assertEqual([fila["id"] for fila in result], list(range(1, 10)))
        self.assertEqual(sorted(counts.values()), [1, 2])
        self.assertTrue(all(t == (20, 45) for t in timeouts))

    def test_error_conexion_tiene_un_solo_reintento(self):
        calls = []
        def get(url, **kw):
            calls.append(kw)
            if len(calls) == 1:
                raise app.requests.exceptions.ConnectionError("Conexión interrumpida")
            return Response({"hourly": {"time": []}})
        result = self._consultar(get, 1)
        self.assertEqual(len(calls), 2)
        self.assertNotIn("error", result[0])

    def test_http_400_no_se_reintenta_y_conserva_estado_publico(self):
        calls = []
        def get(url, **kw):
            calls.append(kw)
            return Response(status=400)
        result = self._consultar(get, 2)
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(result), 2)
        self.assertTrue(all("HTTP 400" in fila["error"] for fila in result))

    def test_json_invalido_no_se_reintenta_ni_expone_excepcion(self):
        calls = []
        def get(url, **kw):
            calls.append(kw)
            return Response(invalid=True)
        result = self._consultar(get, 2)
        self.assertEqual(len(calls), 1)
        self.assertTrue(all("respuesta no válida" in fila["error"] for fila in result))
        self.assertTrue(all("secreto" not in fila["error"] and "https://" not in fila["error"] for fila in result))

    def test_timeout_persistente_son_dos_intentos_y_nunca_datos_cero(self):
        calls = []
        def get(url, **kw):
            calls.append(kw)
            raise app.requests.exceptions.Timeout("https://example.invalid/credencial")
        result = self._consultar(get, 2)
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(result), 2)
        self.assertTrue(all("dos intentos" in fila["error"] for fila in result))
        self.assertTrue(all(set(fila) == {"error"} and "credencial" not in fila["error"] for fila in result))


if __name__ == "__main__":
    unittest.main()
