"""Regresiones SMN: un fallo ajeno no borra un aviso descargado."""
from datetime import timedelta
from email.utils import format_datetime
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from test_integracion_app import app, NOW, SMN_URL, cap_lluvia, fuente_cap


class SMNAuditoriaTests(unittest.TestCase):
    def test_canal_parcial_conserva_aviso_individual_y_explica_limite(self):
        fuente = fuente_cap(app.parsear_cap_smn(cap_lluvia(), SMN_URL))
        for cambio in ({"fallos": 1}, {"truncado": True}):
            with self.subTest(cambio=cambio):
                resultado = app.evaluar_smn_local("Rafaela", {"smn": dict(fuente, **cambio)}, NOW)
                self.assertFalse(resultado["smn_vigente"])
                self.assertEqual(resultado["smn_rango"], 2)
                evidencia = resultado["smn_evidencias"][0]
                self.assertFalse(evidencia["cobertura_canal_completa"])
                self.assertIn("cancelaciones", evidencia["criterio_vigencia"])

    def test_error_global_o_consulta_vencida_no_acredita_aviso(self):
        fuente = fuente_cap(app.parsear_cap_smn(cap_lluvia(), SMN_URL))
        for cambio in ({"error": "No se pudo consultar"},
                       {"consultado": NOW - timedelta(hours=25)},
                       {"consultado": NOW + timedelta(hours=1)}):
            with self.subTest(cambio=cambio):
                resultado = app.evaluar_smn_local("Rafaela", {"smn": dict(fuente, **cambio)}, NOW)
                self.assertFalse(resultado["smn_vigente"])
                self.assertIsNone(resultado["smn_rango"])

    def test_observacion_conserva_fecha_y_rechaza_futuro_o_zona_ausente(self):
        for valor, esperado in ((NOW.isoformat(), "RECIENTE"),
                ((NOW-timedelta(hours=4)).isoformat(), "VENCIDA"),
                ((NOW+timedelta(hours=1)).isoformat(), "FECHA FUTURA"),
                (NOW.replace(tzinfo=None).isoformat(), "SIN FECHA/HORA VERIFICABLE"),
                (None, "SIN FECHA/HORA VERIFICABLE")):
            with self.subTest(valor=valor):
                self.assertEqual(app.vigencia_observacion_smn(valor, NOW), esperado)

    def test_ventana_invertida_no_se_presenta_como_aviso_previsto(self):
        documento = app.parsear_cap_smn(cap_lluvia(onset=(NOW+timedelta(hours=5)).isoformat(),
            expires=(NOW+timedelta(hours=1)).isoformat()), SMN_URL)
        fuente = fuente_cap(documento)
        self.assertIsNone(app.evaluar_smn_local("Rafaela", {"smn": fuente}, NOW)["smn_rango"])

    def test_asociacion_no_renueva_la_fecha_original_de_medicion_o_consulta(self):
        import pandas as pd
        lectura = (NOW-timedelta(hours=4)).isoformat()
        consulta = (NOW-timedelta(minutes=20)).isoformat()
        df = pd.DataFrame([{"lat": -31.25, "lon": -61.49, "estacion": "Estación de prueba",
                            "smn_id": 1, "fecha_smn": lectura, "fecha_consulta_smn": consulta}])
        with patch.object(app, "ahora", return_value=NOW), patch.object(app, "NODOS", [
                {"localidad": "Rafaela", "provincia": "Santa Fe", "lat": -31.25, "lon": -61.49}]):
            fila = app.asociar_smn_a_nodos(df).iloc[0]
        self.assertEqual(fila["fecha_smn"], lectura)
        self.assertEqual(fila["fecha_consulta_smn"], consulta)
        self.assertEqual(fila["estado_observacion_smn"], "VENCIDA")

    def test_fechas_serializadas_se_evaluan_sin_inventar_zonas(self):
        fuente = fuente_cap(app.parsear_cap_smn(cap_lluvia(), SMN_URL))
        fuente["fecha_feed"] = fuente["fecha_feed"].isoformat()
        fuente["consultado"] = fuente["consultado"].isoformat()
        for fila in fuente["filas"]:
            for campo in ("emision", "desde", "hasta"):
                fila[campo] = fila[campo].isoformat()
        resultado = app.evaluar_smn_local("Rafaela", {"smn": fuente}, NOW)
        self.assertEqual(resultado["smn_rango"], 2)
        self.assertTrue(resultado["smn_vigente"])

    def test_ack_no_es_una_alerta_aplicable(self):
        fuente = fuente_cap(app.parsear_cap_smn(cap_lluvia(msgType="Ack"), SMN_URL))
        self.assertIsNone(app.evaluar_smn_local("Rafaela", {"smn": fuente}, NOW)["smn_rango"])

    def _leer_feed_con_referencia(self, tipo, emision):
        aviso = app.parsear_cap_smn(cap_lluvia(), SMN_URL)
        cambio = app.parsear_cap_smn(cap_lluvia(identifier="cambio", msgType=tipo,
            sent=emision.isoformat(), references="SMN,lluvia-rafaela,2026-10-14T11:00:00-03:00"), SMN_URL)
        urls = [SMN_URL, "https://ssl.smn.gob.ar/CAP/cambio.xml"]
        xml = ("<rss><channel><lastBuildDate>" + format_datetime(NOW) + "</lastBuildDate>" +
               "".join("<item><link>" + url + "</link></item>" for url in urls) + "</channel></rss>").encode()
        response = SimpleNamespace(content=xml, raise_for_status=lambda: None)
        app.obtener_cap_litoral.clear()
        with patch.object(app.requests, "get", return_value=response), \
             patch.object(app, "descargar_cap_smn", side_effect=lambda url: (aviso if url == SMN_URL else cambio, "")):
            resultado = app.obtener_cap_litoral()
        app.obtener_cap_litoral.clear()
        return resultado

    def test_ack_y_cancelacion_futura_no_anulan_aviso_actual(self):
        for tipo, emision in (("Ack", NOW), ("Cancel", NOW + timedelta(days=365))):
            with self.subTest(tipo=tipo):
                resultado = self._leer_feed_con_referencia(tipo, emision)
                self.assertEqual([fila["id"] for fila in resultado["filas"]], ["lluvia-rafaela"])

    def _leer_documento(self, documento):
        xml = ("<rss><channel><lastBuildDate>" + format_datetime(NOW) +
               "</lastBuildDate><item><link>" + SMN_URL + "</link></item></channel></rss>").encode()
        response = SimpleNamespace(content=xml, raise_for_status=lambda: None)
        app.obtener_cap_litoral.clear()
        with patch.object(app.requests, "get", return_value=response), \
             patch.object(app, "descargar_cap_smn", return_value=(documento, "")):
            resultado = app.obtener_cap_litoral()
        app.obtener_cap_litoral.clear()
        resultado["consultado"] = NOW
        return resultado

    def test_infos_de_distintas_areas_no_se_pierden_por_compartir_provincia(self):
        from copy import deepcopy
        import xml.etree.ElementTree as ET
        root = ET.fromstring(cap_lluvia())
        info = deepcopy(root.find("cap:info", app.CAP_NS))
        rosario = next(n for n in app.NODOS if n["localidad"] == "Rosario")
        lat, lon = rosario["lat"], rosario["lon"]
        info.find("cap:area/cap:polygon", app.CAP_NS).text = " ".join(
            f"{a},{b}" for a,b in ((lat-.05,lon-.05),(lat-.05,lon+.05),
                                   (lat+.05,lon+.05),(lat+.05,lon-.05),(lat-.05,lon-.05)))
        root.append(info)
        documento = app.parsear_cap_smn(ET.tostring(root), SMN_URL)
        fuente = self._leer_documento(documento)
        self.assertEqual(len(fuente["filas"]), 2)
        for localidad in ("Rafaela", "Rosario"):
            with self.subTest(localidad=localidad):
                self.assertEqual(app.evaluar_smn_local(localidad, {"smn": fuente}, NOW)["smn_rango"], 2)

    def test_infos_con_distinta_severidad_no_degradan_aviso_extremo(self):
        from copy import deepcopy
        import xml.etree.ElementTree as ET
        root = ET.fromstring(cap_lluvia())
        info = deepcopy(root.find("cap:info", app.CAP_NS))
        info.find("cap:severity", app.CAP_NS).text = "Moderate"
        root.append(info)
        fuente = self._leer_documento(app.parsear_cap_smn(ET.tostring(root), SMN_URL))
        self.assertEqual(len(fuente["filas"]), 2)
        self.assertEqual(app.evaluar_smn_local("Rafaela", {"smn": fuente}, NOW)["smn_rango"], 2)

    def test_cancelacion_real_emitida_anula_referencia(self):
        resultado = self._leer_feed_con_referencia("Cancel", NOW)
        self.assertEqual(resultado["filas"], [])


if __name__ == "__main__":
    unittest.main()
