# Revisión de trazabilidad · 15 de octubre de 2026

## Evidencia consultada y corte

- [Ejecución automática 38681841351](https://github.com/FedericoIeraci-cell/Alerta-Litoral/actions/runs/38681841351): `success`, código `11ac9277130d2246c03251793e7f8e51d2f0eb06`.
- [Snapshot común inmutable de las 01:31 ART](https://github.com/FedericoIeraci-cell/Alerta-Litoral/blob/f7c7d5909e0e2748b169226cb02c09acdec43c50/red_integrada.json) y [estado del monitor del mismo corte](https://github.com/FedericoIeraci-cell/Alerta-Litoral/blob/f7c7d5909e0e2748b169226cb02c09acdec43c50/monitor_status.json): 48 nodos evaluados, 8 rojos, 3 sin datos suficientes. Es un análisis de ese corte, no un boletín de riesgo actual.
- Registro SQLite histórico del mismo commit, leído sin modificarlo. El mensaje persistido del evento `0f6bdf5dd12e` permite identificar el pronóstico que acompañó la transición de Reconquista.
- [Reporte INA](https://alerta.ina.gob.ar/a5/diario/reporte_diario), edición fechada 14/10/2026, recuperado el 15/10 y preservado en `tests/fixtures/ina_diario_20261014.html`. Su fecha no incluye hora de emisión. Las URLs de los documentos diarios pueden cambiar de contenido; se conservan fixtures fechados para reproducir la revisión.

El informe más reciente consultado durante la investigación era el de las 07:33 ART, con 9 rojos. Se usó el snapshot histórico para no confundir ambas evaluaciones.

## Qué sostenía cada rojo a las 01:31

Todas las alturas de esta tabla son metros de la escala de la estación. No son cotas del terreno ni profundidad en viviendas. Los índices no son probabilidades de inundación.

| Localidad | Índice meteorológico | Altura observada | Umbrales alerta / evacuación | Causa del rojo identificada |
| --- | ---: | ---: | ---: | --- |
| Reconquista | 23,7 · verde | 4,35 | 5,10 / 5,30 | Previsión textual INA de posible excedencia de evacuación a partir del 18/10, dentro de la ventana operativa de 72 h. No había superación observada. |
| Corrientes | 24,0 · verde | 5,50 | 6,50 / 7,00 | Previsión textual INA de posible excedencia de evacuación a partir del 17/10. Observación debajo de alerta. |
| Goya | 20,6 · verde | 4,78 | 5,20 / 5,70 | Previsión textual INA de posible excedencia de evacuación a partir del 17/10. Observación debajo de alerta. |
| Paso de los Libres | 27,0 · verde | 9,37 | 7,50 / 8,50 | Lectura CTM del 14/10 a las 07:00 ART sobre el umbral de evacuación de la estación vinculado desde INA. |
| Resistencia | 24,8 · verde | 5,40 en Barranqueras | 6,00 / 6,50 | Referencia explícita de tramo Barranqueras; previsión textual INA de posible excedencia de evacuación desde el 17/10. No representa la altura de cada barrio de Resistencia. |
| Puerto Iguazú | 56,1 · rojo | 32,20 | 25,00 / 28,00 | Observación sobre evacuación y, simultáneamente, índice meteorológico ≥55. |
| Barranqueras | 24,8 · verde | 5,40 | 6,00 / 6,50 | Previsión textual INA de posible excedencia de evacuación desde el 17/10; lectura por debajo de alerta. |
| Andresito | 70,6 · rojo | 7,30 | 10,50 / 13,50 | Índice meteorológico ≥55. La serie de altura usada por el monitor estaba debajo de alerta; no se le atribuye un rojo de altura ni se confunde con el caudal del reporte diario. |

Los valores y fechas observadas proceden de las series/URLs conservadas en el snapshot. Para Paso de los Libres, la lectura corresponde al documento CTM identificado allí; el snapshot antiguo no retenía por separado la URL de los umbrales, una limitación corregida en esta implementación. Los valores de los umbrales coinciden con el reporte INA verificado. No se atribuye a SMN un rojo cuando el snapshot antiguo no conserva un aviso que lo demuestre. La reconstrucción offline identifica causas suficientes conforme a las reglas y a la evidencia disponible; no inventa avisos adicionales.

La erogación de Yacyretá aporta contexto aguas arriba. No es la causa local determinante de los tres primeros rojos, ni se deduce de ella una hora de llegada del agua.

## Transición de Reconquista

El registro guarda una transición AMARILLO → ROJO a las **00:31 ART del 15/10**, anterior al control de las 01:31. El índice bajó **24,1 → 24,0**. El mensaje persistido identifica la previsión emitida el 14/10 y el plazo del 18/10. El algoritmo representa internamente una fecha sin hora mediante medianoche ART: al comenzar el día 15, la fecha del 18 entra en sus próximas 72 horas.

La reproducción con el mismo pronóstico a las 23:31 del 14/10 y a las 00:31 del 15/10 confirma el paso amarillo → rojo sin incremento del índice ni de la altura. Esta explicación procede del mensaje conservado y de la regla temporal; no demuestra que hubiera una nueva medición, un nuevo boletín o una subida observada entre controles. La hora del cruce físico sigue sin estar publicada.

El nuevo motivo indica el plazo, la estación y la entrada en la ventana de preparación. No vuelve a enviar ni reescribe ese evento histórico.

## Cambios y verificación

- Evidencias estructuradas generadas en el punto en que cada regla evalúa observaciones, pronósticos, SMN y meteorología.
- Una misma explicación para la ficha local, el hover del mapa, los nuevos mensajes, el motivo persistido del evento y el informe compartido.
- Fechas ART legibles, precisión del documento, umbrales por estación, fuentes diferenciadas, sin inventar una altura numérica para un pronóstico textual.
- Tarjeta de mapa breve y ficha persistente con selector, fuentes y detalle desplegable para celular/teclado.
- Fuentes no verificables o fallidas excluidas como desencadenantes; criterio previo de 72 h y umbrales experimentales conservados. No se recalibra el modelo ni se cambia la deduplicación.
- Pruebas con solicitudes de red bloqueadas y envío sustituido. La prueba de transición verifica también el motivo guardado en SQLite y la ausencia de duplicados. El fixture histórico sólo contiene datos públicos necesarios, sin destinatarios ni credenciales.

Ejecución: `python -m pip install -r requirements_test.txt` y `python -m unittest discover -s tests -v`. La validación de GitHub consulta adicionalmente fuentes actuales con `monitor.py --dry-run`, sin Telegram ni escritura del registro remoto.
