"""Regresiones de adquisición por punto, respaldo y lectores. Sin red ni Telegram."""
import copy
from datetime import datetime, timedelta, timezone
import json
import unittest
from unittest.mock import patch

import requests
import red_integrada as red

NOW = datetime(2026, 10, 20, 15, 0, tzinfo=timezone.utc)


def grid(nodo, ref=NOW, hours=360):
    times = [(ref + timedelta(hours=i)).astimezone(red.TZ).replace(tzinfo=None).isoformat()
             for i in range(-192, hours + 1)]
    return {"latitude": nodo["lat"], "longitude": nodo["lon"],
            "timezone": "America/Argentina/Buenos_Aires", "utc_offset_seconds": -10800,
            "hourly_units": {"precipitation": "mm", "runoff": "mm", "soil_moisture_0_to_7cm": "m³/m³"},
            "hourly": {"time": times, "precipitation": [1.] * len(times),
                       "runoff": [0.] * len(times), "soil_moisture_0_to_7cm": [.2] * len(times)}}


def query(params):
    return next(n for n in red.NODOS_ZC if str(n['lat']) == params['latitude'] and str(n['lon']) == params['longitude'])


def success(session, url, params, **kwargs):
    return grid(query(params)), url


def previous(ref=NOW):
    nodes = []
    for n in red.NODOS_ZC:
        p = grid(n, ref)
        nodes.append(red._guardar_grilla_zc(red.parsear_meteorologia_zc(n, p, ref, ref), p))
    return red.construir_red_integrada([], {}, zc={"nodos": nodes}, referencia=ref)


