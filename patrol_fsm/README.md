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

## Cómo correrlo

Requiere la simulación Gazebo/Nav2 del Andino levantada primero (ver `andino_gz` en `src/`). Con eso arriba:

```bash
source /opt/ros/humble/setup.bash
source install/setup.bash
ros2 launch patrol_fsm patrol.launch.py
```

Abre una terminal xterm con el cliente interactivo.

## Qué falta (conocido, no implementado todavía)

- **Waypoints hardcodeados** en `PatrolNode.__init__` (10 poses fijas) — deberían venir de un archivo YAML de rondas (waypoints, acciones por punto, agenda), para no tener que tocar código para cambiar un recorrido.
- **Sin persistencia a disco** del estado — no sobrevive un reinicio del nodo.
- **Sin integración con twist_mux** (prioridades e-stop > teleoperación > navegación).
- **Sin registro de ejecuciones** ("libro de rondas digital": inicio, waypoints con timestamp, interrupciones, resultado).
- **Sin disparo automático de retorno a base** por batería baja — `return_to_base` es siempre manual.
