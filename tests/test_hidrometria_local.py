"""Regresiones offline de identidad, medición y caducidad de la referencia RHN."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import hidrometria_local as h


FIXTURES = Path(__file__).parent / "fixtures"
OBS = json.loads((FIXTURES / "ina_zarate_observada_29437_20261017.json").read_text())
SIM = json.loads((FIXTURES / "ina_zarate_simulada_29534_metadatos_20261017.json").read_text())
REF = datetime(2026, 10, 18, 2, 57, 19, tzinfo=timezone.utc)
ULTIMA = datetime(2026, 10, 18, 0, 0, tzinfo=timezone.utc)


class Response:
    def __init__(self, url, payload, status=200, raw=None):
        self.url, self.status_code = url, status
        self.body = raw if raw is not None else json.dumps(payload).encode()
        self.closed = False

    def iter_content(self, chunk_size):
        for i in range(0, len(self.body), chunk_size):
            yield self.body[i:i + chunk_size]

    def close(self):
        self.closed = True


class Session:
    def __init__(self, fallo_pronostico=False, respuesta_observada=None):
        self.calls = []
        self.fallo_pronostico = fallo_pronostico
        self.respuesta_observada = respuesta_observada
        self.responses = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        serie = int(parse_qs(urlsplit(url).query)["series_id"][0])
        if serie == h.SERIE_PRONOSTICO and self.fallo_pronostico:
            raise OSError("simulated timeout")
        if serie == h.SERIE_OBSERVADA and self.respuesta_observada is not None:
            response = self.respuesta_observada(url)
        else:
            response = Response(url, OBS if serie == h.SERIE_OBSERVADA else SIM)
        self.responses.append(response)
        return response


def parsear(payload=None, metadatos=None, referencia=REF):
    return h.parsear_altura_zarate(OBS if payload is None else payload,
                                 referencia=referencia, consulta=REF,
                                 metadatos_pronostico=SIM if metadatos is None else metadatos)


class HidrometriaLocalTests(unittest.TestCase):
    def test_snapshot_real_identifica_medicion_y_vinculo_documental(self):
        r = parsear()
        self.assertEqual((r["serie"], r["estacion_id"], r["red"], r["id_externo"]),
                         (29437, 5907, "sat2", "313"))
        self.assertEqual(r["nombre"], "Paraná Las Palmas - Zárate")
        self.assertEqual(r["altura_m"], 1.12)
        self.assertEqual(r["fecha_observada"], ULTIMA.isoformat())
        self.assertEqual(r["unidad"], "m")
        self.assertEqual(r["cero_ign_m"], 0.243)
        self.assertEqual(len(r["observaciones"]), 49)
        self.assertAlmostEqual(r["edad_horas"], 2 + 57 / 60 + 19 / 3600)
        self.assertTrue(r["vigente"])
        self.assertTrue(r["coincidencia_documental_pronostico"])
        self.assertEqual(r["error"], "")
        self.assertEqual(r["registros_descartados"], 0)
        self.assertFalse({"alerta_m", "evacuacion_m", "semaforo", "prioridad"} & set(r))

    def test_caducidad_se_recalcula_sin_rejuvenecer_por_consulta(self):
        original = parsear()
        frozen = deepcopy(original)
        self.assertTrue(h.revalidar_altura_zarate(original, ULTIMA + timedelta(hours=6))["vigente"])
        tarde = h.revalidar_altura_zarate(original, ULTIMA + timedelta(hours=6, seconds=1))
        self.assertFalse(tarde["vigente"])
        self.assertEqual(tarde["altura_m"], 1.12)
        self.assertEqual(tarde["fecha_observada"], ULTIMA.isoformat())
        self.assertEqual(tarde["consulta"], REF.isoformat())
        self.assertEqual(tarde["error"], "")
        self.assertEqual(original, frozen)

    def test_metadata_incompatible_no_puede_pasar_como_otra_escala(self):
        cambios = [
            ("id", 40), ("estacion.id", 40), ("estacion.id_externo", "340"),
            ("estacion.tabla", "alturas_prefe"), ("estacion.red.tabla_id", "alturas_prefe"),
            ("estacion.red.id", 10), ("estacion.nombre", "Campana"),
            ("estacion.cero_ign", 0.388), ("estacion.cero_ign", None),
            ("estacion.automatica", False), ("estacion.propietario", "PNA"),
            ("var.id", 4), ("var.var", "Q"), ("var.def_unit_id", "9"), ("procedimiento.id", 4),
            ("procedimiento.id", True), ("procedimiento.nombre", "Simulado"),
            ("unidades.id", 9), ("unidades.abrev", "cm"), ("unidades.nombre", "centímetros"),
        ]
        for camino, valor in cambios:
            with self.subTest(campo=camino, valor=valor):
                raw = deepcopy(OBS)
                obj = raw
                campos = camino.split(".")
                for campo in campos[:-1]:
                    obj = obj[campo]
                obj[campos[-1]] = valor
                with self.assertRaises(ValueError):
                    parsear(raw)
        raw = deepcopy(OBS)
        raw["estacion"]["geom"]["coordinates"][0] -= 0.01
        with self.assertRaises(ValueError):
            parsear(raw)

    def test_simulado_no_se_acepta_como_observacion(self):
        raw = deepcopy(SIM)
        raw["id"] = h.SERIE_OBSERVADA
        raw["observaciones"] = deepcopy(OBS["observaciones"])
        with self.assertRaises(ValueError):
            parsear(raw)

    def test_no_finitos_booleanos_sin_zona_y_futuro_se_descartan(self):
        raw = deepcopy(OBS)
        antes = len(raw["observaciones"])
        raw["observaciones"] += [
            ["2026-10-18T01:00:00Z", "2026-10-18T01:00:00Z", valor]
            for valor in [float("nan"), float("inf"), -float("inf"), True, None, "NaN", 101]
        ]
        raw["observaciones"] += [
            ["2026-10-18T01:00:00", "2026-10-18T01:00:00", 2.0],
            ["2026-10-18T03:00:00Z", "2026-10-18T03:00:00Z", 2.0],
            ["2026-10-18T01:00:00Z", "2026-10-18T02:00:00Z", 2.0],
        ]
        r = parsear(raw)
        self.assertEqual(r["altura_m"], 1.12)
        self.assertEqual(len(r["observaciones"]), antes)
        self.assertEqual(r["registros_descartados"], 10)
        solo_futuro = deepcopy(OBS)
        solo_futuro["observaciones"] = [["2026-10-18T03:00:00Z", "2026-10-18T03:00:00Z", 2.0]]
        with self.assertRaises(ValueError):
            parsear(solo_futuro)

    def test_duplicados_identicos_se_unifican_y_conflictos_se_rechazan(self):
        raw = deepcopy(OBS)
        raw["observaciones"].append(deepcopy(raw["observaciones"][-1]))
        self.assertEqual(len(parsear(raw)["observaciones"]), 49)
        raw["observaciones"][-1][2] = 1.90
        with self.assertRaises(ValueError):
            parsear(raw)

    def test_error_vinculo_no_descarta_medicion_valida(self):
        sim = deepcopy(SIM)
        sim["estacion"]["cero_ign"] = 0.388
        r = parsear(metadatos=sim)
        self.assertTrue(r["vigente"])
        self.assertEqual(r["altura_m"], 1.12)
        self.assertFalse(r["coincidencia_documental_pronostico"])
        self.assertTrue(r["error_vinculo_pronostico"])
        sin_meta = h.parsear_altura_zarate(OBS, referencia=REF)
        self.assertTrue(sin_meta["vigente"])
        self.assertFalse(sin_meta["coincidencia_documental_pronostico"])

    def test_consulta_solo_dos_endpoints_fijos_y_no_tiene_redirecciones(self):
        session = Session()
        r = h.consultar_altura_zarate(REF, session=session)
        self.assertTrue(r["vigente"])
        self.assertTrue(r["coincidencia_documental_pronostico"])
        self.assertEqual(len(session.calls), 2)
        for (url, opciones), serie in zip(session.calls, [29437, 29534]):
            self.assertTrue(h._url_verificada(url, serie))
            self.assertFalse(opciones["allow_redirects"])
            self.assertTrue(opciones["stream"])
        self.assertTrue(all(r.closed for r in session.responses))

    def test_consulta_fallida_del_pronostico_conserva_observacion(self):
        session = Session(fallo_pronostico=True)
        r = h.consultar_altura_zarate(REF, session=session)
        self.assertTrue(r["vigente"])
        self.assertEqual(r["altura_m"], 1.12)
        self.assertEqual(r["error"], "")
        self.assertFalse(r["coincidencia_documental_pronostico"])
        self.assertTrue(r["error_vinculo_pronostico"])

    def test_respuesta_invalida_no_hace_consulta_adicional(self):
        respuestas = [
            lambda url: Response(url, {}, status=302),
            lambda url: Response("https://example.org/", OBS),
            lambda url: Response(url, {}, raw=b"not JSON"),
            lambda url: Response(url, {}, raw=b" " * (h.MAX_BYTES + 1)),
            lambda url: Response(url, SIM),
        ]
        for respuesta in respuestas:
            with self.subTest(respuesta=respuesta):
                session = Session(respuesta_observada=respuesta)
                r = h.consultar_altura_zarate(REF, session=session)
                self.assertFalse(r["vigente"])
                self.assertIsNone(r["altura_m"])
                self.assertTrue(r["error"])
                self.assertEqual(len(session.calls), 1)
                self.assertTrue(session.responses[0].closed)

    def test_revalidar_no_consulta_red_y_rechaza_contrato_alterado(self):
        r = parsear()
        with patch.object(h, "_leer_json", side_effect=AssertionError("no debe consultar")):
            self.assertTrue(h.revalidar_altura_zarate(r, REF)["vigente"])
        for campo, valor in [("serie", 40), ("nombre", "Campana"), ("cero_ign_m", 0.418),
                             ("unidad", "cm"), ("altura_m", float("nan")),
                             ("fecha_observada", "2026-10-18T03:00:00Z"),
                             ("fecha_observada", "2026-10-18T00:00:00")]:
            with self.subTest(campo=campo):
                altered = deepcopy(r)
                altered[campo] = valor
                out = h.revalidar_altura_zarate(altered, REF)
                self.assertFalse(out["vigente"])
                self.assertIsNone(out["altura_m"])
                self.assertTrue(out["error"])

    def test_revalidar_descarta_campos_ajenos_y_normaliza_los_puntos(self):
        raw = parsear()
        contrato = set(raw)
        raw.update(alerta_m=2, evacuacion_m=2.2, semaforo="VERDE", pronostico=[{"altura": 999}])
        raw["observaciones"][0].update(alerta_m=2, semaforo="ROJO", url="https://example.org")
        raw["observaciones"].append(deepcopy(raw["observaciones"][0]))
        r = h.revalidar_altura_zarate(raw, REF)
        self.assertEqual(set(r), contrato)
        self.assertTrue(r["vigente"])
        self.assertEqual(len(r["observaciones"]), 49)
        for punto in r["observaciones"]:
            self.assertEqual(set(punto), {"fecha", "altura_m", "fecha_original", "id_observacion"})
        self.assertIn("semaforo", raw)

    def test_revalidar_rechaza_altura_fecha_o_curva_contradictorias(self):
        for campo, valor in [("altura_m", 1.99), ("fecha_observada", "2026-10-17T23:00:00Z")]:
            with self.subTest(campo=campo):
                raw = parsear()
                raw[campo] = valor
                r = h.revalidar_altura_zarate(raw, REF)
                self.assertFalse(r["vigente"])
                self.assertIsNone(r["altura_m"])
                self.assertEqual(r["observaciones"], [])
                self.assertTrue(r["error"])
        raw = parsear()
        punto = deepcopy(raw["observaciones"][-1])
        punto["altura_m"] = 1.99
        raw["observaciones"].append(punto)
        r = h.revalidar_altura_zarate(raw, REF)
        self.assertFalse(r["vigente"])
        self.assertTrue(r["error"])

    def test_origen_y_zona_de_referencia_son_obligatorios(self):
        for url in ["http://alerta.ina.gob.ar/a5/getSeriesBySiteAndVar?series_id=29437",
                    "https://alerta.ina.gob.ar.ejemplo.org/a5/getSeriesBySiteAndVar?series_id=29437",
                    h._url(40, REF), h._url(29437, REF) + "&series_id=40"]:
            with self.subTest(url=url), self.assertRaises(ValueError):
                h.parsear_altura_zarate(OBS, referencia=REF, fuente=url)
        with self.assertRaises(ValueError):
            h.revalidar_altura_zarate(parsear(), datetime(2026, 10, 18))


if __name__ == "__main__":
    unittest.main()
