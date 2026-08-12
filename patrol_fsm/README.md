# patrol_fsm

Nodo de rondas para el robot de patrullaje de seguridad, desarrollado sobre Andino (ROS 2 Humble + Nav2). Es la capa de aplicación propia que decide *cuándo* y *hacia dónde* navega el robot; Nav2 se usa tal cual, sin modificaciones.

Este README describe el estado actual del paquete y se va a ir actualizando a medida que se agreguen cosas en próximos pushes.

## Qué hace hoy

Máquina de estados formal con siete estados:

- `EN_BASE` — inactivo, listo para iniciar una ronda.
- `EN_RONDA` — navegando la secuencia de waypoints.
- `PAUSADO` — pausa **deliberada**: solo se llega por decisión de un humano (`/pause_patrol` o `/manual_stop`). Nunca se auto-reanuda, bajo ninguna circunstancia, ni siquiera después de un reinicio — siempre hace falta un comando.
- `MANUAL` — goal de Nav2 cancelado, robot bajo teleoperación.
- `RETORNO` — volviendo al waypoint base.
- `FALLA` — se superaron los reintentos permitidos ante fallas de Nav2 (o se perdió la confianza en la localización navegando, ver más abajo); el robot queda detenido y reportando, sin moverse a ciegas.
- `INTERRUMPIDO` — aterrizaje de un reinicio no planeado a mitad de `EN_RONDA` o `RETORNO`. A diferencia de `PAUSADO`, acá sí se intenta reanudar solo (con chequeos de por medio) — ver la sección de auto-reanudación. Nunca se llega acá por una decisión humana, solo desde el arranque del nodo.

Expuesta como servicios ROS 2 (`std_srvs/Trigger`), cada uno valida el estado actual antes de transicionar:

| Servicio | Desde | Hace |
|---|---|---|
| `/start_patrol` | `EN_BASE` | Arranca la ronda, manda el goal pendiente. |
| `/pause_patrol` | `EN_RONDA`, `RETORNO` o `INTERRUMPIDO` | Cancela el goal activo (si hay), mata cualquier auto-reanudación pendiente, pasa a `PAUSADO`. |
| `/resume_patrol` | `PAUSADO` o `INTERRUMPIDO` | Reenvía el goal pendiente (Nav2 replanifica desde la posición actual) — decisión humana, no pasa por el chequeo de localización. |
| `/manual_start` | `EN_RONDA`, `PAUSADO`, `RETORNO` o `INTERRUMPIDO` | Cancela el goal activo, pasa a `MANUAL`. |
| `/manual_stop` | `MANUAL` | Pasa a `PAUSADO` (no reanuda solo). |
| `/return_to_base` | `EN_RONDA`, `PAUSADO` o `INTERRUMPIDO` | Navega al waypoint 0. |
| `/clear_failure` | `FALLA` | Resetea el contador de fallos y pasa a `EN_BASE` (no mueve al robot). |

**`actividad_previa` — a qué actividad reanudar:** `EN_RONDA` y `RETORNO` son las dos "actividades" (el robot navegando hacia algo); `PAUSADO`, `MANUAL` e `INTERRUMPIDO` son "suspensiones" (el robot detenido, esperando). Cada vez que se sale de una actividad hacia una suspensión (`/pause_patrol` o `/manual_start` desde `EN_RONDA`/`RETORNO`, o el aterrizaje en `INTERRUMPIDO` tras un reinicio) se anota cuál era en `actividad_previa`; `/resume_patrol` la lee para saber a cuál de las dos volver. Las transiciones entre suspensiones (`INTERRUMPIDO`→`PAUSADO`, `PAUSADO`↔`MANUAL`) no la tocan — lo que está pendiente no cambia solo porque el robot cambió de forma de esperar. Se limpia (`None`) al llegar a `EN_BASE` y al arrancar una ronda nueva con `/start_patrol`, para que un dato viejo no le gane a un ciclo completo.

