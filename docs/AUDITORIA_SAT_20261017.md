# Auditoría conjunta de datos, conexión y comunicación

**Aplicaciones:** Alerta Litoral Agro y Alerta Zárate–Campana.  
**Corte de referencia:** informe común del 17/10/2026 a las 19:25:44 ART.  
**Revisión pública de pantallas:** aproximadamente 20:02–20:10 ART del mismo día.  
**Actualización asociada:** ALA V4.8.0 y ZC 1.7.0.

Las cifras de este documento describen ese corte. No constituyen un parte de emergencia permanente. Para una decisión actual se debe volver a consultar la portada, la fecha de cada fuente y los avisos oficiales.

## Resultado principal

Las dos páginas consultaban el mismo archivo público y el monitor de ALA había recuperado sus ejecuciones horarias después de V4.7.1. Sin embargo, todavía existían diferencias de lectura y presentación: el consumidor de Zárate–Campana conservaba colores integrados vencidos; una tabla de fuentes aplicaba 48 horas a observaciones locales que el panel descartaba a las seis; el boletín EBY n.º 07 perdía información distribuida en párrafos y confundía el orden de sus extremos. Las portadas también exigían recorrer demasiados detalles antes de llegar a la localidad, el motivo del aviso y las acciones.

La actualización corrige esas diferencias y pone la evidencia y sus límites en la primera ficha. La condición profesional del proyecto depende también de validar su capacidad predictiva y sus procedimientos locales. Una interfaz mejorada y pruebas de software correctas no acreditan por sí solas un sistema de alerta temprana operativo completo.

## 1. Cobertura del informe auditado

| Conjunto | Resultado al corte | Interpretación |
| --- | ---: | --- |
| Puntos de consulta | 58/58 | 48 del Litoral y 10 referencias meteorológicas de Zárate–Campana; catálogo sin duplicados |
| Meteorología reciente | 58/58 | Variables del modelo dentro de la ventana de uso; no son 58 estaciones instaladas |
| Clasificación integrada del Litoral | 43/48 | 8 rojos, 13 amarillos y 22 verdes; 5 sin clasificación suficiente |
| Estaciones fluviales representadas | 24 | 22 del Litoral y 2 de Zárate–Campana |
| Observaciones fluviales dentro del plazo de uso | 22/24 | Las dos observaciones de Zárate–Campana superaban seis horas |
| Estaciones con pronóstico numérico vigente | 16/24 | No equivale al número de localidades cubiertas por previsiones textuales o numéricas |
| Reportes de represa | 4 registros de 3 represas | Había un duplicado exacto de Yacyretá |
| Filas de procedencia | 31 | La consulta SMN todavía no tenía una fila propia en el informe compartido |

ALA informaba además cobertura meteorológica de 48/48 localidades, observación fluvial de 22/29 localidades vinculadas a ríos, previsión de 16/29, represas de 18/20 y consulta SMN de 48/48. Estos denominadores responden a conjuntos distintos: no deben combinarse como si todos representaran estaciones.

Las localidades sin clasificación integrada suficiente eran **El Colorado, Posadas, El Soberbio, Gualeguay y Chajarí**. Tenían meteorología disponible; el faltante correspondía a otras evidencias necesarias para su evaluación integrada.

Los ocho rojos eran **Reconquista, Corrientes, Goya, Paso de los Libres, Resistencia, Puerto Iguazú, Ituzaingó y Barranqueras**. Contaban con causas estructuradas en el informe. Rojo de ALA no significa que el SMN haya emitido una alerta roja para la misma localidad, ni demuestra una inundación observada en todos sus barrios.

## 2. Correcciones de datos y conexión

