# panel

Panel v0: página web mínima que habla MQTT directo desde el navegador (sin backend propio) contra el mismo Mosquitto que usa `comms_agent`. Es el punto 4 del plan del CLAUDE.md, en su versión más simple: mapa con posición en vivo, estado, botones.

Este README describe el estado actual y se va a ir actualizando con cada push.

## Qué hace hoy

Un solo archivo autocontenido (`index.html`), sin backend ni build — se abre directo como `file://` en el navegador. Usa la librería `mqtt.js` (bundleada localmente en `mqtt.min.js`, no depende de internet) para hablar **MQTT sobre WebSocket** directo con Mosquitto (opción elegida en vez de escribir un backend propio — ver discusión en el historial del proyecto).

- **Mapa**: `depot.png`, convertido una sola vez desde `src/andino_gz/andino_gz/maps/depot/depot.pgm` (formato que el navegador no puede mostrar directo). Es un archivo estático — no viaja por MQTT, se carga una vez.
- **Posición en vivo**: se suscribe a `patrol/default/andino/position` (JSON `{x, y, yaw}`, agregado a `comms_agent` para esto — ver su README) y dibuja un punto sobre el mapa, convirtiendo metros del frame `map` a píxeles de la imagen usando la resolución/origen de `depot.yaml` (**hardcodeado en el HTML** — si cambia el mapa, hay que actualizar `MAP_RESOLUTION`/`MAP_ORIGIN_X`/`MAP_ORIGIN_Y`/`MAP_IMG_W`/`MAP_IMG_H` a mano).
- **Estado**: se suscribe a `patrol/default/andino/state`, lo muestra con un color distinto por estado.
- **Indicador de conexión, en tres niveles distintos** (no es lo mismo "llegué al cartero" que "hay alguien contestando" — ver "Comportamientos no obvios"): (1) conectando a Mosquitto, (2) conectado a Mosquitto pero sin señal del robot (`comms_agent`/`patrol_node` no están corriendo, o no llegó nada todavía), (3) señal del robot recibida hace poco = "activo"; si pasan más de 15s sin ningún `state`/`heartbeat` nuevo, vuelve a avisar.
- **Botones**: Iniciar / Pausar / Reanudar / Retornar a base / Limpiar falla — publican a `patrol/default/andino/cmd`. **Arrancan deshabilitados** y solo se habilitan (el subconjunto que corresponda) cuando llega un `state` real del robot — así no se puede mandar un comando "al aire" sin saber si hay alguien escuchando del otro lado. Siguen la misma tabla de transiciones documentada en `patrol_fsm/README.md`.

**Deliberadamente sin `manual_start`/`manual_stop`**: activar modo manual desde el panel sin una forma real de manejar (eso es WebRTC, todavía no existe) dejaría al robot sin nadie manejándolo — un botón roto. Se suman cuando exista la teleoperación real.

## Cómo correrlo

Requiere: `comms_agent` corriendo (ver su README), y Mosquitto con un listener de WebSocket habilitado (ver más abajo, no viene así por default).

**Local, en esta misma máquina:**
```bash
xdg-open /home/tomashut/humble_robochi/panel/index.html
```
O abrir `file:///home/tomashut/humble_robochi/panel/index.html` directo desde el navegador.

**Desde otro dispositivo en la misma red (celular, otra PC):** hace falta servir la carpeta por HTTP, no alcanza con `file://`. Con el servidor que trae Python de fábrica, sin instalar nada:
```bash
cd /home/tomashut/humble_robochi/panel
python3 -m http.server 8080 --bind 0.0.0.0
```
Y desde el otro dispositivo, abrir `http://<IP-de-esta-maquina-en-la-red>:8080` (buscar la IP con `ip -4 addr show`, la de la interfaz de WiFi/cable real — no la de interfaces virtuales tipo `lxcbr0`/`docker0` si las hay). `MQTT_HOST` en el JS se calcula solo a partir de `window.location.hostname`, así que no hace falta editar nada a mano según desde dónde se abra. Esto **no es un servicio persistente** — es un comando que corre en primer plano mientras lo necesites, no arranca solo al reiniciar la máquina.

### Habilitar el listener de WebSocket + autenticación en Mosquitto (una sola vez)

Por default Mosquitto solo habla MQTT "crudo" (TCP), que un navegador no puede usar — hay que agregar un listener aparte. Y apenas se define un listener a mano, Mosquitto deja de aceptar conexiones anónimas (sale de su "modo local automático"), así que conviene resolver los dos juntos:

```bash
sudo mosquitto_passwd -c -b /etc/mosquitto/passwd andino patrol_2026
sudo nano /etc/mosquitto/conf.d/websockets.conf
```
Contenido:
```
password_file /etc/mosquitto/passwd

listener 1883 127.0.0.1
protocol mqtt

listener 9001 127.0.0.1
protocol websockets
```
```bash
sudo systemctl restart mosquitto
```

`andino`/`patrol_2026` tienen que coincidir con `MQTT_USERNAME`/`MQTT_PASSWORD` en `index.html` y con los parámetros que se le pasen a `comms_agent` — ver "Comportamientos no obvios" sobre qué tan real es esta autenticación.

## Comportamientos no obvios / bugs conocidos

- **"Conectado a Mosquitto" no significa "el robot está prendido"** — Mosquitto es un servicio de sistema que corre solo, sin importar si `patrol_node`/`comms_agent` están corriendo. Por eso el panel distingue "conectado al broker" de "hay señal del robot" (ver arriba) y los botones no se habilitan solo por estar conectado al broker.
- **Tampoco "hay señal del robot" significa "la simulación/Nav2 están operativos"** — `patrol_node` puede estar vivo y contestando (`patrol_state`) sin que Gazebo esté siquiera abierto. El panel no tiene forma de distinguir eso hoy; si mandás `start` en esa situación, `patrol_node` lo va a aceptar pero el goal a Nav2 va a fallar 5 veces y termina en `FALLA` (ver `patrol_fsm/README.md`).
- **Al recargar la página, el cartel rojo ("sin señal del robot") aparece un instante antes de pasar a verde** — es esperado: `state` se publica con `retain=True`, así que Mosquitto lo entrega automáticamente al suscribirse, pero tarda una fracción de segundo. No es un bug, es el panel siendo honesto sobre lo que sabe en cada instante.
- **La autenticación del panel es básica, no un secreto real** — usuario/contraseña quedan en texto plano dentro de `index.html`, visibles para cualquiera que abra el código fuente de la página. Sirve para filtrar conexiones anónimas casuales en la red local, no para proteger contra alguien que se tome el trabajo de mirar el HTML. Ver `comms_agent/README.md` para el detalle completo de la config de Mosquitto.
- **Cargar el panel desde otro dispositivo no pide ningún login** — las credenciales de MQTT viajan adentro de la página y se mandan solas, sin preguntarle nada a quien la abre. O sea que cualquiera que llegue a la URL del panel (por ejemplo, otra persona en la misma red WiFi) entra igual que vos, sin que se le pida nada — confirmado probándolo desde un celular. La autenticación de Mosquitto protege contra alguien hablando MQTT crudo sin credenciales (`mosquitto_pub` a mano), no contra alguien que simplemente abre esta página.
- `password_file` (y antes `allow_anonymous`, `bind_address`) son opciones que **Mosquitto solo permite declarar una vez, arriba de todo el archivo** — repetirlas dentro de cada bloque `listener` rompe el arranque con "Duplicate ... value in configuration". Aprendido a los golpes en varias vueltas de prueba y error.
- **Bug sin resolver, documentado en `comms_agent/README.md`**: el listener 9001 (WebSocket) no respeta `bind_address`/`listener <puerto> 127.0.0.1` — queda escuchando en todas las interfaces de red, no solo `localhost`, a diferencia del 1883. Parece limitación de Mosquitto 2.0.11. Riesgo acotado a la red local, no a internet — aceptado por ahora en esta etapa de prototipo.
- **`ros2 topic echo`/`comms_agent` pueden no ver una posición hasta que el robot se mueve**: AMCL no republica `/amcl_pose` a un ritmo fijo si el robot está quieto, solo en updates — si `comms_agent` arrancó después de setear la pose inicial en RViz, se puede perder esa primera publicación. Se resuelve solo apenas el robot arranca a moverse (`start`).

## Qué falta (conocido, no implementado todavía)

- **Todo hardcodeado a un solo robot/instalación/mapa** (`default`/`andino`/`depot`) — no hay selector ni configuración, coherente con el alcance "v0".
- **Sin servidor persistente** — servirlo en la red es un comando manual (`http.server`), no un servicio que arranca solo.
- **Sin login humano real** — ver el punto de arriba sobre cargar el panel sin que pida nada.
- **Sin teleoperación real** (WebRTC) — los botones de modo manual no están, a propósito (ver arriba).
- **Sin manejo de reconexión visible más allá del indicador de conexión** — si se cae Mosquitto o `comms_agent`, el panel avisa pero no reintenta activamente ni reconstruye el estado más allá de lo que ofrece `mqtt.js` por default.
