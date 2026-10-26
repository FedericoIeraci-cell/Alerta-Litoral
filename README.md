# Alerta Litoral Agro · V4.10.3

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


## Interfaz para celulares

La portada se adapta al ancho del dispositivo. En celulares muestra accesos inferiores a **Mapa**, **Mi localidad**, **Alertas** y **Estado datos**, con espacio para el área segura del teléfono. Las pestañas, selectores y botones tienen controles táctiles amplios; las fichas aumentan el tamaño de texto y mantienen las fechas visibles.

El mapa permite buscar localidad o provincia sin distinguir tildes, filtrar por nivel y alternar entre **Mapa** y **Lista**. La lista es una alternativa accesible a tocar marcadores cercanos. Al tocar una localidad en un celular se abre una ficha amplia con motivo, altura disponible, acciones, fechas y evidencias desplegables. **Cerrar** o Escape devuelve el foco; **Ver todo** limpia los filtros. Abrir esta ficha no cambia la localidad seleccionada en el panel inferior.

Los filtros son sólo visuales: no recalculan el riesgo ni eliminan referencias de la red. Se conservan los estados sin datos y la distinción entre consulta, observación y pronóstico. No se cambian fuentes, umbrales, programación ni envíos. El color continúa siendo una evaluación experimental, no una orden oficial.

Validación reproducible sin red: `python -m unittest discover -s tests -v`, `node --test tests/test_ubicacion_js.cjs tests/test_viirs_js.cjs`. Incluye búsqueda, filtros, mapa/lista, apertura/cierre y foco de la ficha, datos ausentes y el primer dibujo de Streamlit con una fuente que falla. La revisión en Chromium con anchos de 320, 390, 768 y 1280 píxeles comprobó controles, fichas, ausencia de desborde horizontal y conservación de nodos ante fallos de la capa satelital. No sustituye una prueba en teléfonos físicos con Safari/iOS y Android.

## Actualización de datos V4.10.3

La verificación en producción detectó que Open-Meteo entrega `evapotranspiration` sin valores y con unidad `undefined`. Esa ausencia se conserva como tal y no invalida las demás variables. Una unidad sin definir con valores numéricos continúa rechazada. Si la captura no puede prepararse, la validación y la publicación informan el fallo en vez de finalizar como si la captura estuviera disponible.

La página reutiliza la consulta completa de ECMWF que ya realiza el monitor, conservada en `meteorologia_ecmwf.json` de la rama `ala-monitor-state`. Esto evita multiplicar consultas de Open-Meteo desde Streamlit, donde se detectó HTTP 429. El archivo incluye los valores horarios y diarios originales, unidades, faltantes, catálogo de puntos y fecha de consulta; no incluye credenciales ni registros de Telegram.

La sesión abierta revisa los datos cada cinco minutos. Primero descarga la captura compartida; si no puede validarla, utiliza la consulta directa con su caché existente. La captura se rechaza si supera tres horas, tiene una fecha futura inválida o no corresponde a los puntos solicitados. La hora de descarga del modelo queda separada de la evaluación local y de la hora de medición de cada organismo. El botón **Actualizar datos** renueva las consultas guardadas de Streamlit y del informe común.

El monitor publica la captura a partir de la misma respuesta que evaluó, sin una segunda consulta meteorológica. Verifica escritura y relectura; una falla de publicación se informa. La publicación de datos no cambia el último control de Telegram ni envía mensajes. La programación compartida continúa con objetivo de diez minutos, sujeto a las demoras de GitHub Actions y a la disponibilidad de cada fuente; no representa telemetría instantánea.

## Corrección visual V4.10.1

El mapa principal utiliza dibujo 2D con Leaflet incluido en la aplicación. Conserva las provincias, las referencias y sus explicaciones aunque el navegador no pueda usar WebGL. Si no carga el fondo de OpenStreetMap, los puntos y límites del Litoral siguen visibles. El cambio no modifica datos, colores ni reglas de evaluación.

## Cambios de la versión V4.10.0

