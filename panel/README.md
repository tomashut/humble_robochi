# panel

Panel v0: web con login humano real, mapa con posición en vivo, estado y botones para comandar `patrol_fsm`. Es el punto 4 del plan del CLAUDE.md.

Este README describe el estado actual y se va a ir actualizando con cada push.

## Arquitectura (cambió — ya no es solo un HTML suelto)

Hasta la versión anterior, el navegador hablaba MQTT directo contra Mosquitto (MQTT sobre WebSocket), con la contraseña de Mosquitto adentro del JavaScript de la página. Eso permitía filtrar conexiones anónimas casuales, pero **no era un login real** — cualquiera que abriera la página se conectaba automáticamente, sin que se le pidiera nada, y la contraseña real de Mosquitto quedaba visible en el código fuente de cualquiera que la mirara.

Ahora hay un **backend propio** (`server.py`, Python + `aiohttp`) que se para en el medio:

```
navegador  <--WebSocket propio, con login-->  server.py  <--MQTT (paho-mqtt)-->  Mosquitto  <-->  comms_agent  <-->  patrol_node
```

- El navegador **nunca ve la contraseña real de Mosquitto** — solo `server.py` la tiene, pasada por línea de comandos al arrancarlo (igual que se le pasa a `comms_agent`).
- Para entrar hay que loguearse con usuario/contraseña de **persona** (gestionados por `manage_users.py`, guardados hasheados en `users.json`), completamente separados de las credenciales de Mosquitto.
- Una vez logueado, el navegador habla con `server.py` por un WebSocket propio (JSON simple: `{"type": "state", "data": "EN_RONDA"}`, etc.), no por MQTT. `server.py` es el que efectivamente publica/suscribe a Mosquitto de un lado, y reenvía todo al navegador del otro.
- `mqtt.js` ya no se usa (se borró `mqtt.min.js`) — el navegador usa el `WebSocket` nativo del navegador, no necesita ninguna librería.

Analogía (la misma que fuimos usando para pensar esto): antes, el navegador era un huésped de hotel al que le dábamos una copia de la llave maestra para que entrara solo a cualquier puerta. Ahora el navegador le pide las cosas al recepcionista (`server.py`), y es el recepcionista el único que tiene la llave maestra y la usa por vos — si a un huésped le roban su credencial, no sirve para nada fuera de esta recepción.

## Qué hace hoy

- **Login**: `/login` muestra un formulario, valida contra `users.json` (contraseñas con PBKDF2-SHA256 + salt, 310.000 iteraciones — mismo nivel de esfuerzo que usa Django por default). Si es correcto, `server.py` crea una sesión en memoria y pone una cookie `panel_session` (`HttpOnly`, no accesible desde JavaScript). La sesión dura 12 horas (un turno de guardia) o hasta que se reinicie `server.py` (las sesiones no se guardan a disco — ver "Qué falta").
- **Todo lo demás pide sesión**: `/` (el panel), `/depot.png` y `/ws` redirigen a `/login` (o rechazan la conexión, en el caso del WebSocket) si no hay una cookie de sesión válida.
- **Mapa y posición en vivo**: igual que antes — `depot.png` de fondo, posición dibujada convirtiendo metros del frame `map` a píxeles con la resolución/origen de `depot.yaml` (hardcodeado en `index.html`).
- **Estado y botones**: igual que antes — Iniciar / Pausar / Reanudar / Retornar a base / Limpiar falla, habilitados solo según el estado real recibido (misma tabla que `patrol_fsm/README.md`). Deliberadamente sin `manual_start`/`manual_stop` (ver motivo más abajo).
- **Indicador de conexión en tres niveles**: conectando al backend / conectado pero sin señal del robot / robot activo — misma lógica que antes, solo que ahora habla de "el backend" en vez de "Mosquitto", porque el navegador ya ni sabe que Mosquitto existe.
- **Cerrar sesión**: link "Cerrar sesión" en el panel, borra la cookie del lado del servidor y del navegador.

**Deliberadamente sin `manual_start`/`manual_stop`**: activar modo manual desde el panel sin una forma real de manejar (eso es WebRTC, todavía no existe) dejaría al robot sin nadie manejándolo — un botón roto. Se suman cuando exista la teleoperación real.

## Cómo correrlo

Requiere: `python3-aiohttp` instalado (`sudo apt install python3-aiohttp`), Mosquitto corriendo con su usuario/contraseña configurados (ver `comms_agent/README.md`), y al menos un usuario humano dado de alta.

**1. Dar de alta un usuario (una sola vez, o cuando haga falta agregar/cambiar uno):**
```bash
cd /home/tomashut/humble_robochi/panel
python3 manage_users.py tomas
```
Pide la contraseña por teclado (no queda en el historial de la terminal) y la guarda hasheada en `users.json` (no se commitea — ver `.gitignore`).