class Recovery(unittest.TestCase):
    def setUp(self):
        self.sleep = patch.object(red.time, 'sleep').start()
        self.addCleanup(patch.stopall)

    def test_one_timeout_does_not_discard_neighbours_and_attempts_are_bounded(self):
        def get(session, url, params, **kwargs):
            self.assertEqual(kwargs['timeout'], (5, 20))
            n = query(params)
            if n == red.NODOS_ZC[0]:
                raise requests.ReadTimeout('sensitive response never public')
            return grid(n), url
        with patch.object(red, '_request_json', side_effect=get) as call:
            nodes = red.consultar_meteorologia_zc(NOW)
        self.assertEqual(call.call_count, 12)
        self.assertIsNone(nodes[0]['lluvia_prevista_72_mm'])
        self.assertTrue(all(n['lluvia_prevista_72_mm'] == 72 for n in nodes[1:]))
        self.assertNotIn('sensitive', json.dumps(nodes))
        self.assertEqual(len(nodes[0]['intentos_meteorologia']), 3)

    def test_second_attempt_can_recover_a_point(self):
        attempts = {}
        def get(session, url, params, **kwargs):
            n = query(params); name = n['localidad']
            attempts[name] = attempts.get(name, 0) + 1
            if attempts[name] == 1:
                raise requests.ReadTimeout()
            return grid(n), url
        with patch.object(red, '_request_json', side_effect=get):
            nodes = red.consultar_meteorologia_zc(NOW)
        self.assertTrue(all(len(n['intentos_meteorologia']) == 2 for n in nodes))
        self.assertTrue(all(n['lluvia_prevista_72_mm'] == 72 for n in nodes))

    def test_reduced_query_preserves_missing_long_and_soil_variables(self):
        def get(session, url, params, **kwargs):
            if params['hourly'] != 'precipitation':
                raise requests.ReadTimeout()
            self.assertEqual((params['past_days'], params['forecast_days']), (0, 4))
            p = grid(query(params), hours=80)
            p['hourly'].pop('runoff'); p['hourly'].pop('soil_moisture_0_to_7cm')
            return p, url
        with patch.object(red, '_request_json', side_effect=get):
            nodes = red.consultar_meteorologia_zc(NOW)
        for n in nodes:
            self.assertEqual(n['lluvia_prevista_72_mm'], 72)
            self.assertIsNone(n['lluvia_prevista_7d_mm'])
            self.assertIsNone(n['escurrimiento_previsto_72_mm'])
            self.assertIsNone(n['humedad_0_7_m3_m3'])
            self.assertEqual(n['recuperacion'], 'CONSULTA_REDUCIDA')

    def test_http400_not_retried(self):
        response = requests.Response(); response.status_code = 400
        with patch.object(red, '_request_json', side_effect=requests.HTTPError(response=response)) as get:
            nodes = red.consultar_meteorologia_zc(NOW)
        self.assertEqual(get.call_count, 10)
        self.assertTrue(all(n['lluvia_prevista_72_mm'] is None for n in nodes))
        self.assertIn('HTTP 400', nodes[0]['error'])

    def test_retryable_http429_and_503(self):
        for status in (429, 503):
            response = requests.Response(); response.status_code = status
            self.assertTrue(red._meteo_transitorio(requests.HTTPError(response=response)))

    def test_previous_grid_rolls_windows_without_renewing_original_date(self):
        prev = previous(); original = copy.deepcopy(prev)
        # Deliberately vary the new window's final hour to detect copied aggregates.
        for n in prev['nodos']:
            if n['red'] == 'Zárate–Campana':
                n['grilla_verificada']['hourly']['precipitation'][192 + 73] = 9
        later = NOW + timedelta(hours=1)
        with patch.object(red, '_request_json', side_effect=requests.ReadTimeout()):
            nodes = red.consultar_meteorologia_zc(later, anterior=prev)
        self.assertTrue(all(n['lluvia_prevista_72_mm'] == 80 for n in nodes))
        self.assertTrue(all(n['fecha_calculo'] == NOW.isoformat() for n in nodes))
        self.assertTrue(all(n['referencia_pronostico'] == later.astimezone(red.TZ).isoformat() for n in nodes))
        snapshot = red.construir_red_integrada([], {}, zc={'nodos': nodes}, referencia=later)
        with patch.object(red, '_request_json', side_effect=requests.ReadTimeout()):
            expired = red.consultar_meteorologia_zc(NOW + timedelta(hours=4), anterior=snapshot)
        result = red.construir_red_integrada([], {}, zc={'nodos': expired}, referencia=NOW + timedelta(hours=4))
        for n in result['nodos'][48:]:
            self.assertFalse(n['vigente'])
            self.assertEqual(n['estado_tiempo'], 'VENCIDO')
            self.assertEqual(n['recuperacion'], 'GRILLA_PREVIA_VENCIDA')
            self.assertEqual(n['fecha_consulta'], NOW.isoformat())
        self.assertEqual(original['nodos'][48]['lluvia_prevista_72_mm'], 72)

    def test_invalid_future_mismatched_and_absent_backup_cannot_supply_zero(self):
        for mode in ('future', 'coordinates', 'units', 'missing', 'no_cache'):
            prev = previous()
            for n in prev['nodos'][48:]:
                if mode == 'future': n['fecha_consulta'] = (NOW + timedelta(hours=1)).isoformat()
                if mode == 'coordinates': n['grilla_verificada']['latitude'] = 0
                if mode == 'units': n['grilla_verificada']['hourly_units']['precipitation'] = 'inch'
                if mode == 'missing': n['grilla_verificada']['hourly']['precipitation'][200] = None
                if mode == 'no_cache': n.pop('grilla_verificada')
            with patch.object(red, '_request_json', side_effect=requests.ReadTimeout()):
                nodes = red.consultar_meteorologia_zc(NOW, anterior=prev)
            self.assertTrue(all(n['lluvia_prevista_72_mm'] is None for n in nodes), mode)

    def test_recovery_is_revalidated_by_public_reader_and_ui(self):
        prev = previous()
        with patch.object(red, '_request_json', side_effect=requests.ReadTimeout()):
            nodes = red.consultar_meteorologia_zc(NOW + timedelta(hours=1), anterior=prev)
        doc = red.construir_red_integrada([], {}, zc={'nodos': nodes}, referencia=NOW + timedelta(hours=1))
        loaded = red.validar_red_integrada(json.loads(json.dumps(doc)), NOW + timedelta(hours=1))
        self.assertTrue(all(n['vigente'] for n in loaded['nodos'][48:]))
        self.assertLess(len(json.dumps(doc).encode()), red.MAX_BYTES)
        expired = red.validar_red_integrada(doc, NOW + timedelta(hours=4))
        self.assertFalse(any(n['vigente'] for n in expired['nodos']))
        self.assertIn('antiguo', red.estado_publico('VENCIDO', False, True, 'partial'))


if __name__ == '__main__':
    unittest.main()