El **mapa de riesgo del Litoral es el primer bloque de la portada**, con las 48 referencias, fecha de evaluación, leyenda y cantidad de localidades por estado. Debajo aparecen el selector manual, la geolocalización, el estado local, la lluvia prevista para 24 y 72 horas y la altura observada. Las cifras mantienen su fecha, fuente y vigencia a la vista; las explicaciones completas y las referencias quedan en detalles desplegables. La cabecera ocupa menos espacio y el título es visible.

La conexión común separa la fecha del informe de datos y la del último control automático normal. La publicación de comprobación de una actualización no borra la evidencia de un control normal reciente; una lectura fallida o antigua tampoco se presenta como control activo. La cobertura incompleta se informa por separado.

Se incorpora como referencia adicional la estación automática **Paraná Las Palmas - Zárate**, serie observada INA **29437**. Tiene su propio identificador, fecha y escala y se comparte en las dos páginas. Los puertos de Zárate (serie 40) y Campana (41) conservan sus lecturas y referencias por separado. Ver [evidencia hidrométrica](docs/HIDROMETRIA_ZARATE_20261018.md).

## Cómo leer el color y el índice

El **índice meteorológico (0–100)** conserva sus umbrales experimentales: amarillo desde 35 y rojo desde 55, salvo ajuste explícito de la sesión. El **riesgo integrado** puede ser rojo con un índice verde por un nivel fluvial observado, una previsión fluvial dentro de la ventana de preparación o un aviso SMN aplicable y vigente. Se muestran por separado.

Cada localidad conserva `evidencias_alerta`, `desencadenantes` y `motivo_principal`. La causa del color identifica observación/previsión/SMN/meteorología, estación, valores y umbrales disponibles, fecha de lectura/emisión, horizonte, precisión temporal, límite de uso y enlaces. La fuente del umbral se conserva separada de la fuente de la altura cuando difieren (p. ej. lectura CTM y umbral INA). Una fuente fallida o sin URL oficial reconocida no acredita una causa fluvial/SMN. Las represas y las señales aguas arriba aportan vigilancia, sin convertir su caudal en altura local ni asignarles un rojo local.

Una previsión textual no se presenta como altura observada ni se le inventa una altura futura. Si sólo publica un día, se indica **hora no publicada**: entrar en la ventana operativa de 72 horas no significa que se conozca la hora de inundación. El máximo de 48 horas desde la fuente es un criterio de vigencia de ALA; no prolonga la validez oficial.

Al pasar el cursor sobre el mapa aparece una tarjeta breve. El selector **Seleccionar localidad**, al inicio, controla la ficha persistente, sus datos y acciones; también se utiliza con teclado o celular. Los mensajes y registros de transición explican la causa y el cambio del índice por separado. No se reescribe el historial de avisos ya enviados.

El JSON compartido agrega `indice_meteorologico`, `nivel_meteorologico`, `estado_integrado_ala`, `nivel_integrado_vigente`, motivo y evidencias. Los campos históricos `indice_experimental` y `nivel_experimental` se conservan por compatibilidad: el segundo es un campo legado del nivel integrado, no la clasificación del índice. Los informes anteriores sin trazabilidad se identifican como tales. La fecha de generación no renueva las evidencias vencidas.

Ver [investigación de los ocho rojos del 15/10/2026](docs/REVISION_ALERTAS_20261015.md). Las pruebas de regresión reproducen la transición de Reconquista, fuentes fallidas, fechas vencidas/futuras, observación frente a pronóstico, SMN, escenarios numéricos, coherencia entre superficies y HTML de Telegram sin envíos reales.

## Actualización

La versión **V4.9.0** agrega **Usar mi ubicación** junto al selector manual. El navegador pide permiso y calcula una referencia pública cercana; las coordenadas del dispositivo no se envían a la app. Un permiso previamente concedido permite una consulta automática por sesión, sin seguimiento continuo. La distancia es aproximada y en línea recta: no acredita una medición en la calle o el lote, ni una cobertura territorial exacta. Una selección manual posterior se respeta, incluso si llega tarde la respuesta del navegador.