**Decisión de producto, a propósito:** ahora que se puede pausar/tomar manual durante `RETORNO`, `/resume_patrol` en ese caso **siempre** termina de volver a la base — no hay forma de decirle "en realidad seguí la ronda, no vayas a la base". Si eso hace falta, hay que esperar a que llegue a `EN_BASE` y mandar `/start_patrol` de nuevo. No hay comando de "cancelar el retorno" — deliberadamente fuera de alcance por ahora, no un olvido.

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
| `resumed` | `resume_patrol`, o auto-reanudación exitosa desde `INTERRUMPIDO`. | `actividad` (`EN_RONDA`/`RETORNO`), `reason` (ausente si fue un humano; `auto_tras_reinicio` si fue sola) |
| `goal_failed` | Cada intento fallido de Nav2 (rechazado, abortado, cancelado sin pedirlo). | `reason`, `fail_count` |
| `round_ended` | Se cierra la ronda. | `result`: `completada` (dio la vuelta completa al circuito y arranca otra al toque), `retorno_a_base` (se cortó por un `return_to_base` exitoso), `falla` (llegó a `FALLA` por reintentos de Nav2), o `localizacion_perdida` (llegó a `FALLA` por perder la confianza en AMCL navegando). |
| `falla_reconocida` | `clear_failure`. | — |
| `reiniciado` | El nodo arranca y encuentra un estado persistido que no era `EN_BASE`/`FALLA` (aterriza en `INTERRUMPIDO` o `PAUSADO` según el caso, ver más abajo). | `estado_anterior` |
| `auto_resume_girando_para_converger` | Desde `INTERRUMPIDO`, se cumplió el timeout y arranca el giro de convergencia antes de decidir. | — |
| `auto_resume_esperando_localizacion` | El giro terminó pero la localización sigue sin ser confiable — no reanuda, reintenta en el próximo ciclo. | — |
| `localizacion_perdida` | La vigilancia continua detectó que se perdió la confianza en AMCL mientras navegaba (`EN_RONDA`/`RETORNO`) — pasa a `FALLA`. | `estado` (desde cuál de los dos) |

**Qué define una "ronda":** una vuelta completa al circuito de waypoints (del primero al último y de nuevo al principio), no un día completo de patrullaje (eso es la "sesión" = el archivo diario, que puede contener muchas rondas). Pausas y modo manual son interrupciones dentro de la misma ronda; un `return_to_base` exitoso, en cambio, la cierra — porque puede quedarse ahí un tiempo indefinido (cargando) y no queremos una ronda "abierta" cruzando archivos de varios días.

Para leer: es texto plano, una línea por evento — alcanza con `jq` mientras no haya un panel:
```bash
jq 'select(.round_id == "20260807-143210123")' rondas/2026-08-07.jsonl
```

Cada línea escrita acá también sale, en el mismo instante y con el mismo JSON, por el tópico ROS `patrol_events` (`std_msgs/String`, QoS volátil — a propósito sin `TRANSIENT_LOCAL`: un evento es un hecho puntual, no un estado, y un suscriptor nuevo no debe recibir el último evento como si acabara de pasar). Esto le da a `comms_agent` un canal de alarmas separado del de estado (`patrol_state`) — ver `comms_agent/README.md`. El JSONL de este archivo sigue siendo el registro autoritativo; el tópico es solo el canal en tiempo real.

## Persistencia de estado (sobrevive reinicios)

El nodo guarda su estado (`state`, `current_goal`, `fail_count`, `round_id`, `actividad_previa`) en un JSON (`state_file`, default `~/.local/share/patrol_fsm/state.json`) cada vez que algo de eso cambia, con escritura atómica (archivo temporal + rename) para no corromperlo si se corta la luz justo en el medio.

Al arrancar, según el estado guardado:

| Estado persistido | Aterriza en | ¿Auto-reanuda? |
|---|---|---|
| `EN_BASE` o `FALLA` | Igual, tal cual | — (`FALLA` sigue exigiendo `/clear_failure`) |
| `EN_RONDA` o `RETORNO` | `INTERRUMPIDO` (`actividad_previa` guarda cuál de las dos era) | Sí, con el gate de localización de la sección de abajo |
| `INTERRUMPIDO` (reinicio encima de otro reinicio sin resolver) | `INTERRUMPIDO`, conservando la `actividad_previa` que ya tenía | Sí, igual que arriba |
| `MANUAL` | `PAUSADO` directo | **No, nunca** — no hay ningún objetivo de navegación que retomar solo, alguien estaba manejando |
| `PAUSADO` | Igual, `PAUSADO` | **No, nunca** — es una pausa deliberada, un humano ya decidió frenarlo |

Cada uno de estos aterrizajes agrega un evento `reiniciado` al registro de ejecuciones (con `estado_anterior`), para que quede documentado por qué esa ronda se demoró.

Si el archivo de estado no existe, está corrupto, o tiene un `current_goal` fuera de rango, se ignora con un warning y arranca fresco desde `EN_BASE` (a diferencia del YAML de waypoints, que sí falla duro si está mal — acá preferimos degradar a un estado seguro antes que impedir que el nodo arranque).

**Por qué `PAUSADO` e `INTERRUMPIDO` son estados distintos, y no uno solo con una bandera:** la primera versión de esto (2026-08-11) usaba un único estado `PAUSADO` más un campo extra (`pause_reason`) para distinguir "pausa deliberada de un operador" de "aterrizaje de un reinicio sin resolver". Funcionaba, pero es el antipatrón clásico de máquinas de estados: dos situaciones con comportamiento distinto (¿se auto-reanuda? ¿quién la puede sacar? ¿a dónde reanuda?) metidas en el mismo estado, distinguidas por contexto oculto en vez de por la propia máquina. Separarlos en dos estados hace explícito lo que antes había que inferir, y el panel/MQTT/log distinguen los dos casos gratis (es el mismo estado que ya se publica, sin escribir nada extra).

