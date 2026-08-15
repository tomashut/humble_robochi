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

**Bug real encontrado y corregido en vivo (2026-08-12):** `/start_patrol` no reseteaba `current_goal` — si un `return_to_base` anterior había cortado la ronda a mitad de camino (por ejemplo en el waypoint 3), el índice quedaba guardado ahí en `state.json` para siempre, y la próxima ronda arrancada a mano saltaba directo a ese waypoint intermedio en vez de empezar desde el principio del circuito. Corregido: `handle_start_patrol` ahora resetea `current_goal = 0` junto con `actividad_previa`. No confundir con `INTERRUMPIDO`/`resume_patrol` — ahí sí corresponde retomar exactamente donde quedó, porque es la misma ronda continuando; esto era específicamente el caso de una ronda **nueva**, iniciada a mano desde `EN_BASE`.

`patrol_client.py` es un cliente de terminal interactivo para probar todo esto a mano: `s`/`p`/`r`/`m`/`b`/`c`/`q`. El comando `m` encadena `manual_start` → `teleop_twist_keyboard` → `manual_stop` automáticamente al salir del teleop con Ctrl+C. `teleop_twist_keyboard` se lanza con su salida remapeada a `cmd_vel_teleop` (no al `cmd_vel` final) — ver "Arbitraje de velocidad" más abajo.

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
| `reiniciado` | El nodo arranca y encuentra un estado persistido que no era `EN_BASE`/`FALLA` (aterriza en `INTERRUMPIDO` o `PAUSADO` según el caso, ver más abajo). | `estado_anterior`, `segundos_desde_guardado` (ausente si el `state.json` es de antes de este campo, o si `saved_at` está corrupto) |
| `auto_resume_girando_para_converger` | Desde `INTERRUMPIDO`, se cumplió el timeout y arranca el giro de convergencia antes de decidir. | — |
| `auto_resume_esperando_localizacion` | El giro terminó pero la localización sigue sin ser confiable — no reanuda, reintenta en el próximo ciclo. | — |
| `localizacion_perdida` | La vigilancia continua detectó que se perdió la confianza en AMCL mientras navegaba (`EN_RONDA`/`RETORNO`) — pasa a `FALLA`. | `estado` (desde cuál de los dos) |
| `guardado_estado_fallido` | `_save_state()` no pudo escribir `state.json` (disco lleno/solo-lectura). El robot sigue andando igual. | `error` |
| `disco_casi_lleno` | El espacio libre en disco cayó por debajo de `disk_free_warning_pct` (default 10%). Una sola vez por caída, no en cada chequeo. | `libre_pct` |
| `disco_normalizado` | El espacio libre volvió a estar por encima del umbral tras un `disco_casi_lleno`. | `libre_pct` |
| `enlace_perdido` | `comms_agent` avisó (tópico `link_status`) que se cortó el enlace a Mosquitto. | — |
| `enlace_perdido_prolongado` | El corte sigue activo tras `link_loss_grace_sec` (default 600s = 10 min) sin recuperarse. Se dispara siempre, sea cual sea `on_link_loss`. | — |
| `enlace_restablecido` | El enlace volvió. | `duracion_sec` |

**Qué define una "ronda":** una vuelta completa al circuito de waypoints (del primero al último y de nuevo al principio), no un día completo de patrullaje (eso es la "sesión" = el archivo diario, que puede contener muchas rondas). Pausas y modo manual son interrupciones dentro de la misma ronda; un `return_to_base` exitoso, en cambio, la cierra — porque puede quedarse ahí un tiempo indefinido (cargando) y no queremos una ronda "abierta" cruzando archivos de varios días.

Para leer: es texto plano, una línea por evento — alcanza con `jq` mientras no haya un panel:
```bash
jq 'select(.round_id == "20260807-143210123")' rondas/2026-08-07.jsonl
```

Cada línea escrita acá también sale, en el mismo instante y con el mismo JSON, por el tópico ROS `patrol_events` (`std_msgs/String`, QoS volátil — a propósito sin `TRANSIENT_LOCAL`: un evento es un hecho puntual, no un estado, y un suscriptor nuevo no debe recibir el último evento como si acabara de pasar). Esto le da a `comms_agent` un canal de alarmas separado del de estado (`patrol_state`) — ver `comms_agent/README.md`. El JSONL de este archivo sigue siendo el registro autoritativo; el tópico es solo el canal en tiempo real.

## Persistencia de estado (sobrevive reinicios)

El nodo guarda su estado (`state`, `current_waypoint`, `fail_count`, `round_id`, `actividad_previa`, `saved_at`) en un JSON (`state_file`, default `~/.local/share/patrol_fsm/state.json`) cada vez que algo de eso cambia, con escritura atómica (archivo temporal + rename) para no corromperlo si se corta la luz justo en el medio.

