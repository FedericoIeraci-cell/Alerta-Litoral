# Vigilancia automática de Alerta Litoral Agro · V4.6.0

El panel y el monitor horario usan la misma integración de meteorología, hidrología, represas y avisos oficiales. GitHub Actions ejecuta el monitor aunque nadie abra la página. Abrir o actualizar Streamlit no envía alertas automáticas.

Desde el 22/10/2026 el proceso ALA evalúa y publica únicamente el Litoral. Las consultas meteorológicas de Zárate–Campana y su publicación se ejecutan en el repositorio de ZC, en `zc-datos-state`, con su propio workflow y registro. Un fallo ZC no cambia el resultado del control ALA. El SQLite y la deduplicación de ALA no se migran ni reinician.

## 1. Actualizar el paquete completo

Subí juntos estos archivos a la rama principal de tu repositorio, la misma que utiliza Streamlit:

| Archivo | Ubicación y propósito |
| --- | --- |
| `app.py` | Raíz; panel e integración de riesgos |
| `hidrologia.py` | Raíz; INA, Yacyretá y vinculación por cuenca/estación |
| `ctm.py` | Raíz; documentos oficiales de Salto Grande |
| `monitor.py` | Raíz; ejecución automática y estado persistente |
| `requirements.txt` | Raíz; dependencias de Streamlit |
| `requirements_monitor.txt` | Raíz; dependencias del monitor |
| `.github/workflows/ala-monitor.yml` | Programación de GitHub Actions |

También podés subir `README.md`, esta guía y la carpeta `tests/`. Conservá los demás archivos del proyecto, los Secrets existentes y la rama `ala-monitor-state`. No borres `telegram_alerts.sqlite3` ni `monitor_status.json` de esa rama: mantienen los episodios y las confirmaciones entre ejecuciones. La actualización admite el informe anterior sin reiniciar el registro.

El YAML debe estar dentro de `.github/workflows/` en la rama predeterminada. Subirlo a la raíz no activa el horario.

`verificar.py` y `.github/workflows/alerta.yml` mantienen la entrada del monitor antiguo como prueba manual sin envíos. Su programación separada fue retirada: la vigilancia y el registro de avisos se concentran en `ala-monitor.yml`. `.github/workflows/ala-validacion.yml` ejecuta pruebas y una consulta sin mensajes ante cambios de código.

## 2. Secrets de GitHub y Streamlit

En el repositorio abrí **Settings → Secrets and variables → Actions**. Conservá o configurá:

| Nombre | Valor del Secret de GitHub |
| --- | --- |
| `TELEGRAM_BOT_TOKEN` | Solo el token entregado por BotFather, sin comillas ni `TELEGRAM_BOT_TOKEN =` |
| `TELEGRAM_CHAT_ID` | Solo el identificador del chat o `@nombre_del_canal` |

GitHub Actions y Streamlit tienen Secrets independientes. En Streamlit se utiliza TOML; en los campos de GitHub se pega únicamente el valor. Para publicar en un canal, el bot necesita permisos para hacerlo.

El workflow obtiene `GITHUB_TOKEN` de Actions y requiere `contents: write` para guardar el estado durable. Mantiene exclusión entre ejecuciones manuales y programadas.

Para mostrar el informe en Streamlit cuando el repositorio es público, conservá esta configuración con tus datos reales:

```toml
ALA_MONITOR_REPOSITORY = "tu_usuario/tu_repositorio"
```

El panel público actual no autentica la lectura de un repositorio privado; en ese caso comprobá los controles directamente en Actions.

## 3. Comprobar datos y activar el monitor

1. En **Actions → Alerta Litoral Agro - vigilancia → Run workflow**, marcá **Probar los datos sin enviar avisos ni modificar el registro**. Esta prueba consulta las fuentes y devuelve colores y cobertura por fuente; no reserva ni envía eventos.
2. Revisá por separado meteorología, río observado, río previsto, represas y avisos SMN. Un semáforo interpretable no significa que todas las fuentes estén completas.
3. Ejecutá nuevamente con la opción desmarcada para permitir avisos. Revisá el resultado del workflow, el informe y las confirmaciones del historial.

La programación incluida es `7 * * * *`: aproximadamente al minuto 7 de cada hora, todos los días. GitHub puede demorar una ejecución; la fecha del último control permite comprobar la continuidad real.

La primera evaluación establece las referencias de las localidades nuevas. Un rojo exclusivamente meteorológico ya presente no se presenta como un episodio recién iniciado. Una situación crítica fluvial o un aviso SMN severo vigente puede generar un aviso identificado como primera evaluación. Actualizar el paquete conserva las referencias anteriores.

Cero avisos puede ser normal por las reglas de publicación. No significa que no exista riesgo. El envío se considera confirmado solo cuando Bot API devuelve una respuesta válida y un `message_id`; esa confirmación no demuestra que una persona haya leído el mensaje.

## 4. Qué fuentes se integran

