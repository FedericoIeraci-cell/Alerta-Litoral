"""Independent contract scenarios for the shared ALA/Zárate–Campana feed.

All scenarios use recorded-shaped fixtures and an injected HTTP session.  They
check the public contract, timestamps and unavailable-source behavior without
making network requests or importing either complete Streamlit application.
"""
from __future__ import annotations

import copy
from datetime import datetime, timedelta
import json
import math
from pathlib import Path
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

import pandas as pd
import red_integrada as red


TZ = ZoneInfo("America/Argentina/Buenos_Aires")
NOW = datetime(2026, 10, 14, 16, 0, tzinfo=TZ)
ALA_PLACES = (
    "Reconquista", "Avellaneda", "Vera", "Tostado", "Rafaela", "Santa Fe",
    "Esperanza", "Rosario", "Venado Tuerto", "Corrientes", "Goya", "Mercedes",
    "Curuzú Cuatiá", "Paso de los Libres", "Santo Tomé", "Paraná", "La Paz",
    "Concordia", "Resistencia", "General José de San Martín",
    "Presidencia Roque Sáenz Peña", "Charata", "Villa Ángela", "Nueva Pompeya",
    "Formosa", "Clorinda", "Pirané", "El Colorado", "Las Lomitas",
    "Ingeniero Guillermo N. Juárez", "Posadas", "Oberá", "Eldorado",
    "Puerto Iguazú", "San Vicente", "El Soberbio", "Gualeguaychú", "Gualeguay",
    "Victoria", "Villaguay", "Federal", "Chajarí", "Ituzaingó", "Barranqueras",
    "Andresito", "Colón", "Concepción del Uruguay", "San Javier (Misiones)",
)
ALA_PROVINCES = (["Santa Fe"] * 9 + ["Corrientes"] * 6 + ["Entre Ríos"] * 3
                 + ["Chaco"] * 6 + ["Formosa"] * 6 + ["Misiones"] * 6
                 + ["Entre Ríos"] * 6 + ["Corrientes", "Chaco", "Misiones",
                                        "Entre Ríos", "Entre Ríos", "Misiones"])


def ala_frame():
    return pd.DataFrame([{
        "localidad": place, "provincia": province, "lat": -30.5,
        "lon": -59.5, "nivel": "VERDE", "nivel_meteorologico": "BAJO",
        "indice": 0.0, "lluvia_futura_24": 0.0, "lluvia_futura_72": 0.0,
        "lluvia_futura_7d": 0.0, "lluvia_7d": 0.0, "runoff_futuro_72": 0.0,
        "humedad_0_7": 0.2, "actualizado": "14/10/2026 15:50",
        "consulta_meteorologica": (NOW - timedelta(minutes=10)).isoformat(),
        "estado_datos": "OK", "error_datos": "",
        "fecha_evaluacion_integrada": (NOW - timedelta(minutes=8)).isoformat(),
        "cobertura_meteorologica": True, "hidrologia_aplica": False,
        "hidrologia_vigente": False, "hidrologia_pronostico_vigente": False,
        "hidrologia_rango": None, "smn_vigente": True,
        "hidrologia_fecha": None, "accion_prioritaria": "Mantener vigilancia",
    } for place, province in zip(ALA_PLACES, ALA_PROVINCES)])


def panorama():
    # Distinct observations and forecasts must retain their own timestamps.
    observed = (NOW - timedelta(hours=2)).isoformat()
    issued = (NOW - timedelta(hours=8)).isoformat()
    return {
        "version": "1.0", "consultado": NOW.isoformat(),
        "estaciones": {
            "corrientes": {"nombre": "Corrientes", "rio": "Paraná",
                "altura_m": 2.3, "alerta_m": 6.5, "evacuacion_m": 7.0,
                "fecha": observed, "fecha_documento": observed,
                "fuente": "https://alerta.ina.gob.ar/a5/observaciones?seriesId=19",
                "nivel": "BAJO", "series_id": 19,
                "pronostico_numerico": [{"fecha": (NOW + timedelta(hours=24)).isoformat(),
                    "valor": 2.4, "inferior": 2.0, "superior": 2.8,
                    "emision": issued}]},
        },
        "pronosticos": [{"localidades": ["corrientes"], "nivel": "BAJO",
            "fecha_emision": issued, "desde": NOW.isoformat(),
            "hasta": (NOW + timedelta(days=3)).isoformat(),
            "texto": "Previsión oficial de prueba", "central_m": 2.4,
            "inferior_m": 2.0, "superior_m": 2.8,
            "fuente": "https://alerta.ina.gob.ar/a5/diario/reporte_diario"}],
        "represas": {
            "yacyreta": {"nombre": "Yacyretá", "erogado_m3s": 13000.0,
                "fecha": observed, "fecha_documento": observed,
                "fuente": "https://www.eby.gov.py/", "vigente": True},
            "salto_grande": {"nombre": "Salto Grande", "erogado_m3s": 5000.0,
                "fecha": observed, "fecha_documento": observed,
                "fuente": "https://www.saltogrande.org/", "vigente": True},
        },
        "fuentes": [{"id": "ina_diario", "nombre": "INA diario",
            "url": "https://alerta.ina.gob.ar/a5/diario/reporte_diario",
            "estado": "VIGENTE", "fecha_documento": observed,
            "consultado": NOW.isoformat(), "error": None}],
        "avisos": [], "curvas": [], "smn": {"fecha_feed": observed,
            "consultado": NOW.isoformat(), "filas": [], "fallos": [], "error": None},
    }


