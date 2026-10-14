# Alerta Litoral Agro · V4.6.0

Herramienta experimental de apoyo a decisiones para el Litoral argentino. Integra meteorología local, observaciones y pronósticos fluviales, represas y avisos oficiales para preparar acciones ante anegamientos y crecidas.

[Abrir Alerta Litoral Agro](https://alerta-litoral-agro.streamlit.app/). Monitorea 48 puntos de Santa Fe, Corrientes, Entre Ríos, Chaco, Formosa y Misiones; cada fuente indica su cobertura.

| Fuente | Información utilizada |
| --- | --- |
| ECMWF / Open-Meteo | Lluvia, humedad y escorrentía modeladas; índice meteorológico |
| INA | Estaciones, alturas, umbrales y previsiones oficiales disponibles |
| EBY · Yacyretá | Estado del Paraná y boletines de aportes, erogación y previsión |
| CTM · Salto Grande | Reportes observados de niveles y comunicados oficiales |
| SMN · CAP | Avisos cuyos polígonos incluyen cada punto monitoreado |

El panel y GitHub Actions comparten la integración automática. Observación y previsión conservan fechas, estación, unidad, fuente y plazo. El color integrado puede elevarse por riesgo fluvial o avisos SMN aunque el índice meteorológico no cambie. La cobertura parcial se identifica; los faltantes no acreditan un verde seguro.

Las conexiones de cuenca permiten vigilar aportes aguas arriba. La anticipación utiliza puntos y ventanas publicados; no calcula horas de llegada a partir de un caudal ni dibuja profundidad por barrio. **Preparar y coordinar evacuación** exige confirmar fuente, umbral de estación, plazo, autoridad, destino y acceso local; no es una orden oficial.

## Actualización

Subí juntos a la rama utilizada por Streamlit:

- `app.py`, `hidrologia.py`, `ctm.py` y `monitor.py` en la raíz.
- `requirements.txt` y `requirements_monitor.txt` en la raíz.
- `.github/workflows/ala-monitor.yml` en esa ubicación exacta.

Conservá los Secrets de Streamlit y Actions y la rama durable `ala-monitor-state`, incluidos `telegram_alerts.sqlite3` y `monitor_status.json`. El workflow programa controles al minuto 7 de cada hora (`7 * * * *`); la fecha del último control muestra si efectivamente se ejecutaron.

La reserva del evento se guarda antes del envío. Solo una respuesta válida de Telegram con `message_id` confirma el envío; una respuesta incierta no acredita entrega. Cero avisos no equivale a ausencia de riesgo.

Ver [GUIA_MONITOR.md](GUIA_MONITOR.md) para actualización, Secrets, prueba sin envíos y diagnóstico.

## Pruebas

```bash
python -m pip install -r requirements_monitor.txt
python -m unittest discover -s tests -v
```

Las pruebas usan datos controlados y sustituyen envíos. Para consultar fuentes actuales sin modificar el registro ni enviar avisos:

```bash
python monitor.py --dry-run
```

Para abrir el panel local, instalá `requirements.txt` y ejecutá `streamlit run app.py`.