### Auto-reanudación desde `INTERRUMPIDO`, si nadie contesta

Quedarse esperando para siempre tras un reinicio suena seguro, pero si es de noche y no hay nadie mirando el panel, el robot deja de patrullar indefinidamente por un simple corte de luz de 2 segundos — contradice el propósito de un robot de seguridad desatendido. Por eso `INTERRUMPIDO` (a diferencia de `PAUSADO`) sí intenta resolverse solo: arranca un temporizador de `auto_resume_timeout_sec` segundos (parámetro/argumento de launch, **default 300 = 5 minutos**). Si nadie mandó `/resume_patrol`, `/pause_patrol`, `/manual_start` ni `/return_to_base` antes de que se cumpla ese plazo, intenta reanudar solo — con los chequeos de la sección siguiente de por medio, nunca a ciegas.

Si el chequeo no pasa, no se da por vencido: se vuelve a intentar en el próximo ciclo, indefinidamente, hasta que alguien intervenga o la localización se confirme buena. Si Nav2 vuelve a fallar navegando después de una auto-reanudación exitosa, cae en el mismo camino de reintentos/`FALLA` de siempre.

**Salir de `INTERRUMPIDO` por decisión humana:** las tres formas (`/resume_patrol`, `/pause_patrol`, `/return_to_base`) matan el temporizador y cualquier giro en curso. `/pause_patrol` en particular es la forma de decirle "dejá de intentar solo, yo lo veo" — pasa a `PAUSADO`, conservando la `actividad_previa` por si más adelante se pide `/resume_patrol` desde ahí.

### Verificación de localización antes de auto-reanudar (implementado 2026-08-11)

Tres capas, probadas en vivo en la simulación, no solo en teoría:

1. **Chequeo de covarianza**: `patrol_node` se suscribe a `amcl_pose` y no auto-reanuda si la incertidumbre de posición u orientación está por encima de un umbral (`amcl_position_covariance_threshold`, default `0.5`; `amcl_orientation_covariance_threshold`, default `0.3` — medidos en vivo en el mapa `depot`, no son valores de manual genérico, que resultaron demasiado exigentes y causaban que nunca reanudara).
2. **Giro de convergencia**: como AMCL no actualiza nada si el robot está quieto (no supera `update_min_d`/`update_min_a` de su config), antes de evaluar la covarianza el nodo manda un giro de 360° en el lugar (acción `Spin` de Nav2) para forzar una lectura real. **Probado en vivo que esto solo no alcanza siempre** — con una pose inicial mal dada a propósito, un segundo giro pasó el chequeo estando igual de mal ubicado (girar prueba orientación, no si la posición de fondo es correcta).
3. **Vigilancia continua mientras navega**: mientras está en `EN_RONDA` o `RETORNO` (nunca en `INTERRUMPIDO`, que todavía no está navegando), cada mensaje nuevo de AMCL se sigue chequeando. Si la incertidumbre está mal **`localizacion_perdida_confirmaciones` veces seguidas** (default 3 — evita que un solo pico transitorio dispare una falla), cancela el objetivo activo y pasa directo a `FALLA` (evento `localizacion_perdida`, necesita `/clear_failure` de un humano). **Esta fue la única capa que en la práctica agarró todos los casos de "pose falsa" probados** — el movimiento real es la única evidencia que expone una localización mentirosa; girar repetidas veces no.

**Límite conocido y aceptado, no una falla de diseño:** las tres capas juntas todavía necesitan que el robot se mueva un poco (~15-20s en las pruebas) antes de poder detectar una localización falsa — no hay forma de saberlo con el robot parado, solo moviéndose. Durante esa ventana, el evitar-obstáculos local de Nav2 (basado en el láser en tiempo real, independiente de si AMCL sabe bien la posición global) sigue protegiendo contra chocar con algo que tenga físicamente cerca — pero no contra lo que un láser 2D no puede ver (pozos, escalones). El chequeo que cerraría esto del todo sin necesitar moverse (comparar el láser contra el mapa directamente) queda pendiente para cuando esté el robot físico.

**Otro límite conocido, encontrado en vivo el mismo día:** una pose que pasa el chequeo de covarianza es "lo suficientemente segura como para intentar", no "perfectamente exacta" — puede tener un error real de algunos centímetros, suficiente para que el robot crea que un hueco angosto (una puerta, una esquina justa) es pasable cuando en realidad no le entra, y termine chocando o reintentando sin éxito ahí. Ni el gate ni el evitar-obstáculos local de Nav2 cubren completamente este caso intermedio. Sin diseño todavía.

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
