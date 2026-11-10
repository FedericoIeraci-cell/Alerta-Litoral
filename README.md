# Alerta Litoral Agro · V4.11.0

[Abrir A.L.A.](https://alerta-litoral-agro.streamlit.app/)

La vista pública prioriza pocos datos con origen, unidad y fechas comprobables. Incluye 48 referencias del Litoral argentino. **Azul indica disponibilidad de datos; gris indica que no se obtuvieron datos válidos. Ninguno es un nivel de riesgo o una confirmación de seguridad.**

## Qué muestra la página

- Lluvia prevista para 24 y 72 horas del modelo ECMWF IFS 0,25° (~25 km), seleccionado explícitamente como `ecmwf_ifs025` en Open-Meteo. Cada ventana informa inicio, fin y consulta. La API no informa la emisión de la corrida.
- Alturas del río únicamente cuando tienen estación vinculada, unidad en metros, fuente oficial y hora de medición publicada.
- Documentos CAP del SMN aplicables al punto, con emisión y plazo, sólo cuando se pudo comprobar el canal completo. Una consulta sin documentos aplicables no descarta otros peligros.
- Estado de las fuentes y fecha del último control automático. El JSON descargable contiene exclusivamente los datos admitidos por esta vista.

El mapa aparece primero, conserva zoom, búsqueda, filtros, vista de lista y fichas completas. La geolocalización selecciona una referencia pública; no acredita una medición en el domicilio o lote. Ver [detalle de ubicación](docs/GEOLOCALIZACION.md).

## Criterios de validez

La lluvia requiere consulta con fecha, hora y zona de hasta tres horas, identidad del modelo, coordenadas y zona de respuesta explícitas, unidades compatibles y todas las horas contiguas de la ventana. Cada horizonte acredita su propia cobertura. Los valores fuera del dominio de la magnitud se retiran; no se convierten en cero. Un cero se publica sólo si fue recibido como valor válido.

La observación fluvial requiere hora física publicada, zona, fuente, unidad, identidad de estación y antigüedad máxima de seis horas. La fecha de registro de un parte diario no acredita hora de medición. Los límites de uso son reglas conservadoras de A.L.A., no garantías de exactitud de los organismos. Las escalas de INA y CTM no se combinan por coincidencia de nombre.

No se deduce un dato vigente de la hora de generación del informe ni de una nueva consulta. Las fechas futuras, valores imposibles, horarios duplicados contradictorios y ventanas incompletas se rechazan.

## Elementos retirados de la vista pública

Se retiraron el índice experimental de inundación y sus acciones por color, las herramientas de carga y ajuste manual, las copias antiguas como actualidad, los recursos y recorridos sin confirmación operativa y las capas complementarias. La descarga exitosa de una imagen satelital no prueba cobertura local: por eso no se utiliza como confirmación de agua o suelo seco en esta vista.

Las funciones de investigación anteriores permanecen archivadas en el código y en el historial de Git; `render_app` utiliza únicamente `pagina_confiable.mostrar_pagina`. Los procesos de monitorización mantienen contratos anteriores por compatibilidad, con los controles de precisión y vigencia reforzados. No deben confundirse con una predicción validada de inundación.

## Actualización y continuidad

La sesión abierta se revisa cada cinco minutos. Reutiliza la captura meteorológica pública vigente de `ala-monitor-state/meteorologia_ecmwf.json`, o intenta la consulta directa identificada si la captura no pasa la validación. Cada dato conserva su fecha original.

GitHub Actions programa controles y publicaciones, pero una programación no acredita que hayan ocurrido. El último `monitor_status.json` se comprueba por separado de la captura de datos; si está vencido o no puede verificarse, la página informa que la vigilancia automática no está confirmada. Una publicación de CI no rejuvenece el control normal ni confirma entrega de Telegram.

La revisión del 10/11/2026 detectó ejecuciones programadas espaciadas aunque los workflows estaban activos. No se atribuye una causa interna sin evidencia ni se promete vigilancia continua.

## Ejecución y verificación

```sh
python -m pip install -r requirements.txt
streamlit run app.py
```

```sh
python -m pip install -r requirements_test.txt
python -m unittest discover -s tests -v
node tests/test_ubicacion_js.cjs
node --test tests/test_viirs_js.cjs
python -c 'from mapa_litoral import _SCRIPT_MAPA; print(_SCRIPT_MAPA)' | node tests/test_mapa_movil_js.cjs
git diff --check
```

Las pruebas usan escenarios identificados y bloquean los envíos. Los datos sintéticos no se publican como mediciones. El ensayo con fuentes reales puede ejecutarse mediante `python monitor.py --dry-run --export-red /tmp/ala-red.json`, sin credenciales de Telegram ni de escritura remota.

Conservar los archivos de `ala-monitor-state` y los secretos existentes. No se incluyen credenciales en el código ni en el JSON público. [Guía del monitor](GUIA_MONITOR.md) y [validación histórica del prototipo](VALIDACION.md) describen también funciones anteriores; no amplían el alcance de la vista mínima actual.
