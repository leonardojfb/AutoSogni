# Cola Sogni visible por campaña

## Objetivo

Corregir la acción ambigua `Agregar a la cola` de la pestaña Campaign. Debe
agregar trabajos a la campaña Sogni actualmente seleccionada y dejar una lista
visible, persistente y operable de esos trabajos, con el patrón visual de las
colas WaveSpeed e Imágenes.

## Comportamiento

- `Crear campaña nueva` conserva su única responsabilidad: crear una campaña
  nueva a partir de los frames y prompts indicados.
- `Agregar jobs a campaña seleccionada` exige una campaña seleccionada y añade
  a ella el conjunto de trabajos construido desde los frames y prompts actuales.
  No crea una nueva campaña.
- El alta asigna índices e idempotency keys nuevos, y no modifica ni duplica
  jobs existentes de la campaña.
- La pestaña Campaign muestra una tabla `Cola Sogni` para la campaña elegida.
  Cada fila expone orden, frame, prompt, modelo, estado, intentos y la acción
  de reintento cuando corresponda.
- Al cambiar la campaña superior, la tabla y el resumen se refrescan para esa
  campaña. La pestaña Jobs continúa disponible como vista detallada existente.
- Las campañas y jobs ya existentes, incluidos los jobs PENDING creados por el
  botón antiguo, se preservan sin mutación.

## Arquitectura

El repositorio SQLite recibe una operación explícita para añadir el producto
frame × prompt a una campaña existente. La operación obtiene el siguiente
`order_index` de esa campaña y genera claves de idempotencia que no colisionan
con las que ya contiene. El gestor de campañas ofrece esa operación a la UI.

La UI reutiliza el estado seleccionado `current_campaign_id`, el refresco de
tablas y el modelo de job actual. La lista nueva es un widget de cola dedicado
en la pestaña Campaign; sus acciones llaman a las rutas de reintento ya
existentes. No hay migración de datos ni cambios al trabajador Sogni.

## Errores y límites

- Sin campaña seleccionada, la acción muestra un error y no escribe en SQLite.
- Si los inputs no permiten construir frames o prompts, se presenta el error
  existente y no se modifica la campaña.
- La lista no inicia trabajos: solo muestra y permite administrar la cola.
  `Iniciar cola / Reanudar` conserva ese control explícito.

## Pruebas

- Una prueba de repositorio/gestor verifica que añadir trabajos mantiene el id
  de campaña, conserva los jobs anteriores y crea índices y claves nuevos.
- Una prueba de interfaz verifica que el botón no crea una campaña y que la
  lista visible contiene los jobs añadidos de la campaña seleccionada.
- La suite completa mantiene la compatibilidad de creación, selección y
  reintento de campañas existentes.