def meteo_payload(lat=-34.098, lon=-59.028, future_hours=360):
    times = [(NOW + timedelta(hours=i)).replace(tzinfo=None).isoformat(timespec="minutes")
             for i in range(-192, future_hours + 1)]
    return {
        "latitude": lat, "longitude": lon,
        "timezone": "America/Argentina/Buenos_Aires", "utc_offset_seconds": -10800,
        "hourly_units": {"time": "iso8601", "precipitation": "mm", "runoff": "mm",
                         "soil_moisture_0_to_7cm": "m³/m³"},
        "hourly": {"time": times, "precipitation": [0.0] * len(times),
                   "runoff": [0.0] * len(times), "soil_moisture_0_to_7cm": [.22] * len(times)},
    }


def wfs_payload(age=2, date=None):
    observed = date if date is not None else (NOW - timedelta(hours=age)).isoformat()
    return {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {
        "series_id": series, "nombre": place, "var_id": 2, "tabla": "alturas_prefe",
        "nivel_de_alerta": alert, "nivel_de_evacuacion": evacuation,
        "cero_ign": zero, "fecha": observed, "valor": value,
        "timeseries": json.dumps([[observed, value]]),
    }} for place, series, alert, evacuation, zero, value in [
        ("Zárate", 40, 2.0, 2.2, .243, 1.39),
        ("Campana", 41, 2.2, 2.45, .418, .7),
    ]]}


def forecast_payload(series=29534):
    return {"id": 100, "cal_id": 489,
        "forecast_date": (NOW - timedelta(hours=1)).isoformat(),
        "series": [{"series_id": series, "cor_id": 100, "qualifier": qualifier,
            "pronosticos": [{"timestart": (NOW + timedelta(hours=h)).isoformat(),
                             "valor": value} for h in [24, 48, 120]]}
                   for qualifier, value in [("main", 1.5), ("error_band_01", 1.2),
                                            ("error_band_99", 1.8)]]}


def automatic_payload(series=29437):
    """Metadatos INA congelados; fecha y valor de prueba explícitos para NOW."""
    name = ("ina_zarate_observada_29437_20261017.json" if series == 29437
            else "ina_zarate_simulada_29534_metadatos_20261017.json")
    payload = json.loads((Path(__file__).parent / "fixtures" / name).read_text())
    if series == 29437:
        fecha = (NOW - timedelta(hours=1)).isoformat()
        payload["observaciones"] = [[fecha, fecha, 1.12, "observacion-sintetica"]]
    return payload


def source_session(wfs=None, forecast=None, meteo_missing_soil=False, fail_urls=()):
    def handler(url, kwargs):
        if any(fragment in url for fragment in fail_urls):
            import requests
            raise requests.Timeout("private-example-token private-example-chat-id")
        if url == "https://api.open-meteo.com/v1/ecmwf":
            params = kwargs["params"]
            lats = [float(v) for v in params["latitude"].split(",")]
            lons = [float(v) for v in params["longitude"].split(",")]
            payload = [meteo_payload(lat, lon) for lat, lon in zip(lats, lons)]
            if meteo_missing_soil:
                for item in payload:
                    item["hourly"].pop("soil_moisture_0_to_7cm")
                    item["hourly_units"].pop("soil_moisture_0_to_7cm")
            return Response(payload)
        if url == "https://alerta.ina.gob.ar/geoserver/ows":
            return Response(copy.deepcopy(wfs if wfs is not None else wfs_payload()))
        if "calibrados/489/corridas/last" in url:
            series = 29534 if "series_id=29534" in url else 3405
            return Response(copy.deepcopy(forecast if forecast is not None else forecast_payload(series)))
        if url.startswith("https://alerta.ina.gob.ar/a5/getSeriesBySiteAndVar?"):
            series = int(parse_qs(urlsplit(url).query)["series_id"][0])
            if series not in {29437, 29534}:
                raise AssertionError("Unexpected automatic-station series: " + str(series))
            return Response(automatic_payload(series))
        raise AssertionError("Unexpected HTTP request: " + url)
    return Session(handler)


