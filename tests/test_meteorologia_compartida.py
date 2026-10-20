"""Capturas ECMWF públicas: vigencia, datos ausentes y límites, sin acceso a red."""
import base64
import copy
from datetime import datetime, timedelta, timezone
import gzip
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).parents[1]))
import meteorologia_compartida as meteo

AHORA = datetime(2026, 10, 20, 0, 0, tzinfo=timezone.utc)
NODOS = [
    {"localidad": "Rosario", "provincia": "Santa Fe", "lat": -32.946, "lon": -60.639},
    {"localidad": "Paraná", "provincia": "Entre Ríos", "lat": -31.741, "lon": -60.511},
]


def datos():
    return [
        {"latitude": -32.95, "longitude": -60.64, "elevation": 43.0,
         "timezone": "America/Argentina/Buenos_Aires", "timezone_abbreviation": "GMT-3",
         "utc_offset_seconds": -10800, "generationtime_ms": 0.12,
         "hourly": {"time": ["2026-10-19T20:00", "2026-10-19T21:00"],
                    "precipitation": [0, None], "temperature_2m": [23.7, 23.3]},
         "hourly_units": {"time": "iso8601", "precipitation": "mm", "temperature_2m": "°C"},
         "daily": {"time": ["2026-10-19"], "precipitation_sum": [0]},
         "daily_units": {"time": "iso8601", "precipitation_sum": "mm"}},
        {"error": "https://example.invalid/token-secreto: Telegram chat privado"},
    ]


def captura():
    return meteo.crear_captura(NODOS, datos(), AHORA)


def sobre_bytes(raw):
    comprimido = gzip.compress(raw)
    return json.dumps({"schema_version": 1, "encoding": "gzip+base64",
                       "payload": base64.b64encode(comprimido).decode()}).encode()


class Respuesta:
    status_code = 200
    url = meteo.SNAPSHOT_URL

    def __init__(self, body, headers=None):
        self.body = body
        self.headers = headers or {}
        self.cerrada = False
        self.leida = False

    def iter_content(self, chunk_size):
        self.leida = True
        for i in range(0, len(self.body), chunk_size):
            yield self.body[i:i + chunk_size]

    def close(self):
        self.cerrada = True


