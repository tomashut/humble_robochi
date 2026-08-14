import json
import math

import paho.mqtt.client as mqtt

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, QoSDurabilityPolicy
from geometry_msgs.msg import PoseWithCovarianceStamped
from std_msgs.msg import Bool, String
from std_srvs.srv import Trigger


COMMAND_TO_SERVICE = {
    'start': '/start_patrol',
    'pause': '/pause_patrol',
    'resume': '/resume_patrol',
    'manual_start': '/manual_start',
    'manual_stop': '/manual_stop',
    'return_to_base': '/return_to_base',
    'clear_failure': '/clear_failure',
}


class CommsAgentNode(Node):

    def __init__(self, **kwargs):
        super().__init__('comms_agent_node', **kwargs)

        self.declare_parameter('mqtt_host', 'localhost')
        self.declare_parameter('mqtt_port', 1883)
        self.declare_parameter('mqtt_username', '')
        self.declare_parameter('mqtt_password', '')
        self.declare_parameter('installation_id', 'default')
        self.declare_parameter('robot_id', 'andino')
        self.declare_parameter('heartbeat_interval_sec', 5.0)

        mqtt_host = self.get_parameter('mqtt_host').get_parameter_value().string_value
        mqtt_port = self.get_parameter('mqtt_port').get_parameter_value().integer_value
        mqtt_username = self.get_parameter('mqtt_username').get_parameter_value().string_value
        mqtt_password = self.get_parameter('mqtt_password').get_parameter_value().string_value
        installation_id = self.get_parameter('installation_id').get_parameter_value().string_value
        robot_id = self.get_parameter('robot_id').get_parameter_value().string_value
        heartbeat_interval_sec = (
            self.get_parameter('heartbeat_interval_sec').get_parameter_value().double_value)

        base_topic = f'patrol/{installation_id}/{robot_id}'
        self.cmd_topic = f'{base_topic}/cmd'
        self.state_topic = f'{base_topic}/state'
        self.heartbeat_topic = f'{base_topic}/heartbeat'
        self.position_topic = f'{base_topic}/position'
        self.events_topic = f'{base_topic}/events'

        self._service_clients = {
            command: self.create_client(Trigger, service_name)
            for command, service_name in COMMAND_TO_SERVICE.items()
        }

        # ultimo estado/posicion conocidos, para volver a publicarlos si se
        # reconecta a Mosquitto -- ROS sigue avisando cambios aunque el
        # enlace a Mosquitto este caido, asi que esto queda siempre al dia.
        self._last_state = None
        self._last_position = None

        state_qos = QoSProfile(depth=1, durability=QoSDurabilityPolicy.TRANSIENT_LOCAL)
        self.create_subscription(String, 'patrol_state', self._on_patrol_state, state_qos)
        self.create_subscription(PoseWithCovarianceStamped, 'amcl_pose', self._on_amcl_pose, 10)
        self.create_subscription(String, 'patrol_events', self._on_patrol_event, 10)
        # TRANSIENT_LOCAL para que patrol_node se entere del ultimo estado de
        # enlace conocido aunque arranque despues -- el broker en si es el
        # proxy de "hay enlace hacia la central" (vive del otro lado del
        # WireGuard en produccion).
        self._link_status_pub = self.create_publisher(Bool, 'link_status', state_qos)

        # client_id fijo + clean_session=False: le pide a Mosquitto que
        # retenga los comandos QoS 1 (topico .../cmd) que lleguen mientras
        # este desconectado, y los entregue al reconectar -- con
        # clean_session=True (el default de la libreria) el broker no tiene
        # de quien acordarse entre conexiones, y un comando mandado durante
        # un corte se pierde para siempre, aunque el enlace vuelva. clean_id
        # fijo es necesario: paho no deja combinar clean_session=False con
        # un client_id auto-generado (cambia en cada conexion, Mosquitto no
        # lo reconoceria como "el mismo cliente de antes").
        client_id = f'comms_agent-{installation_id}-{robot_id}'
        self._mqtt = mqtt.Client(client_id=client_id, clean_session=False)
        if mqtt_username:
            self._mqtt.username_pw_set(mqtt_username, mqtt_password)
        self._mqtt.on_connect = self._on_mqtt_connect
        self._mqtt.on_disconnect = self._on_mqtt_disconnect
        self._mqtt.on_message = self._on_mqtt_message
        self.get_logger().info(f'Conectando a Mosquitto en {mqtt_host}:{mqtt_port}...')
        # connect_async (en vez de connect) no hace el handshake TCP aca mismo
        # -- lo delega al loop de background, que reintenta solo. Si usaramos
        # connect(), Mosquitto no estando listo en este instante (reinicio no
        # atendido del robot, por ejemplo) tira una excepcion sin capturar que
        # mata el nodo entero antes de arrancar.
        self._mqtt.connect_async(mqtt_host, mqtt_port)
        self._mqtt.loop_start()

        self.create_timer(heartbeat_interval_sec, self._publish_heartbeat)

        self.get_logger().info(
            f'Agente listo. Comandos en "{self.cmd_topic}" '
            f'({"/".join(COMMAND_TO_SERVICE.keys())}). '
            f'Estado en "{self.state_topic}". Heartbeat en "{self.heartbeat_topic}". '
            f'Posicion en "{self.position_topic}". Eventos en "{self.events_topic}".')

    # -- MQTT -> ROS --------------------------------------------------------

    def _on_mqtt_connect(self, client, userdata, flags, rc):
        if rc != 0:
            self.get_logger().error(f'Fallo la conexion a Mosquitto (rc={rc}).')
            return
        client.subscribe(self.cmd_topic, qos=1)
        self.get_logger().info(f'Conectado a Mosquitto, suscripto a "{self.cmd_topic}".')
        self._link_status_pub.publish(Bool(data=True))

        # si esto es una reconexion, ROS ya nos pudo haber avisado cambios
        # mientras estabamos desconectados de Mosquitto -- los reenviamos
        # ahora para que nadie se quede con un valor viejo.
        if self._last_state is not None:
            client.publish(self.state_topic, self._last_state, qos=1, retain=True)
        if self._last_position is not None:
            client.publish(self.position_topic, self._last_position, qos=1, retain=True)

    def _on_mqtt_disconnect(self, client, userdata, rc):
        self.get_logger().warn(f'Desconectado de Mosquitto (rc={rc}).')
        self._link_status_pub.publish(Bool(data=False))

    def _on_mqtt_message(self, client, userdata, msg):
        command = msg.payload.decode('utf-8').strip()
        service_client = self._service_clients.get(command)
        if service_client is None:
            self.get_logger().warn(
                f'Comando MQTT desconocido: "{command}" '
                f'(validos: {", ".join(COMMAND_TO_SERVICE.keys())})')
            return

        self.get_logger().info(f'Comando MQTT recibido: {command}')
        future = service_client.call_async(Trigger.Request())
        future.add_done_callback(lambda f, cmd=command: self._on_service_response(cmd, f))

    def _on_service_response(self, command, future):
        response = future.result()
        if response.success:
            self.get_logger().info(f'{command}: {response.message}')
        else:
            self.get_logger().warn(f'{command} rechazado: {response.message}')

    # -- ROS -> MQTT --------------------------------------------------------

    def _on_patrol_state(self, msg):
        self._last_state = msg.data
        self._mqtt.publish(self.state_topic, msg.data, qos=1, retain=True)

    def _on_patrol_event(self, msg):
        # sin retain, a proposito: un evento es un hecho puntual ("esto
        # paso a tal hora"), no un estado -- un panel que se conecta manana
        # no debe recibir la alarma de anoche como si fuera nueva. La
        # alarma persiste-hasta-reconocida es responsabilidad del
        # panel/central, no de este reenvio.
        self._mqtt.publish(self.events_topic, msg.data, qos=1, retain=False)

    def _on_amcl_pose(self, msg):
        position = msg.pose.pose.position
        orientation = msg.pose.pose.orientation
        yaw = 2 * math.atan2(orientation.z, orientation.w)
        payload = json.dumps({'x': position.x, 'y': position.y, 'yaw': yaw})
        self._last_position = payload
        self._mqtt.publish(self.position_topic, payload, qos=1, retain=True)

    def _publish_heartbeat(self):
        # qos=0 a proposito, distinto del resto: un heartbeat solo vale si
        # llega ahora. Uno viejo reencolado y entregado tarde tras un corte
        # no es informacion, es ruido -- puede hacerle creer al panel que
        # hay senal reciente cuando en realidad es un reintento de algo viejo.
        now = self.get_clock().now().to_msg()
        self._mqtt.publish(self.heartbeat_topic, f'{now.sec}.{now.nanosec}', qos=0)


def main(args=None):
    rclpy.init(args=args)
    node = CommsAgentNode()
    rclpy.spin(node)
    rclpy.shutdown()


if __name__ == '__main__':
    main()
