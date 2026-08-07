import json
from datetime import date, datetime
from enum import Enum
from pathlib import Path

import yaml

import rclpy
from rclpy.node import Node
from rclpy.action import ActionClient
from rclpy.qos import QoSProfile, QoSDurabilityPolicy

from action_msgs.msg import GoalStatus
from ament_index_python.packages import get_package_share_directory
from geometry_msgs.msg import PoseStamped
from nav2_msgs.action import NavigateToPose
from std_msgs.msg import String
from std_srvs.srv import Trigger
from tf_transformations import quaternion_from_euler


class PatrolState(Enum):
    EN_BASE = 'EN_BASE'
    EN_RONDA = 'EN_RONDA'
    PAUSADO = 'PAUSADO'
    MANUAL = 'MANUAL'
    RETORNO = 'RETORNO'
    FALLA = 'FALLA'


class PatrolNode(Node):

    MAX_RETRIES = 5
    RETRY_DELAY_SEC = 1.0
    NEXT_WAYPOINT_DELAY_SEC = 1.0

    def __init__(self):
        super().__init__('patrol_node')

        default_waypoints_file = str(
            Path(get_package_share_directory('patrol_fsm')) / 'config' / 'waypoints.yaml')
        self.declare_parameter('waypoints_file', default_waypoints_file)
        waypoints_file = self.get_parameter('waypoints_file').get_parameter_value().string_value
        self.waypoints = self.load_waypoints(waypoints_file)

        default_rounds_log_dir = str(Path.home() / '.local' / 'share' / 'patrol_fsm' / 'rondas')
        self.declare_parameter('rounds_log_dir', default_rounds_log_dir)
        rounds_log_dir = self.get_parameter('rounds_log_dir').get_parameter_value().string_value
        self.rounds_log_dir = Path(rounds_log_dir).expanduser()
        self.rounds_log_dir.mkdir(parents=True, exist_ok=True)
        self.round_id = None

        self.current_goal = 0
        self.fail_count = 0
        self._current_goal_handle = None
        self._expected_cancel = False

        self.state = PatrolState.EN_BASE

        state_qos = QoSProfile(depth=1, durability=QoSDurabilityPolicy.TRANSIENT_LOCAL)
        self._state_pub = self.create_publisher(String, 'patrol_state', state_qos)

        self._action_client = ActionClient(self, NavigateToPose, 'navigate_to_pose')

        self.create_service(Trigger, '/start_patrol', self.handle_start_patrol)
        self.create_service(Trigger, '/pause_patrol', self.handle_pause_patrol)
        self.create_service(Trigger, '/resume_patrol', self.handle_resume_patrol)
        self.create_service(Trigger, '/manual_start', self.handle_manual_start)
        self.create_service(Trigger, '/manual_stop', self.handle_manual_stop)
        self.create_service(Trigger, '/return_to_base', self.handle_return_to_base)
        self.create_service(Trigger, '/clear_failure', self.handle_clear_failure)

        self.get_logger().info('Esperando al servidor de acción NavigateToPose...')
        self._action_client.wait_for_server()
        self.get_logger().info('Servidor listo.')

        self._enter_state(PatrolState.EN_BASE)

    # -- utilidades de pose -------------------------------------------------

    def load_waypoints(self, waypoints_file):
        self.get_logger().info(f'Cargando waypoints desde {waypoints_file}')
        with open(waypoints_file) as f:
            data = yaml.safe_load(f)

        waypoints = data['waypoints']
        if not waypoints:
            raise ValueError(f'{waypoints_file} no define ningun waypoint.')

        self.waypoint_names = [wp['name'] for wp in waypoints]
        return [
            self.create_pose(wp['x'], wp['y'], wp['yaw'])
            for wp in waypoints
        ]

    def create_pose(self, x, y, yaw):
        pose = PoseStamped()
        pose.header.frame_id = 'map'
        pose.header.stamp = self.get_clock().now().to_msg()
        pose.pose.position.x = x
        pose.pose.position.y = y

        q = quaternion_from_euler(0, 0, yaw)
        pose.pose.orientation.x = q[0]
        pose.pose.orientation.y = q[1]
        pose.pose.orientation.z = q[2]
        pose.pose.orientation.w = q[3]
        return pose

    # -- máquina de estados ---------------------------------------------------

    def _enter_state(self, new_state):
        self.state = new_state
        self.get_logger().info(f'Estado -> {new_state.value}')
        msg = String()
        msg.data = new_state.value
        self._state_pub.publish(msg)

    def _call_later(self, delay_sec, fn):
        timer = self.create_timer(delay_sec, lambda: self._fire_once(timer, fn))

    def _fire_once(self, timer, fn):
        timer.cancel()
        fn()

    # -- registro de rondas ("libro de rondas digital") -----------------------

    def _log_event(self, event, **fields):
        entry = {
            'ts': datetime.now().astimezone().isoformat(timespec='seconds'),
            'round_id': self.round_id,
            'event': event,
        }
        entry.update(fields)

        log_path = self.rounds_log_dir / f'{date.today().isoformat()}.jsonl'
        with open(log_path, 'a') as f:
            f.write(json.dumps(entry, ensure_ascii=False) + '\n')

    def _start_round(self):
        self.round_id = datetime.now().strftime('%Y%m%d-%H%M%S%f')[:-3]
        self._log_event('round_started')

    def _end_round(self, result):
        self._log_event('round_ended', result=result)

    # -- envío de goals -------------------------------------------------------

    def send_next_goal(self):
        if self.state not in (PatrolState.EN_RONDA, PatrolState.RETORNO):
            self.get_logger().warn(
                f'send_next_goal ignorado: estado actual es {self.state.value}')
            return

        goal_msg = NavigateToPose.Goal()
        if self.state == PatrolState.RETORNO:
            goal_msg.pose = self.waypoints[0]
            self.get_logger().info('Enviando goal de retorno a base')
        else:
            goal_msg.pose = self.waypoints[self.current_goal]
            self.get_logger().info(f'Enviando goal #{self.current_goal + 1}')

        send_goal_future = self._action_client.send_goal_async(
            goal_msg, feedback_callback=self.feedback_callback)
        send_goal_future.add_done_callback(self.goal_response_callback)

    def goal_response_callback(self, future):
        goal_handle = future.result()
        if not goal_handle.accepted:
            self._register_failure(f'Goal #{self.current_goal + 1} rechazado por el servidor de acciones')
            return

        self._current_goal_handle = goal_handle
        get_result_future = goal_handle.get_result_async()
        get_result_future.add_done_callback(self.get_result_callback)

    def feedback_callback(self, feedback_msg):
        feedback = feedback_msg.feedback
        self.get_logger().debug(
            f'Feedback: {feedback.current_pose.pose.position.x:.2f}, '
            f'{feedback.current_pose.pose.position.y:.2f}')

    def get_result_callback(self, future):
        status = future.result().status
        self._current_goal_handle = None

        if status == GoalStatus.STATUS_SUCCEEDED:
            self.fail_count = 0
            if self.state == PatrolState.RETORNO:
                self.get_logger().info('Base alcanzada.')
                self._end_round('retorno_a_base')
                self._enter_state(PatrolState.EN_BASE)
            elif self.state == PatrolState.EN_RONDA:
                self.get_logger().info(f'Waypoint #{self.current_goal + 1} alcanzado.')
                self._log_event('waypoint_reached', waypoint_name=self.waypoint_names[self.current_goal])
                self.current_goal = (self.current_goal + 1) % len(self.waypoints)
                if self.current_goal == 0:
                    self._end_round('completada')
                    self._start_round()
                self._call_later(self.NEXT_WAYPOINT_DELAY_SEC, self.send_next_goal)

        elif status == GoalStatus.STATUS_CANCELED:
            if self._expected_cancel:
                self._expected_cancel = False
                self.get_logger().info('Goal cancelado (transición de estado solicitada).')
            else:
                self._register_failure('Goal cancelado inesperadamente')

        elif status == GoalStatus.STATUS_ABORTED:
            self._register_failure(f'Nav2 abortó el waypoint #{self.current_goal + 1}')

        else:
            self.get_logger().warn(f'Resultado de goal con status inesperado: {status}')

    def _register_failure(self, reason):
        self.fail_count += 1
        self.get_logger().error(f'{reason} (fallo {self.fail_count}/{self.MAX_RETRIES})')
        self._log_event('goal_failed', reason=reason, fail_count=self.fail_count)

        if self.fail_count >= self.MAX_RETRIES:
            self._end_round('falla')
            self._enter_state(PatrolState.FALLA)
            self.get_logger().error(
                'Máximo de reintentos alcanzado. Robot detenido, esperando /clear_failure.')
        else:
            self._call_later(self.RETRY_DELAY_SEC, self.send_next_goal)

    def _cancel_active_goal(self):
        if self._current_goal_handle is not None:
            self._expected_cancel = True
            self._current_goal_handle.cancel_goal_async()

    # -- servicios --------------------------------------------------------------

    def handle_start_patrol(self, request, response):
        if self.state != PatrolState.EN_BASE:
            response.success = False
            response.message = f'No se puede iniciar la ronda desde {self.state.value}.'
            return response

        self._enter_state(PatrolState.EN_RONDA)
        self._start_round()
        self.send_next_goal()
        response.success = True
        response.message = 'Ronda iniciada.'
        return response

    def handle_pause_patrol(self, request, response):
        if self.state != PatrolState.EN_RONDA:
            response.success = False
            response.message = f'No se puede pausar desde {self.state.value}.'
            return response

        self._enter_state(PatrolState.PAUSADO)
        self._cancel_active_goal()
        self._log_event('interrupted', reason='pausado')
        response.success = True
        response.message = 'Ronda pausada.'
        return response

    def handle_resume_patrol(self, request, response):
        if self.state != PatrolState.PAUSADO:
            response.success = False
            response.message = f'No se puede reanudar desde {self.state.value}.'
            return response

        self._enter_state(PatrolState.EN_RONDA)
        self._log_event('resumed')
        self.send_next_goal()
        response.success = True
        response.message = 'Ronda reanudada.'
        return response

    def handle_manual_start(self, request, response):
        if self.state not in (PatrolState.EN_RONDA, PatrolState.PAUSADO):
            response.success = False
            response.message = f'No se puede tomar control manual desde {self.state.value}.'
            return response

        self._enter_state(PatrolState.MANUAL)
        self._cancel_active_goal()
        self._log_event('interrupted', reason='manual')
        response.success = True
        response.message = 'Control manual activado.'
        return response

    def handle_manual_stop(self, request, response):
        if self.state != PatrolState.MANUAL:
            response.success = False
            response.message = f'No hay control manual activo ({self.state.value}).'
            return response

        self._enter_state(PatrolState.PAUSADO)
        response.success = True
        response.message = 'Control manual liberado. Ronda en pausa, pedí /resume_patrol para continuar.'
        return response

    def handle_return_to_base(self, request, response):
        if self.state not in (PatrolState.EN_RONDA, PatrolState.PAUSADO):
            response.success = False
            response.message = f'No se puede retornar a base desde {self.state.value}.'
            return response

        self._enter_state(PatrolState.RETORNO)
        self._cancel_active_goal()
        self._log_event('interrupted', reason='retorno_a_base')
        self._call_later(self.NEXT_WAYPOINT_DELAY_SEC, self.send_next_goal)
        response.success = True
        response.message = 'Retornando a base.'
        return response

    def handle_clear_failure(self, request, response):
        if self.state != PatrolState.FALLA:
            response.success = False
            response.message = f'No hay falla activa ({self.state.value}).'
            return response

        self.fail_count = 0
        self._log_event('falla_reconocida')
        self._enter_state(PatrolState.EN_BASE)
        response.success = True
        response.message = 'Falla reconocida. Pedí /start_patrol para reanudar la ronda.'
        return response


def main(args=None):
    rclpy.init(args=args)
    node = PatrolNode()
    rclpy.spin(node)
    rclpy.shutdown()


if __name__ == '__main__':
    main()