**2. Levantar el backend:**
```bash
cd /home/tomashut/humble_robochi/panel
python3 server.py --mqtt-username andino --mqtt-password patrol_2026
```
Por default escucha en `0.0.0.0:8080` (accesible desde cualquier dispositivo de la LAN, igual que antes) y se conecta a Mosquitto en `localhost:1883`. Ver `python3 server.py --help` para los demás parámetros (`--http-port`, `--mqtt-host`, `--mqtt-port`).

**3. Abrir el panel:** `http://localhost:8080` (esta PC) o `http://<IP-de-esta-máquina-en-la-red>:8080` (otro dispositivo de la LAN — ver `comms_agent/README.md`/sesiones anteriores sobre cómo encontrar esa IP). Va a pedir login antes de mostrar nada.

Como antes, **no es un servicio persistente** — es un comando en primer plano. Sigue pendiente convertirlo en un servicio `systemd` para que sobreviva reinicios sin intervención manual (ver "Qué falta").

## Comportamientos no obvios / bugs conocidos

- **El login protege el acceso al panel, no solo la conexión a Mosquitto.** Esto es la diferencia real con la versión anterior: antes, cualquiera que llegara a la URL entraba sin que se le pidiera nada. Ahora hace falta usuario/contraseña de persona antes de ver cualquier cosa.
- **La sesión vive en memoria del proceso `server.py`, no en disco.** Si el backend se reinicia (se cae, se actualiza el código, se reinicia la PC), todas las sesiones activas se pierden y hay que volver a loguearse — no hay "recordarme" persistente todavía. Aceptable para este estado del proyecto, pero hay que saberlo.
- **Sigue sin haber TLS/HTTPS.** El login manda la contraseña por HTTP plano dentro de la red local — cualquiera en esa misma red con acceso al tráfico (Wireshark, un switch mal configurado) podría verla. Es el mismo nivel de exposición que ya tenía Mosquitto (documentado en `comms_agent/README.md`) — la solución de fondo es la VPN (WireGuard) que ya está anotada como trabajo futuro, no algo para resolver en el panel en sí.
- **"Conectado al backend" no significa "el robot está prendido"** — mismo comportamiento que antes, solo que ahora el navegador no sabe que existe Mosquitto: distingue "conectado a `server.py`" de "hay señal del robot" (state/heartbeat reales).
- **`mqtt.js` ya no está** — si algo en el navegador tira un error tipo "mqtt is not defined", es señal de estar mirando una versión vieja cacheada del HTML; recargar forzando (Ctrl+Shift+R).
- **`server.py` le manda al navegador, apenas se conecta, el último estado/posición/heartbeat conocidos** (`app['last_known']`), no solo lo que llegue de ahí en adelante. Antes de este fix, un navegador que se conectaba después de que Mosquitto ya tenía el estado retenido se quedaba sin enterarse de nada hasta el próximo cambio real del robot — quedaba en gris para siempre. Corregido 2026-08-10.
- **Pendiente, ya diagnosticado — próxima acción a hacer y probar antes que cualquier otra cosa de este proyecto**: sigue existiendo una carrera de arranque más chica. `server.py` empieza a aceptar conexiones de navegador apenas termina de arrancar, pero eso puede pasar *antes* de que termine de conectarse a Mosquitto y reciba los mensajes retenidos que llenan `last_known`. Si un navegador se conecta justo en esa ventana (típicamente el primer segundo después de lanzar `server.py`), se queda con "conectando..." para siempre — un segundo refresh alcanza para solucionarlo, porque para ese momento la ventana ya cerró. El arreglo: en `create_app()`, hacer que `on_startup` espere (con un `asyncio.Event` que el callback `on_connect` de MQTT dispare) a que la conexión a Mosquitto esté confirmada antes de dejar que `aiohttp` empiece a aceptar conexiones — con un timeout, para no colgarse si Mosquitto está caído.
- Los gotchas de Mosquitto en sí (per-listener vs. global, el bug del puerto 9001, AMCL sin publicar hasta que el robot se mueve) siguen aplicando igual — ver `comms_agent/README.md`. El listener 9001 (WebSocket) de Mosquitto ya no lo usa nadie desde el navegador (ahora solo lo usa `server.py`, que igual podría hablarle por el 1883 normal — queda pendiente simplificar y sacar el listener 9001 si ya no hace falta).

## Qué falta (conocido, no implementado todavía)

- **Servidor sin persistencia real**: hay que convertirlo en servicio `systemd` para producción (mismo pendiente que ya existía, ahora aplica al backend nuevo en vez de al `http.server` viejo).
- **Sesiones no persistidas a disco** — se pierden al reiniciar `server.py`.
- **Un solo nivel de usuario** — cualquiera que se loguee tiene el mismo acceso (todos los botones habilitados según el estado). No hay roles (por ejemplo, "solo ver" vs. "puede comandar").
- **Sin TLS/HTTPS** — ver arriba.
- **Todo hardcodeado a un solo robot/instalación/mapa** (`default`/`andino`/`depot`) — no hay selector, coherente con el alcance "v0".
- **Sin teleoperación real** (WebRTC) — los botones de modo manual no están, a propósito.
