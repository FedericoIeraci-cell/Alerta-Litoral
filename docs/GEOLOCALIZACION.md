# Referencia cercana mediante ubicación del navegador

Incorporación prevista en ALA V4.9.0 y Zárate–Campana 1.8.0. Esta función sólo elige qué referencia existente se consulta: no cambia nodos meteorológicos, estaciones INA, umbrales, índices, alertas ni el monitor.

## Comportamiento público

El botón **Usar mi ubicación** se ubica junto al selector manual. En la primera consulta, el navegador solicita permiso. Si su Permissions API ya informa permiso concedido, el componente puede realizar una única consulta automática durante la sesión. La ausencia de esa API mantiene disponible el botón. Denegar permiso no activa reintentos automáticos.

Se utiliza `getCurrentPosition`, con una única respuesta por solicitud, sin seguimiento continuo. El selector manual permanece disponible durante la espera. Cada cambio manual incrementa una revisión de selección: el navegador descarta una respuesta pendiente para una revisión anterior y el backend también la rechaza. Un clic nuevo en el botón puede volver a elegir una referencia. Las respuestas ya consumidas no vuelven a aplicarse al recalcular la página.

La selección no confirma que la persona esté en esa localidad, provincia o partido. Se informa **referencia cercana** y distancia aproximada en línea recta. La información describe una celda o estación de referencia y no una medición en la calle, isla, vivienda o lote del usuario. La cercanía tampoco demuestra un acceso transitable ni la equivalencia de escalas fluviales a ambos lados de un río.

## Precisión y alcance de la ayuda

El catálogo reúne las 48 referencias ALA y las 10 referencias Zárate–Campana ya publicadas. Sus coordenadas son públicas. El navegador ordena por distancia geodésica aproximada sobre una esfera, utilizando el mismo catálogo que recibe desde la app.

Para elegir automáticamente se limita la búsqueda a **50 km de una referencia ALA** y **15 km de una referencia Zárate–Campana**. Son guardas de presentación acordadas para un catálogo regional disperso y uno local concentrado; no definen cobertura meteorológica, jurisdicciones, zonas inundables ni precisión del modelo.

También se exige precisión reportada por el navegador de hasta **2.000 m**. Aunque cumpla ese máximo, no se elige si la diferencia entre las dos referencias más cercanas es menor o igual a dos veces esa precisión. Esto evita forzar una distinción entre puntos como centro y costanera cuando la ubicación es demasiado imprecisa. La incertidumbre reportada no es una garantía de exactitud del dispositivo.

La distancia a la referencia más la incertidumbre debe quedar dentro del alcance de búsqueda. Si el margen cruza ese límite, se informa precisión insuficiente; sólo si incluso el extremo más cercano está fuera se informa que la referencia es demasiado lejana para elegirla como local. En ambos casos se conserva la selección previa y se ofrece el selector manual. No se afirma que el usuario está fuera de un límite administrativo.

Si la referencia pública más cercana pertenece a la otra aplicación y cumple sus mismas guardas, se ofrece un enlace voluntario, sin redirección. El enlace usa la dirección fija de esa app y únicamente `?referencia=<identificador-público>`. La app de destino valida que el ID pertenezca a su catálogo y preselecciona una vez; no solicita ubicación para seguir ese enlace.

## Datos y privacidad

El componente local calcula con las coordenadas del dispositivo dentro del navegador. Nunca envía latitud, longitud, precisión, altitud, velocidad, marcas temporales del dispositivo, objetos `Position` ni mensajes libres de error a Python. Tampoco los escribe en URLs, cookies, almacenamiento web, registros o archivos. No consulta servicios de geocodificación ni carga scripts de CDN. La política del documento bloquea conexiones `fetch`/XHR desde el componente.

El navegador y el sistema operativo pueden utilizar sus propios servicios para estimar la ubicación; esta función no sustituye sus políticas ni elimina su gestión de permisos. El usuario controla ese permiso desde el navegador.

El mensaje al backend contiene un estado de una lista cerrada y, cuando corresponde, el ID de una referencia pública y una distancia redondeada a kilómetros enteros. Se agregan un identificador de evento aleatorio, un identificador de sesión y la revisión de selección para impedir repeticiones y respuestas tardías. Python valida el conjunto exacto de campos, el ID, la aplicación y la distancia; no admite coordenadas ni URLs aportadas por el cliente. Sólo esos datos de la ayuda permanecen en `session_state`; el historial meteorológico y el monitor no los utilizan.

## Compatibilidad y permiso del marco

Se utiliza `streamlit.components.v1.declare_component` con archivos locales y carga perezosa. V1 permite conservar `streamlit>=1.40,<2`, el requisito existente en ambas apps. Importar `ubicacion_local.py` no importa Streamlit ni realiza consultas, por lo que la importación sin interfaz del monitor sigue disponible.

Se revisaron la documentación oficial y las fuentes de Streamlit **1.40.0** y **1.66.0**: el componente V1 usa un `iframe` con la política de características predeterminada que incluye `geolocation`, y mantiene el protocolo de componentes de versión 1. La página contenedora y el navegador aún pueden bloquear ese permiso; el componente respeta ese bloqueo. La app debe abrirse mediante HTTPS. En una página externa que incruste la app, todos los marcos superiores deben permitir la capacidad; se ofrece abrir la app directamente o elegir manualmente cuando no sea posible.

Fuentes de implementación consultadas:

- [Declaración de componentes V1 en Streamlit 1.40](https://docs.streamlit.io/1.40.0/develop/api-reference/custom-components/st.components.v1.declare_component).
- [Introducción oficial a componentes V1](https://docs.streamlit.io/develop/concepts/custom-components/components-v1/intro).
- [Política de iframe, Streamlit 1.40](https://github.com/streamlit/streamlit/blob/1.40.0/frontend/lib/src/util/IFrameUtil.ts) y [Streamlit 1.66](https://github.com/streamlit/streamlit/blob/1.66.0/frontend/lib/src/util/IFrameUtil.ts).
- [Aplicación de la política al componente V1](https://github.com/streamlit/streamlit/blob/1.40.0/frontend/lib/src/components/widgets/CustomComponent/ComponentInstance.tsx).
- [Protocolo V1 oficial](https://github.com/streamlit/streamlit/blob/1.40.0/component-lib/src/streamlit.ts).
- [MDN: `getCurrentPosition`](https://developer.mozilla.org/en-US/docs/Web/API/Geolocation/getCurrentPosition) y [política `geolocation`](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Permissions-Policy/geolocation).

## Pruebas reproducibles

Las pruebas utilizan exclusivamente coordenadas sintéticas de ciudades o casos de borde, sin solicitar ubicación real ni conceder permisos del navegador.

```bash
python -m unittest discover -s tests -p 'test_ubicacion*.py' -v
node tests/test_ubicacion_js.cjs
```

La prueba JavaScript ejecuta el cálculo y el componente reales en un entorno simulado: permisos previos, clic inicial, errores, bloqueo de política, espera agotada, cancelación tras selección manual y descarte de una respuesta tardía. Inspecciona todos los mensajes para asegurar que sólo sale el contrato previsto. Python prueba la validación y deduplicación; AppTest verifica el selector Streamlit real, las dos redes, la prioridad de una elección manual y los enlaces por ID público. Los workflows existentes agregan estos controles de verificación; no activan un monitor ZC ni envíos.

La inspección final del marco y del diseño en las aplicaciones publicadas se realiza aparte, sin solicitar ubicación real. Este documento no modifica ni sustituye los cortes temporales de las auditorías de datos anteriores.
