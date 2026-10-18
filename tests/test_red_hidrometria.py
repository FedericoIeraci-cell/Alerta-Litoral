"""Integración de la automática RHN como referencia adicional, sin red ni monitor.

Los metadatos y la observación de 1,12 m proceden de fixtures congeladas. Las
22 referencias del Litoral que completan el inventario son casos sintéticos.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

import hidrometria_local as hid
import red_integrada as red


NOW = datetime(2026, 10, 18, 1, 0, tzinfo=timezone.utc)
FIXTURES = Path(__file__).parent / "fixtures"


def observation(age=1):
    payload = json.loads((FIXTURES / "ina_zarate_observada_29437_20261017.json").read_text())
    metadata = json.loads((FIXTURES / "ina_zarate_simulada_29534_metadatos_20261017.json").read_text())
    # Sólo los escenarios de caducidad desplazan las fechas; conserva la serie
    # temporal y sus valores, y no representa una consulta o dato nuevos.
    if age != 1:
        last = datetime.fromisoformat(payload["observaciones"][-1][0].replace("Z", "+00:00"))
        delta = NOW - timedelta(hours=age) - last
        for point in payload["observaciones"]:
            for index in (0, 1):
                point[index] = (datetime.fromisoformat(point[index].replace("Z", "+00:00")) + delta).isoformat()
    return hid.parsear_altura_zarate(payload, referencia=NOW, consulta=NOW,
                                    metadatos_pronostico=metadata)


def port_rows():
    rows = []
    for name, series, forecast, value, alert, evacuation in (
            ("Zárate", 40, 29534, 1.55, 2.0, 2.2),
            ("Campana", 41, 3405, 1.20, 2.2, 2.45)):
        rows.append({
            "red": "Zárate–Campana", "estacion": name, "serie_observada": series,
            "serie_pronostico": forecast, "altura_m": value,
            "alerta_m": alert, "evacuacion_m": evacuation,
            "fecha_observada": (NOW - timedelta(hours=2)).isoformat(),
            "fecha_emision": (NOW - timedelta(hours=1)).isoformat(),
            "fuente": "https://alerta.ina.gob.ar/geoserver/ows", "error": "",
            "pronostico": [{"fecha": (NOW + timedelta(hours=12)).isoformat(), "central_m": 1.5}],
        })
    return rows


def automatic_row(value):
    # Contrato de transporte: el validador debe reconstruir los campos visibles.
    return {"red": "Zárate–Campana", "estacion": "Paraná Las Palmas - Zárate",
            "serie_observada": 29437, "serie_pronostico": None,
            "observacion_automatica": deepcopy(value)}


def document(value=None, include_automatic=True):
    value = observation() if value is None else value
    rivers = port_rows()
    sources = []
    if include_automatic:
        rivers.append(automatic_row(value))
        sources.append({"red": "Zárate–Campana", "organismo": "INA / RHN · estación automática de Zárate",
                        "url": value["fuente"], "consultado": value["consulta"],
                        "fecha_documento": value["fecha_observada"], "error": value["error"]})
    stations = {f"sintetica_{i}": {
        "nombre": f"Estación sintética del Litoral {i:02d}", "series_id": 1000 + i,
        "altura_m": 2.3, "fecha": (NOW - timedelta(hours=2)).isoformat(),
        "fuente": f"https://alerta.ina.gob.ar/a5/observaciones?seriesId={1000 + i}",
    } for i in range(1, 23)}
    return red.construir_red_integrada([], {"estaciones": stations},
        zc={"nodos": [], "rios": rivers, "fuentes": sources}, referencia=NOW,
        monitor={"nodes_total": 48, "nodes_valid": 43, "dry_run": False})


def automatic(document):
    return next(r for r in document["rios"]
                if r["red"] == "Zárate–Campana" and r["serie_observada"] == 29437)


class RedHidrometriaTests(unittest.TestCase):
    def setUp(self):
        for target in ("requests.sessions.Session.request", "urllib.request.OpenerDirector.open"):
            guard = patch(target, side_effect=AssertionError("HTTP real prohibido en esta integración"))
            guard.start()
            self.addCleanup(guard.stop)

    def test_25_referencias_conservan_22_litoral_y_puertos_40_41(self):
        baseline = document(include_automatic=False)
        added = document()
        self.assertEqual(added["rios"][:24], baseline["rios"])
        self.assertEqual(len(added["rios"]), 25)
        self.assertEqual(sum(r["red"] == "Litoral" for r in added["rios"]), 22)
        self.assertEqual([r["serie_observada"] for r in added["rios"] if r["red"] == "Zárate–Campana"], [40, 41, 29437])
        self.assertEqual(len(added["nodos"]), 58)
        self.assertEqual(sum(n["red"] == "Litoral" for n in added["nodos"]), 48)
        self.assertEqual(added["nodos"], baseline["nodos"])
        self.assertEqual(added["monitor"]["nodes_total"], 48)
        self.assertFalse(added["envia_telegram"])
        self.assertFalse(added["permite_orden_evacuacion"])
        self.assertEqual(automatic(added)["altura_m"], 1.12)

    def test_precision_del_registro_litoral_cruza_el_informe_sin_inventar_medicion(self):
        fecha=(NOW-timedelta(hours=2)).isoformat()
        fuente="https://alerta.ina.gob.ar/geoserver/ows"
        data=red.construir_red_integrada([], {"estaciones":{"corrientes":{
            "nombre":"Corrientes", "series_id":19, "altura_m":5.1,
            "fecha":fecha, "fecha_registro":fecha, "precision_fecha":"fecha_registro_fuente",
            "fuente":fuente, "fuente_pronostico":"https://alerta.ina.gob.ar/a5/pronosticos"}}}, referencia=NOW)
        rio=next(r for r in red.validar_red_integrada(data,NOW)["rios"] if r["serie_observada"]==19)
        self.assertEqual(rio["fecha_observada"],fecha)
        self.assertEqual(rio["fecha_registro"],fecha)
        self.assertFalse(rio["fecha_medicion_confirmada"])
        self.assertEqual(rio["precision_observada"],"fecha_registro_fuente")
        self.assertIn("hora de medición no confirmada",red.fecha_publica(rio["fecha_observada"],rio["precision_observada"]))
        self.assertEqual(rio["fuente_pronostico"],"https://alerta.ina.gob.ar/a5/pronosticos")

    def test_puertos_wfs_tienen_fecha_registro_y_automatica_conserva_medicion(self):
        data=document()
        for rio in data["rios"]:
            if rio["serie_observada"] in (40,41):
                self.assertEqual(rio["precision_observada"],"fecha_registro_fuente")
                self.assertFalse(rio["fecha_medicion_confirmada"])
                self.assertEqual(rio["fecha_observada"],rio["fecha_registro"])
        auto=automatic(data)
        self.assertNotEqual(auto.get("precision_observada"),"fecha_registro_fuente")
        self.assertNotIn("no confirmada",red.fecha_publica(auto["fecha_observada"],auto.get("precision_observada")))

    def test_tabla_publica_muestra_precision_del_registro_sin_ocultarla(self):
        from streamlit.testing.v1 import AppTest

        def app(data,referencia):
            import streamlit as st
            from red_integrada import mostrar_red_integrada
            mostrar_red_integrada(st,datos=data,estado_monitor={},referencia=referencia)

        at=AppTest.from_function(app,args=(document(),NOW)).run(timeout=20)
        self.assertFalse(at.exception)
        tabla=next(d.value for d in at.dataframe if "Fecha de esa lectura" in d.value.columns)
        puertos=tabla[tabla["Estación"].isin(["Zárate","Campana"])]
        self.assertEqual(len(puertos),2)
        self.assertTrue(puertos["Fecha de esa lectura"].str.contains("hora de medición no confirmada").all())
        auto=tabla[tabla["Estación"]=="Paraná Las Palmas - Zárate"].iloc[0]
        self.assertNotIn("no confirmada",auto["Fecha de esa lectura"])

    def test_fila_remota_no_importa_umbral_curva_ni_orden(self):
        data = document()
        row = automatic(data)
        original = deepcopy(row["observacion_automatica"])
        row.update(altura_m=99, alerta_m=0.1, evacuacion_m=0.2, cero_ign_m=80,
                   fecha_observada=NOW.isoformat(), fecha_emision=NOW.isoformat(),
                   fuente="https://ejemplo.invalid/otra-escala", escala_equivalente=True,
                   pronostico=[{"fecha": (NOW + timedelta(hours=1)).isoformat(), "central_m": 99}],
                   prioridad=3, orden_evacuacion=True, rango_evacuacion=3)
        row["observacion_automatica"].update(alerta_m=0.1, evacuacion_m=0.2, curva=[99],
                                             pronostico=[99], orden_evacuacion=True)
        checked = automatic(red.validar_red_integrada(data, NOW))
        self.assertEqual(checked["altura_m"], original["altura_m"])
        self.assertEqual(checked["fecha_observada"], original["fecha_observada"])
        self.assertEqual(checked["cero_ign_m"], .243)
        self.assertEqual(checked["fuente"], original["fuente"])
        self.assertIsNone(checked["alerta_m"])
        self.assertIsNone(checked["evacuacion_m"])
        self.assertIsNone(checked["fecha_emision"])
        self.assertEqual(checked["pronostico"], [])
        self.assertFalse(checked["escala_equivalente"])
        for key in ("prioridad", "orden_evacuacion", "rango_evacuacion"):
            self.assertNotIn(key, checked)
        for key in ("alerta_m", "evacuacion_m", "curva", "pronostico", "orden_evacuacion"):
            self.assertNotIn(key, checked["observacion_automatica"])

    def test_identidad_anidada_incompatible_no_produce_altura_actual(self):
        for key, value in (("serie", 40), ("estacion_id", 5908), ("red", "alturas_prefe"),
                           ("id_externo", "otro"), ("unidad", "cm"), ("cero_ign_m", .418),
                           ("nombre", "Campana"), ("fuente", "https://ejemplo.invalid/dato")):
            with self.subTest(field=key):
                data = document()
                automatic(data)["observacion_automatica"][key] = value
                row = automatic(red.validar_red_integrada(data, NOW))
                self.assertIsNone(row["altura_m"])
                self.assertFalse(row["observacion_vigente"])
                self.assertFalse(row["observacion_automatica"]["coincidencia_documental_pronostico"])

    def test_identidad_exterior_o_duplicado_no_se_acepta_como_automatica(self):
        for key, value in (("estacion", "Campana"), ("serie_pronostico", 29534),
                           ("serie_observada", 29438)):
            with self.subTest(field=key):
                data = document()
                automatic(data)[key] = value
                with self.assertRaises(ValueError):
                    red.validar_red_integrada(data, NOW)
                self.assertIsNone(red.altura_automatica_zarate_de_red(data, NOW))
        data = document()
        data["rios"].append(deepcopy(automatic(data)))
        with self.assertRaises(ValueError):
            red.validar_red_integrada(data, NOW)
        self.assertIsNone(red.altura_automatica_zarate_de_red(document(include_automatic=False), NOW))

    def test_lectura_de_mas_de_seis_horas_conserva_historia_sin_vigencia(self):
        for age, expected in ((6, True), (6 + 1 / 3600, False)):
            with self.subTest(age=age):
                value = observation(age)
                data = document(value)
                row = automatic(data)
                self.assertEqual(row["observacion_vigente"], expected)
                self.assertEqual(row["altura_m"], 1.12)
                self.assertEqual(row["fecha_observada"], value["fecha_observada"])
                extracted = red.altura_automatica_zarate_de_red(data, NOW)
                self.assertEqual(extracted["vigente"], expected)
                self.assertEqual(extracted["fecha_observada"], value["fecha_observada"])

    def test_fecha_invalida_futura_o_escalar_contradictorio_no_se_promueve(self):
        for key, value in (("fecha_observada", "2026-10-18T00:00:00"),
                           ("fecha_observada", "2026-10-18"),
                           ("fecha_observada", (NOW + timedelta(hours=1)).isoformat()),
                           ("altura_m", 99)):
            with self.subTest(field=key, value=value):
                data = document()
                automatic(data)["observacion_automatica"].update({key: value, "vigente": True})
                checked = automatic(red.validar_red_integrada(data, NOW))
                self.assertFalse(checked["observacion_vigente"])
                self.assertIsNone(checked["altura_m"])

    def test_informe_caducado_bloquea_extraccion_aunque_medicion_tenga_menos_de_6h(self):
        data = document()
        before = deepcopy(data)
        boundary = NOW + timedelta(hours=3)
        self.assertIsNotNone(red.altura_automatica_zarate_de_red(data, boundary))
        late = boundary + timedelta(seconds=1)
        checked = red.validar_red_integrada(data, late)
        self.assertFalse(checked["snapshot_vigente"])
        self.assertFalse(automatic(checked)["observacion_vigente"])
        self.assertIsNone(red.altura_automatica_zarate_de_red(data, late))
        self.assertEqual(checked["generated_at"], data["generated_at"])
        self.assertEqual(data, before)

    def test_campana_no_hereda_la_altura_automatica_de_zarate(self):
        data = document()
        campana = next(r for r in data["rios"] if r["estacion"] == "Campana")
        campana.update(altura_m=None, fecha_observada=None, error="Sin lectura de Campana")
        checked = red.validar_red_integrada(data, NOW)
        campana = next(r for r in checked["rios"] if r["estacion"] == "Campana")
        self.assertIsNone(campana["altura_m"])
        self.assertFalse(campana["observacion_vigente"])
        self.assertEqual(campana["serie_observada"], 41)
        self.assertEqual(campana["serie_pronostico"], 3405)
        self.assertNotIn("observacion_automatica", campana)
        self.assertEqual(automatic(checked)["altura_m"], 1.12)

    def test_fecha_canonica_unica_en_fila_fuente_y_dato_extraido(self):
        value = observation()
        equivalent = datetime.fromisoformat(value["fecha_observada"]).astimezone(red.TZ).isoformat()
        value["fecha_observada"] = equivalent
        duplicate = deepcopy(value["observaciones"][-1])
        duplicate["fecha"] = equivalent
        value["observaciones"].append(duplicate)
        data = document(value)
        frozen = json.loads(json.dumps(data, allow_nan=False))
        row = automatic(frozen)
        extracted = red.altura_automatica_zarate_de_red(frozen, NOW)
        canonical = "2026-10-18T00:00:00+00:00"
        self.assertEqual(row["fecha_observada"], canonical)
        self.assertEqual(extracted["fecha_observada"], canonical)
        self.assertEqual(extracted["observaciones"][-1]["fecha"], canonical)
        self.assertEqual(frozen["fuentes"][0]["fecha_documento"], canonical)
        dates = [point["fecha"] for point in extracted["observaciones"]]
        self.assertEqual(len(dates), len(set(dates)))
        self.assertEqual(extracted["altura_m"], extracted["observaciones"][-1]["altura_m"])
        self.assertEqual(red.validar_red_integrada(frozen, NOW), frozen)

    def test_cero_es_una_medicion_y_no_se_convierte_en_ausencia(self):
        value = observation()
        value["altura_m"] = 0.0
        value["observaciones"][-1]["altura_m"] = 0.0
        row = automatic(document(value))
        self.assertEqual(row["altura_m"], 0.0)
        self.assertTrue(row["observacion_vigente"])

    def test_import_puro_no_carga_apps_monitor_ni_hace_http(self):
        code = """from unittest.mock import patch
import sys
with patch('urllib.request.OpenerDirector.open', side_effect=AssertionError('HTTP prohibido')):
    with patch('socket.socket.connect', side_effect=AssertionError('Red prohibida')):
        import red_integrada
        import hidrometria_local
assert not {'app', 'monitor', 'streamlit'} & set(sys.modules)
print('importaciones puras')
"""
        process = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1],
                                 text=True, capture_output=True, timeout=20)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(process.stdout.strip(), "importaciones puras")


if __name__ == "__main__":
    unittest.main()
