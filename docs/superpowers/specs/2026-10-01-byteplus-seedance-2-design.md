# BytePlus Seedance 2.0 directo: diseño

## Objetivo

Incorporar una pestaña independiente de WaveSpeed que genere, edite y extienda vídeo con la API oficial BytePlus ModelArk y el único modelo `dreamina-seedance-2-0-260128`. Debe conservar la experiencia de campañas y cola secuencial de WaveSpeed sin compartir claves, tareas, historial, archivos de campaña ni salidas.

## Alcance

La pestaña se llamará **BytePlus · Seedance 2.0** y permitirá:

- guardar una API key de ModelArk independiente;
- crear, consultar, listar y cancelar/eliminar tareas de vídeo;
- usar texto, imagen inicial, imagen inicial/final y referencias omni de imagen, vídeo y audio;
- usar los modos de edición y extensión documentados para Seedance 2.0;
- configurar `generate_audio`, `seed`, `resolution`, `ratio`, `duration`, `watermark`, `return_last_frame`, `callback_url` y `execution_expires_after` sólo cuando sean compatibles con el modo elegido;
- encolar trabajos en campañas persistentes, ejecutarlos uno a uno, pausar, reanudar, reintentar fallidos y recuperar tareas remotas por ID;
- descargar el vídeo y, cuando exista, el último frame solicitado; y
- mostrar estado remoto, uso/tokens, respuesta sin modificar y errores de API.

No se incorporan Seedance 2.0 Fast, Mini o 2.5; tampoco se implementa la biblioteca privada de retratos de BytePlus ni verificación de personas. Esas capacidades requieren autorización/allowlist fuera del alcance de una pestaña local de generación.

## Arquitectura

Se agregará un paquete `app/byteplus/` con responsabilidades claras:

| Componente | Responsabilidad |
| --- | --- |
| `client.py` | Llamadas HTTP autenticadas a ModelArk, serialización de tareas y descarga de resultados. |
| `schemas.py` | Modelos de tarea, ítem de cola, campaña y estados remotos. |
| `validation.py` | Reglas locales por modo y referencias; no transmite campos incompatibles. |
| `queue.py` / `queue_runner.py` | Ejecución secuencial recuperable y persistencia de campañas. |
| `history.py` | Historial local independiente de tareas y resultados. |
| `app/ui/byteplus_queue.py` | Widget de campañas/cola, adaptado de la interfaz existente sin acoplarlo al proveedor WaveSpeed. |

`MainWindow` sólo construirá la pestaña, tomará un snapshot inmutable de sus controles y despachará trabajo en segundo plano. El cliente se dirigirá a `https://ark.ap-southeast.bytepluses.com/api/v3/contents/generations/tasks` con `Authorization: Bearer <ARK_API_KEY>`.

La reutilización se limitará a patrones neutros de la integración WaveSpeed: selector de ficheros, señales/eventos Qt, worker, tabla de historial y semántica de campañas. No se reutilizarán el cliente, payload, upload, clave ni almacenamiento de WaveSpeed.

## Interfaz y payload

La pestaña contendrá:

1. Conexión: API key BytePlus separada y el endpoint/modelo fijo visible.
2. Modo de creación: texto; primer frame; primer y último frame; referencias omni; editar vídeo; extender vídeo.
3. Prompt y listas ordenables de imagen, vídeo y audio. La UI asignará el rol correcto de cada elemento al construir `content`.
4. Parámetros: audio, semilla aleatoria/fija, resolución 480p/720p/1080p/4K, ratio `21:9`, `16:9`, `4:3`, `1:1`, `3:4`, `9:16` o `adaptive`, duración 4–15, watermark y último frame.
5. Avanzados: callback URL y expiración de ejecución; los campos no documentados o no compatibles no se mostrarán como opciones funcionales.
6. Acciones: generar, agregar a cola, consultar/listar y cancelar/eliminar por task ID.
7. Campañas, cola, historial y salida local de BytePlus.

La validación identifica antes de enviar: prompt inexistente cuando el modo lo requiere, número/tipo de referencias inválidos, audio sin imagen o vídeo de apoyo, combinaciones de primer/último frame incorrectas, duración/resolución/ratio fuera de rango y rutas inexistentes. Las referencias se subirán o transformarán al mecanismo oficial que determine la API de ModelArk; no se enviarán rutas locales como URL públicas.

Cada payload será específico de Seedance 2.0. `content` se compondrá de elementos `text`, `image_url`, `video_url` y `audio_url` con el rol correspondiente; edición/extensión incluirán exclusivamente los campos de tarea omni documentados. Las opciones habilitadas determinarán cuáles de `generate_audio`, `seed`, `resolution`, `ratio`, `duration`, `watermark`, `return_last_frame`, `callback_url` y `execution_expires_after` se envían.

## Datos y recuperación

Los datos de BytePlus estarán separados de los de WaveSpeed:

- key: `data/byteplus_api_key.txt`;
- campañas/cola: `data/byteplus_campaigns.json`;
- historial: `data/byteplus_history.json`;
- resultados: `data/byteplus_outputs/` por defecto.

Los archivos son estado local ignorado por Git. El código nunca leerá ni escribirá `wavespeed_*`, `flux_campaigns.json`, `data/app.db` o colas Sogni para ejecutar BytePlus.

El runner guardará la campaña después de cada transición. Antes de reenviar un ítem que posee `task_id`, consultará la tarea remota. Si terminó correctamente, descargará el resultado y lo marcará completado; si está en curso, continuará el sondeo; sólo volverá a enviar cuando la tarea no pueda recuperarse. Esto evita duplicar gasto por una interrupción local.

## Errores, seguridad y límites

La API key se guarda mediante el mismo mecanismo local de claves ya usado por la aplicación, con campo enmascarado y sin volcarla en historial, logs ni respuestas visibles. Los errores HTTP incluyen código/mensaje de BytePlus, pero se redaccionan encabezados y secretos.

La cola trata `queued` y `running` como recuperables; `succeeded` descarga y valida el artefacto; `failed`, `cancelled` y `expired` quedan visibles para reintento explícito. La cancelación local detiene el sondeo; cancelar/eliminar remoto sólo sucede mediante el botón dedicado y task ID confirmado por el usuario.

Los resultados de task ID se consideran efímeros según la retención del proveedor, por lo que se persisten URL, metadatos, uso y archivo descargado localmente cuando la tarea termina.

## Pruebas y aceptación

Se seguirá TDD con pruebas unitarias y de UI que primero fallen y luego cubran:

- serialización correcta de los payloads de cada modo y ausencia de campos incompatibles;
- validación de combinaciones, límites y rutas de referencias;
- autenticación, creación, consulta, listado, cancelación/eliminación y normalización de respuestas del cliente mediante transporte falso;
- recuperación de `task_id` sin nuevo envío, transiciones de cola y persistencia aislada;
- controles de la pestaña y su habilitación por modo;
- descarga del vídeo/último frame y registro de historial sin secretos.

La verificación final ejecutará las pruebas focalizadas y la suite disponible con `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`. Una prueba de API real requerirá una API key de BytePlus provista por la persona usuaria y se distinguirá de la validación local; no se realizará una generación facturable sin esa clave y confirmación explícita.

## Decisiones

- Se elige un módulo de proveedor aislado, no clonar WaveSpeed ni refactorizar todo su subsistema.
- Se fija el modelo premium exacto solicitado, sin selector de variantes.
- Persistencia, claves, historial y outputs tienen propietarios separados por proveedor.
- Sólo se exponen capacidades documentadas y aplicables a Seedance 2.0; la UI evita fabricar toggles no soportados.