| Hallazgo reproducido | Corrección | Control |
| --- | --- | --- |
| El lector de Red integrada en ZC conservaba los ocho rojos del Litoral y evidencias marcadas vigentes al releer un informe cuatro horas después | Un mismo lector vuelve a comprobar generación, evaluación y vencimiento de cada evidencia | Informe antiguo sin colores actuales ni causas vigentes |
| INA Zárate–Campana: seis horas en el río, 48 en la tabla de fuentes | Seis horas para observaciones y emisiones locales en ambas superficies | El mismo dato antiguo se identifica de igual modo |
| La generación del JSON podía confundirse con actualidad de todas sus fuentes | Fechas de generación, cálculo, consulta, observación y emisión separadas | Actualizar no cambia las fechas originales |
| SMN no tenía una procedencia explícita en el informe común | Se agrega el canal consultado por ALA, su fecha de listado, consulta y estado parcial/completo | Una consulta incompleta no confirma ausencia de avisos |
| Boletín EBY n.º 07 con extremos 39.000–33.000 m³/s | Se conservan el orden publicado y el mínimo/máximo numérico correctos | Regresión con su estructura y datos; comparación adicional con la descarga congelada |
| El caudal de 39.500 m³/s era una previsión del día | Se identifica como aporte previsto, separado de la descarga | No se lo convierte en medición observada |
| La lectura de Ituzaingó estaba dividida en varios párrafos | Se procesa el bloque del mismo puerto hasta el encabezado de Ayolas | Puertos y referencias permanecen separados |
| Yacyretá aparecía dos veces con el mismo informe | Sólo se eliminan reportes exactamente iguales | Fechas o contenidos diferentes se conservan |
| El horizonte extendido de ALA no se exportaba | Se exportan acumulado, cobertura horaria y fin real | Horas ausentes, repetidas, fraccionadas o sin valor no producen un total |
| La fecha observada no se veía en la tabla de causas | Se usa la fecha de observación y se conserva su precisión | Una fecha de calendario no se anuncia como medición de medianoche |
| Una fila sin variables podía decir que su lluvia estaba dentro del plazo | El error de cobertura participa en el rótulo público | “Sin dato” y “datos incompletos” se muestran juntos |

### Yacyretá: lectura correcta del documento

