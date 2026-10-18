# Consulta automática de ríos y represas · 18/10/2026

El refresco de la página y la fecha de una medición son cosas distintas. El nuevo
panel consulta directamente las fuentes públicas cada cinco minutos mientras la
página está abierta y actualiza la presentación cada minuto mediante un fragmento
Streamlit. Ambas aplicaciones ejecutan los mismos módulos. No reejecuta el cálculo
meteorológico ni el circuito de Telegram. El informe compartido continúa siendo
publicado y verificado por el monitor cada hora (sujeto a las demoras de Actions).

## Fuentes y alcance

- INA WFS `public2:ultimas_alturas_con_timeseries`: 24 series explícitas de alturas
  de Prefectura, con identidad de estación, red y variable verificadas. Una sola
  consulta obtiene todas las lecturas. Conserva la fecha registrada por INA;
  algunas series son diarias y esa fecha no prueba telemetría instantánea.
- Zárate RHN 29437: reutiliza el adaptador estricto `hidrometria_local.py`, con
  metadatos, estación, variable, unidad y cero de escala documentados. No sustituye
  las escalas de los puertos ni traslada sus umbrales.
- CTM Salto Grande: `https://www.saltogrande.org/datos_horarios.php`. Se extraen
  fecha/hora del cuadro, turbinado, vertido, cota del embalse y restitución.
  Descarga total = turbinado + vertido de la misma central y hora. No se suman
  estaciones sucesivas ni se convierte caudal en altura de un puerto.
- Yacyretá: se conserva el boletín oficial del informe compartido, claramente
  identificado como boletín, con fecha, fuente, vigencia y distinción entre aporte
  previsto y descarga observada. No hay telemetría pública en vivo verificada
  incorporada para esta represa.

## Vigencia y fallos

La consulta reciente no cambia la fecha de la medición. En el panel, CTM de más de
2 horas y lecturas de ríos de más de 6 horas se marcan atrasadas. Una fecha futura,
unidad incompatible, identidad ambigua o lectura no finita no se acepta como actual.
Las consultas usan HTTPS, orígenes limitados, límite de tamaño y tiempos máximos.

La caché común por proceso limita consultas a una cada cinco minutos por fuente,
incluso cuando falla. Ante una caída conserva la última lectura con aviso explícito
sin declararla vigente. Una respuesta anterior no reemplaza una medición más nueva.
La vigencia se recalcula cada minuto aun con la respuesta cacheada. Los boletines
mantienen su cadencia propia; no se etiquetan como datos en vivo.

## Publicación y seguridad

`hidrologia.obtener_panorama` incorpora `salto_grande_horario` como evidencia
independiente de los partes y previsiones existentes. La normalización del informe
preserva unidades, hora, fuente y caducidad de dos horas. No se cambian umbrales,
destinatarios, reservas durables, deduplicación ni reglas de envío de Telegram.

Las pruebas usan capturas oficiales del 18/10 y respuestas sintéticas, bloquean
HTTP real y cubren caché, recuperación, fechas, escalas, unidades, interfaz
Streamlit y contrato compartido. La comprobación real de fuentes sólo hace GET;
la publicación de integración se ejecuta con `--publish-red-only`, sin credenciales
de Telegram.
