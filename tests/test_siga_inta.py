"""Contratos SIGA: fixtures oficiales capturados el 15/10/2026, sin datos personales."""
import copy
import json
from pathlib import Path
import sys
import unittest
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).parents[1]))
import siga_inta as s

FIX = Path(__file__).parent / "fixtures"
CAT = json.loads((FIX / "siga_catalogo_bella_vista_20261015.json").read_text())
DATA = json.loads((FIX / "siga_diarios_491_20261010_13.json").read_text())
JS = (FIX / "siga_urlserver_fragmento.js").read_text()
REF = "2026-10-15T12:00:00-03:00"


def estacion():
    return next(e for e in s.parsear_catalogo(CAT, REF) if e["id"] == 491)


class Response:
    status_code = 200
    headers = {}
    def __init__(self, data):
        self.data = data.encode() if isinstance(data, str) else data
    def raise_for_status(self):
        pass
    def iter_content(self, chunk_size=65536):
        yield self.data
    def close(self):
        pass


class Session:
    def __init__(self):
        self.calls = []
    def get(self, url, **kw):
        self.calls.append((url, kw))
        p = urlparse(url)
        if p.path == "/js/urlserver.js":
            return Response(JS)
        q = parse_qs(p.query, keep_blank_values=True)
        if q.get("param_type") == ["estacion"]:
            return Response(json.dumps(CAT))
        if q.get("param_type") == ["diario"]:
            return Response(json.dumps(DATA))
        raise AssertionError("No se permite otro destino en el test")


