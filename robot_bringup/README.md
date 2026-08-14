# robot_bringup

Arranque de la capa de movimiento del robot: Nav2 + arbitraje de velocidad (`twist_mux`) + dead-man timer real para el teleop. Separado a propósito de `andino_gz` (solo simulación, submódulo de terceros, no tocar) y de `patrol_fsm` (lógica de rondas) — ver la discusión de arquitectura del 2026-08-14 en el historial del proyecto para el porqué completo. La idea: el día que cambie la plataforma (Andino físico, o el rover propio a futuro), este es el único paquete que hay que reemplazar — `patrol_fsm`/`comms_agent`/`panel` no se enteran.

Este README describe el estado actual y se va a ir actualizando con cada push.

## El problema que resuelve

Hasta el 2026-08-14, Nav2 y `teleop_twist_keyboard` escribían los dos directo al mismo tópico `/cmd_vel`, sin ningún árbitro — quien publicara último ganaba, sin ninguna regla. Además, no existía ningún dead-man timer real: CLAUDE.md pide que ~500ms sin consigna de teleop frenen al robot, y eso no estaba implementado en ningún lado.

## Qué hace hoy

`launch/robot_bringup.launch.py` levanta tres cosas:

1. **Nav2** (`nav2_bringup`'s `bringup_launch.py`, los mismos argumentos que antes usaba `andino_gz.launch.py` con `nav2:=True` — reusa el `nav2_params.yaml` de `andino_gz` por path, no lo duplica), con el `cmd_vel` de salida del `controller_server` remapeado a **`cmd_vel_nav`** (en vez del `/cmd_vel` final) para que `twist_mux` pueda arbitrarlo.
2. **`twist_mux`** (paquete de terceros, `ros-humble-twist-mux`, instalar con `apt`): arbitra entre `cmd_vel_nav` (prioridad 10) y `cmd_vel_teleop` (prioridad 100, el remap que le agregamos a `teleop_twist_keyboard` en `patrol_fsm/patrol_client.py`), publica el resultado en `/cmd_vel` — el mismo tópico que ya escuchaba el puente de `andino_gz` hacia Gazebo, sin tocar ese puente para nada.
3. **`teleop_watchdog.py`** (nodo propio, `scripts/teleop_watchdog.py`): el dead-man timer real, ver más abajo por qué hace falta.
4. **RViz**, con el mismo config con mapa/plan que antes traía `andino_gz.launch.py` con `nav2:=True` (`andino_gz_nav2.rviz`) — como Nav2 ya no lo levanta `andino_gz`, hay que traerlo acá para no perder el panel de mapa.

**Requiere lanzar `andino_gz.launch.py` con `nav2:=False`** (y `rviz:=False`, para no abrir dos ventanas de RViz) — ver el README de la raíz para el comando completo.

## `twist_mux` NO tiene un dead-man timer propio — por qué hace falta `teleop_watchdog.py`

Esto se descubrió probando en vivo (2026-08-14): configuramos el `timeout` de `twist_mux` pensando que era un watchdog activo, y no lo es. Fuimos a leer el código fuente real (`ros-teleop/twist_mux`, rama `humble`) para confirmarlo: el único timer que tiene el nodo es para diagnósticos (`updateDiagnostics()`, no toca velocidad). El `timeout` de la config (`config/twist_mux.yaml`) solo se revisa de forma **pasiva**, dentro del callback de un mensaje entrante — o sea, `twist_mux` recién se pregunta "¿hay algo vencido?" cuando le llega un mensaje *nuevo* de cualquier tópico. Si nada vuelve a publicar nunca más en ningún tópico de entrada, `twist_mux` no tiene ningún disparador que lo haga revisar el reloj por su cuenta.

Consecuencia real, confirmada en vivo: `teleop_twist_keyboard` publica **una vez por tecla apretada**, no en un stream continuo (a diferencia de un joystick real, que sondea y publica todo el tiempo aunque el stick esté quieto). Se apretó una tecla, `twist_mux` la relayó, y ese fue el último mensaje que cualquiera publicó — el robot siguió con esa orden **para siempre**, sin frenar solo, hasta que se probó esto en vivo y se confirmó el problema.

**El arreglo:** `teleop_watchdog.py` es un nodo propio, chico, con un `create_timer` real (chequea cada `check_period_sec`, default 0.1s). Se suscribe a `cmd_vel_teleop`, anota cuándo llegó el último mensaje no-cero, y si pasan más de `timeout_sec` (default 0.5s, debe coincidir con el timeout de `teleop` en `twist_mux.yaml`) sin uno nuevo, **publica él mismo** un Twist en cero sobre `cmd_vel_teleop` — como si el joystick se hubiera centrado solo. `twist_mux` lo recibe como un mensaje más y lo relaya con su lógica de prioridades de siempre, sin que el watchdog necesite saber nada de árbitraje. Confirmado en vivo (2026-08-14): con el watchdog, una tecla apretada una sola vez frena al robot ~0.5-0.6s después, solo, sin intervención.

**Nota de diseño:** esto no depende de qué dispositivo mande las órdenes — sondeo continuo (joystick) o eventos puntuales (teclado) da lo mismo si lo que se corta es el **canal entero** (WiFi del operador, WebRTC a futuro): ahí no llega nada de ninguna fuente, y el watchdog es la única red de seguridad, sea cual sea el input.

## Cómo correrlo

Requiere `ros-humble-twist-mux` instalado:
```bash
sudo apt install ros-humble-twist-mux ros-humble-twist-mux-msgs
```
Si al arrancar `twist_mux` muere con `error while loading shared libraries: libdiagnostic_updater.so: cannot open shared object file`, es una versión desactualizada de esa dependencia — `sudo apt install --only-upgrade ros-humble-diagnostic-updater` lo resuelve (visto en vivo 2026-08-14).

Con la simulación ya arriba (`andino_gz.launch.py ... nav2:=False rviz:=False`, ver README de la raíz):
```bash
source /opt/ros/humble/setup.bash
source install/setup.bash
ros2 launch robot_bringup robot_bringup.launch.py map:=depot
```

## Comportamientos no obvios

- **Cada reinicio de `robot_bringup` resetea AMCL, aunque Gazebo nunca se haya tocado.** Antes, Nav2/AMCL vivían en el mismo proceso que la simulación (`andino_gz.launch.py` con `nav2:=True`) — reiniciar `patrol_node`/`comms_agent` nunca los tocaba, así que la localización sobrevivía. Ahora que Nav2 vive acá, reiniciar esta terminal crea una instancia de AMCL nueva, sin memoria del pose estimate anterior — hay que volver a darle un **2D Pose Estimate** en RViz cada vez, aunque el robot en Gazebo no se haya movido. Costo operativo nuevo de este split, no un bug.
- **No lanzar con `nav2:=True` en `andino_gz.launch.py` al mismo tiempo que este launch** — arrancarían dos Nav2 completos peleándose por los mismos tópicos/servicios. Son mutuamente excluyentes: o Nav2 lo levanta `andino_gz` (`nav2:=True`, sin este paquete), o lo levanta este paquete (`andino_gz` con `nav2:=False`).
- **Solo cubre el caso de un robot sin namespace** (el uso actual del proyecto). Multi-robot necesitaría threadear el namespace igual que hace `andino_gz.launch.py`, no implementado.

## Qué falta (conocido, no implementado todavía)

- **Sin lock de e-stop en `twist_mux`** — queda para cuando vuelva el Andino físico y haya un botón de e-stop real que pueda publicar el `Bool` que `twist_mux` espera para un lock.
- **`teleop_watchdog.py` no maneja `Ctrl+C` prolijo** — tira un traceback al cortar el launch (visto en vivo). Cosmético, no afecta el funcionamiento mientras corre, pendiente de limpiar.
- **No aplica a producción tal cual** — la config de `twist_mux` (prioridades, timeouts) sí se porta sin cambios entre simulación, Andino físico y el rover propio a futuro; el wiring de Nav2 (`map`, `params_file`) es específico de esta simulación y va a necesitar su propia versión cuando cambie la plataforma.
