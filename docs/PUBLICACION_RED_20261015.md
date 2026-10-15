# Publicación de la red integrada · 15/10/2026

## Causa reproducida

La ejecución [38752632596](https://github.com/FedericoIeraci-cell/Alerta-Litoral/actions/runs/38752632596) terminó en éxito con `red_integrada_publicada: false`. El último informe compartido conservaba `generated_at: 2026-10-15T10:33:53.874365+00:00` (07:33 ART), anterior a la incorporación de las explicaciones estructuradas de los riesgos.

Una evaluación nueva sin Telegram, generada a las 13:53 ART, produjo **534.239 bytes** usando la serialización anterior. `GitHubStateStore.save_red_integrada` rechazó ese documento antes de contactar GitHub: «El resumen de la red integrada supera el tamaño permitido». El límite local era 500.000 bytes, mientras que ambos lectores ya admitían 2.000.000. El diagnóstico original era genérico: no permite reconstruir el tamaño exacto del intento de las 11:35.

Además, el estado se guardaba antes de intentar publicar y no volvía a persistirse; el error quedaba sólo en la salida final y no cambiaba el código de salida del proceso.

## Corrección

- Un único límite importado del lector (2 MB), JSON compacto y conservación completa de evidencias. El documento real de la reproducción ocupa aproximadamente 412 KB compacto.
- Comparación SHA antes de escribir y relectura de contenido/SHA después. Un HTTP exitoso con contenido anterior no acredita publicación. Los archivos mayores a 1 MB se verifican mediante la lectura de blobs ya soportada por el almacén.
- Estado inicial pendiente y estado final persistido, sin insertar el documento grande en `monitor_status.json`. Códigos de error, etapa y tamaño permiten distinguir tamaño, contrato, permisos, conflictos, conexión y verificación.
- Si falla la red, el proceso devuelve 1; la evaluación regional y el historial de envíos ya confirmados se conservan. Un error de Telegram mantiene su categoría y el error de red se registra por separado.
- `--dry-run` comprueba también la preparación y el tamaño publicable. `--publish-red-only` permite comprobar la escritura real sin secretos, envíos ni cambios al registro de Telegram. Nunca renueva artificialmente la fecha de un informe anterior.
- La validación en la rama principal publica en ese modo seguro después de las pruebas, bajo el mismo grupo de concurrencia del monitor horario.

## Regresiones

`tests/test_publicacion_red.py` simula el API de GitHub con toda conexión externa bloqueada. Cubre documentos mayores a 500 KB, dos actualizaciones sucesivas con SHA, documentos mayores a 1 MB, rechazo por encima de 2 MB, relectura antigua, 403/409/503, timeout, informe vencido y campos privados.

`tests/test_monitor_red_integrada.py` comprueba persistencia del resultado, fallo explícito, conservación del resultado de Telegram y ausencia de lecturas de secretos/SQLite/envíos en el modo de publicación. La suite existente conserva las pruebas de reserva durable previa al envío y deduplicación. Las llamadas de Telegram de las pruebas son simuladas.

Los lectores vigentes de ambos repositorios aceptaron el documento real de la reproducción: 58 puntos, contrato versión 1 e informe vigente. La publicación real y sus ejecuciones de validación se verifican en GitHub después de integrar esta corrección.
