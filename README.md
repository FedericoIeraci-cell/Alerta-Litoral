# Alerta Litoral Agro · V4.7.1

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


## Cómo leer el color y el índice

El **índice meteorológico (0–100)** conserva sus umbrales experimentales: amarillo desde 35 y rojo desde 55, salvo ajuste explícito de la sesión. El **riesgo integrado** puede ser rojo con un índice verde por un nivel fluvial observado, una previsión fluvial dentro de la ventana de preparación o un aviso SMN aplicable y vigente. Se muestran por separado.

Cada localidad conserva `evidencias_alerta`, `desencadenantes` y `motivo_principal`. La causa del color identifica observación/previsión/SMN/meteorología, estación, valores y umbrales disponibles, fecha de lectura/emisión, horizonte, precisión temporal, límite de uso y enlaces. La fuente del umbral se conserva separada de la fuente de la altura cuando difieren (p. ej. lectura CTM y umbral INA). Una fuente fallida o sin URL oficial reconocida no acredita una causa fluvial/SMN. Las represas y las señales aguas arriba aportan vigilancia, sin convertir su caudal en altura local ni asignarles un rojo local.

Una previsión textual no se presenta como altura observada ni se le inventa una altura futura. Si sólo publica un día, se indica **hora no publicada**: entrar en la ventana operativa de 72 horas no significa que se conozca la hora de inundación. El máximo de 48 horas desde la fuente es un criterio de vigencia de ALA; no prolonga la validez oficial.

Al pasar el cursor sobre el mapa aparece una tarjeta breve. El selector **Detalle del nodo del mapa** abre una ficha persistente con acción y fuentes, también utilizable con teclado o celular. Los futuros mensajes y registros de transición explican la causa y el cambio del índice por separado. No se reescribe el historial de avisos ya enviados.

El JSON compartido agrega `indice_meteorologico`, `nivel_meteorologico`, `estado_integrado_ala`, `nivel_integrado_vigente`, motivo y evidencias. Los campos históricos `indice_experimental` y `nivel_experimental` se conservan por compatibilidad: el segundo es un campo legado del nivel integrado, no la clasificación del índice. Los informes anteriores sin trazabilidad se identifican como tales. La fecha de generación no renueva las evidencias vencidas.

Ver [investigación de los ocho rojos del 15/10/2026](docs/REVISION_ALERTAS_20261015.md). Las pruebas de regresión reproducen la transición de Reconquista, fuentes fallidas, fechas vencidas/futuras, observación frente a pronóstico, SMN, escenarios numéricos, coherencia entre superficies y HTML de Telegram sin envíos reales.

## Actualización

La versión **V4.7.1 (17/10/2026)** corrige el bloqueo al combinar pronósticos
hidrológicos con fechas completas y fechas ausentes, reproducido en Concordia.
Conserva las fechas originales, los niveles y las firmas previamente válidas.
El panel comprueba la revisión del motor al recargar, admite pronósticos cortos
de uno a cinco días y el monitor informa el tipo y la etapa de un error de
evaluación sin publicar mensajes HTTP ni credenciales. Las 185 pruebas de
regresión se ejecutan sin envíos reales.

Subí juntos a la rama utilizada por Streamlit:

- `app.py`, `hidrologia.py`, `ctm.py`, `monitor.py`, `red_integrada.py` y `explicaciones_alerta.py` en la raíz.
- `siga_inta.py`, `ficha_proyecto.py`, `decisiones_agro.py` y `panel_proyecto.py` en la raíz.
- `requirements.txt` y `requirements_monitor.txt` en la raíz.
- `.github/workflows/ala-monitor.yml` en esa ubicación exacta.

Conservá los Secrets de Streamlit y Actions y la rama durable `ala-monitor-state`, incluidos `telegram_alerts.sqlite3` y `monitor_status.json`. El workflow programa controles al minuto 7 de cada hora (`7 * * * *`); la fecha del último control muestra si efectivamente se ejecutaron.

La reserva del evento se guarda antes del envío. Solo una respuesta válida de Telegram con `message_id` confirma el envío; una respuesta incierta no acredita entrega. Cero avisos no equivale a ausencia de riesgo.

Ver [GUIA_MONITOR.md](GUIA_MONITOR.md) para actualización, Secrets, prueba sin envíos y diagnóstico.

## Ficha del problema y decisiones agropecuarias

La solapa **Plan y ficha del proyecto** reúne el semáforo integrado de la localidad, el usuario que decide, horizontes de lluvia e inventario de evidencias presentes y faltantes. Genera un plan con acciones, plazos, verificaciones y fuentes para productor, comité de cuenca o municipio/Defensa Civil, y permite descargarlo.

