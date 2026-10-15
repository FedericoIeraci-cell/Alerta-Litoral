# Ficha del problema y alcance de la aplicación

Revisión del 15 de octubre de 2026 sobre la Entrega 1 de la Diplomatura en Meteorología Inteligente aportada por el autor.

Alerta Litoral Agro es un prototipo de semáforo hídrico para preparar decisiones ante anegamientos. El ámbito original del trabajo es Entre Ríos, Corrientes y Santa Fe. La aplicación amplía el monitoreo a Chaco, Formosa y Misiones, sin afirmar cobertura continua ni validación en cada lote.

La pregunta del proyecto consiste en integrar pronóstico de lluvia, condiciones hídricas del suelo y capas territoriales para orientar productores, comités de cuenca, municipios y Defensa Civil. Las decisiones abarcan hacienda, siembra y cosecha, drenajes, accesos, refugios y salud. Se combinan casos históricos y propuesta de aplicación.

| Requisito | Función disponible | Alcance y condición pendiente |
| --- | --- | --- |
| Lluvia y pronóstico de corto y mediano plazo | ECMWF/Open-Meteo y horizontes de planificación | Antecedentes de grilla, no pluviómetros. Siete días requieren 168 horas válidas; días 8–14 requieren siete días completos. |
| Lluvia observada | Consulta INTA SIGA por localidad con estación y distancia | Disponibilidad desigual. Conserva la fecha diaria y no suma dos clases de precipitación del mismo registro. No se afirma una descarga de lluvia medida SMN que no fue comprobada. |
| Humedad y saturación | Humedad volumétrica modelada y agua útil INTA cargada con procedencia | Son variables distintas de saturación física y portancia. No se certifica la capacidad de absorción o tránsito de un lote sin propiedades y observaciones locales. |
| Agua en superficie | Productos visuales SEPA; Sentinel-2 NDWI/NDVI/MNDWI y máscaras de calidad; CSV | Consulta por sesión. Nubes, sombras, NoData y antigüedad pueden impedir su uso actual. Agua permanente no equivale a inundación dañina. |
| Niveles y previsiones fluviales | INA, EBY/Yacyretá y CTM/Salto Grande con fechas y conexiones de cuenca | Cobertura parcial según localidad; no se suman caudales sucesivos ni se inventa una hora de llegada desde compuertas. |
| Alertas y clima estacional | CAP del SMN y documento trimestral con período y leyendas | La perspectiva trimestral aporta contexto, no activa una orden. El documento del repositorio conserva su período original aunque se descargue hoy. |
| Cotas | Malla de muestras Copernicus GLO-90 vía Open-Meteo | No resuelve canales o defensas. Una escala hidrométrica no es una cota compatible para restar del terreno. El MDE-Ar/IGN es una fuente alternativa de referencia, no una lectura incorporada automáticamente. |
| Cortes, caminos y desvíos | Adaptador de Vialidad Nacional; fuentes provinciales; importación de registros y revisión de recorridos | No hay un feed provincial uniforme ni garantía de paso. Un trazado OSRM o la ausencia de un corte reportado no habilita un recorrido. |
| Refugios y salud | Directorios, antecedentes y registros con autoridad, evidencia, verificación y vigencia | No se verificó un inventario completo de refugios abiertos, cupos, guardias ni stock sanitario. No se convierten antecedentes históricos en disponibilidad actual. |
| Decisiones por usuario | Plan de productor, comité de cuenca o municipio/Defensa Civil, con acciones y verificaciones | Coordina destino, transporte, acceso y recursos; no emite una orden oficial ni garantiza una ventana segura de siembra/cosecha. |
| Caso histórico y calibración | Concordia/abril 2016 multifuente, Vera/mayo 2025, episodios y controles negativos documentados, herramientas de evaluación | Faltan emisiones e índices completos de una cohorte independiente. No hay validación predictiva demostrada para las tres provincias originales; la evaluación exploratoria no cierra ese requisito. |
| Inventario y calidad | Tabla por localidad con fuente, fecha, unidad, estado, representatividad y uso en el monitor | Distingue función disponible de dato efectivamente obtenido. Ausente o vencido nunca equivale a cero ni a seguridad. |

## Uso

En **Plan y ficha del proyecto**, elegir localidad y usuario. El color proviene de la misma evaluación integrada de la pantalla principal. Consultar SIGA para contrastar lluvia, revisar las evidencias que faltan y descargar el plan con inventario y fuentes. Las capas de suelo/satélite/relieve se completan en sus solapas y conservan su fecha.

En **Territorio y rutas**, registrar recursos y caminos con autoridad, evidencia, fecha de verificación, vigencia y acceso. Una capacidad desconocida se conserva desconocida. Directorios y puntos de encuentro sirven para planificación, sin acreditar refugios habilitados.

El monitor horario conserva su evaluación automática y el registro durable de Telegram. Las cargas privadas de la sesión y las consultas complementarias de SIGA no alteran sus avisos.

## Comprobaciones de fuentes

Se comprobó la consulta pública completa de SIGA: catálogo de 182 estaciones y registros diarios con cobertura parcial explícita. El contrato se descubre desde el JavaScript oficial y se prueba con respuestas pequeñas conservadas en `tests/fixtures`; no utiliza credenciales ni formularios de registro.

Se comprobó el procesamiento real de una escena Sentinel-2 de Corrientes del 02/10/2026. Su antigüedad impide interpretarla como observación del 15/10. Se comprobó el PDF trimestral disponible en el repositorio SMN, de agosto–septiembre–octubre de 2026: no se lo presenta como un pronóstico nuevo de los próximos 90 días.

Fuentes de referencia:

- [INTA SIGA](https://siga.inta.gob.ar/) y su [servicio público declarado](https://siga.inta.gob.ar/js/urlserver.js).
- [INTA SEPA](https://sepa.inta.gob.ar/) y [agua útil del perfil](https://sepa.inta.gob.ar/productos/geosepa/agua_en_suelo/pj/).
- [INTA: recomendaciones ante El Niño en espacios rurales](https://www.argentina.gob.ar/noticias/recomendaciones-para-prevenir-riesgos-y-reducir-el-impacto-del-fenomeno-del-nino-0).
- [SMN: repositorio trimestral](https://repositorio.smn.gob.ar/handle/20.500.12160/1179).
- [IGN: modelo digital de elevaciones](https://www.ign.gob.ar/NuestrasActividades/Geodesia/ModeloDigitalElevaciones/Introduccion).
- [Vialidad Nacional: estado de rutas](https://www.argentina.gob.ar/transporte/vialidad-nacional/estado-de-rutas).

La aplicación cubre el diseño de la propuesta de semáforo y permite documentar el análisis de casos. El cierre de la calibración predictiva y de la disponibilidad operacional local exige evidencia adicional real.
