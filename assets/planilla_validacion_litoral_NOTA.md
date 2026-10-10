# Validación histórica del Litoral · V4.4.1

Revisión del 10/10/2026. La planilla reúne 15 antecedentes reales y cinco controles con ausencia documentada. Conserva las filas anteriores y agrega siete episodios de Chaco, Formosa y Misiones y un control de Resistencia. Ituzaingó 2023 continúa sin etiqueta positiva confirmada de anegamiento: lluvia intensa por sí sola no documenta inundación.

## Resultado de las nuevas reconstrucciones

Se conservaron seis consultas, sus parámetros, originales recibidos, coordenadas, cobertura y SHA-256 en `corridas_historicas/registro_descargas_norte.json`.

- Resistencia, 21–22/12/2025: índice completo 33,9, VERDE, con anegamiento urbano documentado. Es un falso negativo puntual frente a 35 y 55. Hay 72 valores reales de escorrentía igual a cero; no son relleno de ausencias.
- Pirané 07/05/2025, Posadas 02/05/2024 y 03/10/2026 y control Resistencia 01/08/2026: escorrentía antecedente 0/72 horas válidas. `indice` queda vacío.
- El Espinillo, parte 20/04/2026: error de descarga de la corrida y antecedentes sin escorrentía suficiente. `indice` queda vacío; además se desconoce el inicio del impacto.
- Villa Río Bermejito: anegamiento documentado, sin coordenada de cálculo ni corrida vinculada. El Soberbio 2023: crecida del Uruguay anterior al archivo HRES.

Los 408 mm de Resistencia publicados por SMN corresponden a diciembre completo. Los 108 mm publicados para Pirané no tienen ventana ni estación precisas. No se comparan esos totales con lluvia de 72 horas como si fueran la misma variable.

## Fechas, independencia y límites

La fecha del impacto, la publicación de la evidencia, la inicialización UTC y el corte supuesto +6 h se conservan por separado. Las fechas mensuales quedan como mes; una fecha de documentación de un evento en curso no fija su inicio y se rechaza para calibración temporal.

Open-Meteo documenta hindcasts IFS49R1 desde el 14/03/2024 hasta el 12/05/2026 06 UTC. El archivo y el corte reconstruido no acreditan hora de publicación, versión operativa ni disponibilidad original. Todas las consultas nuevas mantienen `tipo_emision=corte_reconstruido` y `disponibilidad_confirmada=no`.

Varias localidades o cortes del mismo episodio comparten `grupo_evento`. Vera conserva sus tres valores anteriores únicamente en `indice_parcial`: falta escorrentía suficiente para un índice completo. Las descargas anteriores de Gualeguaychú y Corrientes también quedan incompletas. No se reemplazan datos ausentes por cero ni por reanálisis.

La evidencia de los cinco controles tiene límites espaciales y temporales explícitos. Un control urbano no valida automáticamente lotes rurales. La aplicación exige cohortes de entrenamiento y prueba con ambas clases en las seis provincias para aplicar un ajuste regional; el conjunto actual no cumple esa condición.

## Dónde consultar y completar

En **Caso histórico y ficha → Calibración documentada y prueba temporal** se muestran casos, controles, emisiones, el contraste del norte y las cuatro planillas descargables. El conector permite consultar una corrida fija o aportar JSON conservados con coordenadas, zona horaria y unidades.

En **Territorio y rutas → Conectar registros oficiales de refugios y caminos provinciales/rurales** figuran 31 referencias nuevas y un acta descargable para confirmación local. Son antecedentes/directorios; no acreditan disponibilidad actual. El CSV territorial y el GeoJSON vial permiten aportar verificaciones con autoridad, evidencia, fecha, vigencia y ubicación/trazado.

La copia oficial de Vialidad Nacional contiene 154 tramos con partes del 22/09 al 09/10/2026. La app recalcula antigüedad por fila y permite actualizar la consulta. No acredita todos los caminos provinciales/rurales ni accesos a refugios.

Los catálogos sanitarios anteriores conservan sus originales. Una ubicación, teléfono o guardia publicados no prueban camas libres, atención efectiva, acceso ni stock sanitario. Las capacidades históricas u ocupantes no se convierten en cupos actuales.

La validación regional, las emisiones originales y las confirmaciones operativas siguen pendientes. Los resultados de pruebas locales están en `VERIFICACION_V4_4_1.md`; no equivalen a pruebas de servicios en la aplicación publicada.