class SIGATests(unittest.TestCase):
    def test_descubre_endpoint_de_js_y_no_usa_ejemplos_comentados(self):
        self.assertEqual(s.descubrir_servicio(JS), "https://siga.inta.gob.ar/CdnaUV0iiERRpFQE.php?param_type=estacion&param_value=")
        sample = '/* var URL_MYSQL_ESTACION = "https://otro.example/a.php"; */\n' + JS
        self.assertEqual(s.descubrir_servicio(sample), s.descubrir_servicio(JS))
        for bad in ('var URL_MYSQL_ESTACION="https://otro.example/a.php?param_type=estacion&param_value=";',
                    'var URL_MYSQL_ESTACION="https://siga.inta.gob.ar/a.php?token=123";'):
            with self.assertRaises(ValueError):
                s.descubrir_servicio(bad)

    def test_catalogo_real_distingue_estacion_actual_e_historica(self):
        c = {e["id"]: e for e in s.parsear_catalogo(CAT, REF)}
        self.assertEqual(c[491]["ultimo_dia_catalogo"], "2026-10-13")
        self.assertEqual(c[491]["estado_catalogo"], "RECIENTE")
        self.assertEqual(c[41]["estado_catalogo"], "VENCIDO")
        self.assertEqual((c[491]["lat"], c[491]["lon"]), (-28.45, -58.99))

    def test_cero_es_medicion_y_fecha_diaria_no_se_desplaza_a_ayer(self):
        p = s.parsear_diarios(DATA, estacion(), "2026-10-10", "2026-10-13", REF)
        self.assertEqual(p["registros"][0]["fecha_diaria"], "2026-10-10")
        self.assertEqual(p["registros"][0]["fecha_original"], "2026-10-10T00:00:00Z[UTC]")
        self.assertEqual(p["registros"][0]["precipitacion_mm"], 0.4)
        self.assertEqual(p["registros"][1]["precipitacion_mm"], 0.0)
        self.assertEqual(p["registros"][0]["precipitacion_pluviometro_mm"], 0.0)
        self.assertEqual(p["registros"][0]["precipitacion_campo"], "precDiaCrono")
        self.assertAlmostEqual(p["acumulado_completo_mm"], 5.4)
        self.assertTrue(p["cobertura_completa"])
        self.assertEqual(p["estado"], "VIGENTE")

    def test_dia_faltante_no_se_convierte_en_cero_ni_acumulado_completo(self):
        p = s.parsear_diarios(DATA, estacion(), "2026-10-10", "2026-10-14", REF)
        self.assertEqual(p["dias_faltantes"], ["2026-10-14"])
        self.assertIsNone(p["acumulado_completo_mm"])
        self.assertAlmostEqual(p["suma_dias_disponibles_mm"], 5.4)
        self.assertFalse(p["cobertura_completa"])
        self.assertEqual(p["estado"], "PARCIAL")

    def test_null_negativos_e_id_de_otra_estacion_no_suman(self):
        datos = copy.deepcopy(DATA)
        datos[0]["precDiaCrono"] = None
        datos[1]["precDiaCrono"] = -999
        datos[2]["idEstacion"] = 403
        datos[3]["HMedia"] = 110
        p = s.parsear_diarios(datos, estacion(), "2026-10-10", "2026-10-13", REF)
        self.assertIsNone(p["acumulado_completo_mm"])
        self.assertEqual(p["suma_dias_disponibles_mm"], 2.6)
        self.assertEqual(p["dias_con_lluvia_valida"], 1)
        self.assertIsNone(p["registros"][-1]["humedad_relativa_media_pct"])
        self.assertTrue(any("otra estación" in a for a in p["avisos"]))

    def test_duplicado_ambiguo_y_dia_actual_no_acreditan_cobertura(self):
        datos = copy.deepcopy(DATA) + [copy.deepcopy(DATA[0])]
        futuro = copy.deepcopy(DATA[0]); futuro["fechaHora"] = "2026-10-16T00:00:00Z[UTC]"
        hoy = copy.deepcopy(DATA[0]); hoy["fechaHora"] = "2026-10-15T00:00:00Z[UTC]"
        mal = copy.deepcopy(DATA[0]); mal["fechaHora"] = "2026-10-12malformado"
        p = s.parsear_diarios(datos + [futuro, hoy, mal], estacion(), "2026-10-10", "2026-10-16", REF)
        self.assertEqual(len(p["registros"]), 3)
        self.assertIn("2026-10-10", p["dias_faltantes"])
        self.assertEqual(p["ultimo_dia"], "2026-10-13")
        self.assertFalse(p["cobertura_completa"])

    def test_lectura_vencida_y_tipo_desconocido_no_acreditan_actualidad(self):
        p = s.parsear_diarios(DATA, estacion(), "2026-10-10", "2026-10-13", "2026-10-17T12:00:00-03:00")
        self.assertEqual(p["estado"], "VENCIDO")
        e = estacion(); e["id_tipo"] = None
        p = s.parsear_diarios(DATA, e, "2026-10-10", "2026-10-13", REF)
        self.assertIsNone(p["acumulado_completo_mm"])
        self.assertTrue(all(r["precipitacion_mm"] is None for r in p["registros"]))

    def test_asociacion_cercana_prioriza_reciente_y_explica_representatividad(self):
        c = s.parsear_catalogo(CAT, REF)
        n = {"localidad": "Referencia", "lat": -28.43, "lon": -58.92}
        a = s.seleccionar_estaciones_cercanas([n], c, 50)[0]
        self.assertEqual(a["estacion"]["id"], 491)
        self.assertGreater(a["distancia_km"], 0)
        self.assertIn("pueden diferir", a["representatividad"])
        a = s.seleccionar_estaciones_cercanas([n], c, 0)[0]
        self.assertEqual(a["estacion"]["id"], 41)  # A distancia cero sólo queda la histórica.
        self.assertEqual(a["estacion"]["estado_catalogo"], "VENCIDO")

    def test_panorama_no_duplica_consultas_de_estacion_y_preserva_parcialidad(self):
        session = Session()
        n = {"localidad": "Bella Vista", "lat": -28.45, "lon": -58.99}
        p = s.obtener_panorama_siga([n, dict(n, localidad="Otra referencia")], REF, session, dias=5)
        self.assertEqual(len(session.calls), 3)  # JS, catálogo y una estación.
        self.assertEqual(len(p["consultas_por_id"]), 1)
        self.assertEqual(p["fuente"]["estado"], "PARCIAL")
        self.assertEqual(p["asociaciones"][0]["consulta_estado"], "PARCIAL")
        self.assertTrue(all(kw["allow_redirects"] is False and "verify" not in kw for _, kw in session.calls))
        diario = [url for url, _ in session.calls if "diario" in url][0]
        self.assertIn("491/10-10-2026/15-10-2026", parse_qs(urlparse(diario).query)["param_value"])

    def test_fallo_catalogo_no_produce_mediciones_cero(self):
        class ErrorSession:
            def get(self, *args, **kwargs):
                raise RuntimeError("No se conecta; no exponer https://example.invalid/secreto")
        p = s.obtener_panorama_siga([{"localidad": "X", "lat": -28.45, "lon": -58.99}], REF, ErrorSession())
        self.assertEqual(p["fuente"]["estado"], "ERROR")
        self.assertEqual(p["consultas_por_id"], {})
        self.assertNotIn("secreto", json.dumps(p))


if __name__ == "__main__":
    unittest.main()