class CapturasMeteorologicasTests(unittest.TestCase):
    def setUp(self):
        prohibir_red = patch("requests.sessions.Session.request", side_effect=AssertionError("HTTP real prohibido"))
        prohibir_red.start()
        self.addCleanup(prohibir_red.stop)

    def test_roundtrip_preserva_ceros_nulos_valores_y_fecha_de_consulta(self):
        original = captura()
        contenido = meteo.serializar_captura(original)
        recibido = meteo.deserializar_captura(contenido, NODOS, AHORA + timedelta(hours=1))
        self.assertEqual(recibido, original)
        self.assertEqual(recibido["datos"][0], datos()[0])
        self.assertEqual(recibido["datos"][0]["hourly"]["precipitation"], [0, None])
        self.assertEqual(recibido["datos"][1], {"error": meteo.ERROR_PUBLICO})
        self.assertEqual(recibido["consultado"], AHORA.isoformat())
        self.assertEqual(contenido, meteo.serializar_captura(original))

    def test_publicacion_solo_allowlist_no_copias_privadas_ni_errores_crudos(self):
        raw = datos()
        raw[0].update(telegram_bot_token="privado", authorization="privado", _ala_origen="privado")
        raw[0]["hourly"]["secret"] = ["privado", "privado"]
        raw[0]["hourly_units"]["password"] = "privado"
        limpio = meteo.crear_captura(NODOS, raw, AHORA)
        self.assertEqual(limpio["datos"][0], datos()[0])
        self.assertNotIn("privado", json.dumps(limpio))
        self.assertNotIn("token-secreto", json.dumps(limpio))
        limpio["datos"][0]["hourly"]["precipitation"][0] = 20
        self.assertEqual(raw[0]["hourly"]["precipitation"][0], 0)

    def test_fallo_total_no_se_publica_y_cero_si_es_dato(self):
        for fallidas in ([{"error": "falló"}, {"error": True}], [{}, {}],
                         [{"hourly": {"time": ["2026-10-19T20:00"], "precipitation": [None]}}, {}]):
            with self.subTest(fallidas=fallidas), self.assertRaises(meteo.CapturaMeteorologicaError):
                meteo.crear_captura(NODOS, fallidas, AHORA)
        cero = [{"hourly": {"time": ["2026-10-19T20:00"], "precipitation": [0]}}, {}]
        self.assertEqual(meteo.crear_captura(NODOS, cero, AHORA)["datos"], cero)

    def test_variable_ecmwf_no_disponible_preserva_undefined_nulos_y_resto_del_pronostico(self):
        # Respuesta real ECMWF: evapotranspiration no disponible, aunque
        # precipitación, temperatura y demás variables sí contienen datos.
        raw = datos()
        raw[0]["hourly"]["evapotranspiration"] = [None, None]
        raw[0]["hourly_units"]["evapotranspiration"] = "undefined"
        original = meteo.crear_captura(NODOS, raw, AHORA)
        recibido = meteo.deserializar_captura(meteo.serializar_captura(original), NODOS, AHORA)
        self.assertEqual(recibido["datos"][0], raw[0])
        self.assertEqual(recibido["datos"][0]["hourly"]["precipitation"], [0, None])
        self.assertEqual(recibido["datos"][0]["hourly"]["temperature_2m"], [23.7, 23.3])

    def test_unidad_undefined_solo_admite_series_presentes_y_completamente_nulas(self):
        for campo, variable in (("hourly", "evapotranspiration"), ("daily", "cape_max")):
            raw = datos()
            count = len(raw[0][campo]["time"])
            raw[0][campo][variable] = [None] * count
            raw[0][campo + "_units"][variable] = "undefined"
            original = meteo.crear_captura(NODOS, raw, AHORA)
            self.assertEqual(original["datos"][0][campo][variable], [None] * count)
            for numero in (0, 1.5):
                invalida = copy.deepcopy(original)
                invalida["datos"][0][campo][variable][0] = numero
                with self.subTest(campo=campo, numero=numero), self.assertRaises(meteo.CapturaMeteorologicaError):
                    meteo.validar_captura(invalida, NODOS, AHORA)
            del raw[0][campo][variable]
            with self.assertRaises(meteo.CapturaMeteorologicaError):
                meteo.crear_captura(NODOS, raw, AHORA)
        raw = datos()
        raw[0]["hourly_units"]["time"] = "undefined"
        with self.assertRaises(meteo.CapturaMeteorologicaError):
            meteo.crear_captura(NODOS, raw, AHORA)

    def test_limites_edad_y_futuro_con_zona_verificada(self):
        for diferencia, valido in ((timedelta(hours=3), True),
                                   (timedelta(hours=3, seconds=1), False),
                                   (timedelta(minutes=-5), True),
                                   (timedelta(minutes=-5, seconds=-1), False)):
            with self.subTest(diferencia=diferencia):
                if valido:
                    meteo.validar_captura(captura(), NODOS, AHORA + diferencia)
                else:
                    with self.assertRaises(meteo.CapturaMeteorologicaError):
                        meteo.validar_captura(captura(), NODOS, AHORA + diferencia)
        for invalida in ("2026-10-20", "2026-10-20T00:00:00", AHORA.replace(tzinfo=None), "privado"):
            with self.subTest(invalida=invalida), self.assertRaises(meteo.CapturaMeteorologicaError):
                meteo.crear_captura(NODOS, datos(), invalida)
        zona_local = AHORA.astimezone(timezone(timedelta(hours=-3)))
        self.assertEqual(meteo.crear_captura(NODOS, datos(), zona_local)["consultado"], AHORA.isoformat())

    def test_identidad_orden_y_cantidad_de_nodos_no_se_pueden_cambiar(self):
        catalogos = [NODOS[::-1], NODOS[:1]]
        for campo, valor in (("lat", -32), ("lon", -60), ("localidad", "Otra"), ("provincia", "Otra")):
            copia = copy.deepcopy(NODOS)
            copia[0][campo] = valor
            catalogos.append(copia)
        for catalogo in catalogos:
            with self.subTest(catalogo=catalogo), self.assertRaises(meteo.CapturaMeteorologicaError):
                meteo.validar_captura(captura(), catalogo, AHORA)
        with self.assertRaises(meteo.CapturaMeteorologicaError):
            meteo.crear_captura(NODOS, datos()[:1], AHORA)

    def test_rechaza_campos_no_publicos_en_captura_recibida(self):
        mutaciones = [lambda c: c.update(token="privado"),
                      lambda c: c["nodos"][0].update(chat_id="privado"),
                      lambda c: c["datos"][0].update(password="privado"),
                      lambda c: c["datos"][0]["hourly"].update(authorization=["privado"]),
                      lambda c: c["datos"][0]["hourly_units"].update(token="privado"),
                      lambda c: c["datos"][1].update(error="privado")]
        for mutacion in mutaciones:
            c = captura()
            mutacion(c)
            with self.subTest(mutacion=mutacion), self.assertRaises(meteo.CapturaMeteorologicaError) as e:
                meteo.deserializar_captura(sobre_bytes(json.dumps(c).encode()), NODOS, AHORA)
            self.assertNotIn("privado", str(e.exception))

    def test_rechaza_datos_corruptos_no_finitos_y_texto_en_campos_numericos(self):
        for valor in (True, "secreto", {}, float("nan"), float("inf"), 10**400):
            c = datos()
            c[0]["hourly"]["precipitation"][0] = valor
            with self.subTest(valor=repr(valor)), self.assertRaises(meteo.CapturaMeteorologicaError):
                meteo.crear_captura(NODOS, c, AHORA)
        for mutar in (lambda c: c[0]["hourly"].update(precipitation=[1]),
                      lambda c: c[0]["hourly"].update(time=["secreto", "2026-10-19T21:00"]),
                      lambda c: c[0]["hourly"].update(time=["2026-10-19T21:00", "2026-10-19T20:00"]),
                      lambda c: c[0].update(timezone=["secreto"]),
                      lambda c: c[0]["daily_units"].update(precipitation_sum="token-secreto")):
            c = datos()
            mutar(c)
            with self.assertRaises(meteo.CapturaMeteorologicaError):
                meteo.crear_captura(NODOS, c, AHORA)

    def test_limites_bytes_descompresion_y_payload_danado(self):
        valido = meteo.serializar_captura(captura())
        for contenido in (b"", b"{}", b"[]", b"no es JSON", valido[:-4],
                          sobre_bytes(b'{"schema_version":1,"schema_version":1}'),
                          sobre_bytes(b'{"dato":NaN}'), sobre_bytes(b"[" * 1500)):
            with self.subTest(contenido=contenido[:30]), self.assertRaises(meteo.CapturaMeteorologicaError):
                meteo.deserializar_captura(contenido, NODOS, AHORA)
        with patch.object(meteo, "MAX_BYTES", 100), self.assertRaises(meteo.CapturaMeteorologicaError):
            meteo.deserializar_captura(valido, NODOS, AHORA)
        with patch.object(meteo, "MAX_UNCOMPRESSED_BYTES", 100), self.assertRaises(meteo.CapturaMeteorologicaError):
            meteo.deserializar_captura(sobre_bytes(b"x" * 10000), NODOS, AHORA)
        sobre = json.loads(valido)
        compressed = base64.b64decode(sobre["payload"])
        for invalido in (compressed[:-1], compressed + gzip.compress(b"{}"), compressed + b"basura"):
            sobre["payload"] = base64.b64encode(invalido).decode()
            with self.assertRaises(meteo.CapturaMeteorologicaError):
                meteo.deserializar_captura(json.dumps(sobre).encode(), NODOS, AHORA)

    def test_descarga_publica_acotada_cierra_respuesta_y_no_reintenta(self):
        response = Respuesta(meteo.serializar_captura(captura()))
        session = Mock()
        session.get.return_value = response
        result = meteo.consultar_captura(NODOS, AHORA, session)
        self.assertEqual(result, captura())
        self.assertTrue(response.cerrada)
        args, kwargs = session.get.call_args
        self.assertEqual(args, (meteo.SNAPSHOT_URL,))
        self.assertEqual(kwargs["timeout"], (5, 15))
        self.assertTrue(kwargs["stream"])
        self.assertFalse(kwargs["allow_redirects"])
        self.assertNotIn("Authorization", kwargs["headers"])
        session.get.assert_called_once()

    def test_descarga_rechaza_redireccion_tamano_exceso_y_sanea_error_http(self):
        response = Respuesta(meteo.serializar_captura(captura()), {"Content-Length": str(meteo.MAX_BYTES + 1)})
        session = Mock()
        session.get.return_value = response
        with self.assertRaises(meteo.CapturaMeteorologicaError):
            meteo.consultar_captura(NODOS, AHORA, session)
        self.assertFalse(response.leida)
        self.assertTrue(response.cerrada)
        for status, url in ((302, meteo.SNAPSHOT_URL), (429, meteo.SNAPSHOT_URL), (200, "https://privado.invalid")):
            response = Respuesta(b"{}")
            response.status_code, response.url = status, url
            session.get.return_value = response
            with self.assertRaises(meteo.CapturaMeteorologicaError):
                meteo.consultar_captura(NODOS, AHORA, session)
            self.assertFalse(response.leida)
        response = Respuesta(b"x" * 201)
        session.get.return_value = response
        with patch.object(meteo, "MAX_BYTES", 200), self.assertRaises(meteo.CapturaMeteorologicaError):
            meteo.consultar_captura(NODOS, AHORA, session)
        session.get.side_effect = RuntimeError("credencial privada https://privado.invalid")
        with self.assertRaises(meteo.CapturaMeteorologicaError) as e:
            meteo.consultar_captura(NODOS, AHORA, session)
        self.assertNotIn("privada", str(e.exception))


if __name__ == "__main__":
    unittest.main()
