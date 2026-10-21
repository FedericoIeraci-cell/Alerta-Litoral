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
    # La prueba reducida no acredita que el archivo completo pueda renovarse.
    # Este mismo paso de CI verifica el productor real, sin publicar ni enviar.
    from meteorologia_compartida import crear_captura_zc, CapturaMeteorologicaError
    inicio = time.monotonic()
    try:
        captura = crear_captura_zc()
    except CapturaMeteorologicaError as exc:
        print(json.dumps({'caso': 'captura_completa_zc', 'resultado': 'error',
                          'error': str(exc)}, ensure_ascii=False), flush=True)
        raise SystemExit(1) from None
    print(json.dumps({'caso': 'captura_completa_zc', 'resultado': 'validada',
                      'consultado': captura['consultado'], 'nodos': len(captura['datos']),
                      'segundos': round(time.monotonic() - inicio, 2)}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    if len(sys.argv) == 2:
        informe = red.validar_red_integrada(json.loads(Path(sys.argv[1]).read_text()))
        nodos = [n for n in informe['nodos'] if n['red'] == 'Zárate–Campana']
        print(json.dumps({'generated_at': informe['generated_at'], 'referencias': len(nodos),
                          'lluvia_72_vigentes': sum(n['vigente'] and n['lluvia_prevista_72_mm'] is not None for n in nodos)}))
        for n in nodos:
            print(json.dumps({k: n.get(k) for k in ('localidad', 'fecha_consulta', 'fecha_calculo',
                'referencia_pronostico', 'vigente', 'estado_datos', 'recuperacion',
                'lluvia_prevista_24_mm', 'lluvia_prevista_72_mm', 'error', 'error_actualizacion',
                'intentos_meteorologia')}, ensure_ascii=False))
    else:
        main()
