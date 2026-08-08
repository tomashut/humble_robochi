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

## Persistencia de estado (sobrevive reinicios)

**Esto ya está implementado — no es un pendiente.** El nodo guarda su estado (`state`, `current_goal`, `fail_count`, `round_id`) en un JSON (`state_file`, default `~/.local/share/patrol_fsm/state.json`) cada vez que algo de eso cambia, con escritura atómica (archivo temporal + rename) para no corromperlo si se corta la luz justo en el medio.

Al arrancar, si encuentra un estado guardado:
- Si era `EN_BASE` o `FALLA`, lo respeta tal cual (`FALLA` sigue exigiendo `/clear_failure` — un reinicio no debe borrar una falla real).
- Si era cualquier otro estado activo (`EN_RONDA`, `PAUSADO`, `MANUAL`, `RETORNO`) — es decir, se cortó a media ronda — **nunca reanuda navegación solo**. Aterriza en `PAUSADO`, conservando `current_goal` y `round_id` intactos, y agrega un evento `reiniciado` al registro de ejecuciones (con `estado_anterior`) para que quede documentado por qué esa ronda se demoró. Hace falta `/resume_patrol` (continúa el mismo punto pendiente) o `/return_to_base` para que vuelva a moverse — nunca se mueve a ciegas después de un apagado no planeado.
- Si el archivo de estado no existe, está corrupto, o tiene un `current_goal` fuera de rango, se ignora con un warning y arranca fresco desde `EN_BASE` (a diferencia del YAML de waypoints, que sí falla duro si está mal — acá preferimos degradar a un estado seguro antes que impedir que el nodo arranque).

### Auto-reanudación tras un reinicio, si nadie contesta

**También implementado, así funciona hoy por default — es personalizable.** Quedarse en `PAUSADO` para siempre tras un reinicio suena seguro, pero si es de noche y no hay nadie mirando el panel, el robot deja de patrullar indefinidamente por un simple corte de luz de 2 segundos — contradice el propósito de un robot de seguridad desatendido.

Por eso, **solo en el caso de aterrizar en `PAUSADO` por un reinicio no planeado** (no aplica a un `/pause_patrol` manual y deliberado — ese respeta la decisión del operador y no se auto-reanuda), el nodo arranca un temporizador de `auto_resume_timeout_sec` segundos (parámetro/argumento de launch, **default 300 = 5 minutos**). Si nadie mandó `/resume_patrol`, `/manual_start` ni `/return_to_base` antes de que se cumpla ese plazo, el nodo reanuda la ronda solo (evento `resumed` con `reason: auto_tras_reinicio` en el registro, para que quede claro que no fue un operador). Si sí intervino un operador antes, el temporizador se cancela y no hace nada. `auto_resume_timeout_sec:=0` desactiva esto por completo (vuelve al comportamiento "espera humano para siempre") para instalaciones donde eso sea preferible.

Si Nav2 vuelve a fallar tras la auto-reanudación, cae en el mismo camino de reintentos/`FALLA` de siempre — no hace falta lógica especial, la máquina de estados ya contiene ese caso.

**Idea a futuro, todavía sin diseñar:** en vez de un timeout ciego, usar visión (cámara de percepción) + LiDAR con algo de procesamiento IA para verificar que el robot todavía entiende bien su posición/orientación y que el entorno inmediato es seguro antes de decidir auto-reanudar — en vez de confiar solo en que pasó tiempo sin que nadie contestara.

## Comportamientos no obvios (aprendidos probando en simulación)

