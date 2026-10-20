"""Comparación acotada, sin escrituras, credenciales ni Telegram.

Cada caso se realiza una vez: es evidencia puntual, no prueba de causalidad.
"""
import json
from pathlib import Path
import sys
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import red_integrada as red


def main():
    for nombre, nodos, reducido in [('original_8', red.NODOS_ZC[:8], False),
                                    ('individual_completo', red.NODOS_ZC[:1], False),
                                    ('individual_lluvia_4d', red.NODOS_ZC[:1], True)]:
        params = {'latitude': ','.join(str(n['lat']) for n in nodos),
                  'longitude': ','.join(str(n['lon']) for n in nodos),
                  'hourly': 'precipitation' if reducido else 'precipitation,runoff,soil_moisture_0_to_7cm',
                  'past_days': 0 if reducido else 8, 'forecast_days': 4 if reducido else 15,
                  'timezone': 'America/Argentina/Buenos_Aires', 'precipitation_unit': 'mm', 'cell_selection': 'land'}
        inicio = time.monotonic()
        record = {'caso': nombre, 'consulta': red._ahora().isoformat(), 'parametros': params}
        try:
            data, _ = red._request_json(None, red.API_METEO, params, timeout=(5, 45 if len(nodos) == 8 else 20))
            rows = data if isinstance(data, list) else [data]
            now = red._ahora()
            record.update(resultado='respuesta', coordenadas=len(rows), bytes=len(json.dumps(data).encode()),
                          lluvia_72_verificada=[red.parsear_meteorologia_zc(n, p, now, now)['lluvia_prevista_72_mm']
                                               for n, p in zip(nodos, rows)])
        except Exception as exc:
            record.update(resultado=type(exc).__name__, http=getattr(getattr(exc, 'response', None), 'status_code', None))
        record['segundos'] = round(time.monotonic() - inicio, 2)
        print(json.dumps(record, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