La consulta pública a **INTA SIGA** descubre estaciones y registros diarios desde el servicio utilizado por su propia web. Conserva estación, distancia, día informado, ceros medidos, días sin datos y procedencia. No reemplaza el pronóstico ni atribuye la medición a cada lote. Es un servicio web sin API versionada: los cambios o fallas se muestran como faltantes.

La cobertura meteorológica del índice de 72 horas no acredita siete días completos. El acumulado de siete días exige 168 horas únicas, contiguas y válidas; el de días calendario 8–14 exige sus siete días completos. No se rellenan faltantes con cero ni se calcula una ventana segura de trabajo por una cantidad universal de milímetros.

Suelo INTA, satélite y relieve se incorporan mediante consultas/cargas de la sesión. No se envían al monitor horario. El panel principal y Territorio reutilizan la integración automática para evitar que una crecida roja quede oculta por un índice meteorológico bajo.

Ver [FICHA_DEL_PROYECTO.md](FICHA_DEL_PROYECTO.md) para la correspondencia con la entrega y los requisitos que aún dependen de validación predictiva o confirmación operativa local.

## Verificación

```bash
python -m pip install -r requirements_test.txt
python -m unittest discover -s tests -v
```

Las pruebas usan datos controlados y sustituyen envíos. Para consultar fuentes actuales sin modificar el registro ni enviar avisos:

```bash
python monitor.py --dry-run
```

Para abrir el panel local, instalá `requirements.txt` y ejecutá `streamlit run app.py`.

## Red integrada Litoral–Zárate–Campana

Las dos aplicaciones tienen una solapa **Red integrada** que consulta el mismo informe público: `red_integrada.json` en la rama `ala-monitor-state` del repositorio Alerta-Litoral.

El informe reúne los **48 puntos del Litoral y 10 referencias meteorológicas de Zárate–Campana**, estaciones y pronósticos fluviales oficiales disponibles y reportes de represas. Conserva fechas, cobertura y fuentes. Los puntos cercanos pueden representar la misma celda meteorológica.

El monitor existente de ALA prepara el informe común después de guardar su evaluación regional. La consulta adicional de Zárate–Campana no incorpora nuevos candidatos ni destinatarios a Telegram. Cada aplicación conserva sus criterios y registros regionales; el informe común no activa una orden de evacuación.

El publicador y los lectores comparten un límite de **2.000.000 bytes**. El JSON se guarda compacto, sin recortar evidencias. Cada escritura utiliza el SHA leído previamente y se confirma releyendo el contenido y el SHA remoto. `monitor_status.json` guarda primero `red_integrada_estado: pendiente` y luego el resultado final: `red_integrada_publicada`, estado, confirmación (SHA, bytes, generación y verificación) o código/etapa del error. Un fallo de publicación hace fallar Actions aunque la evaluación regional haya terminado; `health_evaluacion` conserva el resultado regional. Las reservas, confirmaciones y deduplicación de Telegram mantienen su circuito anterior.

Un informe con más de 3 horas se señala como vencido. Las alturas y corridas locales de Zárate–Campana también se revisan con un máximo de 6 horas; la fecha de generación del informe no renueva una lectura antigua. No se suman caudales de estaciones sucesivas ni se calcula una hora de llegada desde las compuertas.

Para actualizar, incluí `red_integrada.py` en la raíz de ambos repositorios. ALA también requiere el `monitor.py` actualizado. Una prueba de consulta y exportación sin envíos es:

```bash
python monitor.py --dry-run --export-red red_integrada_revision.json
```

Para recuperar y verificar **sólo la publicación**, con permisos de GitHub y sin credenciales de Telegram:

```bash
python monitor.py --publish-red-only
```

Este modo consulta fuentes actuales, publica y relee `red_integrada.json`, pero no lee ni escribe el SQLite de avisos ni `monitor_status.json`. El informe indica `dry_run: true` para no atribuirle una evaluación de envíos. La siguiente ejecución horaria normal lo reemplaza. El workflow de validación ejecuta este modo después de superar las pruebas en un push a la rama principal, con la misma exclusión de concurrencia que el monitor horario. En ramas y pull requests sólo valida, sin publicaciones ni envíos.

Diagnóstico y regresiones de la incidencia: [Publicación de la red, 15/10/2026](docs/PUBLICACION_RED_20261015.md).

[Abrir Alerta Litoral Agro](https://alerta-litoral-agro.streamlit.app/) · [Abrir Alerta Zárate–Campana](https://alerta-zarate-campana.streamlit.app/)

