# Contexto del proyecto — Robot autónomo de seguridad

## Qué es esto
Prototipo de robot de patrullaje para seguridad electrónica (depósitos, plantas, oficinas, barrios cerrados), desarrollado sobre el robot **Andino** (open source, Ekumen/UNLP, ROS 2 + Nav2). Objetivo de largo plazo: producto propio con rover para exteriores. El negocio de fondo: empresa de seguridad electrónica (instalación y monitoreo de alarmas/cámaras) que quiere una división de robótica e ingeniería.

## Estado REAL del proyecto (no confundir diseño con hecho)
- **Hecho:** lógica de patrulla funcionando EN SIMULACIÓN (Gazebo): ronda por waypoints, interrupción, control manual y reanudación. Se hizo hace tiempo; hay que verificar que aún levanta.
- **Incierto:** el Andino físico está prestado/en manos de UNLP. Tuvo problemas de hardware; avisaron que está arreglado y que Nav2 "supuestamente" anda. NO verificado.
- **Solo diseño (cero código):** backend, panel, detección IA, agente MQTT.

## Decisiones de arquitectura ya tomadas
- **Frontera de propiedad:** ROS 2 + Nav2 + slam_toolbox + AMCL + ros2_control se usan tal cual (open source de grado industrial, NO reescribir). El desarrollo propio se concentra en la capa de aplicación de seguridad.
- **Nodo de rondas** (desarrollo propio): máquina de estados EN_BASE / EN_RONDA / PAUSADO / MANUAL / RETORNO / FALLA / INTERRUMPIDO (+ futuro INVESTIGANDO para despachos por alerta externa). `INTERRUMPIDO` (agregado 2026-08-11) es el aterrizaje de un reinicio no planeado a mitad de ronda/retorno — a diferencia de `PAUSADO` (solo alcanzable por decisión humana, nunca se auto-reanuda), `INTERRUMPIDO` sí intenta retomar la actividad solo, con verificación de localización (AMCL) de por medio; ver `patrol_fsm/README.md` para el detalle completo. Rondas definidas como DATOS (YAML: waypoints, acciones por punto, agenda), no hardcodeadas. Interrupción = cancelar goal de Nav2 y congelar índice de waypoint; reanudación = replanificar desde la posición actual al waypoint pendiente. Estado persistido a disco (sobrevive reinicios). twist_mux con prioridades: e-stop > teleoperación > navegación.
- **Trazabilidad:** cada ejecución de ronda escribe su registro (inicio, waypoints con timestamp, interrupciones, resultado) — "libro de rondas digital", es entregable del producto.
- **Agente de comunicaciones** (desarrollo propio): único punto de contacto con el exterior. MQTT sobre VPN (WireGuard). Publica telemetría/eventos/heartbeat; recibe comandos (iniciar/pausar/reanudar/abortar ronda). Comandos idempotentes, QoS 1. Teleoperación con dead-man timer (~500 ms sin consigna → robot se detiene).
- **Video:** NO viaja por tópicos ROS hacia afuera. Cámara CCTV IP comercial (Hikvision/Dahua del catálogo habitual) montada en el robot como payload de vigilancia: emite RTSP nativo, el VMS de la central la da de alta como una cámara más. Cámara de percepción aparte (USB/profundidad, frames crudos a la computadora) para navegación y teleoperación de baja latencia (WebRTC a futuro).
- **Red del robot:** todo cableado adentro (cámara→computadora por ethernet/USB); el ÚNICO enlace inalámbrico es robot↔mundo (WiFi del predio y/o 4G, dentro de VPN). Por eso la detección conviene a bordo en producción: por la radio viajan eventos y clips, no video continuo obligatorio. Grabación local en el robot como respaldo.
- **Detección IA:** servicio con FUENTE DE VIDEO DESACOPLADA (interfaz "fuente de cuadros" con dos implementaciones: tópico ROS y cliente RTSP). Pipeline: cuadro → YOLO (detección de persona/vehículo) → capa de reglas (zona + horario + persistencia temporal de N segundos para filtrar falsos positivos) → evento con clip → MQTT → alerta. Muestreo de 2–5 fps alcanza (no analizar los 25 fps). CRÍTICO: política de descarte de cuadros viejos (analizar siempre el más reciente) para que no se acumule latencia. Licencia: Ultralytics YOLO es AGPL (para prototipo OK; para comercializar, licencia paga o alternativa permisiva).
- **Hardware objetivo producción:** Jetson Orin Nano como cerebro (el Andino usa Raspberry Pi 4, sin capacidad para IA a bordo). Rover con ruedas (no cuadrúpedo) para exteriores, GPS RTK, cámara IR.
- **Descartado por ahora:** integración con receptora de alarmas (los eventos viven en panel propio con video y mapa); motor de despacho cámara-fija→robot (diseñado, queda para después); todo el backend/videoanalítica avanzado.

## Plan de trabajo en simulación (orden acordado)
1. **Endurecer el nodo de rondas**: refactor a máquina de estados formal + servicios ROS + persistencia + twist_mux.
2. **Rondas como datos**: YAML + registro de ejecuciones.
3. **Agente MQTT mínimo**: Mosquitto local + nodo puente. Hito: comandar la ronda desde una terminal fuera de ROS publicando MQTT.
4. **Panel v0**: web mínima (mapa con posición en vivo vía MQTT/WebSocket, estado, botones iniciar/pausar/reanudar).
5. **Detección de personas**, dos vías con el mismo circuito:
   - **5a (independiente de ROS, puede hacerse ya):** cliente RTSP contra una Hikvision real de la oficina, OpenCV decodifica, YOLO detecta, evento por MQTT. Valida el modelo con video real.
   - **5b:** misma detección consumiendo la cámara simulada de Gazebo vía tópico ROS, con figuras humanas en el mundo. Valida la integración con el robot (evento con posición/contexto de punta a punta).

## Próximo paso inmediato
Levantar la simulación existente y verificar que aún funciona (el software de robótica se rompe con el tiempo: versiones de ROS/Gazebo/Ubuntu). Si no está en git, inicializar repo y commit de todo ANTES de tocar nada. Después, punto 1.

## Checklist para el día que vuelva el Andino físico (en orden, cada paso depende del anterior)
1. Odometría: pedirle 2 m reales y 360° y comparar contra la realidad.
2. LiDAR: scan limpio, sin falsos ecos.
3. Mapear con slam_toolbox el espacio REAL de operación (no el laboratorio).
4. Localización estable con AMCL (no perderse al moverlo a mano o con gente cerca).
5. Nav2 punto a punto: 20 corridas consecutivas sin pérdida de localización.
6. Recién entonces: portar el nodo de rondas de simulación al físico.

## Convenciones
- Todo el desarrollo en ROS 2 (distro que soporte el Andino: Humble o Jazzy).
- Diseñar multi-robot/multi-instalación desde el día uno en cualquier estructura de datos, aunque haya un solo robot.
- Ante fallas, el robot siempre queda detenido y reportando; nunca moviéndose a ciegas ni mudo.