- **`start_patrol` y `resume_patrol` no "arrancan de cero"**: `current_goal` (el índice del waypoint pendiente) solo avanza cuando se completa un waypoint estando en `EN_RONDA`. Ni `return_to_base` ni `clear_failure` lo resetean. Entonces si mandás el robot a la base a mitad de ronda (`b`) y después arrancás de nuevo (`s`), retoma en el waypoint donde había quedado, no en el 0. Es el comportamiento buscado (no repetir lo ya recorrido), pero el nombre `start_patrol` puede confundir.
- **Ctrl+C en el prompt `>` del cliente lo mata**, no solo cancela el comando en curso — `patrol_client` se cierra por completo (el robot sigue su goal actual sin enterarse, eso está bien). Como el launch levanta el cliente con `xterm -hold`, la ventana queda "viva" pero inútil, no vuelve a lanzar el cliente solo. Para recuperar el control: en cualquier terminal, `source install/setup.bash && ros2 run patrol_fsm patrol_client` — se reconecta a los mismos servicios del `patrol_node`, que sigue corriendo.
- Dentro del modo manual (`m`), en cambio, Ctrl+C es el flujo esperado: corta el teleop y vuelve al prompt en `PAUSADO`.

## Qué falta (conocido, no implementado todavía)

- **Sin acciones por punto ni agenda/horario** en el YAML de waypoints.
- **Sin disparo automático de retorno a base** por batería baja — `return_to_base` es siempre manual.

### Sin integración con twist_mux (prioridades e-stop > teleoperación > navegación) — investigado, no implementado

Hoy Nav2 y el teleop (`teleop_twist_keyboard`) escriben los dos al **mismo tópico** `cmd_vel`, sin ningún árbitro — el bridge de Gazebo recibe los mensajes de ambos mezclados, sin poder distinguir de dónde vino cada uno. `twist_mux` es la pieza estándar de ROS 2 para resolver esto (prioridades + timeout tipo dead-man por fuente + un "lock" separado para e-stop, que es justo el mecanismo correcto para eso — corta todo sin pasar por la lógica de prioridades normal).

**Por qué no está hecho todavía:** para que `twist_mux` realmente controle el robot (no solo decida "en el aire" sin que nadie lo escuche), hace falta que el consumidor final del `cmd_vel` deje de escuchar la salida cruda de Nav2 y pase a escuchar la salida ya arbitrada de `twist_mux`. En simulación, ese consumidor final es el bridge de Gazebo — y la línea que lo conecta está en `andino_gz/config/bridge_config.yaml`, **adentro del submódulo git `andino_gz`**, sin ningún argumento de launch para pasarle una ruta alternativa (a diferencia de `world_name`/`map`/`params_file`, que sí son parametrizables). El remapeo final de Nav2 hacia `cmd_vel` tampoco es tocable desde afuera: está hardcodeado adentro del propio paquete de sistema `nav2_bringup`, no es configuración de `andino_gz`.

**Riesgo de la solución (editar una línea de `bridge_config.yaml`):**
- Es una modificación local a un archivo trackeado dentro de un submódulo de terceros — diverge del repo original (`github.com/ekumenlabs/andino_gz`).
- Si algún día se actualiza el submódulo (`git submodule update`), esa línea se puede pisar o entrar en conflicto, y si nadie se acuerda de reaplicarla, `twist_mux` queda armado pero sin efecto — Nav2 y teleop vuelven a chocar sin arbitrar en `cmd_vel`, **en silencio, sin ningún error que lo avise**. Hay que tenerlo presente como checklist post-actualización del submódulo si esto se implementa.
- No es lógica de Nav2/AMCL/slam_toolbox/ros2_control (que es lo que el CLAUDE.md dice no reescribir) — es una tabla de nombres de tópicos específica de la simulación en Gazebo, sin ningún equivalente en el robot físico.

**Alcance:** esto es 100% específico de la simulación. En el robot físico, el "último cable" hacia los motores reales sería otro mecanismo (`ros2_control` con el driver real), que hoy no existe en este repo — habrá que resolver el mismo problema ahí, por separado, cuando llegue ese momento. La config de `twist_mux` en sí (prioridades, timeouts, e-stop como lock) sí se porta sin cambios entre simulación, Andino físico, y el rover propio a futuro.