def zc_fixture(**kwargs):
    return red.consultar_zc_red(referencia=NOW, session=source_session(**kwargs))


def build_fixture(frame=None, hydro=None, zc=None, monitor=None):
    return red.construir_red_integrada(ala_frame() if frame is None else frame,
        panorama() if hydro is None else hydro, zc_fixture() if zc is None else zc,
        monitor=monitor, referencia=NOW, version_ala="V4.6.0")


def node_of(document, region, place):
    return next(item for item in document["nodos"]
                if item["red"] == region and item["localidad"] == place)


def river_of(document, place):
    return next(item for item in document["rios"]
                if item["red"] == "Zárate–Campana" and item["estacion"] == place)


def assert_json_finite(case, value):
    """Reject NaN, infinity, objects, secret values and unsupported JSON types."""
    encoded = json.dumps(value, allow_nan=False, ensure_ascii=False)
    case.assertEqual(json.loads(encoded), value)
    case.assertNotIn("private-example-token", encoded)
    case.assertNotIn("private-example-chat-id", encoded)
    return encoded


class Response:
    def __init__(self, payload=None, status=200, text=None):
        self.status_code = status
        self._payload = payload
        self.text = text if text is not None else json.dumps(payload)
        self.content = self.text.encode("utf-8")
        self.headers = {"Content-Type": "application/json"}
        self.closed = False

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError("public source unavailable", response=self)

    def json(self):
        return copy.deepcopy(self._payload)

    def iter_content(self, chunk_size):
        for start in range(0, len(self.content), chunk_size):
            yield self.content[start:start + chunk_size]

    def close(self):
        self.closed = True


class Session:
    """Deterministic HTTP fake that fails on any unexpected public request."""
    def __init__(self, handler):
        self.handler = handler
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        response = self.handler(url, kwargs)
        if not getattr(response, "url", None):
            response.url = url
        return response


class FakeStreamlit:
    """Capture consumer output while retaining normal context-manager layout."""
    def __init__(self, root=None):
        self.root = root or self
        if root is None:
            self.calls = []
            self.session_state = {}

    def __enter__(self):
        return self

    def __exit__(self, *unused):
        return False

    def columns(self, spec, **kwargs):
        count = spec if isinstance(spec, int) else len(spec)
        return [FakeStreamlit(self.root) for _ in range(count)]

    def tabs(self, labels):
        self.root.calls.append(("tabs", labels, {}))
        return [FakeStreamlit(self.root) for _ in labels]

    def expander(self, label, **kwargs):
        self.root.calls.append(("expander", label, kwargs))
        return FakeStreamlit(self.root)

    def selectbox(self, label, options, **kwargs):
        self.root.calls.append(("selectbox", (label, options), kwargs))
        return options[0]

    def __getattr__(self, name):
        if name == "column_config":
            return self
        def record(*args, **kwargs):
            self.root.calls.append((name, args, kwargs))
            return None
        return record


class SharedContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reference_document = build_fixture()

    def fixture(self):
        return copy.deepcopy(self.reference_document)

    def test_all_58_points_and_original_fixed_geography_remain_visible(self):
        document = self.fixture()
        self.assertEqual(len(document["nodos"]), 58)
        self.assertEqual(sum(n["red"] == "Litoral" for n in document["nodos"]), 48)
        self.assertEqual(sum(n["red"] == "Zárate–Campana" for n in document["nodos"]), 10)
        self.assertEqual(node_of(document, "Litoral", "Reconquista")["lat"], -29.144)
        self.assertEqual(node_of(document, "Zárate–Campana", "Zárate centro")["lon"], -59.028)
        self.assertTrue(document["snapshot_vigente"])
        assert_json_finite(self, document)

    def test_zero_rain_is_an_observation_and_absent_rain_is_unknown(self):
        document = self.fixture()
        self.assertEqual(node_of(document, "Zárate–Campana", "Zárate centro")["lluvia_prevista_72_mm"], 0)
        frame = ala_frame()
        frame.loc[frame.localidad == "Reconquista", "lluvia_futura_72"] = None
        missing = node_of(build_fixture(frame=frame), "Litoral", "Reconquista")
        self.assertIsNone(missing["lluvia_prevista_72_mm"])
        self.assertEqual(missing["lluvia_prevista_24_mm"], 0)

    def test_partial_soil_does_not_erase_verified_rain(self):
        document = build_fixture(zc=zc_fixture(meteo_missing_soil=True))
        node = node_of(document, "Zárate–Campana", "Zárate centro")
        self.assertEqual(node["lluvia_prevista_72_mm"], 0)
        self.assertEqual(node["escurrimiento_previsto_72_mm"], 0)
        self.assertIsNone(node["humedad_0_7_m3_m3"])
        self.assertEqual(node["estado_datos"], "PARCIAL")

    def test_no_input_frames_or_source_dictionaries_are_mutated(self):
        frame, hydro, local = ala_frame(), panorama(), zc_fixture()
        before_frame = frame.copy(deep=True)
        before_hydro, before_local = copy.deepcopy(hydro), copy.deepcopy(local)
        build_fixture(frame=frame, hydro=hydro, zc=local)
        pd.testing.assert_frame_equal(frame, before_frame)
        self.assertEqual(hydro, before_hydro)
        self.assertEqual(local, before_local)

    def test_stale_snapshot_turns_every_current_status_unknown(self):
        original = self.fixture()
        later = NOW + timedelta(hours=3, seconds=1)
        document = red.validar_red_integrada(original, referencia=later)
        self.assertFalse(document["snapshot_vigente"])
        self.assertEqual(document["snapshot_estado"], "VENCIDO")
        self.assertTrue(all(not n["vigente"] for n in document["nodos"]))
        self.assertTrue(all(n["nivel_vigente"] == "SIN DATOS" for n in document["nodos"]))
        self.assertFalse(river_of(document, "Zárate")["observacion_vigente"])
        self.assertEqual(document["generated_at"], original["generated_at"])
        self.assertEqual(river_of(document, "Zárate")["fecha_observada"], river_of(original, "Zárate")["fecha_observada"])

    def test_exact_three_hour_snapshot_boundary_is_valid(self):
        document = red.validar_red_integrada(self.fixture(), referencia=NOW + timedelta(hours=3))
        self.assertTrue(document["snapshot_vigente"])

    def test_six_hour_local_water_boundary_and_one_second_after(self):
        document = self.fixture()
        height = river_of(document, "Zárate")
        height["fecha_observada"] = (NOW - timedelta(hours=6)).isoformat()
        self.assertTrue(river_of(red.validar_red_integrada(document, referencia=NOW), "Zárate")["observacion_vigente"])
        height["fecha_observada"] = (NOW - timedelta(hours=6, seconds=1)).isoformat()
        checked = river_of(red.validar_red_integrada(document, referencia=NOW), "Zárate")
        self.assertFalse(checked["observacion_vigente"])
        self.assertEqual(checked["altura_m"], 1.39)
        self.assertEqual(checked["observacion_estado"], "VENCIDO")

    def test_unzoned_date_only_and_future_water_dates_never_become_current(self):
        for value in ["2026-10-14", "2026-10-14T15:00:00",
                      (NOW + timedelta(hours=1)).isoformat()]:
            with self.subTest(value=value):
                document = self.fixture()
                river_of(document, "Zárate")["fecha_observada"] = value
                river_of(document, "Zárate")["fecha_emision"] = value
                checked = river_of(red.validar_red_integrada(document, referencia=NOW), "Zárate")
                self.assertFalse(checked["observacion_vigente"])
                self.assertFalse(checked["pronostico_vigente"])
                self.assertEqual(checked["fecha_observada"], value)

    def test_fresh_generation_never_rewrites_old_source_dates(self):
        hydro = panorama()
        original = "2026-10-10T10:00:00-03:00"
        hydro["estaciones"]["corrientes"]["fecha"] = original
        hydro["fuentes"][0]["fecha_documento"] = original
        document = build_fixture(hydro=hydro)
        station = next(r for r in document["rios"] if r["estacion"] == "Corrientes")
        self.assertEqual(datetime.fromisoformat(station["fecha_observada"]), datetime.fromisoformat(original))
        self.assertFalse(station["observacion_vigente"])
        self.assertFalse(document["fuentes"][0]["vigente"])
        self.assertTrue(document["snapshot_vigente"])

    def test_fresh_integrated_evaluation_does_not_refresh_old_meteorological_calculation(self):
        frame = ala_frame()
        mask = frame.localidad == "Reconquista"
        frame.loc[mask, "actualizado"] = "14/10/2026 08:00"
        frame.loc[mask, "consulta_meteorologica"] = (NOW - timedelta(hours=8)).isoformat()
        frame.loc[mask, "fecha_evaluacion_integrada"] = NOW.isoformat()
        node = node_of(build_fixture(frame=frame), "Litoral", "Reconquista")
        self.assertFalse(node["vigente"])
        self.assertEqual(datetime.fromisoformat(node["fecha_consulta"]), NOW - timedelta(hours=8))

    def test_date_only_represa_and_source_remain_documentary(self):
        hydro = panorama()
        hydro["represas"]["yacyreta"]["fecha"] = "2026-10-14"
        hydro["fuentes"][0]["fecha_documento"] = "2026-10-14"
        document = build_fixture(hydro=hydro)
        dam = next(d for d in document["represas"] if d["nombre"] == "Yacyretá")
        self.assertFalse(dam["vigente"])
        self.assertEqual(dam["fecha_original"], "2026-10-14")
        self.assertFalse(document["fuentes"][0]["vigente"])
        self.assertEqual(document["fuentes"][0]["fecha_documento_original"], "2026-10-14")

    def test_separate_represa_values_do_not_generate_sum_arrival_or_evacuation(self):
        document = self.fixture()
        self.assertEqual([r["erogado_m3s"] for r in document["represas"]], [13000, 5000])
        self.assertFalse(document["permite_orden_evacuacion"])
        self.assertFalse(document["envia_telegram"])
        encoded = assert_json_finite(self, document)
        self.assertNotIn("18000", encoded)
        for forbidden in ["caudal_total", "fecha_llegada", "tiempo_viaje"]:
            self.assertNotIn('"' + forbidden, encoded)

    def test_monitor_private_history_is_not_copied_to_public_snapshot(self):
        monitor = {"checked_at": NOW.isoformat(), "health": "ok", "nodes_total": 48,
            "nodes_valid": 48, "nodes_red": 0, "frequency_minutes": 60,
            "token": "private-example-token", "chat_id": "private-example-chat-id",
            "history": [{"mensaje": "private-example-token"}],
            "result": {"telegram_bot_token": "private-example-token"}}
        document = build_fixture(monitor=monitor)
        encoded = assert_json_finite(self, document)
        self.assertNotIn('"history"', encoded)
        self.assertNotIn('"result"', encoded)
        self.assertEqual(document["monitor"]["nodes_total"], 48)

    def test_bad_catalog_unknown_duplicate_and_changed_geography_are_rejected(self):
        for mutation in ["unknown", "duplicate", "missing", "coordinates"]:
            with self.subTest(mutation=mutation):
                document = self.fixture()
                if mutation == "unknown":
                    document["nodos"][0]["localidad"] = "Nodo no identificado"
                elif mutation == "duplicate":
                    document["nodos"][1] = copy.deepcopy(document["nodos"][0])
                elif mutation == "missing":
                    document["nodos"].pop()
                else:
                    document["nodos"][0]["lat"] += .01
                with self.assertRaises(ValueError):
                    red.validar_red_integrada(document, referencia=NOW)

    def test_bad_snapshot_generation_timestamps_and_schema_are_rejected(self):
        for value in ["2026-10-14", "2026-10-14T15:00:00",
                      (NOW + timedelta(hours=1)).isoformat()]:
            with self.subTest(value=value):
                document = self.fixture()
                document["generated_at"] = value
                with self.assertRaises(ValueError):
                    red.validar_red_integrada(document, referencia=NOW)
        for version in [True, 2, "1", None]:
            document = self.fixture()
            document["schema_version"] = version
            with self.assertRaises(ValueError):
                red.validar_red_integrada(document, referencia=NOW)

    def test_private_fields_at_any_depth_are_rejected(self):
        for target in ["monitor", "node", "river", "forecast", "source"]:
            with self.subTest(target=target):
                document = self.fixture()
                container = {
                    "monitor": document["monitor"], "node": document["nodos"][0],
                    "river": document["rios"][0], "forecast": document["rios"][0]["pronostico"][0],
                    "source": document["fuentes"][0],
                }[target]
                container["metadata"] = {"TELEGRAM_BOT_TOKEN": "private-example-token"}
                with self.assertRaises(ValueError):
                    red.validar_red_integrada(document, referencia=NOW)

    def test_incoming_nonfinite_numbers_remain_unknown_in_finite_json(self):
        for bad in [float("nan"), float("inf"), -float("inf")]:
            document = self.fixture()
            document["nodos"][0]["lluvia_prevista_72_mm"] = bad
            checked = red.validar_red_integrada(document, referencia=NOW)
            self.assertIsNone(checked["nodos"][0]["lluvia_prevista_72_mm"])
            assert_json_finite(self, checked)

    def test_fresh_forecast_issue_without_future_points_is_not_current(self):
        document = self.fixture()
        river = river_of(document, "Zárate")
        river["pronostico"] = [{"fecha": (NOW - timedelta(hours=1)).isoformat(),
                               "central_m": 1.8, "inferior_m": 1.5, "superior_m": 2.0}]
        checked = river_of(red.validar_red_integrada(document, referencia=NOW), "Zárate")
        self.assertFalse(checked["pronostico_vigente"])

    def test_remote_rank_or_evacuation_order_cannot_enable_local_action(self):
        document = self.fixture()
        document["permite_orden_evacuacion"] = True
        document["envia_telegram"] = True
        river = river_of(document, "Zárate")
        river.update(prioridad=3, orden_evacuacion=True, rango_evacuacion=3)
        checked = red.validar_red_integrada(document, referencia=NOW)
        self.assertFalse(checked["permite_orden_evacuacion"])
        self.assertFalse(checked["envia_telegram"])
        self.assertTrue(all(key not in river_of(checked, "Zárate")
                            for key in ["prioridad", "orden_evacuacion", "rango_evacuacion"]))


