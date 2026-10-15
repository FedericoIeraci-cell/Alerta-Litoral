# Verificación de Alerta Litoral Agro

## Versión V4.7.0 y ficha del proyecto

Revisión del 15 de octubre de 2026.

- 152 pruebas automatizadas aprobadas. Se agregan casos de consistencia principal/Territorio/exportación, integridad temporal de siete días, observaciones SIGA, faltantes y planes por usuario.
- Prueba de interfaz de la página principal y la nueva solapa: nueve solapas, 31 métricas y nueve tablas, cero excepciones. La prueba usa datos controlados; los paneles secundarios se evalúan por separado. Cambio de usuario en el plan probado sin excepción.
- Consulta real sin envíos del monitor y la red integrada: 48 puntos; meteorología 48/48, observaciones fluviales 22/29, previsión fluvial 16/29, represas 18/20, avisos SMN 48/48. Es el corte de la prueba, no una garantía de cobertura futura. No se escribió el estado remoto.
- INTA SIGA: consulta pública completa comprobada, catálogo de 182 estaciones. Bella Vista: 10–13/10 y faltante 14/10; acumulado completo ausente y suma disponible identificada como parcial. Ceros, fechas diarias y tipos de lluvia se conservan sin mezclarse.
- Sentinel-2: lectura real de bandas COG, máscara y GeoTIFF en Corrientes, escena del 02/10/2026. Sirve como antecedente y no como agua superficial del 15/10.
- SMN trimestral: PDF del repositorio oficial de agosto–septiembre–octubre 2026. La fecha de descarga no cambia el período del producto.

La calibración predictiva de una cohorte independiente y la disponibilidad local de caminos, refugios y atención siguen pendientes de evidencia operacional. Las pruebas de software no los dan por resueltos. Ver `FICHA_DEL_PROYECTO.md`.

## Antecedente de verificación V4.6.0

Fecha de verificación: 14 de octubre de 2026.

## Comprobaciones realizadas

- 68 pruebas automatizadas aprobadas con `python -m unittest discover -s tests`.
- Compilación de `app.py`, `hidrologia.py`, `ctm.py` y `monitor.py` sin errores.
- Prueba de interfaz Streamlit: panel integrado, recomendaciones y curva oficial de Paso de los Libres; cero excepciones, cuatro métricas y cuatro tablas.
- Consulta nueva del canal SMN: 16 documentos de 16 leídos, sin fallos. El parser conserva polígonos, emisión, inicio, vencimiento, referencias y cancelaciones.
- Consulta hidrológica final: 26 fuentes vigentes, 22 estaciones y 184 entradas de previsión; sin errores de consulta. Esto no equivale a un pronóstico para cada localidad: varias entradas pertenecen a distintas fechas o bandas de una misma estación.
- Prueba del monitor en modo `dry_run`, con meteorología recuperada y un panorama oficial previamente recuperado. Corte de evaluación explícito: 14/10/2026 14:00 ART. No hubo envíos a Telegram ni cambios del registro remoto. La consulta meteorológica respondió para los 48 puntos; la cobertura fluvial y de represas siguió siendo parcial y se informó por separado.

Los conteos corresponden a estas comprobaciones. No son datos en vivo ni una certificación de continuidad de la aplicación publicada.

## Escenarios operativos comprobados

- Una altura por encima del umbral local de evacuación eleva el color a rojo aunque el índice meteorológico sea 27,9.
- Un pronóstico oficial que entra en las próximas 72 horas puede elevar la prioridad y generar un aviso sin cambio meteorológico.
- Una banda superior incierta por sí sola no se convierte en evacuación cierta.
- Las señales no cruzan entre cuencas sin conexión ni se propagan aguas arriba.
- Datos viejos, emitidos en el futuro, ausentes o ilegibles no certifican riesgo bajo.
- Un aviso SMN se aplica al punto que cubre su polígono; no a toda la provincia.
- La pérdida de una fuente crítica no cierra el episodio ni provoca duplicados al recuperarse.
- El registro durable se confirma antes del envío; un fallo de persistencia bloquea el envío.
- Mensajes largos respetan el límite de Telegram y mantienen HTML válido.
- Los lotes meteorológicos se reintentan una sola vez ante un fallo de conexión o timeout, conservando el orden de los puntos. HTTP inválido y JSON inválido no se reintentan.

## Alcance y comprobación después de actualizar

Esta versión integra pronósticos oficiales y reglas de vigilancia. Las pruebas de software no demuestran precisión predictiva, tasas de falsas alarmas ni tiempos de evacuación. Falta validación sobre más episodios independientes y cartografía local calibrada para barrios, rutas y refugios. No se extrapolan alturas a partir de caudales de represas ni se calcula una hora de llegada con una velocidad fija.

Después de cada actualización, comprobar un workflow `dry_run`, la versión V4.6.0 visible y la cobertura de las fuentes; luego verificar un control real y sus confirmaciones. Subir el código por sí solo no acredita vigilancia activa. La prueba sin envíos no acredita entrega en Telegram. La guía incluida explica el procedimiento.

Los documentos de `tests/fixtures/` son ejemplos oficiales fechados para pruebas. El monitor no los usa como respaldo de datos actuales.

