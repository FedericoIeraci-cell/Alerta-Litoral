"""Regresiones públicas del informe común, sin HTTP real ni aplicaciones completas.

Los escenarios comprueban el dato que ve el usuario, su fecha y su caducidad;
reutilizan el catálogo completo y las respuestas deterministas del contrato.
"""
from __future__ import annotations

import copy
from datetime import timedelta, timezone
import json
import unittest
from unittest.mock import patch

import red_integrada as red
from test_red_integrada import (
    NOW, FakeStreamlit, Response, ala_frame, build_fixture, node_of,
    panorama, river_of, zc_fixture,
)


def utc(value):
    return value.astimezone(timezone.utc).isoformat()


def source_of(document, name):
    return next(f for f in document["fuentes"] if f["organismo"] == name)


def public_row(document, place):
    return next(r for r in red.filas_publicas_nodos(document)
                if r["Localidad"] == place)


def dataframes(streamlit):
    return [args[0] for name, args, _ in streamlit.calls if name == "dataframe"]


class PublicReportContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Cualquier llamada no inyectada es un fallo de la prueba, no un acceso real.
        with patch("requests.sessions.Session.request", side_effect=AssertionError("HTTP real prohibido")):
            cls.zc = zc_fixture()
            cls.document = build_fixture(zc=cls.zc)

    def setUp(self):
        guard = patch("requests.sessions.Session.request", side_effect=AssertionError("HTTP real prohibido"))
        guard.start()
        self.addCleanup(guard.stop)
        red.limpiar_cache_red_integrada()
        self.addCleanup(red.limpiar_cache_red_integrada)

    def fixture(self):
        return copy.deepcopy(self.document)

    def build(self, frame=None, hydro=None):
        return build_fixture(frame=frame, hydro=hydro, zc=copy.deepcopy(self.zc))

    def red_fixture(self):
        document = self.fixture()
        node = node_of(document, "Litoral", "Corrientes")
        node.update(estado_integrado_ala="ROJO", nivel_experimental="ROJO",
                    motivo_principal="Altura de Corrientes sobre su referencia de alerta.",
                    explicacion_version=1)
        evidence = {"tipo": "observacion_fluvial", "estacion": "Corrientes",
                    "fecha_observada": utc(NOW - timedelta(hours=2)),
                    "valida_hasta": utc(NOW + timedelta(hours=12)),
                    "fuente": "https://alerta.ina.gob.ar/a5/observaciones?seriesId=19",
                    "vigente": True}
        node["desencadenantes"] = [copy.deepcopy(evidence)]
        node["evidencias_alerta"] = [copy.deepcopy(evidence)]
        return document

    def test_ina_local_fuentes_y_rios_comparten_limite_de_seis_horas(self):
        for elapsed, expected in [(timedelta(hours=6), True),
                                  (timedelta(hours=6, seconds=1), False)]:
            with self.subTest(elapsed=elapsed):
                document = self.fixture()
                published = utc(NOW - elapsed)
                for place in ("Zárate", "Campana"):
                    river = river_of(document, place)
                    river["fecha_observada"] = published
                    river["fecha_emision"] = published
                    for name in ("INA · " + place, "INA · pronóstico " + place):
                        source_of(document, name)["fecha_documento"] = published
                checked = red.validar_red_integrada(document, NOW)
                self.assertTrue(checked["snapshot_vigente"])
                for place in ("Zárate", "Campana"):
                    river = river_of(checked, place)
                    self.assertEqual(river["observacion_vigente"], expected)
                    self.assertEqual(river["pronostico_vigente"], expected)
                    self.assertEqual(river["fecha_observada"], published)
                    for name in ("INA · " + place, "INA · pronóstico " + place):
                        source = source_of(checked, name)
                        self.assertEqual(source["limite_antiguedad_horas"], 6)
                        self.assertEqual(source["vigente"], expected)
                        self.assertEqual(source["fecha_documento"], published)

    def test_smn_exporta_fecha_y_consulta_y_declara_fallo_parcial_sin_exponer_error(self):
        name = "SMN · canal de avisos consultado por ALA"
        source = source_of(self.document, name)
        self.assertEqual(source["fecha_documento"], utc(NOW - timedelta(hours=2)))
        self.assertEqual(source["consultado"], utc(NOW))
        self.assertEqual(source["estado_publicado"], "CONSULTA_COMPLETA")
        self.assertTrue(source["vigente"])
        private = "private-example-token HTTP-body-example"
        for failure in ({"error": private}, {"fallos": [{"error": private}]}, {"truncado": True}):
            with self.subTest(failure=next(iter(failure))):
                hydro = panorama()
                hydro["smn"].update(failure)
                document = self.build(hydro=hydro)
                source = source_of(document, name)
                self.assertEqual(source["estado_publicado"], "CONSULTA_INCOMPLETA")
                self.assertFalse(source["vigente"])
                self.assertEqual(source["fecha_documento"], utc(NOW - timedelta(hours=2)))
                self.assertEqual(source["consultado"], utc(NOW))
                self.assertIn("incompleta", source["error"])
                self.assertNotIn(private, json.dumps(document))
                self.assertEqual(red.estado_publico(source["estado_tiempo"], source["vigente"],
                                                   True, source["error"]),
                                 "Datos incompletos: revisar fuente")

    def test_feed_smn_caduca_a_24_horas_aunque_la_consulta_sea_nueva(self):
        for elapsed, expected in [(timedelta(hours=24), True),
                                  (timedelta(hours=24, seconds=1), False)]:
            with self.subTest(elapsed=elapsed):
                hydro = panorama()
                hydro["smn"].update(fecha_feed=(NOW - elapsed).isoformat(),
                                     consultado=NOW.isoformat())
                document = self.build(hydro=hydro)
                source = source_of(document, "SMN · canal de avisos consultado por ALA")
                self.assertTrue(document["snapshot_vigente"])
                self.assertEqual(source["limite_antiguedad_horas"], 24)
                self.assertEqual(source["vigente"], expected)
                self.assertEqual(source["fecha_documento"], utc(NOW - elapsed))
                self.assertEqual(source["consultado"], utc(NOW))

    def test_fin_explicito_del_parte_o_fuente_prevalece_sobre_fecha_reciente(self):
        for until, expected in [(NOW, True), (NOW - timedelta(seconds=1), False)]:
            with self.subTest(until=until):
                document = self.fixture()
                document["represas"][0]["hasta"] = utc(until)
                source_of(document, "SMN · canal de avisos consultado por ALA")["valido_hasta"] = utc(until)
                checked = red.validar_red_integrada(document, NOW)
                dam = checked["represas"][0]
                source = source_of(checked, "SMN · canal de avisos consultado por ALA")
                self.assertEqual(dam["vigente"], expected)
                self.assertEqual(source["vigente"], expected)
                self.assertEqual(dam["hasta"], utc(until))
                self.assertEqual(source["valido_hasta"], utc(until))
                self.assertEqual(dam["fecha"], document["represas"][0]["fecha"])
                if not expected:
                    self.assertEqual(dam["estado_tiempo"], "VENCIDO")
                    self.assertEqual(source["estado_tiempo"], "VENCIDO")

    def test_represas_deduplican_solo_partes_identicos_y_conservan_la_secuencia(self):
        original = {"nombre": "Yacyretá", "fecha": utc(NOW - timedelta(hours=2)),
                    "fuente": "https://www.eby.gov.py/parte-publicado/", "texto": "Descenso informado",
                    "afluente_m3s": 39500, "afluente_tipo": "prevision_del_dia",
                    "afluente_previsto_min_m3s": 33000, "afluente_previsto_max_m3s": 39000,
                    "afluente_previsto_extremos_publicados_m3s": [39000, 33000],
                    "afluente_previsto_interpretacion": "Secuencia publicada; no es una banda probabilística."}
        hydro = panorama()
        hydro["represas"] = {
            "parte": original, "alias_del_parte": dict(reversed(list(original.items()))),
            "otra_fecha": {**original, "fecha": utc(NOW - timedelta(hours=3))},
            "otra_fuente": {**original, "fuente": "https://www.eby.gov.py/otro-parte/"},
            "otro_contenido": {**original, "texto": "Actualización del descenso informado"},
        }
        unchanged = copy.deepcopy(hydro)
        reports = self.build(hydro=hydro)["represas"]
        self.assertEqual(len(reports), 4)
        self.assertEqual(hydro, unchanged)
        self.assertEqual({r["nombre"] for r in reports}, {"Yacyretá"})
        for report in reports:
            self.assertEqual(report["afluente_previsto_extremos_publicados_m3s"], [39000, 33000])
            self.assertEqual(report["afluente_previsto_min_m3s"], 33000)
            self.assertEqual(report["afluente_previsto_max_m3s"], 39000)
            self.assertEqual(report["afluente_tipo"], "prevision_del_dia")

    def test_informe_mas_cuatro_horas_degrada_colores_evidencias_y_filas_publicas(self):
        document = self.red_fixture()
        original = copy.deepcopy(document)
        current = red.resumen_publico_red(document, NOW)
        self.assertEqual(current["rojos"], ["Corrientes"])
        self.assertEqual((current["nodos"], current["litoral"], current["zarate_campana"]), (58, 48, 10))
        later = NOW + timedelta(hours=4)
        summary = red.resumen_publico_red(document, later)
        self.assertEqual(summary["estado"], "antiguo")
        self.assertEqual(summary["rojos"], [])
        self.assertEqual(summary["lluvia_72_reciente"], 0)
        self.assertEqual(summary["alturas_recientes"], 0)
        self.assertEqual(summary["pronosticos_rio_recientes"], 0)
        node = node_of(summary["datos"], "Litoral", "Corrientes")
        self.assertEqual(node["estado_integrado_ala"], "ROJO")
        self.assertEqual(node["nivel_integrado_vigente"], "SIN DATOS")
        for field in ("desencadenantes", "evidencias_alerta"):
            self.assertTrue(node[field])
            self.assertFalse(any(e["vigente"] for e in node[field]))
            self.assertEqual(node[field][0]["fecha_observada"], utc(NOW - timedelta(hours=2)))
        row = public_row(summary["datos"], "Corrientes")
        self.assertEqual(row["Evaluación integrada"], "SIN DATOS")
        self.assertIn("Informe antiguo", row["Vigencia de la lluvia"])
        self.assertEqual(row["Consulta / cálculo de lluvia"], public_row(current["datos"], "Corrientes")["Consulta / cálculo de lluvia"])
        self.assertEqual(document, original)
        st = FakeStreamlit()
        with patch.object(red, "_ahora", return_value=later):
            red.mostrar_red_integrada(st, datos=document)
        self.assertFalse(any(name == "error" for name, _, _ in st.calls))
        self.assertTrue(any(name == "warning" and "Informe antiguo" in args[0]
                            for name, args, _ in st.calls))
        for rows in dataframes(st):
            for rendered in rows:
                if "Evaluación integrada" in rendered:
                    self.assertNotEqual(rendered["Evaluación integrada"], "ROJO")
                if "Vigente al consultar" in rendered:
                    self.assertEqual(rendered["Vigente al consultar"], "No")

    def test_cero_lluvia_es_dato_y_none_es_ausencia_en_filas_y_cobertura(self):
        document = self.fixture()
        node_of(document, "Litoral", "Corrientes")["lluvia_prevista_72_mm"] = None
        summary = red.resumen_publico_red(document, NOW)
        self.assertEqual(summary["lluvia_72_reciente"], 57)
        self.assertEqual(public_row(summary["datos"], "Corrientes")["Lluvia prevista 72 h"], "Sin dato")
        self.assertEqual(public_row(summary["datos"], "Goya")["Lluvia prevista 72 h"], "0 mm")
        self.assertEqual(red.valor_publico(0, "mm"), "0 mm")
        self.assertEqual(red.valor_publico(None, "mm"), "Sin dato")

    def test_consulta_reciente_sin_variables_se_muestra_como_incompleta(self):
        records = ala_frame().to_dict("records")
        records[0].update(cobertura_meteorologica=False, estado_datos="SIN DATOS",
                          nivel="SIN DATOS", error_datos="No llegó la precipitación prevista")
        document = self.build(frame=records)
        row = public_row(document, records[0]["localidad"])
        self.assertEqual(row["Lluvia prevista 24 h"], "Sin dato")
        self.assertEqual(row["Lluvia prevista 72 h"], "Sin dato")
        self.assertEqual(row["Vigencia de la lluvia"], "Datos incompletos: revisar fuente")
        self.assertEqual(row["Consulta / cálculo de lluvia"], "14/10/2026 15:50 ART")

    def test_horizonte_extendido_conserva_su_fin_real_sin_completar_quince_dias(self):
        records = ala_frame().to_dict("records")
        end = utc(NOW + timedelta(hours=288))
        records[0].update(cobertura_pronostico_extendido=True, pronostico_extendido_horas=288,
                          pronostico_extendido_hasta=end, lluvia_futura_extendida=0.0)
        document = self.build(frame=records)
        node = node_of(document, "Litoral", records[0]["localidad"])
        self.assertEqual(node["lluvia_prevista_15d_mm"], 0.0)
        coverage = node["cobertura_horaria"]["lluvia_prevista_15d"]
        self.assertEqual(coverage["validas"], 288)
        self.assertEqual(coverage["esperadas"], 288)
        self.assertEqual(coverage["hasta"], end)
        self.assertEqual(coverage["maximo_solicitado_horas"], 360)
        st = FakeStreamlit()
        with patch.object(red, "_ahora", return_value=NOW):
            red.mostrar_red_integrada(st, datos=document)
        rendered = next(r for rows in dataframes(st) for r in rows
                        if r.get("Localidad") == records[0]["localidad"] and "Fin de ese horizonte" in r)
        self.assertEqual(rendered["Lluvia en el horizonte disponible"], "0 mm")
        self.assertEqual(rendered["Fin de ese horizonte"], red.fecha_publica(end))

    def test_horizonte_extendido_incompleto_no_exporta_un_total(self):
        good = {"cobertura_pronostico_extendido": True, "pronostico_extendido_horas": 288,
                "pronostico_extendido_hasta": utc(NOW + timedelta(hours=288)),
                "lluvia_futura_extendida": 12.5}
        invalid = ({"cobertura_pronostico_extendido": False},
                   {"pronostico_extendido_hasta": None},
                   {"pronostico_extendido_hasta": "2026-10-26"},
                   {"pronostico_extendido_horas": 0},
                   {"pronostico_extendido_horas": 360.5},
                   {"lluvia_futura_extendida": None})
        for missing in invalid:
            with self.subTest(missing=missing):
                records = ala_frame().to_dict("records")
                records[0].update(good, **missing)
                node = node_of(self.build(frame=records), "Litoral", records[0]["localidad"])
                self.assertIsNone(node["lluvia_prevista_15d_mm"])
                self.assertNotIn("lluvia_prevista_15d", node["cobertura_horaria"])

    def test_actualizar_fuerza_respuesta_http_y_no_renueva_las_fechas_del_dato(self):
        original = self.fixture()
        changed = copy.deepcopy(original)
        changed["generated_at"] = utc(NOW + timedelta(minutes=1))
        changed["producer"]["version"] = "V4.8.0"
        with patch("requests.get", side_effect=[Response(original), Response(changed)]) as get, \
                patch.object(red.time, "monotonic", return_value=1234.0), \
                patch.object(red.time, "time_ns", return_value=123456789):
            first = red.consultar_red_integrada(referencia=NOW)
            cached = red.consultar_red_integrada(referencia=NOW)
            self.assertEqual(get.call_count, 1)
            self.assertEqual(cached["generated_at"], first["generated_at"])
            refreshed = red.consultar_red_integrada(referencia=NOW + timedelta(minutes=1), actualizar=True)
            self.assertEqual(refreshed["error"], "")
            self.assertEqual(get.call_count, 2)
            self.assertEqual(get.call_args.kwargs["params"], {"revision": "123456789"})
            self.assertEqual(refreshed["producer"]["version"], "V4.8.0")
            self.assertEqual(refreshed["generated_at"], changed["generated_at"])
            for collection, fields in {
                "nodos": ("fecha_calculo", "fecha_evaluacion_integrada"),
                "rios": ("fecha_observada", "fecha_emision"),
                "fuentes": ("fecha_documento", "consultado"),
                "represas": ("fecha", "hasta"),
            }.items():
                for before, after in zip(first[collection], refreshed[collection]):
                    for field in fields:
                        self.assertEqual(after.get(field), before.get(field), (collection, field))
            older = red.consultar_red_integrada(referencia=NOW + timedelta(hours=4))
            self.assertEqual(get.call_count, 2)
            self.assertFalse(older["snapshot_vigente"])
            self.assertEqual(older["generated_at"], changed["generated_at"])

    def test_control_reciente_distingue_ejecucion_automatica_de_prueba(self):
        for monitor, automatic, dry_run in [
            ({"checked_at": utc(NOW), "health": "ok", "dry_run": False}, True, False),
            ({"checked_at": utc(NOW), "health": "datos_parciales", "dry_run": False}, True, False),
            ({"checked_at": utc(NOW), "health": "ok", "dry_run": True}, False, True),
            ({"checked_at": utc(NOW - timedelta(hours=4)), "health": "ok", "dry_run": False}, False, False),
            ({"checked_at": utc(NOW), "health": "error", "dry_run": False}, False, False),
        ]:
            with self.subTest(monitor=monitor):
                document = self.fixture()
                document["monitor"] = monitor
                summary = red.resumen_publico_red(document, NOW)
                self.assertEqual(summary["control_reciente"], automatic)
                self.assertEqual(summary["prueba"], dry_run)

    def test_metadatos_monitor_incompatibles_no_rompen_la_vista_publica(self):
        for monitor in ("estado incompatible", ["estado"], [], 1, True):
            with self.subTest(monitor=monitor):
                document = self.fixture()
                document["monitor"] = monitor
                with self.assertRaises(ValueError):
                    red.validar_red_integrada(document, NOW)
                summary = red.resumen_publico_red(document, NOW)
                self.assertEqual(summary["estado"], "sin_confirmar")
        for monitor in (None, {}):
            with self.subTest(monitor=monitor):
                document = self.fixture()
                document["monitor"] = monitor
                summary = red.resumen_publico_red(document, NOW)
                self.assertEqual(summary["estado"], "reciente")
                self.assertFalse(summary["control_reciente"])

    def test_tabla_evidencias_muestra_fecha_de_observacion_fluvial(self):
        document = self.red_fixture()
        st = FakeStreamlit()
        with patch.object(red, "_ahora", return_value=NOW):
            red.mostrar_red_integrada(st, datos=document)
        observation = next(r for rows in dataframes(st) for r in rows
                           if r.get("Tipo de evidencia") == "observacion_fluvial")
        self.assertEqual(observation["Fecha publicada"],
                         red.fecha_publica(utc(NOW - timedelta(hours=2))))


if __name__ == "__main__":
    unittest.main()