Cuando no hay precisión suficiente, la referencia está muy alejada o el navegador no permite obtener ubicación, se conserva la selección manual. Si la referencia cercana pertenece a Zárate–Campana, se ofrece un enlace voluntario que contiene sólo su identificador público. Ver [funcionamiento, privacidad y pruebas de ubicación](docs/GEOLOCALIZACION.md).

La versión **V4.8.0 (17/10/2026)** pone primero la localidad elegida, el motivo del color, lluvia y río con sus fechas, represas, avisos SMN y acciones para vecinos y productores. Se distingue el momento de cálculo de la fecha de los datos y de la referencia de los períodos de lluvia. Los detalles, mapas y descargas siguen disponibles.

La red compartida utiliza el mismo lector en las dos páginas. Al vencer el informe se invalidan tanto el color actual como sus evidencias. Las tablas de fuentes y ríos aplican el mismo límite de seis horas a las lecturas y emisiones INA de Zárate–Campana. El botón de actualización solicita una nueva descarga sin modificar las fechas originales. El informe agrega la trazabilidad de la consulta SMN, elimina únicamente partes de represa idénticos y publica el horizonte real de lluvia disponible, sin completar días ausentes ni modificar el índice meteorológico.

Se corrige la lectura del boletín EBY n.º 07: respeta los párrafos del mismo puerto, los extremos publicados en descenso, la condición de caudal previsto y la precisión de sus fechas. Cada dato conserva la fuente original.

Ver [auditoría de datos, conexión y presentación del 17/10/2026](docs/AUDITORIA_SAT_20261017.md) para el corte revisado, las limitaciones y los controles de esta actualización.

### Corrección anterior

La versión **V4.7.1 (17/10/2026)** corrige el bloqueo al combinar pronósticos
hidrológicos con fechas completas y fechas ausentes, reproducido en Concordia.
Conserva las fechas originales, los niveles y las firmas previamente válidas.
El panel comprueba la revisión del motor al recargar, admite pronósticos cortos
de uno a cinco días y el monitor informa el tipo y la etapa de un error de
evaluación sin publicar mensajes HTTP ni credenciales. Las 185 pruebas de
regresión se ejecutan sin envíos reales.

Subí juntos a la rama utilizada por Streamlit:

- `app.py`, `hidrologia.py`, `ctm.py`, `monitor.py`, `red_integrada.py`, `hidrometria_local.py`, `portada_local.py` y `explicaciones_alerta.py` en la raíz.
- `ubicacion_local.py` en la raíz y la carpeta completa `ubicacion_local_frontend/`, incluidos su catálogo, HTML, JavaScript y CSS locales.
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

### Agua detectada por satélite y colores ENOS

En el mapa principal, **Agua por satélite → Mostrar agua detectada · VIIRS**
activa una capa independiente de los nodos. Ofrece compuestos de 1 y 5 días,
opacidad regulable, leyenda del proveedor, fecha nominal UTC y hora de consulta
argentina. La fecha nominal identifica el producto, no la hora de observación
de cada píxel. Las imágenes se fijan a esa fecha; si no se puede verificar el
producto o faltan fechas no se muestra una imagen sin fechar. La consulta sólo
se realiza al activar o actualizar la capa. Los fallos de imágenes o leyenda se
informan sin alterar las evaluaciones de los nodos.

