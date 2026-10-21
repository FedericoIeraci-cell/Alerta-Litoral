# Recuperación meteorológica de Zárate–Campana

El informe de 20/10/2026 00:32 ART tenía diez puntos sin meteorología y ReadTimeout. Al revisar la publicación de 11:35 ART, ocho puntos ya tenían lluvia prevista y dos seguían sin dato. Esto acredita intermitencia y el impacto de descartar lotes enteros; no demuestra saturación de Open-Meteo ni que una variable concreta cause el timeout.

La documentación oficial admite 15 días, consultas por coordenadas y las variables de precipitación, suelo y escurrimiento: https://open-meteo.com/en/docs/ecmwf-api. Se conservan los parámetros completos (8 días previos, 15 de horizonte, hora argentina, mm, selección terrestre). Los días previos son estimaciones archivadas del modelo, no pluviómetros. El escurrimiento puede seguir siendo parcial y debe aparecer ausente si no tiene cobertura completa.

Cada referencia se consulta separadamente, con tres solicitudes simultáneas como máximo. Los límites por solicitud son 5 segundos de conexión y 20 de lectura. Un timeout, error de conexión, HTTP 429 o 500/502/503/504 permite un segundo intento completo tras un segundo de pausa. Si también falla, se intenta una vez precipitación sola con cero días previos y cuatro días de horizonte. No se reintentan errores permanentes como HTTP 400 ni respuestas incompatibles. Cada punto conserva el modo, resultado y duración de sus intentos, sin publicar cuerpos de error HTTP.

Una respuesta reducida conserva los faltantes de suelo, escurrimiento y horizontes no cubiertos. No se calcula un índice ZC ni se modifica el riesgo. Una precipitación ausente nunca equivale a cero.

Si todos los intentos fallan, el productor lee el informe público anterior. Sólo una grilla horaria guardada con contrato versión 1, identidad, coordenadas, unidades y cobertura verificadas puede servir de respaldo. Se vuelven a calcular las ventanas respecto de la hora actual, sin copiar acumulados de otro período. Se conserva la fecha de consulta/cálculo original; el nuevo intento se fecha en otro campo. La emisión del modelo sigue sin atribuirse si la fuente no la informa.

El respaldo cuenta como actual durante las tres horas que ya admitía el lector. Después sólo se conserva como referencia vencida, con sus ventanas originales y sus colores sin vigencia. Una nueva publicación nunca rejuvenece el respaldo. Los informes antiguos sin grilla no se reutilizan: sus acumulados no permiten reconstruir una ventana que cambió. No se reutilizan observaciones locales ni de otra estación como sustituto de un pronóstico ECMWF.

Ambos repositorios usan la misma versión del lector y muestran el origen de la lluvia (directo, reducido, respaldo vigente o vencido). Las portadas conservan sus evaluaciones locales: el contrato compartido no convierte estas variables en una orden de evacuación.

## Captura completa para la página

El productor completo consulta las diez referencias con dos trabajadores y
reutiliza una conexión independiente por trabajador. Después de dos fallos
transitorios intenta dos partes con todas las variables y el mismo horizonte.
Si una parte vuelve a agotar el tiempo de lectura, sólo esa parte se divide una
vez más. Una parte ya obtenida no se descarga de nuevo. Las uniones exigen la
misma celda, zona horaria y ejes horarios/diarios; no se mezclan grillas distintas.

El conjunto de descargas tiene un presupuesto de 180 segundos, comprobado antes
de cada petición y de respetar una espera `Retry-After`. Los límites de conexión
y lectura se reducen al tiempo restante. Esto acota los nuevos intentos; la
transferencia de una respuesta ya iniciada puede prolongar la duración total.
HTTP 403/429 y respuestas incompatibles no activan una subdivisión. Si falta una
referencia o se agota el presupuesto no se reemplaza la captura completa ni se
renueva su fecha. Esta recuperación no convierte escurrimiento nulo en disponible.

## Comprobación reproducible

`python scripts/diagnosticar_meteo_zc.py` compara una sola vez el lote original de ocho, una coordenada con parámetros completos y una coordenada con lluvia a cuatro días. Registra hora, parámetros, duración, tipo de error o cobertura obtenida. Es una muestra puntual, no una prueba causal ni una medición de disponibilidad sostenida.

La validación automática ejecuta esta comparación y el monitor en modo `--dry-run`. Tras integrar en main, el workflow de validación publica y relee el informe mediante `--publish-red-only`, sin secretos de Telegram ni acceso a su registro de avisos. Las pruebas de regresión usan fuentes simuladas e incluyen fallos aislados, reintento exitoso, respuesta reducida, HTTP permanente/transitorio, grilla vigente/vencida, fechas futuras, identidad/unidades inválidas, lluvia ausente y relectura pública.