**Se persiste el nombre del waypoint pendiente, no su posición en la lista (2026-08-14).** Antes se guardaba `current_goal` como índice crudo (`3`, por ejemplo). El problema: ese índice solo es válido mientras `waypoints.yaml` no cambie entre el guardado y la próxima carga. Si entre medio alguien edita la ronda — agrega una parada nueva, saca una, las reordena — todos los índices posteriores al cambio se corren, sin que nadie haya tocado el waypoint que en realidad estaba pendiente. El robot, al reiniciar, retomaba en "la posición 3 de la lista actual", que podía ser un waypoint físicamente distinto del que había quedado pendiente — sin ningún error, sin ningún síntoma, un punto de la instalación silenciosamente sin cubrir esa noche. Ahora se guarda `current_waypoint` con el **nombre** del waypoint (`'wp3'`); al cargar, se busca ese nombre en la lista actual y se resuelve a la posición que le corresponda hoy, sea cual sea. Internamente el nodo sigue trabajando con un índice en memoria (`current_goal`) — la resolución por nombre pasa solo en el borde de persistencia (`_save_state`/`_load_saved_state`), no hizo falta tocar el resto de la lógica.

**Resiliente a disco lleno/solo-lectura (2026-08-13).** `_save_state()` se llama desde adentro de callbacks de acción de Nav2 — si la escritura fallara con una excepción sin capturar (disco lleno, tarjeta SD en modo solo-lectura, algo real en una Raspberry Pi que lleva meses prendida), esa excepción mataría el nodo entero en medio de una ronda, sin pasar por `FALLA`, sin loguear nada, sin avisar a nadie: justo el "queda mudo" que el proyecto busca evitar. Ahora la escritura está en un try/except: si falla, el robot sigue andando (un reinicio en ese momento retomaría con estado desactualizado, riesgo ya conocido, ver más abajo) y se dispara un evento `guardado_estado_fallido` por el canal de eventos (`patrol_events`/`.../events`, ver más abajo). `_log_event()` en sí también quedó blindado — si el JSONL de rondas tampoco se puede escribir, igual publica el evento por el tópico ROS, para que `guardado_estado_fallido` no termine fallando al intentar registrarse a sí mismo.

**Aviso preventivo antes de llegar a ese punto.** Un chequeo periódico (`_check_disk_space`, cada `DISK_CHECK_INTERVAL_SEC`=60s) mide el espacio libre en el disco donde vive `state_file` y dispara un evento `disco_casi_lleno` si cae por debajo de `disk_free_warning_pct` (parámetro, default 10%) — una sola vez mientras se mantenga por debajo del umbral (no en cada chequeo), y un `disco_normalizado` cuando se recupera.

Al arrancar, según el estado guardado:

| Estado persistido | Aterriza en | ¿Auto-reanuda? |
|---|---|---|
| `EN_BASE` o `FALLA` | Igual, tal cual | — (`FALLA` sigue exigiendo `/clear_failure`) |
| `EN_RONDA` o `RETORNO` | `INTERRUMPIDO` (`actividad_previa` guarda cuál de las dos era) | Sí, con el gate de localización de la sección de abajo |
| `INTERRUMPIDO` (reinicio encima de otro reinicio sin resolver) | `INTERRUMPIDO`, conservando la `actividad_previa` que ya tenía | Sí, igual que arriba |
| `MANUAL` | `PAUSADO` directo | **No, nunca** — no hay ningún objetivo de navegación que retomar solo, alguien estaba manejando |
| `PAUSADO` | Igual, `PAUSADO` | **No, nunca** — es una pausa deliberada, un humano ya decidió frenarlo |

Cada uno de estos aterrizajes agrega un evento `reiniciado` al registro de ejecuciones (con `estado_anterior`), para que quede documentado por qué esa ronda se demoró.

**`saved_at` — desde cuándo estaba pendiente lo que se retoma (2026-08-15).** El payload de `state.json` ahora incluye `saved_at` (mismo formato ISO que usa `_log_event()` en el JSONL), actualizado en cada guardado. Al reiniciar, si el estado persistido no era `EN_BASE`/`FALLA`, el evento `reiniciado` lleva además `segundos_desde_guardado` — sin esto, un reinicio de 30 segundos y uno de 3 días se veían exactamente igual, y en ambos casos se retomaba la misma ronda vieja sin ninguna señal de que había pasado tanto tiempo. Es puramente informativo por ahora — no cambia ninguna decisión de la FSM, solo mejora la trazabilidad del libro de rondas. `state.json` de antes de este campo (sin `saved_at`) o con un valor corrupto (por ejemplo, editado a mano) siguen cargando bien — el campo se omite del evento en vez de tumbar el arranque.

Si el archivo de estado no existe, está corrupto, o su `current_waypoint` ya no existe en la lista actual de waypoints (por ejemplo, se borró esa parada), se ignora con un warning y arranca fresco desde `EN_BASE` (a diferencia del YAML de waypoints, que sí falla duro si está mal — acá preferimos degradar a un estado seguro antes que impedir que el nodo arranque).

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

### Qué hace el robot si pierde el enlace hacia la central (implementado 2026-08-13)

