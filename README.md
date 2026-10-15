# Alerta Litoral Agro · V4.7.0

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

## Actualización

Subí juntos a la rama utilizada por Streamlit:

- `app.py`, `hidrologia.py`, `ctm.py`, `monitor.py` y `red_integrada.py` en la raíz.
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
python -m pip install -r requirements_monitor.txt
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

Un informe con más de 3 horas se señala como vencido. Las alturas y corridas locales de Zárate–Campana también se revisan con un máximo de 6 horas; la fecha de generación del informe no renueva una lectura antigua. No se suman caudales de estaciones sucesivas ni se calcula una hora de llegada desde las compuertas.

Para actualizar, incluí `red_integrada.py` en la raíz de ambos repositorios. ALA también requiere el `monitor.py` actualizado. Una prueba de consulta y exportación sin envíos es:

```bash
python monitor.py --dry-run --export-red red_integrada_revision.json
```

[Abrir Alerta Litoral Agro](https://alerta-litoral-agro.streamlit.app/) · [Abrir Alerta Zárate–Campana](https://alerta-zarate-campana.streamlit.app/)

