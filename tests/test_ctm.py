"""Regresiones con PDF oficiales CTM capturados el 14/10/2026."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).parents[1]))
import ctm

FIX = Path(__file__).parent / "fixtures"
NOW = "2026-10-14T12:00:00-03:00"
HOME = (FIX / "ctm_inicio_20261014.html").read_bytes()
NIVELES = (FIX / "ctm_niveles_20261014.pdf").read_bytes()
COMUNICADO = (FIX / "ctm_comunicado_20261014.pdf").read_bytes()


class Response:
    def __init__(self, data=b"", status=200, headers=None):
        self.data = data
        self.status_code = status
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def iter_content(self, chunk_size=65536):
        for i in range(0, len(self.data), chunk_size):
            yield self.data[i:i + chunk_size]

    def close(self):
        pass


class Session:
    def __init__(self, fail=None):
        self.calls = []
        self.fail = fail

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if "CaudalesNiveles.pdf" in url:
            return Response(NIVELES, 403 if self.fail == "niveles" else 200)
        if "Comunicado.pdf" in url:
            return Response(COMUNICADO, 403 if self.fail == "comunicado" else 200)
        return Response(HOME, 403 if self.fail == "home" else 200)


class CTMTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.niveles_texto = ctm._texto_pdf(NIVELES)
        cls.comunicado_texto = ctm._texto_pdf(COMUNICADO)

    def test_pdf_real_magic_fechas_y_ultima_columna(self):
        self.assertTrue(NIVELES.startswith(b"%PDF-"))
        p = ctm.parsear_niveles(self.niveles_texto, NOW)
        self.assertEqual(p["fuente"]["fecha_documento"], "2026-10-14T09:00:00-03:00")
        self.assertEqual(p["estaciones"]["concordia"]["fecha"], "2026-10-14T07:00:00-03:00")
        self.assertEqual(p["estaciones"]["concordia"]["altura_m"], 11.10)
        self.assertEqual(p["estaciones"]["paso de los libres"]["altura_m"], 9.37)
        self.assertEqual(p["estaciones"]["san javier"]["altura_m"], 6.05)
        self.assertIsNone(p["estaciones"]["concordia"]["alerta_m"])
        self.assertEqual(p["estaciones"]["san javier"]["series_id"], 65)

    def test_caudales_media_diaria_y_embalse_separado(self):
        p = ctm.parsear_niveles(self.niveles_texto, NOW)
        self.assertEqual(p["represa"]["erogado_m3s"], 16896)
        self.assertEqual(p["represa"]["vertedero_m3s"], 9456)
        self.assertEqual(p["represa"]["embalse_m"], 32.88)
        self.assertNotIn("salto grande", p["estaciones"])
        self.assertNotIn("federacion", p["estaciones"])
        self.assertNotEqual(p["estaciones"]["concordia"]["altura_m"], p["represa"]["embalse_m"])

    def test_manana_desde_fecha_documento_no_desde_hora_consulta(self):
        p = ctm.parsear_comunicado(self.comunicado_texto, NOW)
        self.assertEqual(p["fuente"]["valido_hasta"], "2026-10-15T15:00:00-03:00")
        self.assertEqual(p["represa"]["afluente_m3s"], 18328)
        self.assertEqual(p["represa"]["erogado_instantaneo_m3s"], 16424)
        self.assertEqual(p["represa"]["erogado_previsto_min_m3s"], 16000)
        self.assertEqual(p["represa"]["erogado_previsto_max_m3s"], 16800)
        f = p["pronosticos"][0]
        self.assertEqual(f["localidades"], ["concordia"])
        self.assertEqual(f["cota_tipo"], "limite_superior")
        self.assertEqual(f["nivel"], "SIN_DATOS")
        self.assertEqual(f["maximo_m"], 11.20)
        self.assertIsNone(f["minimo_m"])
        expired = ctm.parsear_comunicado(self.comunicado_texto, "2026-10-15T15:01:00-03:00")
        self.assertEqual(expired["fuente"]["estado"], "VENCIDO")

    def test_vigencia_dato_viejo_y_fecha_futura(self):
        self.assertEqual(ctm.parsear_niveles(self.niveles_texto, "2026-10-16T12:00:00-03:00")["fuente"]["estado"], "VENCIDO")
        self.assertEqual(ctm.parsear_niveles(self.niveles_texto, "2026-10-14T08:00:00-03:00")["fuente"]["estado"], "FECHA_FUTURA")

    def test_fecha_columnas_incompatible_no_produce_valor(self):
        text = self.niveles_texto.replace("Niveles Hora 07:00 (m)\n11/10/2026", "Niveles Hora 07:00 (m)\n15/10/2026")
        with self.assertRaises(ValueError):
            ctm.parsear_niveles(text, NOW)

    def test_resuelve_query_actual_y_rechaza_otros_hosts(self):
        html = '<a href="docs/hidrologia/Comunicado.pdf?nuevo=123&amp;v=4">a</a><a href="https://evil.example/docs/hidrologia/CaudalesNiveles.pdf">x</a>'
        urls = ctm.resolver_documentos(html)
        self.assertEqual(urls, {"comunicado": "https://www.saltogrande.org/docs/hidrologia/Comunicado.pdf?nuevo=123&v=4"})

    def test_fetch_offline_preserva_query_limites_y_observaciones(self):
        s = Session()
        p = ctm.obtener_ctm(NOW, s)
        self.assertEqual(p["fuente"]["estado"], "VIGENTE")
        self.assertEqual(len(p["estaciones"]), 7)
        self.assertEqual(p["pronosticos"][0]["maximo_m"], 11.2)
        self.assertEqual(len(p["fuentes"]), 2)
        pdf_calls = [(url, kw) for url, kw in s.calls if ".pdf" in url]
        self.assertTrue(all("?" in url for url, _ in pdf_calls))
        self.assertTrue(all(kw["timeout"] <= 20 and not kw["allow_redirects"] for _, kw in s.calls))
        self.assertTrue(all("verify" not in kw for _, kw in s.calls))

    def test_403_sin_verde_ni_cero_sustituto(self):
        p = ctm.obtener_ctm(NOW, Session("home"))
        self.assertEqual(p["fuente"]["estado"], "ERROR")
        self.assertEqual(p["estaciones"], {})
        self.assertIsNone(p["represa"])
        partial = ctm.obtener_ctm(NOW, Session("niveles"))
        self.assertEqual(partial["fuentes"][0]["estado"], "ERROR")
        self.assertEqual(partial["estaciones"], {})
        self.assertNotIn("erogado_m3s", partial["represa"])
        self.assertTrue(partial["avisos"])

    def test_html_en_lugar_de_pdf_y_documento_sobredimensionado(self):
        class InvalidSession(Session):
            def get(self, url, **kwargs):
                if ".pdf" in url:
                    return Response(b"<html>Forbidden</html>")
                return Response(HOME)
        p = ctm.obtener_ctm(NOW, InvalidSession())
        self.assertEqual(p["fuente"]["estado"], "ERROR")
        self.assertEqual(p["observaciones"], [])
        with self.assertRaises(ValueError):
            ctm._texto_pdf(b"%PDF-" + b"x" * ctm.MAX_BYTES)
        class LargeSession:
            def get(self, *args, **kwargs):
                return Response(b"%PDF-", headers={"Content-Length": str(ctm.MAX_BYTES + 1)})
        with self.assertRaises(ValueError):
            ctm._descargar(LargeSession(), ctm.INICIO, pdf=True)


if __name__ == "__main__":
    unittest.main()