class PublicSourceAcquisition(unittest.TestCase):
    def test_all_sources_success_with_no_forecast_extension_or_scale_fusion(self):
        session = source_session()
        zc = red.consultar_zc_red(referencia=NOW, session=session)
        self.assertEqual(len(zc["nodos"]), 10)
        self.assertEqual([r["serie_observada"] for r in zc["rios"]], [40, 41, 29437])
        self.assertEqual([r["serie_pronostico"] for r in zc["rios"]], [29534, 3405, None])
        self.assertTrue(all(r["escala_equivalente"] is False for r in zc["rios"]))
        self.assertTrue(all(len(r["pronostico"]) == 2 for r in zc["rios"][:2]))
        automatic = zc["rios"][2]
        self.assertEqual(automatic["altura_m"], 1.12)
        self.assertTrue(automatic["observacion_automatica"]["coincidencia_documental_pronostico"])
        self.assertEqual(automatic["pronostico"], [])
        self.assertIsNone(automatic["alerta_m"])
        self.assertIsNone(automatic["evacuacion_m"])
        self.assertEqual(len(session.calls), 7)
        automatic_sources = [f for f in zc["fuentes"] if "estación automática" in f["organismo"]]
        self.assertEqual(len(automatic_sources), 1)
        self.assertEqual(automatic_sources[0]["fecha_documento"], automatic["fecha_observada"])
        assert_json_finite(self, zc)

    def test_failed_weather_source_keeps_known_heights_and_missing_rain(self):
        zc = zc_fixture(fail_urls=("open-meteo",))
        self.assertTrue(all(n["lluvia_prevista_72_mm"] is None for n in zc["nodos"]))
        self.assertEqual(zc["rios"][0]["altura_m"], 1.39)
        self.assertTrue(zc["rios"][0]["pronostico"])
        assert_json_finite(self, zc)

    def test_every_source_failed_keeps_10_unknown_nodes_and_three_unknown_rivers(self):
        zc = zc_fixture(fail_urls=("https://",))
        document = build_fixture(zc=zc)
        self.assertEqual(len(zc["nodos"]), 10)
        self.assertEqual([r["serie_observada"] for r in zc["rios"]], [40, 41, 29437])
        self.assertTrue(all(n["lluvia_prevista_72_mm"] is None for n in zc["nodos"]))
        self.assertTrue(all(r["altura_m"] is None and not r["pronostico"] for r in zc["rios"]))
        self.assertTrue(all(n["nivel_vigente"] == "SIN DATOS" for n in document["nodos"] if n["red"] == "Zárate–Campana"))
        assert_json_finite(self, document)

    def test_unverified_units_grid_and_cell_never_become_precipitation(self):
        for mutation in ["units", "duplicate", "gap", "coordinates", "timezone"]:
            with self.subTest(mutation=mutation):
                payload = meteo_payload()
                if mutation == "units":
                    payload["hourly_units"]["precipitation"] = "inches"
                elif mutation == "duplicate":
                    payload["hourly"]["time"][1] = payload["hourly"]["time"][0]
                elif mutation == "gap":
                    payload["hourly"]["time"].pop(1)
                elif mutation == "coordinates":
                    payload["latitude"] = -26.0
                else:
                    payload["timezone"] = "UTC"
                result = red.parsear_meteorologia_zc(red.NODOS_ZC[0], payload, NOW, referencia=NOW)
                self.assertIsNone(result["lluvia_prevista_72_mm"])

    def test_short_provider_coverage_does_not_invent_fifteen_day_accumulation(self):
        payload = meteo_payload(future_hours=240)
        payload["hourly"]["precipitation"] = [1.0] * len(payload["hourly"]["time"])
        result = red.parsear_meteorologia_zc(red.NODOS_ZC[0], payload, NOW, referencia=NOW)
        self.assertEqual(result["lluvia_prevista_7d_mm"], 168)
        self.assertEqual(result["lluvia_prevista_15d_mm"], 240)
        self.assertEqual(result["cobertura_horaria"]["lluvia_prevista_15d"]["validas"], 240)
        self.assertEqual(result["cobertura_horaria"]["lluvia_prevista_15d"]["esperadas"], 240)
        self.assertEqual(datetime.fromisoformat(result["cobertura_horaria"]["lluvia_prevista_15d"]["hasta"]), NOW + timedelta(hours=240))

    def test_wrong_water_identity_or_unzoned_date_is_not_a_live_height(self):
        for mutation in ["station", "network", "date_only", "unzoned"]:
            with self.subTest(mutation=mutation):
                payload = wfs_payload()
                props = payload["features"][0]["properties"]
                if mutation == "station":
                    props["nombre"] = "Otro puerto"
                elif mutation == "network":
                    props["tabla"] = "otra_red"
                else:
                    value = "2026-10-14" if mutation == "date_only" else "2026-10-14T15:00:00"
                    props["fecha"] = value
                    props["timeseries"] = json.dumps([[value, 1.39]])
                rivers = red.parsear_alturas_zc(payload, "https://alerta.ina.gob.ar/geoserver/ows", NOW, referencia=NOW)
                self.assertIsNone(rivers[0]["altura_m"])
                self.assertEqual(rivers[1]["altura_m"], .7)

    def test_future_unzoned_emission_and_wrong_forecast_product_are_rejected(self):
        for mutation in ["future", "unzoned", "product", "run"]:
            with self.subTest(mutation=mutation):
                payload = forecast_payload()
                if mutation == "future":
                    payload["forecast_date"] = (NOW + timedelta(hours=1)).isoformat()
                elif mutation == "unzoned":
                    payload["forecast_date"] = "2026-10-14T15:00:00"
                elif mutation == "product":
                    payload["cal_id"] = 490
                else:
                    payload["series"][0]["cor_id"] = 101
                with self.assertRaises(ValueError):
                    red.parsear_pronostico_zc(payload, 29534, NOW, referencia=NOW)


