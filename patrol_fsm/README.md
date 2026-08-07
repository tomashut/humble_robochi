# patrol_fsm

Nodo de rondas para el robot de patrullaje de seguridad, desarrollado sobre Andino (ROS 2 Humble + Nav2). Es la capa de aplicación propia que decide *cuándo* y *hacia dónde* navega el robot; Nav2 se usa tal cual, sin modificaciones.

Este README describe el estado actual del paquete y se va a ir actualizando a medida que se agreguen cosas en próximos pushes.

## Qué hace hoy

Máquina de estados formal con seis estados:

- `EN_BASE` — inactivo, listo para iniciar una ronda.
- `EN_RONDA` — navegando la secuencia de waypoints.
- `PAUSADO` — ronda interrumpida, esperando reanudación.
- `MANUAL` — goal de Nav2 cancelado, robot bajo teleoperación.
- `RETORNO` — volviendo al waypoint base.
- `FALLA` — se superaron los reintentos permitidos ante fallas de Nav2; el robot queda detenido y reportando, sin moverse a ciegas.

Expuesta como servicios ROS 2 (`std_srvs/Trigger`), cada uno valida el estado actual antes de transicionar:

| Servicio | Desde | Hace |
|---|---|---|
| `/start_patrol` | `EN_BASE` | Arranca la ronda, manda el goal pendiente. |
| `/pause_patrol` | `EN_RONDA` | Cancela el goal activo, pasa a `PAUSADO`. |
| `/resume_patrol` | `PAUSADO` | Reenvía el goal pendiente (Nav2 replanifica desde la posición actual). |
| `/manual_start` | `EN_RONDA` o `PAUSADO` | Cancela el goal activo, pasa a `MANUAL`. |
| `/manual_stop` | `MANUAL` | Pasa a `PAUSADO` (no reanuda solo). |
| `/return_to_base` | `EN_RONDA` o `PAUSADO` | Navega al waypoint 0. |
| `/clear_failure` | `FALLA` | Resetea el contador de fallos y pasa a `EN_BASE` (no mueve al robot). |

Reintentos: hasta 5 fallos consecutivos de Nav2 (goal rechazado, abortado, o cancelado sin que lo haya pedido el propio nodo) antes de pasar a `FALLA`.

`patrol_client.py` es un cliente de terminal interactivo para probar todo esto a mano: `s`/`p`/`r`/`m`/`b`/`c`/`q`. El comando `m` encadena `manual_start` → `teleop_twist_keyboard` → `manual_stop` automáticamente al salir del teleop con Ctrl+C.

Los waypoints se leen de un YAML (`config/waypoints.yaml`, lista plana de `name`/`x`/`y`/`yaw`; el primero es la base) vía el parámetro ROS `waypoints_file`. Sin acciones por punto ni agenda todavía — eso es alcance futuro, cuando haya algo real que las use. Si el archivo falta o está mal formado, el nodo falla al arrancar en vez de arrancar en silencio con datos por defecto.

## Cómo correrlo

Requiere la simulación Gazebo/Nav2 del Andino levantada primero (ver `andino_gz` en `src/`). Con eso arriba:

```bash
source /opt/ros/humble/setup.bash
source install/setup.bash
ros2 launch patrol_fsm patrol.launch.py
```

Abre una terminal xterm con el cliente interactivo. Para usar un YAML de waypoints distinto (pensado para cuando haya más de un robot/instalación): `ros2 launch patrol_fsm patrol.launch.py waypoints_file:=/ruta/a/otro.yaml`.

## Registro de ejecuciones ("libro de rondas digital")

El nodo escribe un JSON Lines por día (`rounds_log_dir`, default `~/.local/share/patrol_fsm/rondas/AAAA-MM-DD.jsonl`) con un evento por línea. Cada línea tiene `ts` (timestamp con huso horario), `round_id` y `event`, más campos propios de ese evento:

| Evento | Cuándo | Campos extra |
|---|---|---|
| `round_started` | Se abre una ronda (`start_patrol` desde `EN_BASE`, o automáticamente al cerrar el círculo del recorrido). | — |
| `waypoint_reached` | El robot llega a un waypoint navegando en `EN_RONDA`. | `waypoint_name` |
| `interrupted` | `pause_patrol`, `manual_start` o `return_to_base`. | `reason` (`pausado`/`manual`/`retorno_a_base`) |
| `resumed` | `resume_patrol`. | — |
| `goal_failed` | Cada intento fallido de Nav2 (rechazado, abortado, cancelado sin pedirlo). | `reason`, `fail_count` |
| `round_ended` | Se cierra la ronda. | `result`: `completada` (dio la vuelta completa al circuito y arranca otra al toque), `retorno_a_base` (se cortó por un `return_to_base` exitoso), o `falla` (llegó a `FALLA`). |
| `falla_reconocida` | `clear_failure`. | — |

**Qué define una "ronda":** una vuelta completa al circuito de waypoints (del primero al último y de nuevo al principio), no un día completo de patrullaje (eso es la "sesión" = el archivo diario, que puede contener muchas rondas). Pausas y modo manual son interrupciones dentro de la misma ronda; un `return_to_base` exitoso, en cambio, la cierra — porque puede quedarse ahí un tiempo indefinido (cargando) y no queremos una ronda "abierta" cruzando archivos de varios días.

Para leer: es texto plano, una línea por evento — alcanza con `jq` mientras no haya un panel:
```bash
jq 'select(.round_id == "20260807-143210123")' rondas/2026-08-07.jsonl
```

## Comportamientos no obvios (aprendidos probando en simulación)

- **`start_patrol` y `resume_patrol` no "arrancan de cero"**: `current_goal` (el índice del waypoint pendiente) solo avanza cuando se completa un waypoint estando en `EN_RONDA`. Ni `return_to_base` ni `clear_failure` lo resetean. Entonces si mandás el robot a la base a mitad de ronda (`b`) y después arrancás de nuevo (`s`), retoma en el waypoint donde había quedado, no en el 0. Es el comportamiento buscado (no repetir lo ya recorrido), pero el nombre `start_patrol` puede confundir.
- **Ctrl+C en el prompt `>` del cliente lo mata**, no solo cancela el comando en curso — `patrol_client` se cierra por completo (el robot sigue su goal actual sin enterarse, eso está bien). Como el launch levanta el cliente con `xterm -hold`, la ventana queda "viva" pero inútil, no vuelve a lanzar el cliente solo. Para recuperar el control: en cualquier terminal, `source install/setup.bash && ros2 run patrol_fsm patrol_client` — se reconecta a los mismos servicios del `patrol_node`, que sigue corriendo.
- Dentro del modo manual (`m`), en cambio, Ctrl+C es el flujo esperado: corta el teleop y vuelve al prompt en `PAUSADO`.

## Qué falta (conocido, no implementado todavía)

- **Sin acciones por punto ni agenda/horario** en el YAML de waypoints.
- **Sin persistencia a disco** del estado de la máquina de estados (el registro de ejecuciones sí persiste, pero el estado actual del nodo no sobrevive un reinicio).
- **Sin integración con twist_mux** (prioridades e-stop > teleoperación > navegación).
- **Sin disparo automático de retorno a base** por batería baja — `return_to_base` es siempre manual.
