# humble_robochi

Workspace de colcon para el prototipo de robot de patrullaje (robot Andino, ROS 2 Humble). Ver `CLAUDE.md` para el contexto completo del proyecto (qué es, decisiones de arquitectura, estado real). Este README es solo una chuleta de comandos — cada paquete tiene su propio README con el detalle de qué hace y por qué.

## Layout

```
humble_robochi/
├── src/andino_gz/       # submodulo, sim de Gazebo (no tocar, es de Ekumen/UNLP)
├── robot_bringup/       # Nav2 + twist_mux + dead-man timer del teleop (desarrollo propio)
├── patrol_fsm/          # nodo de rondas (desarrollo propio)
├── comms_agent/         # puente MQTT <-> ROS (desarrollo propio)
├── panel/               # backend + web del panel (desarrollo propio, no es paquete ROS)
└── patrol_behavior/     # legacy, no se usa
```

## Build y tests

Desde la raíz del workspace:

```bash
source /opt/ros/humble/setup.bash
colcon build --packages-select patrol_fsm comms_agent robot_bringup   # o sin --packages-select para todo
source install/setup.bash
```

**Siempre recompilar después de tocar código y reiniciar el nodo corriendo** — `ros2 run`/`ros2 launch` usan lo compilado en `install/`, no el source directamente.

```bash
colcon test --packages-select patrol_fsm comms_agent
colcon test-result --all   # resumen de pass/fail
```

## Levantar todo (varias terminales)

**Terminal 1 — simulación (Gazebo + RViz sin Nav2, ese lo levanta `robot_bringup`):**
```bash
cd /home/tomashut/humble_robochi
source /opt/ros/humble/setup.bash
source install/setup.bash
ros2 launch andino_gz andino_gz.launch.py nav2:=False rviz:=False world_name:=depot.sdf map:=depot
```
`nav2:=False rviz:=False` es a propósito (desde 2026-08-14) — Nav2 y su RViz con mapa los levanta la Terminal 2 de abajo, para poder meter `twist_mux` en el medio (ver `robot_bringup/README.md`). Gazebo arranca **pausado** — hay que darle ▶ Play a mano. No hace falta repetirlo mientras el mismo Gazebo siga corriendo — solo `patrol_node`/`comms_agent` necesitan reiniciarse entre pruebas.

**Terminal 2 — robot_bringup (Nav2 + twist_mux + dead-man timer del teleop + RViz):**
```bash
cd /home/tomashut/humble_robochi
source /opt/ros/humble/setup.bash
source install/setup.bash
ros2 launch robot_bringup robot_bringup.launch.py map:=depot
```
Acá va el "2D Pose Estimate" manual en RViz (AMCL vive en esta terminal ahora). Sin eso, Nav2 rechaza todos los goals (parece un bug de `patrol_fsm`, pero es esto). **A diferencia de Gazebo, esta terminal sí hay que resetearla con AMCL de nuevo cada vez que se reinicia** — aunque el robot en Gazebo no se haya movido, un `robot_bringup` nuevo trae un AMCL nuevo, sin memoria del pose estimate anterior. Instala `ros-humble-twist-mux` antes de la primera vez (ver `robot_bringup/README.md` si tira un error de librería faltante al arrancar).

**Terminal 3 — patrol_fsm (nodo de rondas + cliente interactivo):**
```bash
cd /home/tomashut/humble_robochi
source /opt/ros/humble/setup.bash
source install/setup.bash
ros2 launch patrol_fsm patrol.launch.py
```
Abre un xterm con el cliente. Comandos: `s` iniciar, `p` pausar, `r` reanudar, `m` manual/teleop (Ctrl+C para volver a pausa), `b` volver a base, `c` limpiar falla, `q` salir. Si esa ventana se cierra sola (Ctrl+C la mata), reconectar con `ros2 run patrol_fsm patrol_client` en cualquier terminal — el robot sigue andando igual, no depende del cliente.

**Terminal 4 — comms_agent (puente MQTT), requiere Mosquitto corriendo:**
```bash
cd /home/tomashut/humble_robochi
source /opt/ros/humble/setup.bash
source install/setup.bash
ros2 launch comms_agent comms_agent.launch.py mqtt_username:=andino mqtt_password:=patrol_2026
```
Probar desde cualquier terminal:
```bash
mosquitto_sub -t 'patrol/default/andino/#' -v -u andino -P patrol_2026
mosquitto_pub -t patrol/default/andino/cmd -m start -u andino -P patrol_2026
```
Comandos válidos: `start`/`pause`/`resume`/`manual_start`/`manual_stop`/`return_to_base`/`clear_failure`.

**Terminal 5 — panel (web):**
```bash
cd /home/tomashut/humble_robochi/panel
python3 manage_users.py tomas   # una sola vez, da de alta un usuario humano
python3 server.py --mqtt-username andino --mqtt-password patrol_2026
```
Abrir `http://localhost:8080` (o la IP de la máquina desde otro dispositivo en la LAN) y loguearse con el usuario de arriba.

## Mosquitto (servicio del sistema)

```bash
sudo systemctl status mosquitto
sudo systemctl stop mosquitto     # para probar reconexión/resiliencia
sudo systemctl start mosquitto
sudo systemctl restart mosquitto  # despues de tocar /etc/mosquitto/conf.d/*
```

## Dónde está cada cosa

- **Config de Mosquitto**: `/etc/mosquitto/conf.d/` (usuario/contraseña dev: `andino`/`patrol_2026` — ver `comms_agent/README.md`).
- **Estado persistido / libro de rondas**: `~/.local/share/patrol_fsm/` (`state.json`, `rondas/AAAA-MM-DD.jsonl`) — placeholder de desarrollo, ver `comms_agent/README.md` y memoria del proyecto para el path real en producción.
- **Waypoints**: `patrol_fsm/config/waypoints.yaml`.
- **Usuarios del panel**: `panel/users.json` (gitignored, no tocar a mano).