class RiverTimestampProvenance(unittest.TestCase):
    def test_latest_record_keeps_date_and_distinct_forecast_source(self):
        station = panorama()["estaciones"]["corrientes"]
        station.update(precision_fecha="fecha_registro_fuente",
            fecha_registro=station["fecha"],
            fuente="https://alerta.ina.gob.ar/geoserver/ows",
            fuente_pronostico="https://alerta.ina.gob.ar/a5/serie/19",
            fuente_umbrales="https://alerta.ina.gob.ar/geoserver/ows")
        river = red._normalizar_rio_ala("corrientes", station)
        self.assertEqual(river["fecha_registro"], river["fecha_observada"])
        self.assertEqual(red._fecha(river["fecha_registro"]), red._fecha(station["fecha"]))
        self.assertFalse(river["fecha_medicion_confirmada"])
        self.assertNotEqual(river["fuente"], river["fuente_pronostico"])
        self.assertIn("hora de medición no confirmada",
            red.fecha_publica(river["fecha_observada"], river["precision_observada"]))


class ConsumerBehavior(unittest.TestCase):
    def test_feed_failure_returns_unknown_without_private_error_or_exception(self):
        session = Session(lambda url, kwargs: (_ for _ in ()).throw(
            TimeoutError("private-example-token private-example-chat-id")))
        result = red.consultar_red_integrada("public-owner/public-repo", session=session, referencia=NOW)
        self.assertFalse(result["snapshot_vigente"])
        self.assertTrue(result["error"])
        assert_json_finite(self, result)
        self.assertEqual(len(session.calls), 1)
        self.assertEqual(session.calls[0][0], "https://raw.githubusercontent.com/public-owner/public-repo/ala-monitor-state/red_integrada.json")

    def test_bad_repository_never_causes_http_request(self):
        session = Session(lambda *args: self.fail("Invalid repository contacted HTTP"))
        for repository in ["owner/repo/branch", "https://github.com/owner/repo", "owner/repo?token=private-example-token"]:
            result = red.consultar_red_integrada(repository, session=session, referencia=NOW)
            self.assertFalse(result["snapshot_vigente"])
            self.assertTrue(result["error"])
        self.assertEqual(session.calls, [])

    def test_public_feed_rejects_redirect_to_an_unexpected_origin(self):
        payload = build_fixture()
        response = Response(payload)
        response.url = "https://unverified-example.test/red_integrada.json"
        result = red.consultar_red_integrada("owner/repo", session=Session(lambda *args: response), referencia=NOW)
        self.assertFalse(result["snapshot_vigente"])
        self.assertTrue(result["error"])

    def test_valid_feed_keeps_original_timestamps_and_unknowns(self):
        payload = build_fixture(zc=zc_fixture(fail_urls=("open-meteo",)))
        result = red.consultar_red_integrada("owner/repo", session=Session(lambda *args: Response(payload)), referencia=NOW)
        self.assertEqual(result["generated_at"], payload["generated_at"])
        self.assertEqual(result["error"], "")
        self.assertIsNone(node_of(result, "Zárate–Campana", "Zárate centro")["lluvia_prevista_72_mm"])
        assert_json_finite(self, result)

    def test_ui_works_with_58_rows_and_emits_no_evacuation_or_telegram_control(self):
        st = FakeStreamlit()
        payload = build_fixture()
        with patch.object(red, "_ahora", return_value=NOW):
            result = red.mostrar_red_integrada(st, "owner/repo", "ALA", datos=payload)
        self.assertTrue(result["snapshot_vigente"])
        tables = [args[0] for name, args, kwargs in st.calls if name == "dataframe"]
        self.assertEqual(len(tables[0]), 58)
        urls = [args[1] for name, args, kwargs in st.calls if name == "link_button"]
        self.assertIn("https://alerta-litoral-agro.streamlit.app/", urls)
        self.assertIn("https://alerta-zarate-campana.streamlit.app/", urls)
        controls = [args[0] for name, args, kwargs in st.calls if name in {"button", "link_button"}]
        self.assertFalse(any("evacuar" in str(label).lower() or "telegram" in str(label).lower() for label in controls))

    def test_ui_feed_down_shows_unknown_and_navigation_to_both_apps(self):
        st = FakeStreamlit()
        unavailable = {"error": "No se obtuvo el informe común", "snapshot_vigente": False,
                       "actions_url": "https://github.com/owner/repo/actions"}
        result = red.mostrar_red_integrada(st, "owner/repo", "ZC", datos=unavailable)
        self.assertFalse(result["snapshot_vigente"])
        self.assertTrue(any(name == "warning" for name, args, kwargs in st.calls))
        urls = [args[1] for name, args, kwargs in st.calls if name == "link_button"]
        self.assertIn("https://github.com/owner/repo/actions", urls)
        self.assertIn("https://alerta-litoral-agro.streamlit.app/", urls)
        self.assertIn("https://alerta-zarate-campana.streamlit.app/", urls)

    def test_cached_ui_rechecks_snapshot_age_without_refreshing_generation(self):
        st = FakeStreamlit()
        payload = build_fixture()
        with patch.object(red, "_ahora", return_value=NOW + timedelta(hours=4)):
            result = red.mostrar_red_integrada(st, "owner/repo", "ALA", datos=payload)
        self.assertFalse(result["snapshot_vigente"])
        self.assertEqual(result["generated_at"], payload["generated_at"])
        self.assertTrue(any(name == "warning" for name, args, kwargs in st.calls))


if __name__ == "__main__":
    unittest.main(verbosity=2)