| Fuente | Uso automático |
| --- | --- |
| ECMWF mediante Open-Meteo | Lluvia antecedente y prevista, humedad y escorrentía modeladas; índice meteorológico |
| INA | Alturas y umbrales de estaciones, reportes y puntos/ventanas de pronóstico publicados |
| EBY · Yacyretá | Estado del Paraná, aportes/erogación y previsiones locales que el boletín permite identificar |
| CTM · Salto Grande | Niveles observados, embalse, erogación y previsiones expresamente publicadas |
| SMN · CAP | Avisos oficiales vigentes o previstos cuyos polígonos incluyen el punto monitoreado |

Las observaciones y el pronóstico se muestran separados con su estación, fecha, unidad, fuente y plazo. Los datos de una represa no se convierten en una profundidad de inundación ni en una hora de llegada por una velocidad fija. Las cotas de embalse no se comparan con la escala de otro puerto.

El índice 0–100 conserva su fórmula meteorológica y umbrales de referencia 35/55. El color integrado puede elevarse por evidencia fluvial o por un aviso SMN aunque el índice no aumente. Las conexiones aguas arriba indican dónde vigilar aportes; la anticipación local utiliza puntos y ventanas oficiales disponibles, sin inventar un pronóstico donde falta.

Los denominadores de cobertura fluvial y de represas corresponden a las localidades a las que se aplican esas fuentes. Una fuente vencida, ilegible o ausente queda señalada. En un nodo fluvial, si faltan observación o previsión válidas, un verde meteorológico no acredita riesgo bajo y se muestra `SIN DATOS`; otra evidencia vigente puede mantener amarillo o rojo. El monitor informa `datos_parciales` y puede evaluar un evento severo respaldado por una fuente vigente.

## 5. Reglas de publicación y continuidad

- **Alerta nueva:** transición conocida a rojo integrado; contempla la primera evaluación crítica señalada anteriormente.
- **Actualización meteorológica:** permanece rojo y el índice cambia al menos 10 puntos, con tres horas entre avisos; un aumento de 20 puntos permite avisar sin esperar ese intervalo.
- **Cambio severo de otra fuente:** un empeoramiento fluvial, un cruce observado posterior a su previsión o un aviso SMN severo nuevo puede generar un evento aunque el índice meteorológico permanezca estable. Cambiar solo la hora de consulta no genera un aviso.
- **Bajada:** requiere controles válidos separados por al menos 15 minutos; con el cron horario normalmente se confirma en el control siguiente. Una fuente ausente no cierra por sí sola el episodio.
- **Persistencia:** la reserva se confirma en `ala-monitor-state` antes de llamar a Telegram. Si esa escritura falla, se bloquea el envío.
- **Respuesta rechazada:** puede reintentarse el mismo evento tras al menos 15 minutos, si la evaluación actual sigue siendo crítica.
- **Respuesta incierta:** no se repite automáticamente el mismo evento. Revisá el canal; un cambio material posterior puede generar otro identificador.

Los avisos incluyen localidad, observación y previsión fluvial, umbral de la estación, plazo, fuentes y acciones. Las capas particulares de suelo, satélite o relieve de una sesión no se trasladan al monitor público.

## 6. Preparar y coordinar acciones

Ante un umbral fluvial de evacuación observado o previsto, la herramienta propone **preparar y coordinar la evacuación**: verificar el boletín y su plazo, contactar a la autoridad y confirmar un destino y un acceso seguro. La etiqueta se basa en la estación de referencia y no constituye una orden oficial.

Un punto monitoreado no describe cada barrio o lote. Los destinos, refugios, rutas, capacidad y hora de traslado requieren validación local. La previsión estacional de El Niño aporta contexto y no fija una fecha de inundación.

## 7. Pruebas locales sin envíos

Desde la raíz del proyecto, con Python 3.12 y las dependencias del monitor instaladas:

```bash
python -m pip install -r requirements_monitor.txt
python -m unittest discover -s tests -v
```

Las pruebas utilizan datos de prueba y sustituyen los envíos; no requieren pytest ni publican mensajes. Para comprobar las fuentes actuales sin modificar el registro, usá la opción `dry_run` del workflow o:

```bash
python monitor.py --dry-run
```

Esta última consulta sí utiliza la red. Un ensayo local no activa la programación de GitHub.

## 8. Si falla un control

| Resultado | Comprobación |
| --- | --- |
| Falta integración automática | Subí juntos `app.py`, `hidrologia.py`, `ctm.py` y `monitor.py`, además de ambos requirements |
| Cobertura parcial | Revisá fuente, fecha, estación, documento y próximo control; no reemplaces faltantes por cero |
| Telegram rechazado | Revisá el detalle del historial y los Secrets de Actions; `Not Found` puede indicar un token mal pegado o un endpoint inválido |
| Envío sin confirmación | Contrastá ID del evento e historial con el canal; no supongas entrega |
| Fallo del registro | Revisá permisos `contents: write`, la rama de estado y su última versión válida |
| Informe antiguo | Comprobá Actions, que el workflow esté habilitado y que el YAML esté en la rama predeterminada |

Para pausar los avisos, deshabilitá el workflow en Actions. Conservá la rama de estado al reanudar. El paquete prepara la implementación; solo una ejecución real y un informe reciente permiten confirmar vigilancia activa.