El [boletín EBY n.º 07](https://www.eby.gov.py/alerta-hidrologico-n07-octubre/) describe el estado al 16/10/2026. Publica un aporte previsto para ese día cercano a **39.500 m³/s** y extremos futuros en descenso de **39.000 a 33.000 m³/s**, hasta el 19/10. Son aportes al embalse, no una descarga medida ni una altura local.

Para Puerto Ituzaingó informa **4,81 m a las 12:30 del 16/10**, una referencia de alerta de **3,50 m**, una referencia de evacuación de **4,00 m** y extremos previstos de **5,0 a 4,4 m**. La hora de lectura sí está publicada; las fechas del boletín y del horizonte no publican un instante de cruce. La observación posterior INA contenida en el corte auditado, **4,85 m del 17/10**, conserva su identidad y precedencia frente a ese antecedente EBY.

## 3. Lo que ve primero el usuario

En ambas páginas, el orden público es: localidad, situación o preparación, motivo, datos con sus fechas, acciones y ausencias que requieren confirmación. Las tablas amplias, detalles del modelo y documentos quedan disponibles después de ese resumen.

**ALA:** una sola selección controla la ficha principal y el detalle meteorológico; el mapa puede consultarse sin imponer una segunda localidad. Se muestran por separado el color integrado y el índice meteorológico. Las familias y los productores reciben acciones distintas. Los rojos regionales continúan visibles aunque se elija una localidad de menor nivel.

**Zárate–Campana:** la preparación regional conserva las causas y el partido al que corresponden. Un aviso moderado de Zárate no oculta un cruce de referencia de Campana que justifique una preparación mayor. El selector cambia la evidencia del lugar; no elimina el contexto regional.

Los bloques principales distinguen:

- **Lluvia prevista:** 24 y 72 horas, referencia temporal de esas ventanas, consulta o cálculo y emisión del modelo no informada cuando la API no la publica.
- **Río:** lectura observada, estación, fecha, referencias de esa escala y previsión por separado. Un dato antiguo queda como antecedente.
- **Represas:** parte, fecha, aportes previstos o salidas informadas y plazo publicado. No se calcula una llegada automática de la crecida.
- **SMN:** avisos aplicables, fechas y alcance de la consulta. Los casos parciales o inciertos se explican como cobertura sin confirmar.

Un dato faltante no se reemplaza por cero. Un verde conserva el significado de seguimiento con las evidencias disponibles. El índice meteorológico de 0 a 100 no es una probabilidad porcentual de inundación.

## 4. Fuentes de otros horizontes y datos territoriales

| Producto revisado | Evidencia observada | Uso correcto |
| --- | --- | --- |
| Radar ALA seleccionado, RMA13 | Imagen 17/10 17:10 ART; consulta 20:06; atraso de 176 minutos | Mostrar la fecha y el aviso de atraso; no llamarlo imagen actual |
| Radar ZC seleccionado, RMA2 | Imagen 17/10 19:50 ART; consulta aproximadamente 20:10 | Contexto de precipitación reciente, con hora visible |
| INTA SEPA | Producto decadal identificado con período 01–10/10; otros productos conservan su propia periodicidad | Contexto de humedad/vegetación del período, sin atribuirlo a una medición de hoy en cada lote |
| ENOS NOAA | Diagnóstico emitido 08/10; RONI de julio–agosto–septiembre; próxima emisión prevista 12/11 | Contexto climático mensual/estacional; no fija una fecha de inundación |
| Trimestral SMN usado en ZC | Emitido 31/07 para agosto–septiembre–octubre | Incluye octubre, pero no acredita una edición de octubre ni información para noviembre/diciembre |
| Territorio | Directorios y cartografía con fechas de consulta | No confirman por sí solos camas, refugios abiertos, cupos ni caminos transitables |
| Validación histórica | Ejemplos y controles retrospectivos disponibles | No demuestra calibración y capacidad predictiva en todas las localidades |

La revisión del repositorio SMN no permitió confirmar un trimestral posterior al documento utilizado. El informe ENOS de octubre es otro producto y no se utiliza como sustituto. Referencia: [registro del trimestral agosto–octubre](https://repositorio.smn.gob.ar/handle/20.500.12160/3302).

## 5. Cómo están conectadas las páginas

El monitor de ALA consulta sus fuentes y las referencias meteorológicas/fluviales adicionales de Zárate–Campana. Publica `red_integrada.json` en la rama `ala-monitor-state`. Ambas páginas leen ese mismo archivo con el mismo contrato y lector.

Cada portada también consulta fuentes para su evaluación regional. Puede tener un cálculo posterior al informe horario. Por eso la concordancia debe comprobarse sobre **misma localidad, variable, unidad, serie, fecha de fuente y período**, no sólo comparando dos números abiertos a distinta hora.

Las señales del Paraná aguas arriba aportan preparación y contexto. No se trasladan automáticamente como un rojo local de Zárate o Campana. Las series de observación INA 40/41 y de pronóstico 29534/3405 no tienen equivalencia vertical confirmada en la integración. No se aplican umbrales observados a los pronósticos sin esa comprobación.

El cron de ALA está programado al minuto 7 de cada hora; las ejecuciones reales pueden demorarse. En el tramo auditado se verificaron 18 ejecuciones horarias consecutivas correctas después de la corrección de madrugada. Una corrida con cobertura parcial puede terminar correctamente y publicar el informe: esto no significa que todas las fuentes estén completas.

Zárate–Campana conserva pendiente su propio Telegram. La red compartida no agrega destinatarios al canal ALA. El nuevo workflow de ZC verifica código y portada sin credenciales de envío; no activa su archivo de monitor guardado fuera de `.github/workflows`.

## 6. Verificación de la actualización

Las pruebas automáticas abarcan el motor existente, los errores reproducidos y la presentación. Incluyen datos recientes y vencidos, fallos de fuente, informes incompatibles, cero frente a ausencia, fechas de calendario sin hora, identidad de estaciones, rangos de EBY ascendentes/descendentes y conservación de la lluvia de 72 horas al agregar el horizonte extendido.

Streamlit AppTest ejercita selección de localidad y acciones por público con fuentes congeladas. En ALA verifica Corrientes con evidencia fluvial roja y lluvia baja, cambio a Rafaela sin datos y regreso sin arrastre de valores. En ZC verifica cambio Zárate/Campana, productor, fuente ausente y dos paneles que reutilizan la misma lectura. Los envíos y la red están bloqueados en esas pruebas.

La integración continua de ALA ejecuta además una consulta actual `--dry-run`. El paso de publicación de la rama principal relee el informe remoto; ese paso no usa credenciales Telegram ni escribe su registro de avisos. Un informe así generado se identifica como comprobación, hasta que lo reemplace el siguiente control horario normal.

## 7. Condiciones pendientes para un SAT operativo completo

La [OMM](https://wmo.int/activities/early-warnings-all/wmo-and-early-warnings-all-initiative) organiza un SAT en conocimiento del riesgo, vigilancia y pronóstico, comunicación, y preparación/respuesta. La evaluación de este proyecto frente a esos componentes exige completar:

1. **Mediciones locales y escalas:** continuidad de alturas recientes en Zárate y Campana, referencias documentadas y equivalencia vertical cuando se necesite comparar observación, pronóstico y relieve.
2. **Impacto local:** zonas expuestas verificadas, condiciones de drenaje/arroyos, anegamiento por lluvia, viento y marea en el Delta. Un punto del modelo no representa cada vivienda o lote.
3. **Validación predictiva:** episodios con daño y controles sin evento, aciertos, falsas alarmas, omisiones y anticipación real; evaluación separada por localidad, amenaza y horizonte.
4. **Respuesta confirmada:** responsables, teléfonos vigentes, protocolos, recursos disponibles, recorridos y destinos comprobados antes de recomendarlos como operativos.
5. **Continuidad de servicio y comunicación:** supervisión de los controles, aviso ante interrupciones, respaldo de fuentes y comprobación de recepción de mensajes; ejercicios con vecinos y productores para verificar comprensión.

Estos faltantes no se completan inventando un umbral, una hora de llegada, una capacidad de refugio o una probabilidad. Deben incorporarse con evidencia y validación local. Las correcciones de esta versión hacen explícito cuándo esa información no está confirmada.

## Fuentes y evidencia verificable

- [ALA publicada](https://alerta-litoral-agro.streamlit.app/) y [Zárate–Campana publicada](https://alerta-zarate-campana.streamlit.app/).
- [Informe público compartido](https://raw.githubusercontent.com/FedericoIeraci-cell/Alerta-Litoral/ala-monitor-state/red_integrada.json), archivo mutable con fecha propia.
- [Control horario de las 19:25 ART](https://github.com/FedericoIeraci-cell/Alerta-Litoral/actions/runs/39074986093).
- [EBY, boletín n.º 07 del 16/10/2026](https://www.eby.gov.py/alerta-hidrologico-n07-octubre/).
- [SMN, trimestral agosto–octubre emitido 31/07/2026](https://repositorio.smn.gob.ar/handle/20.500.12160/3302).
- [OMM, componentes de un SAT centrado en las personas](https://wmo.int/activities/early-warnings-all/wmo-and-early-warnings-all-initiative).
- [SINAGIR, prevención ante inundaciones](https://www.argentina.gob.ar/sinagir/riesgos-frecuentes/inundacion/prevencion).
- [INTA, preparación de espacios rurales](https://www.argentina.gob.ar/noticias/recomendaciones-para-prevenir-riesgos-y-reducir-el-impacto-del-fenomeno-del-nino-0).

Las páginas públicas y las fuentes son dinámicas. El informe y la planilla de la auditoría conservan el corte indicado para que los hallazgos puedan distinguirse de las condiciones posteriores.