`comms_agent` avisa a `patrol_node` (tópico ROS `link_status`, ver `comms_agent/README.md`) cuando se corta o se recupera su conexión a Mosquitto. La pregunta de fondo, investigada contra cómo lo resuelve la industria (patrones de "retro-traverse" en robots teleoperados, y la estrategia de auto-reparación de RCAMP para robots autónomos): ¿qué vale más durante un corte, un robot patrullando sin reportar o un robot quieto/volviendo a base? Para un servicio de seguridad, la respuesta elegida es **patrullando**: la disuasión (robot visible, moviéndose) sigue funcionando sin red, y el libro de rondas sigue escribiéndose a disco — cuando vuelve el enlace, la evidencia de que las rondas se hicieron está completa. Una política que frena la patrulla ante cualquier corte le regala a un intruso un método barato (cortar el WiFi apaga la ronda), y eso es un agujero de diseño en un producto de seguridad, no una salvaguarda.

**Dos decisiones de diseño, para que no se malinterprete si se retoca esto:**
1. **La pérdida de enlace no es un estado de la FSM ni una `FALLA`.** Es una condición ortogonal a la actividad — el robot puede estar `EN_RONDA` con o sin enlace. Meterla como estado duplicaría la máquina entera; y no es `FALLA` porque no hay nada roto que un humano deba limpiar — la recuperación es automática en cuanto vuelve el enlace.
2. **Si la política manda a `RETORNO` y el enlace se recupera a mitad de camino, no pasa nada automático** — termina de volver a base igual, la central ve el estado resincronizado (ver `comms_agent/README.md`) y decide si relanza la ronda. Evita el mismo problema de "reanudaciones automáticas en cadena" que ya se pensó para `INTERRUMPIDO`.

**Parámetros** (`on_link_loss`, default `continue`; `link_loss_grace_sec`, default `600` = 10 min):
- `continue` (default): no cambia nada del comportamiento del robot. Sigue patrullando.
- `return_to_base`: si el corte supera `link_loss_grace_sec`, vuelve a base (que suele estar cerca del punto de acceso a la red — la versión práctica de "moverse hacia una posición con conexión segura").
- `pause`: se detiene donde está. Documentado pero no recomendado — robot quieto e invisible para la central es lo peor de los tres casos.

La política solo actúa si el robot está `EN_RONDA` en ese momento (desde `PAUSADO`/`MANUAL`/`FALLA`/`EN_BASE` no hay nada que "continuar" o "volver"). El evento `enlace_perdido_prolongado`, en cambio, se dispara siempre que se cumple el umbral, **incluso con la política en `continue`** — es puramente para que el panel lo marque con más urgencia si alguien está mirando en vivo, no dispara ninguna acción por sí solo.

### Arbitraje de velocidad y dead-man timer del teleop — ahora en `robot_bringup` (implementado 2026-08-14)

Nav2 y el teleop ya no escriben los dos al mismo `cmd_vel` sin árbitro, y el dead-man timer de ~500ms que pide el CLAUDE.md ya está implementado de verdad. Se resolvió en un paquete nuevo y separado, **`robot_bringup`** (no adentro de `patrol_fsm` ni tocando el submódulo `andino_gz`) — ver `robot_bringup/README.md` para el detalle completo (por qué es un paquete aparte, cómo arbitra `twist_mux`, y un hallazgo importante: `twist_mux` no trae un dead-man timer propio, hubo que agregarle un watchdog al lado).

Lo único que cambió de este lado: `patrol_client.py` remapea la salida de `teleop_twist_keyboard` a `cmd_vel_teleop` en vez del `cmd_vel` final (una línea), para que `robot_bringup` pueda arbitrarlo. Nada de la máquina de estados se tocó — `patrol_fsm` sigue sin saber que `twist_mux` existe.

## Comportamientos no obvios (aprendidos probando en simulación)

- **`start_patrol` y `resume_patrol` no "arrancan de cero"**: `current_goal` (el índice del waypoint pendiente) solo avanza cuando se completa un waypoint estando en `EN_RONDA`. Ni `return_to_base` ni `clear_failure` lo resetean. Entonces si mandás el robot a la base a mitad de ronda (`b`) y después arrancás de nuevo (`s`), retoma en el waypoint donde había quedado, no en el 0. Es el comportamiento buscado (no repetir lo ya recorrido), pero el nombre `start_patrol` puede confundir.
- **Ctrl+C en el prompt `>` del cliente lo mata**, no solo cancela el comando en curso — `patrol_client` se cierra por completo (el robot sigue su goal actual sin enterarse, eso está bien). Como el launch levanta el cliente con `xterm -hold`, la ventana queda "viva" pero inútil, no vuelve a lanzar el cliente solo. Para recuperar el control: en cualquier terminal, `source install/setup.bash && ros2 run patrol_fsm patrol_client` — se reconecta a los mismos servicios del `patrol_node`, que sigue corriendo.
- Dentro del modo manual (`m`), en cambio, Ctrl+C es el flujo esperado: corta el teleop y vuelve al prompt en `PAUSADO`.

## Qué falta (conocido, no implementado todavía)

- **Sin acciones por punto ni agenda/horario** en el YAML de waypoints.
- **Sin disparo automático de retorno a base** por batería baja — `return_to_base` es siempre manual.
