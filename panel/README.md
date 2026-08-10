# panel

Panel v0: página web mínima que habla MQTT directo desde el navegador (sin backend propio) contra el mismo Mosquitto que usa `comms_agent`. Es el punto 4 del plan del CLAUDE.md, en su versión más simple: mapa con posición en vivo, estado, botones.

Este README describe el estado actual y se va a ir actualizando con cada push.

## Qué hace hoy

Un solo archivo autocontenido (`index.html`), sin backend ni build — se abre directo como `file://` en el navegador. Usa la librería `mqtt.js` (bundleada localmente en `mqtt.min.js`, no depende de internet) para hablar **MQTT sobre WebSocket** directo con Mosquitto (opción elegida en vez de escribir un backend propio — ver discusión en el historial del proyecto).

- **Mapa**: `depot.png`, convertido una sola vez desde `src/andino_gz/andino_gz/maps/depot/depot.pgm` (formato que el navegador no puede mostrar directo). Es un archivo estático — no viaja por MQTT, se carga una vez.
- **Posición en vivo**: se suscribe a `patrol/default/andino/position` (JSON `{x, y, yaw}`, agregado a `comms_agent` para esto — ver su README) y dibuja un punto sobre el mapa, convirtiendo metros del frame `map` a píxeles de la imagen usando la resolución/origen de `depot.yaml` (**hardcodeado en el HTML** — si cambia el mapa, hay que actualizar `MAP_RESOLUTION`/`MAP_ORIGIN_X`/`MAP_ORIGIN_Y`/`MAP_IMG_W`/`MAP_IMG_H` a mano).
- **Estado**: se suscribe a `patrol/default/andino/state`, lo muestra con un color distinto por estado.
- **Heartbeat**: si no llega ninguno en más de 15s, muestra una advertencia ("robot caído?").
- **Botones**: Iniciar / Pausar / Reanudar / Retornar a base / Limpiar falla — publican a `patrol/default/andino/cmd`. Se habilitan/deshabilitan según el estado actual, siguiendo la misma tabla de transiciones documentada en `patrol_fsm/README.md`.

**Deliberadamente sin `manual_start`/`manual_stop`**: activar modo manual desde el panel sin una forma real de manejar (eso es WebRTC, todavía no existe) dejaría al robot sin nadie manejándolo — un botón roto. Se suman cuando exista la teleoperación real.

## Cómo correrlo

Requiere: `comms_agent` corriendo (ver su README), y Mosquitto con un listener de WebSocket habilitado (ver más abajo, no viene así por default).

```bash
xdg-open /home/tomashut/humble_robochi/panel/index.html
```
O abrir `file:///home/tomashut/humble_robochi/panel/index.html` directo desde el navegador.

### Habilitar el listener de WebSocket en Mosquitto (una sola vez)

Por default Mosquitto solo habla MQTT "crudo" (TCP), que un navegador no puede usar. Hay que agregar un listener aparte:

```bash
sudo nano /etc/mosquitto/conf.d/websockets.conf
```
Contenido:
```
listener 1883 127.0.0.1
protocol mqtt
allow_anonymous true

listener 9001 127.0.0.1
protocol websockets
allow_anonymous true
```
```bash
sudo systemctl restart mosquitto
```

**`allow_anonymous true` es necesario**: apenas definís un `listener` a mano, Mosquitto deja de aceptar conexiones anónimas por default (deja su "modo local automático"). Sin esta línea, tanto `comms_agent` como el panel se quedan sin poder conectarse (`Connection Refused: not authorised`, rc=5). Es una config de desarrollo — no hay usuario/contraseña real, ver "Qué falta".

## Comportamientos no obvios / bugs conocidos

- **`allow_anonymous` es por-listener, no global**, aunque se ponga una sola vez arriba de todo en el archivo — mismo tipo de sorpresa que `bind_address` (ver más abajo). En la práctica alcanzó con ponerlo una vez al principio del archivo y reiniciar bien el servicio (`systemctl restart`, no alcanza con `reset-failed` solo) — pero si en algún momento deja de conectar con `rc=5`, repetir `allow_anonymous true` dentro de cada bloque `listener` es el primer lugar donde mirar.
- **Bug sin resolver, documentado en `comms_agent/README.md`**: el listener 9001 (WebSocket) no respeta `bind_address`/`listener <puerto> 127.0.0.1` — queda escuchando en todas las interfaces de red, no solo `localhost`, a diferencia del 1883. Parece limitación de Mosquitto 2.0.11. Riesgo acotado a la red local, no a internet — aceptado por ahora en esta etapa de prototipo.
- **`ros2 topic echo`/`comms_agent` pueden no ver una posición hasta que el robot se mueve**: AMCL no republica `/amcl_pose` a un ritmo fijo si el robot está quieto, solo en updates — si `comms_agent` arrancó después de setear la pose inicial en RViz, se puede perder esa primera publicación. Se resuelve solo apenas el robot arranca a moverse (`start`).

## Qué falta (conocido, no implementado todavía)

- **Todo hardcodeado a un solo robot/instalación/mapa** (`default`/`andino`/`depot`) — no hay selector ni configuración, coherente con el alcance "v0".
- **Sin autenticación** — cualquiera que llegue a hablarle a Mosquitto puede mandar comandos, ver más arriba el bug del puerto 9001.
- **Sin teleoperación real** (WebRTC) — los botones de modo manual no están, a propósito (ver arriba).
- **Sin manejo de reconexión visible más allá del indicador de conexión** — si se cae Mosquitto o `comms_agent`, el panel avisa pero no reintenta activamente ni reconstruye el estado más allá de lo que ofrece `mqtt.js` por default.
