# comms_agent

Agente de comunicaciones: el puente entre MQTT y los servicios ROS 2 de `patrol_fsm`. Es el "único punto de contacto con el exterior" que describe el CLAUDE.md — traduce comandos MQTT en llamados a los servicios de `patrol_node`, y republica el estado del robot y un heartbeat hacia MQTT.

Este README describe el estado actual del paquete y se va a ir actualizando a medida que se agreguen cosas en próximos pushes.

## Qué hace hoy

`comms_agent_node` corre como un nodo ROS 2 más, pero además abre una segunda conexión (MQTT, vía `paho-mqtt`) hacia un broker Mosquitto. Es un traductor con un pie en cada mundo:

```
patrol_node  ⟷ (ROS: tópicos/servicios) ⟷  comms_agent  ⟷ (MQTT: red) ⟷  Mosquitto (broker)  ⟷ (MQTT: red) ⟷  cualquier otro cliente MQTT
```

Tópicos MQTT, con el patrón `patrol/<installation_id>/<robot_id>/...` (parámetros `installation_id`/`robot_id`, default `default`/`andino` — pensado para multi-instalación/multi-robot desde el día uno, aunque hoy haya uno solo):

- **`.../cmd`** (se suscribe, QoS 1) — comandos como texto plano. Payload = una de estas palabras, tal cual estan definidas en `COMMAND_TO_SERVICE` en `comms_agent_node.py`:
  `start` · `pause` · `resume` · `manual_start` · `manual_stop` · `return_to_base` · `clear_failure`
  Cada una llama al servicio ROS correspondiente de `patrol_node` (mismos servicios que ya usa `patrol_client.py`). Son idempotentes de por sí: los servicios de `patrol_node` devuelven `success=False` sin romper nada si se llaman desde un estado que no corresponde.
- **`.../state`** (publica, retenido) — repropaga el tópico ROS `patrol_state` tal cual, cada vez que cambia.
- **`.../heartbeat`** (publica cada `heartbeat_interval_sec`, default 5s) — timestamp, para que algo del otro lado pueda detectar un robot caído aunque no haya cambios de estado.

MQTT en sí no valida nada — no tiene el concepto de "comando válido". Toda la validación de qué es un comando conocido vive en este nodo (`comms_agent`), no en Mosquitto ni en herramientas como `mosquitto_pub`.

Sin VPN todavía — el listener MQTT normal corre local (`127.0.0.1:1883`), solo acepta conexiones de esta misma máquina. El listener de WebSocket para el panel (puerto 9001) *debería* tener la misma restricción pero no la respeta — ver bug documentado más abajo. La capa de WireGuard es trabajo futuro, para cuando haya que exponerlo hacia afuera de verdad.

## Cómo correrlo

Requiere Mosquitto corriendo (`sudo apt install mosquitto mosquitto-clients python3-paho-mqtt`, se instala como servicio systemd y arranca solo) y `patrol_fsm` levantado.

```bash
source /opt/ros/humble/setup.bash
source install/setup.bash
ros2 run comms_agent comms_agent_node
```

O con el launch (mismos parámetros como argumentos, más `mqtt_host`/`mqtt_port`):
```bash
ros2 launch comms_agent comms_agent.launch.py
```

Para probarlo a mano, sin escribir ningún cliente:
```bash
# mirar todo lo que pasa por los tres topicos
mosquitto_sub -t 'patrol/default/andino/#' -v

# mandar un comando
mosquitto_pub -t patrol/default/andino/cmd -m start
```

## Comportamientos no obvios

- **Un `state.json` con `FALLA` persistido de una corrida anterior hace que `patrol_node` arranque directo en `FALLA`**, y por lo tanto `start` por MQTT no va a hacer nada hasta mandar `clear_failure` primero — no es un bug de `comms_agent`, es la persistencia de estado de `patrol_fsm` funcionando como se diseñó (ver README de `patrol_fsm`). Si un comando no parece tener efecto, lo primero a chequear es el estado actual (`mosquitto_sub` al tópico `.../state`, o mirar el log de `patrol_node`).
- Si Gazebo arranca pausado (sin apretar ▶) o sin pose inicial en RViz, no hay odometría (`/odom` no publica nada) y Nav2 rechaza cualquier goal — después de 5 rechazos el robot cae en `FALLA` por las de siempre. No tiene nada que ver con MQTT ni con este agente, es un tema de la simulación.

## Qué falta (conocido, no implementado todavía)

- **Sin VPN (WireGuard)** — no hay capa de seguridad de red todavía.
- **Sin heartbeat/telemetría del lado "detectar robot caído"** — hoy se publica el heartbeat, pero nada consume/vigila que deje de llegar.
- **Sin autenticación/TLS en MQTT** — Mosquitto corre con la config default, sin usuario/contraseña ni certificados.
- **Comandos como texto plano, no JSON** — suficiente para el hito minimo; si en el futuro los comandos necesitan parámetros (por ejemplo, elegir qué ronda correr), va a hacer falta un formato más rico.
- **Bug conocido, no resuelto: el listener de WebSocket (puerto 9001, agregado para el panel v0) no respeta `bind_address`/`listener <puerto> 127.0.0.1`** — a diferencia del listener MQTT normal (puerto 1883), que sí quedó correctamente restringido a `localhost`. Probamos dos sintaxis de configuración distintas (`listener 9001 127.0.0.1` y `bind_address 127.0.0.1` como directiva separada, esta segunda ademas rota porque `bind_address` es una opcion de listener "default" unica, no se puede repetir por listener) y en ambos casos el puerto 9001 sigue escuchando en todas las interfaces de red (`ss -tln` muestra `*:9001` en vez de `127.0.0.1:9001`). Parece una limitación real de Mosquitto 2.0.11 (la version que instala `apt` en Ubuntu 22.04) con el listener de WebSockets especificamente, no un error de configuración nuestro. **Riesgo real hoy:** cualquier dispositivo en la misma red local (WiFi/cable) podria conectarse al panel via MQTT-sobre-WebSocket, no solo esta maquina — acotado (no es exposicion a internet), pero real. Sin resolver todavia; posibles mitigaciones futuras: regla de firewall (`ufw`) bloqueando el puerto 9001 desde otras interfaces, actualizar Mosquitto a una version mas nueva, o resolverlo cuando se implemente la capa de VPN/autenticacion real.