Fuente: [VIIRS Flood / SSEC-CIMSS, NOAA y GMU](https://www.ssec.wisc.edu/flood-map-demo/flood-products/).
Es una observación regional (~375 m), no un pronóstico ni una comprobación de
cada calle. Una composición de varios días puede combinar observaciones de
distintas fechas. Nubes, sombras, vegetación y faltantes limitan la detección;
una zona sin color no confirma ausencia de agua. La disponibilidad del servicio
externo no se garantiza. Se conserva su leyenda, incluida el agua permanente,
para no confundirla con inundación nueva. La capa no modifica el semáforo ni
los envíos del monitor.

La solapa **El Niño / La Niña** colorea el valor y las barras RONI en rojo para
anomalías positivas, azul para negativas y gris para cero. El signo no confirma
una fase ENOS: se conservan la serie, los trimestres, la procedencia, los criterios
de persistencia y las advertencias de NOAA. `enos_visual.py` y `assets/viirs/`
deben desplegarse junto con `app.py`, `mapa_inicio.py` y `mapa_litoral.py`.

Prueba de fechas, cargas tardías, desactivación y fallos de VIIRS:
`node --test tests/test_viirs_js.cjs`.

El historial local tolera registros CSV con bytes inválidos sin bloquear los
registros legibles. Antes de guardar una recuperación conserva el original exacto
en `historial_alerta_litoral.csv.respaldo-<hash>.bin` y deja un aviso permanente en
el panel con el detalle y la descarga del respaldo. No se adivinan codificaciones
ni se inventan registros; una cabecera dañada impide reemplazar el archivo. Las
escrituras son atómicas y los fallos de guardado se muestran en pantalla.
Estos archivos siguen siendo locales a Streamlit y pueden perderse al reiniciar
o desplegar: el respaldo permite descargarlos, pero no constituye almacenamiento
durable del histórico. Los registros sin fecha o índice válido se conservan en
el CSV y se excluyen del gráfico.

```bash
python -m pip install -r requirements_test.txt
python -m unittest discover -s tests -v
```

Las pruebas usan datos controlados y sustituyen envíos. Para consultar fuentes actuales sin modificar el registro ni enviar avisos:

```bash
python monitor.py --dry-run
```

Para abrir el panel local, instalá `requirements.txt` y ejecutá `streamlit run app.py`.

## Procesos independientes de ALA y Zárate–Campana

ALA consulta y publica exclusivamente sus **48 localidades del Litoral**, sus fuentes fluviales, represas y avisos SMN. No descarga meteorología ZC ni espera a su actualizador.

- ALA: repositorio `Alerta-Litoral`, rama `ala-monitor-state`; `monitor_status.json`, `red_integrada.json` y `meteorologia_ecmwf.json`.
- Zárate–Campana: repositorio `alerta-zarate-campana`, rama `zc-datos-state`; `datos_status.json`, `red_integrada.json` y `meteorologia_zc_ecmwf.json`.
- Los enlaces entre páginas se conservan para navegar. El estado de una página no acredita ni bloquea el de la otra.

El formato regional declara `regiones: ["Litoral"]` y contiene 48 puntos. El lector conserva compatibilidad con los informes históricos de 58 puntos, pero el monitor ya no los produce. Un nodo ausente dentro de la región sigue siendo un error de contrato; la ausencia de la otra región es deliberada.

`.github/workflows/ala-monitor.yml` mantiene la evaluación horaria y su registro de avisos. `.github/workflows/ala-red-integrada.yml` publica sólo datos del Litoral con cadencia objetivo de diez minutos. Ambos conservan la exclusión de escritores de ALA; no comparten cola, estado ni credenciales con ZC. La validación de ALA tampoco ejecuta consultas ZC.

Prueba sin envíos ni escrituras remotas:

```bash
python monitor.py --dry-run --export-red red_litoral_revision.json
```

Publicación de datos ALA sin leer secretos de Telegram ni modificar su registro:

```bash
python monitor.py --publish-red-only
```

Los problemas de fuentes de ALA siguen informándose en ALA. El registro, las reservas y la deduplicación de Telegram se conservan. Una fuente caída, una previsión vencida o un parte diario sin hora permanecen identificados con su fecha real; separar las páginas no completa información ausente.

GitHub Actions puede demorar el inicio de las tareas. La frecuencia configurada no garantiza telemetría continua. El lector recalcula la vigencia de cada evidencia y no cambia su hora al abrir la página.

[Abrir Alerta Litoral Agro](https://alerta-litoral-agro.streamlit.app/) · [Abrir Alerta Zárate–Campana](https://alerta-zarate-campana.streamlit.app/)

