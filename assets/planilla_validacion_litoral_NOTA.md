# Planilla de validación histórica del Litoral

`planilla_validacion_litoral_completada.csv` reúne ocho antecedentes reales documentados y cuatro controles negativos con ausencia explícita de anegamiento/inundación.

## Qué quedó completado

- Identificación, fecha, etiqueta de impacto y fuente oficial de los episodios.
- `anegamiento=1` sólo donde la fuente documenta anegamiento, inundación o evacuación.
- Cuatro controles con `anegamiento=0`, autoridad, fecha del parte, localidad/alcance y observación textual de ausencia.
- Fuentes SMN, INA e INTA para el caso Concordia 2016.
- Tres corridas retrospectivas de Vera 2025: inicialización 00 UTC y corte supuesto +6 h, equivalente a 03 ART. La inicialización no es la hora de publicación.
- Dos consultas adicionales descargadas: Gualeguaychú (inicialización 19/03/2024) y Corrientes (07/10/2026). Sus corridas, antecedentes, parámetros, fuentes y huellas se conservan en `corridas_historicas/`.
- Se corrigió el tratamiento de escorrentía ausente. Vera y las dos nuevas consultas no tienen escorrentía antecedente suficiente: `indice` queda vacío. Los valores anteriores de Vera, 25.2, 27.2 y 44.3, figuran sólo como `indice_parcial`, fuera de calibración.

## Qué no se completó deliberadamente

- No se inventaron emisiones ni índices históricos para episodios sin una corrida archivada.
- Ituzaingó 2023 queda pendiente de confirmar el impacto local de anegamiento.
- Los tres registros de Vera pertenecen al mismo episodio y no deben contarse como tres episodios independientes.
- Los controles ya tienen etiqueta observada, pero todavía necesitan índices completos y emisiones documentadas para entrar en métricas temporales. La descarga de Corrientes no completa el índice ni certifica la disponibilidad original.
- La consulta Single Runs usa una única inicialización UTC. El futuro de una corrida no se mezcla con emisiones posteriores; los antecedentes siguen siendo retrospectivos.
- El archivo HRES Single Runs comienza el 14/03/2024. Los episodios anteriores y las fechas mensuales requieren otra fuente de pronósticos archivados y fechas puntuales.
- La ausencia del control 01 sólo cubre la mañana; el control 03 sólo cubre tres cruces. Los controles urbanos no validan por sí solos lotes rurales.
- Se corrigieron cinco filas que tenían una columna vacía extra. Las fechas mensuales se conservan como mes y no como un día inventado. Un boletín de impacto no se coloca en `fuente_pronostico`.

## Uso en la app V4.3.1

Caso histórico y ficha > Evaluar umbrales muestra controles y vinculaciones. El conector permite consultar corridas o aportar dos JSON con coordenadas, zona horaria y unidades.

`tipo_emision=corte_reconstruido` y `disponibilidad_confirmada=no` identifican reconstrucciones. Varias corridas comparten `grupo_evento` y no cuentan como episodios independientes. Las evaluaciones retrospectivas no habilitan umbrales operativos.

Territorio y rutas exige habilitación de hasta 24 h, acceso confirmado, capacidad de refugios y atención/horario/teléfono de salud. La plantilla de recursos está vacía: no se inventan capacidades ni guardias actuales. Los catálogos de Entre Ríos descargados el 10/10/2026 incluyen 212 registros legibles de centros de salud, una fila inconsistente excluida de la vista y 65 registros de hospitales. Los originales están en `catalogos_sanitarios/` y pueden descargarse en la app. La fecha del relevamiento no fue informada y la operación actual sigue sin confirmar.

Caso histórico y ficha > Pruebas de servicios realiza nuevas consultas desde el servidor y permite exportar el acta. Telegram valida HTTP, `ok` y `message_id`, sin incluir token ni chat en la exportación. La recepción y el mapa requieren comprobación manual.

Revisión: 10/10/2026. Se conservaron las dos nuevas consultas históricas y los dos catálogos sanitarios. Pasaron 30 pruebas internas; no se ejecutó la prueba en la web publicada ni se envió un mensaje real a Telegram. Sin índices completos, esas pruebas y las confirmaciones locales, la aplicación no queda certificada como plenamente operativa.

La planilla contiene la evidencia documental completa de los cuatro controles. La cohorte final de calibración automática sigue pendiente hasta tener índices/emisiones históricos suficientes y episodios independientes en entrenamiento y prueba.
