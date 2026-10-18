# Observación automática de Zárate: identidad, fechas y límites

**Revisión: 18/10/2026 UTC.** Este documento conserva los cortes consultados; los valores de las pruebas no se presentan como datos actuales al ejecutarlas.

## Hallazgo verificable

El catálogo oficial INA identifica una serie de **medición directa** adicional a la escala del puerto: **29437, Paraná Las Palmas - Zárate**, estación **5907**, red **RHN - SAT** (`sat2`), identificador externo **313**. En la consulta del **17/10/2026 a las 23:57:19 ART** devolvió 49 puntos desde el período solicitado. El último fue **1,12 m a las 21:00 ART**, equivalente a **18/10/2026 00:00 UTC**, con una antigüedad de **2 h 57 min**. La respuesta publica la zona horaria explícitamente en sus observaciones.

Fuentes: [catálogo de mediciones de la estación 5907](https://alerta.ina.gob.ar/a5/obs/puntual/series?estacion_id=5907&var_id=2&proc_id=1&limit=1000) y [respuesta observada consultada](https://alerta.ina.gob.ar/a5/getSeriesBySiteAndVar?series_id=29437&tipo=puntual&timestart=2026-10-16&timeend=2026-10-18T02%3A57%3A18.583440%2B00%3A00&includeProno=false).

Una comprobación posterior del lector, a las **00:08:50 ART del 18/10**, recibió una lectura añadida de **1,12 m a las 22:00 ART del 17/10**, con aproximadamente **2,15 h** de antigüedad. Se conserva el corte anterior en los fixtures: el avance observado acredita una actualización de la fuente durante la revisión, sin garantizar disponibilidad futura ni una frecuencia fija de entrega.

## Medición y pronóstico de la misma referencia

Los metadatos de las series **29437** y **29534** coinciden en la identidad física y referencia vertical siguientes:

| Campo | Observada 29437 | Simulada 29534 |
|---|---|---|
| Estación INA | 5907 | 5907 |
| Nombre | Paraná Las Palmas - Zárate | Paraná Las Palmas - Zárate |
| Red / identificador externo | `sat2` / `313` | `sat2` / `313` |
| Coordenadas de catálogo, longitud / latitud | −59,008888889 / −34,101944444 | −59,008888889 / −34,101944444 |
| Variable / unidad | 2, altura hidrométrica / 11, metros | 2, altura hidrométrica / 11, metros |
| Cero IGN publicado | 0,243 m | 0,243 m |
| Procedimiento | 1, medición directa | 4, simulado mediante un modelo |

Fuente del vínculo: [metadatos oficiales de la serie 29534](https://alerta.ina.gob.ar/a5/getSeriesBySiteAndVar?series_id=29534&tipo=puntual&timestart=2026-10-16&timeend=2026-10-18&includeProno=false). Esa serie no devolvió observaciones: sus salidas simuladas siguen siendo pronósticos. La identidad documental permite mostrar medición y evolución prevista de **esta referencia**, identificadas por separado. No acredita precisión predictiva, una profundidad en cada calle ni una orden de evacuación.

## Puerto PNA/INA y Campana se conservan separados

La escala del puerto de Zárate **INA 40 / PNA 340** corresponde a otra estación de catálogo. INA conserva allí **1,55 m del 17/10 a las 00:00 ART**. La [tabla oficial de PNA](https://contenidosweb.prefecturanaval.gob.ar/alturas/) consultada a las **23:50 ART** publicaba una lectura posterior de **1,27 m, 17/OCT/26 - 1200**. Esta última también excedía seis horas de antigüedad; la tabla no indica expresamente la zona horaria y se conserva su fecha textual como publicada.

Hay una discrepancia material en los umbrales de **ese puerto**: INA 40 publica **2,00 / 2,20 m** para alerta / evacuación, mientras PNA 340 publica **2,20 / 2,40 m**. La revisión no encontró una disposición fechada que resolviera la diferencia. El nuevo lector **no importa ninguno de esos umbrales**, ni los asigna a la estación automática. La coincidencia numérica del cero IGN de dos estaciones no permite trasladar sus umbrales.

Para Campana, el [catálogo INA de mediciones directas](https://alerta.ina.gob.ar/a5/obs/puntual/series?search=Campana&var_id=2&proc_id=1&limit=1000) devolvió la serie **41**, con último dato del **17/10 a las 00:00 ART**, y **31866, INTA Delta**, terminada en mayo de 2026 y con otro cero de escala. PNA 350 publicaba **0,90 m, 17/OCT/26 - 1200**. Ninguna de estas lecturas resolvía la falta de una observación local de hasta seis horas en el corte investigado. La [red horaria del SHN](https://www.hidro.gov.ar/Oceanografia/AlturasHorarias.asp) aporta datos de otros mareógrafos, referidos a su propio plano de reducción; no se usan para completar Campana o Zárate.

Presentación correspondiente: **última altura publicada + fecha de esa lectura + fuente + antigüedad**, seguida de «No permite confirmar la altura actual» cuando está vencida o su fecha no está verificada. La observación automática de Zárate no se asigna a Campana ni genera un semáforo nuevo.

## Contrato de integración

`hidrometria_local.py` es idéntico en las dos aplicaciones. No depende de Streamlit, no mantiene caché y no consulta servicios al importarse.

- `consultar_altura_zarate(referencia=None, session=None)` consulta la observada 29437 y los metadatos de la simulada 29534. Usa exclusivamente el endpoint INA indicado; rechaza redirecciones, respuesta excesiva e identidades incompatibles. Un fallo de metadatos del pronóstico conserva la medición verificada y deja el vínculo sin confirmar.
- `parsear_altura_zarate(...)` permite verificar respuestas congeladas sin red.
- `revalidar_altura_zarate(datos, referencia=None)` recalcula la antigüedad al mostrar un informe guardado. La vigencia exige fecha con zona, no futura y edad de **hasta 6 h**. Una consulta reciente no modifica la edad de la lectura.

El diccionario público contiene `nombre`, `serie`, `estacion_id`, `red`, `id_externo`, `altura_m`, `fecha_observada`, `edad_horas`, `vigente`, `unidad`, `cero_ign_m`, `fuente`, `consulta`, `error`, `coincidencia_documental_pronostico`, `serie_pronostico`, `fuente_metadatos_pronostico`, `error_vinculo_pronostico`, `observaciones` y `registros_descartados`. Cada observación conserva sólo `fecha`, `altura_m`, `fecha_original` e `id_observacion`.

Se eliminan campos ajenos, incluidos semáforos y umbrales. Los puntos no finitos, futuros o sin zona se descartan; los duplicados incompatibles y la contradicción entre última lectura y serie invalidan el resultado. Una lectura vencida conserva su valor y fecha con `vigente=False`.

## Evidencia y pruebas

Se incluyen los [49 registros originales y sus metadatos](../tests/fixtures/ina_zarate_observada_29437_20261017.json), los [metadatos originales de la serie simulada](../tests/fixtures/ina_zarate_simulada_29534_metadatos_20261017.json) y un [manifiesto con URL, fecha de consulta y SHA-256](../tests/fixtures/hidrometria_zarate_manifest_20261018.json).

Las **14 pruebas offline** de `tests/test_hidrometria_local.py` cubren identidad de estación y unidad, medición frente a simulación, caducidad exacta, zona horaria, futuro, no finitos, duplicados, coherencia entre lectura y serie, filtrado del contrato, origen de las respuestas y fallo independiente del vínculo de pronóstico. Estas pruebas verifican el lector; no certifican el sensor ni calibran el pronóstico.
