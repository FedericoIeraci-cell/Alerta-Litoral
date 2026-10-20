"""Datos y control normal independientes; pruebas de lectura sin monitor ni envíos."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import unittest
from unittest.mock import patch

import requests
import red_integrada as red


NOW = datetime(2026, 10, 18, 2, 40, tzinfo=timezone.utc)


def status(checked=None, health="datos_parciales"):
    checked = checked or NOW - timedelta(minutes=10)
    valid = 43 if health == "datos_parciales" else 48
    return {
        "schema_version": 1, "checked_at": checked.isoformat(), "frequency_minutes": 60,
        "scope": "Meteorología + hidrología + represas", "health": health,
        "nodes_total": 48, "nodes_valid": valid, "nodes_red": 8,
        "dry_run": False, "publication_only": False,
        "red_integrada_publicada": True, "red_integrada_estado": "verificada",
        "coverage": {"meteorologia": {"vigentes": 48, "aplicables": 48},
                     "hidrologia_observada": {"vigentes": 22, "aplicables": 29},
                     "hidrologia_prevista": {"vigentes": 17, "aplicables": 29},
                     "represas": {"vigentes": 18, "aplicables": 20}},
        "red_integrada_confirmacion": {"sha": "a" * 40, "bytes": 456293,
            "generated_at": (checked + timedelta(seconds=2)).isoformat(),
            "verified_at": (checked + timedelta(seconds=9)).isoformat()},
    }


def document(generated=NOW, dry_run=True):
    return red.construir_red_integrada([], {}, referencia=generated, monitor={
        "checked_at": generated.isoformat(), "health": "dry_run" if dry_run else "ok",
        "frequency_minutes": 60, "dry_run": dry_run, "nodes_total": 48,
        "nodes_valid": 43, "nodes_red": 8,
    })


class Response:
    def __init__(self, payload, size=None):
        self.payload = deepcopy(payload)
        self.content = json.dumps(payload, ensure_ascii=False).encode()
        if size:
            self.content = b"x" * size

    def raise_for_status(self):
        pass

    def json(self):
        return deepcopy(self.payload)


class FakeStreamlit:
    def __init__(self):
        self.calls = []
        self.depth = 0

    def __enter__(self):
        self.depth += 1
        return self

    def __exit__(self, *_):
        self.depth -= 1

    def __getattr__(self, name):
        def method(*args, **kwargs):
            self.calls.append((name, args, kwargs, self.depth))
            if name == "columns":
                return [self] * (args[0] if isinstance(args[0], int) else len(args[0]))
            if name == "selectbox":
                return args[1][0]
            if name == "button":
                return False
            if name == "expander":
                return self
        return method


class EstadoOperativoTests(unittest.TestCase):
    def setUp(self):
        self.guard = patch("requests.sessions.Session.request", side_effect=AssertionError("HTTP real y envíos prohibidos"))
        self.guard.start()
        self.addCleanup(self.guard.stop)
        red.limpiar_cache_red_integrada()
        self.addCleanup(red.limpiar_cache_red_integrada)

    def test_ci_posterior_no_oculta_control_normal_ni_renueva_su_fecha(self):
        registro, datos = status(), document()
        before = deepcopy((registro, datos))
        out = red.resumen_estado_operativo(datos, registro, NOW)
        self.assertEqual(out["datos"]["estado"], "reciente")
        self.assertTrue(out["datos"]["comprobacion"])
        self.assertEqual(out["control"]["estado"], "datos_parciales")
        self.assertTrue(out["control"]["control_reciente"])
        self.assertEqual(out["control"]["fecha"], registro["checked_at"])
        self.assertEqual(out["datos"]["fecha"], datos["generated_at"])
        self.assertNotEqual(out["control"]["fecha"], out["datos"]["fecha"])
        self.assertEqual((registro, datos), before)

    def test_informe_solo_no_acredita_control_aunque_declara_modo_normal(self):
        out = red.resumen_estado_operativo(document(dry_run=False), None, NOW)
        self.assertEqual(out["datos"]["estado"], "reciente")
        self.assertEqual(out["control"]["estado"], "sin_verificacion")
        self.assertFalse(out["control"]["control_reciente"])

    def test_todos_los_marcadores_de_comprobacion_impiden_control_normal(self):
        for key, value in (("dry_run", True), ("publication_only", True), ("health", "dry_run")):
            with self.subTest(key=key):
                registro = status()
                registro[key] = value
                out = red.resumen_publico_monitor(registro, NOW)
                self.assertEqual(out["estado"], "comprobacion")
                self.assertFalse(out["control_reciente"])

    def test_control_y_datos_caducan_por_sus_fechas_independientes(self):
        for elapsed, expected in ((timedelta(hours=3), "datos_parciales"),
                                  (timedelta(hours=3, seconds=1), "vencido")):
            with self.subTest(elapsed=elapsed):
                out = red.resumen_estado_operativo(document(), status(NOW - elapsed), NOW)
                self.assertEqual(out["datos"]["estado"], "reciente")
                self.assertEqual(out["control"]["estado"], expected)
        out = red.resumen_estado_operativo(document(NOW - timedelta(hours=4)), status(), NOW)
        self.assertEqual(out["datos"]["estado"], "antiguo")
        self.assertEqual(out["control"]["estado"], "datos_parciales")
        self.assertTrue(out["control"]["control_reciente"])

    def test_control_sin_faltantes_y_modos_de_fallo_se_distinguen(self):
        registro = status(health="ok")
        for row in registro["coverage"].values():
            row["vigentes"] = row["aplicables"]
        out = red.resumen_publico_monitor(registro, NOW)
        self.assertEqual(out["estado"], "normal_reciente")
        self.assertEqual(out["faltantes"], [])
        for health in ("error_red_integrada", "error_meteorologia", "error_meteorologia_zc", "error_registro", "envio_sin_confirmacion", "envio_rechazado", "configuracion_invalida", "sin_datos"):
            with self.subTest(health=health):
                failed = status(health=health)
                out = red.resumen_publico_monitor(failed, NOW)
                self.assertEqual(out["estado"], "con_error")
                self.assertFalse(out["control_reciente"])

    def test_cobertura_es_de_localidades_aplicables_y_no_de_estaciones_o_represas(self):
        out = red.resumen_publico_monitor(status(), NOW)
        self.assertEqual(out["nodes_invalid"], 5)
        self.assertIn("Observaciones de ríos: 22 de 29 localidades aplicables con información vigente.", out["faltantes"])
        self.assertIn("Previsión de ríos: 17 de 29 localidades aplicables con información vigente.", out["faltantes"])
        self.assertIn("Información de represas: 18 de 20 localidades aplicables con información vigente.", out["faltantes"])
        self.assertNotIn("estaciones", " ".join(out["faltantes"]))

    def test_fecha_modo_cobertura_y_confirmacion_incompatibles_no_acreditan_control(self):
        variants = [
            {"schema_version": True}, {"checked_at": "2026-10-18T02:30:00"},
            {"checked_at": (NOW + timedelta(minutes=6)).isoformat()},
            {"frequency_minutes": True}, {"health": "inventado"}, {"dry_run": "false"},
            {"publication_only": None}, {"nodes_valid": 49}, {"nodes_red": True},
            {"coverage": {"represas": {"vigentes": 21, "aplicables": 20}}},
            {"red_integrada_confirmacion": {"sha": "malformado"}},
        ]
        for change in variants:
            with self.subTest(change=change):
                out = red.resumen_publico_monitor({**status(), **change}, NOW)
                self.assertEqual(out["estado"], "sin_verificacion")
                self.assertFalse(out["control_reciente"])
        pending = status()
        pending.update(red_integrada_publicada=False, red_integrada_estado="pendiente")
        pending.pop("red_integrada_confirmacion")
        self.assertEqual(red.resumen_publico_monitor(pending, NOW)["estado"], "sin_verificacion")
        missing = status()
        missing.pop("red_integrada_confirmacion")
        self.assertFalse(red.resumen_publico_monitor(missing, NOW)["control_reciente"])

    def test_lector_solo_get_del_estado_cachea_campos_publicos_y_refresco_no_renueva_fechas(self):
        registro = status()
        registro.update(history=[{"texto": "secreto-artificial"}], result={"pendientes": 8},
                        errors=["secreto-artificial"], ubicacion={"latitude": 1, "longitude": 2})
        response = Response(registro)
        with patch("requests.get", return_value=response) as get:
            first = red.consultar_estado_monitor(referencia=NOW)
            second = red.consultar_estado_monitor(referencia=NOW + timedelta(hours=4))
            self.assertEqual(get.call_count, 1)
            self.assertEqual(red.resumen_publico_monitor(second, NOW + timedelta(hours=4))["estado"], "vencido")
            third = red.consultar_estado_monitor(actualizar=True, referencia=NOW)
            self.assertEqual(get.call_count, 2)
            self.assertIn("revision", get.call_args.kwargs["params"])
            self.assertTrue(all(call.args[0].endswith("/monitor_status.json") for call in get.call_args_list))
        self.assertEqual(first["checked_at"], third["checked_at"])
        self.assertEqual(first["red_integrada_confirmacion"], third["red_integrada_confirmacion"])
        serialized = json.dumps([first, second, third, red._CACHE_MONITOR], ensure_ascii=False)
        for forbidden in ("secreto-artificial", "history", "pendientes", "latitude", "longitude", "ubicacion"):
            self.assertNotIn(forbidden, serialized)

    def test_estado_pendiente_no_queda_congelado_en_cache(self):
        pending = status()
        pending.update(red_integrada_publicada=False, red_integrada_estado="pendiente")
        pending.pop("red_integrada_confirmacion")
        with patch("requests.get", side_effect=[Response(pending), Response(status())]) as get:
            first = red.consultar_estado_monitor()
            second = red.consultar_estado_monitor()
        self.assertEqual(get.call_count, 2)
        self.assertFalse(red.resumen_publico_monitor(first, NOW)["control_reciente"])
        self.assertTrue(red.resumen_publico_monitor(second, NOW)["control_reciente"])

    def test_error_http_y_tamano_rechazados_sin_exponer_cuerpo_ni_promover_datos(self):
        with patch("requests.get", side_effect=requests.HTTPError("secreto-artificial")):
            error = red.consultar_estado_monitor()
        self.assertNotIn("secreto-artificial", json.dumps(error))
        self.assertEqual(red.resumen_estado_operativo(document(), error, NOW)["control"]["estado"], "sin_verificacion")
        with patch("requests.get", return_value=Response(status(), red.MONITOR_MAX_BYTES + 1)):
            oversized = red.consultar_estado_monitor()
        self.assertTrue(oversized["error"])

    def test_portada_usa_control_separado_y_explica_ci_solo_en_detalles(self):
        st = FakeStreamlit()
        out = red.mostrar_estado_conexion(st, "zc", datos=document(), estado_monitor=status(), referencia=NOW)
        top = [str(args[0]) for _, args, _, depth in st.calls if args and depth == 0]
        details = [str(args[0]) for _, args, _, depth in st.calls if args and depth > 0]
        self.assertTrue(out["estado_operativo"]["control"]["control_reciente"])
        self.assertTrue(any("Control normal con datos parciales" in text for text in top))
        self.assertFalse(any("no confirma" in text or "comprobación" in text for text in top))
        self.assertTrue(any("modo de comprobación" in text for text in details))

    def test_tabla_compartida_conserva_json_de_datos_sin_insertar_estado_horario(self):
        datos = document()
        original = deepcopy(datos)
        st = FakeStreamlit()
        red.mostrar_red_integrada(st, datos=datos, estado_monitor=status(), referencia=NOW)
        downloaded = next(args[1] for name, args, _, _ in st.calls if name == "download_button")
        export = json.loads(downloaded)
        self.assertTrue(export["monitor"]["dry_run"])
        self.assertNotIn("estado_operativo", export)
        self.assertEqual(export["generated_at"], original["generated_at"])
        self.assertEqual(datos, original)


if __name__ == "__main__":
    unittest.main()
