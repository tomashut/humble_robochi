import json
import math
import shutil
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
from geometry_msgs.msg import PoseStamped, PoseWithCovarianceStamped
from nav2_msgs.action import NavigateToPose, Spin
from std_msgs.msg import Bool, String
from std_srvs.srv import Trigger
from tf_transformations import quaternion_from_euler


class PatrolState(Enum):
    EN_BASE = 'EN_BASE'
    EN_RONDA = 'EN_RONDA'
    PAUSADO = 'PAUSADO'
    MANUAL = 'MANUAL'
    RETORNO = 'RETORNO'
    FALLA = 'FALLA'
    # aterrizaje de un reinicio a media ronda/retorno, todavia sin resolver
    # -- ver README para el porque no es lo mismo que PAUSADO
    INTERRUMPIDO = 'INTERRUMPIDO'


class PatrolNode(Node):

    MAX_RETRIES = 5
    RETRY_DELAY_SEC = 1.0
    NEXT_WAYPOINT_DELAY_SEC = 1.0
    DISK_CHECK_INTERVAL_SEC = 60.0

    def __init__(self, wait_for_nav2=True, **kwargs):
        super().__init__('patrol_node', **kwargs)

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

        default_state_file = str(Path.home() / '.local' / 'share' / 'patrol_fsm' / 'state.json')
        self.declare_parameter('state_file', default_state_file)
        state_file = self.get_parameter('state_file').get_parameter_value().string_value
        self.state_file = Path(state_file).expanduser()
        self.state_file.parent.mkdir(parents=True, exist_ok=True)

        # aviso preventivo de disco casi lleno -- avisa ANTES de que
        # _save_state/_log_event empiecen a fallar de verdad, no solo cuando
        # ya paso.
        self.declare_parameter('disk_free_warning_pct', 10.0)
        self.disk_free_warning_pct = (
            self.get_parameter('disk_free_warning_pct').get_parameter_value().double_value)
        self._disco_en_alerta = False
        self.create_timer(self.DISK_CHECK_INTERVAL_SEC, self._check_disk_space)

        self.declare_parameter('auto_resume_timeout_sec', 300)
        self.auto_resume_timeout_sec = (
            self.get_parameter('auto_resume_timeout_sec').get_parameter_value().integer_value)
        self._auto_resume_timer = None

        # umbrales de confianza de AMCL antes de reanudar solo tras un reinicio --
        # medidos en vivo en la simulacion (mapa depot) el 2026-08-11: AMCL
        # opera normalmente entre 0.15-0.3 en posicion, hasta ~0.12 en
        # orientacion durante giros; estos umbrales dejan margen por encima
        # de eso. Si el mapa o el sensor cambian, hay que volver a medir.
        self.declare_parameter('amcl_position_covariance_threshold', 0.5)
        self.amcl_position_covariance_threshold = (
            self.get_parameter('amcl_position_covariance_threshold')
            .get_parameter_value().double_value)
        self.declare_parameter('amcl_orientation_covariance_threshold', 0.3)
        self.amcl_orientation_covariance_threshold = (
            self.get_parameter('amcl_orientation_covariance_threshold')
            .get_parameter_value().double_value)
        self._latest_amcl_covariance = None  # (var_x, var_y, var_yaw) o None si nunca llego

        # cuantas lecturas malas SEGUIDAS hacen falta, mientras navega, para
        # dar por perdida la localizacion -- una sola lectura mala puede ser
        # un pico transitorio (una zona con poca geometria, por ejemplo), no
        # necesariamente que se perdio de verdad.
        self.declare_parameter('localizacion_perdida_confirmaciones', 3)
        self.localizacion_perdida_confirmaciones = (
            self.get_parameter('localizacion_perdida_confirmaciones')
            .get_parameter_value().integer_value)
        self._lecturas_amcl_malas_seguidas = 0

        # que hacer si se corta el enlace comms_agent<->Mosquitto por mucho
        # tiempo -- 'continue' (default) sigue patrullando sin reportar,
        # 'return_to_base'/'pause' reaccionan solo despues de
        # link_loss_grace_sec sin recuperarse. Ortogonal a la maquina de
        # estados (no es un estado ni una FALLA): el robot puede estar
        # EN_RONDA con o sin enlace, y la recuperacion es automatica al
        # volver la conexion, no requiere un humano.
        self.declare_parameter('on_link_loss', 'continue')
        self.on_link_loss = self.get_parameter('on_link_loss').get_parameter_value().string_value
        self.declare_parameter('link_loss_grace_sec', 600)
        self.link_loss_grace_sec = (
            self.get_parameter('link_loss_grace_sec').get_parameter_value().integer_value)
        self._link_ok = True
        self._link_lost_at = None
        self._link_loss_grace_timer = None

        saved_state = self._load_saved_state()
        if saved_state is None:
            self.current_goal = 0
            self.fail_count = 0
            self.round_id = None
            self.actividad_previa = None
            previous_state = None
        else:
            self.current_goal = saved_state['current_goal']
            self.fail_count = saved_state['fail_count']
            self.round_id = saved_state['round_id']
            # .get() a proposito: archivos de estado de antes de este campo
            # no lo tienen.
            self.actividad_previa = saved_state.get('actividad_previa')
            previous_state = PatrolState(saved_state['state'])

        self._current_goal_handle = None
        self._expected_cancel = False

        self.state = previous_state or PatrolState.EN_BASE

        state_qos = QoSProfile(depth=1, durability=QoSDurabilityPolicy.TRANSIENT_LOCAL)
        self._state_pub = self.create_publisher(String, 'patrol_state', state_qos)
        # volatil a proposito (sin TRANSIENT_LOCAL): un evento es un hecho
        # puntual, no un estado -- un suscriptor nuevo no debe recibir el
        # ultimo evento como si acabara de pasar.
        self._events_pub = self.create_publisher(String, 'patrol_events', 10)

        self.create_subscription(
            PoseWithCovarianceStamped, 'amcl_pose', self._on_amcl_pose, 10)
        # TRANSIENT_LOCAL, igual que patrol_state pero al reves: si
        # patrol_node arranca despues que comms_agent, se entera del ultimo
        # estado de enlace conocido sin esperar el proximo cambio.
        link_qos = QoSProfile(depth=1, durability=QoSDurabilityPolicy.TRANSIENT_LOCAL)
        self.create_subscription(Bool, 'link_status', self._on_link_status, link_qos)

        self._action_client = ActionClient(self, NavigateToPose, 'navigate_to_pose')
        self._spin_action_client = ActionClient(self, Spin, 'spin')
        self._spin_goal_handle = None
        self._spin_in_progress = False

        self.create_service(Trigger, '/start_patrol', self.handle_start_patrol)
        self.create_service(Trigger, '/pause_patrol', self.handle_pause_patrol)
        self.create_service(Trigger, '/resume_patrol', self.handle_resume_patrol)
        self.create_service(Trigger, '/manual_start', self.handle_manual_start)
        self.create_service(Trigger, '/manual_stop', self.handle_manual_stop)
        self.create_service(Trigger, '/return_to_base', self.handle_return_to_base)
        self.create_service(Trigger, '/clear_failure', self.handle_clear_failure)

        if wait_for_nav2:
            self.get_logger().info('Esperando al servidor de acción NavigateToPose...')
            self._action_client.wait_for_server()
            self.get_logger().info('Servidor listo.')

        if previous_state is None or previous_state in (PatrolState.EN_BASE, PatrolState.FALLA):
            self._enter_state(previous_state or PatrolState.EN_BASE)
        elif previous_state == PatrolState.MANUAL:
            # estaba bajo control manual cuando se corto -- no hay ningun
            # objetivo de navegacion que retomar solo, y reanudar autonomia
            # sin que un humano lo pida explicitamente es mas riesgoso que
            # en los demas casos. Aterriza directo en PAUSADO, nunca se
            # auto-reanuda -- siempre hace falta un humano.
            self.get_logger().warn(
                'Estado persistido era MANUAL; por seguridad arranca en PAUSADO -- nadie '
                'retoma el control manual solo. Pedí /resume_patrol, /manual_start o '
                '/return_to_base.')
            self._log_event('reiniciado', estado_anterior=previous_state.value)
            self._enter_state(PatrolState.PAUSADO)
        elif previous_state == PatrolState.PAUSADO:
            # ya estaba pausado -- por un humano, la unica forma de llegar
            # a PAUSADO -- cuando se corto. Se mantiene igual, sin timer.
            self.get_logger().warn(
                'Estado persistido era PAUSADO; se mantiene igual. Nadie retoma una '
                'pausa deliberada solo.')
            self._log_event('reiniciado', estado_anterior=previous_state.value)
            self._enter_state(PatrolState.PAUSADO)
        else:
            # EN_RONDA, RETORNO, o INTERRUMPIDO (reinicio encima de un
            # reinicio anterior sin resolver) -- se corto a media actividad
            # real. Aterriza (o se queda) en INTERRUMPIDO, que si intenta
            # retomarla sola, con el giro + chequeo de localizacion de por
            # medio.
            self.get_logger().warn(
                f'Estado persistido era {previous_state.value}; por seguridad arranca en '
                'INTERRUMPIDO en vez de reanudar navegación solo. Pedí /resume_patrol, '
                '/pause_patrol o /return_to_base.')
            self._log_event('reiniciado', estado_anterior=previous_state.value)
            if previous_state != PatrolState.INTERRUMPIDO:
                self.actividad_previa = previous_state.value
            # si previous_state YA era INTERRUMPIDO, se conserva la
            # actividad_previa cargada arriba (doble reinicio sin resolver)
            self._enter_state(PatrolState.INTERRUMPIDO)
            self._schedule_auto_resume()

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
        if new_state in (PatrolState.EN_RONDA, PatrolState.RETORNO):
            # arranca a navegar de nuevo -- que no arrastre un conteo de
            # lecturas malas de un tramo anterior (por ejemplo, de antes de
            # una pausa).
            self._lecturas_amcl_malas_seguidas = 0
        self.state = new_state
        self.get_logger().info(f'Estado -> {new_state.value}')
        msg = String()
        msg.data = new_state.value
        self._state_pub.publish(msg)
        self._save_state()

    def _call_later(self, delay_sec, fn):
        timer = self.create_timer(delay_sec, lambda: self._fire_once(timer, fn))

    def _fire_once(self, timer, fn):
        timer.cancel()
        fn()

    def _resume(self, reason=None):
        activity = (PatrolState.RETORNO if self.actividad_previa == PatrolState.RETORNO.value
                    else PatrolState.EN_RONDA)
        self._enter_state(activity)
        extra = {'reason': reason} if reason else {}
        self._log_event('resumed', actividad=activity.value, **extra)
        self.send_next_goal()

    def _schedule_auto_resume(self):
        if self.auto_resume_timeout_sec <= 0:
            return
        self._auto_resume_timer = self.create_timer(
            float(self.auto_resume_timeout_sec), self._fire_auto_resume)

    def _cancel_auto_resume(self):
        if self._auto_resume_timer is not None:
            self._auto_resume_timer.cancel()
            self._auto_resume_timer = None

    # -- enlace comms_agent<->Mosquitto --------------------------------------

    def _on_link_status(self, msg):
        if msg.data == self._link_ok:
            return  # sin cambio real (o eco), no hay nada que hacer

        self._link_ok = msg.data
        if not self._link_ok:
            self._link_lost_at = datetime.now()
            self.get_logger().warn('Se perdio el enlace hacia Mosquitto.')
            self._log_event('enlace_perdido')
            self._schedule_link_loss_grace()
        else:
            duracion_sec = (datetime.now() - self._link_lost_at).total_seconds()
            self.get_logger().info(f'Enlace restablecido tras {duracion_sec:.0f}s.')
            self._log_event('enlace_restablecido', duracion_sec=round(duracion_sec, 1))
            self._link_lost_at = None
            self._cancel_link_loss_grace()

    def _schedule_link_loss_grace(self):
        if self.link_loss_grace_sec <= 0:
            return
        self._link_loss_grace_timer = self.create_timer(
            float(self.link_loss_grace_sec), self._on_link_loss_grace_expired)

    def _cancel_link_loss_grace(self):
        if self._link_loss_grace_timer is not None:
            self._link_loss_grace_timer.cancel()
            self._link_loss_grace_timer = None

    def _on_link_loss_grace_expired(self):
        self._cancel_link_loss_grace()
        if self._link_ok:
            return  # se recupero justo antes de que este timer disparara

        # se comunica siempre, aunque la politica sea 'continue' -- alguien
        # mirando el panel en vivo deberia poder distinguir un corte breve
        # de uno que ya lleva rato, aunque el robot no cambie de conducta.
        self._log_event('enlace_perdido_prolongado')

        if self.state != PatrolState.EN_RONDA:
            return  # nada que "continuar" o "volver" desde otro estado

        if self.on_link_loss == 'return_to_base':
            self.handle_return_to_base(Trigger.Request(), Trigger.Response())
        elif self.on_link_loss == 'pause':
            self.handle_pause_patrol(Trigger.Request(), Trigger.Response())
        # 'continue' (default): no hace nada mas alla del evento de arriba.

    def _on_amcl_pose(self, msg):
        cov = msg.pose.covariance
        self._latest_amcl_covariance = (cov[0], cov[7], cov[35])  # var_x, var_y, var_yaw

        # el chequeo de antes de arrancar (gate + giro) no alcanza si la
        # localizacion se pierde DESPUES, ya caminando -- eso solo se nota
        # con evidencia real de movimiento, y para entonces el robot ya
        # esta en marcha. Por eso se vuelve a mirar en cada mensaje nuevo
        # mientras esta navegando de verdad (EN_RONDA/RETORNO), no solo
        # antes de reanudar.
        if self.state in (PatrolState.EN_RONDA, PatrolState.RETORNO):
            if self._localizacion_confiable():
                self._lecturas_amcl_malas_seguidas = 0
            else:
                self._lecturas_amcl_malas_seguidas += 1
                if self._lecturas_amcl_malas_seguidas >= self.localizacion_perdida_confirmaciones:
                    self._localizacion_perdida_durante_navegacion()

    def _localizacion_confiable(self):
        if self._latest_amcl_covariance is None:
            return False
        var_x, var_y, var_yaw = self._latest_amcl_covariance
        return (var_x < self.amcl_position_covariance_threshold
                and var_y < self.amcl_position_covariance_threshold
                and var_yaw < self.amcl_orientation_covariance_threshold)

    def _localizacion_perdida_durante_navegacion(self):
        self.get_logger().error(
            'Se perdio la confianza en la localizacion (AMCL) mientras navegaba '
            f'(estado {self.state.value}); deteniendo el robot.')
        self._log_event('localizacion_perdida', estado=self.state.value)
        self._end_round('localizacion_perdida')
        self._enter_state(PatrolState.FALLA)
        self._cancel_active_goal()

    def _fire_auto_resume(self):
        if self.state != PatrolState.INTERRUMPIDO:
            self._cancel_auto_resume()
            return

        if self._spin_in_progress:
            return  # ya hay un giro de convergencia en curso, no arrancar otro

        # Antes de confiar en la covarianza hay que forzar una actualizacion real
        # de AMCL -- quieto, AMCL no revisa nada (no supera update_min_d/a), asi
        # que el numero puede seguir "congelado" en lo que haya quedado de un
        # pose estimate viejo o de fabrica, sin reflejar la realidad. Un giro en
        # el lugar (sin trasladarse, mas seguro que arrancar a caminar a ciegas)
        # alcanza para que AMCL vuelva a mirar de verdad.
        self._spin_in_progress = True
        self.get_logger().warn(
            f'Nadie reanudo en {self.auto_resume_timeout_sec}s tras el reinicio; '
            'girando en el lugar para que AMCL confirme la localizacion antes de decidir.')
        self._log_event('auto_resume_girando_para_converger')

        goal_msg = Spin.Goal()
        goal_msg.target_yaw = 2 * math.pi
        send_goal_future = self._spin_action_client.send_goal_async(goal_msg)
        send_goal_future.add_done_callback(self._on_spin_goal_response)

    def _on_spin_goal_response(self, future):
        goal_handle = future.result()
        if not goal_handle.accepted:
            self._spin_in_progress = False
            self.get_logger().error(
                'El giro de convergencia fue rechazado; reintento en el proximo ciclo.')
            return
        self._spin_goal_handle = goal_handle
        result_future = goal_handle.get_result_async()
        result_future.add_done_callback(self._on_spin_result)

    def _on_spin_result(self, future):
        self._spin_goal_handle = None
        self._spin_in_progress = False

        if self.state != PatrolState.INTERRUMPIDO:
            return  # alguien tomo control mientras giraba

        if not self._localizacion_confiable():
            self.get_logger().warn(
                'Localizacion (AMCL) sigue sin ser confiable despues de girar; no reanudo '
                f'solo. Reintento en {self.auto_resume_timeout_sec}s.')
            self._log_event('auto_resume_esperando_localizacion')
            return  # no cancelo el timer: vuelve a sonar solo, se re-evalua entonces

        self._cancel_auto_resume()
        self.get_logger().warn('Localizacion confiable tras el giro; reanudando la ronda sola.')
        self._resume(reason='auto_tras_reinicio')

    def _cancel_convergence_spin(self):
        if self._spin_goal_handle is not None:
            self._spin_goal_handle.cancel_goal_async()
            self._spin_goal_handle = None
        self._spin_in_progress = False

    # -- persistencia de estado (sobrevive reinicios) --------------------------

    def _load_saved_state(self):
        if not self.state_file.exists():
            return None

        try:
            with open(self.state_file) as f:
                data = json.load(f)
            PatrolState(data['state'])  # valida que el valor sea un estado conocido
            if not (0 <= data['current_goal'] < len(self.waypoints)):
                raise ValueError('current_goal fuera de rango')
            return data
        except (json.JSONDecodeError, OSError, KeyError, ValueError) as e:
            self.get_logger().warn(
                f'Estado persistido en {self.state_file} invalido o corrupto ({e}); '
                'se ignora y arranca desde EN_BASE.')
            return None

    def _save_state(self):
        payload = {
            'state': self.state.value,
            'current_goal': self.current_goal,
            'fail_count': self.fail_count,
            'round_id': self.round_id,
            'actividad_previa': self.actividad_previa,
        }
        tmp_path = self.state_file.with_suffix('.tmp')
        try:
            with open(tmp_path, 'w') as f:
                json.dump(payload, f)
            tmp_path.replace(self.state_file)
        except OSError as e:
            # disco lleno/solo-lectura no debe matar el nodo -- el robot
            # sigue andando, solo que un reinicio en este momento retomaria
            # con un estado desactualizado. _log_event publica el aviso
            # igual aunque el JSONL tampoco se pueda escribir (ver abajo).
            self.get_logger().error(f'No se pudo guardar el estado en disco: {e}')
            self._log_event('guardado_estado_fallido', error=str(e))

    def _check_disk_space(self):
        try:
            uso = shutil.disk_usage(self.state_file.parent)
        except OSError:
            return
        libre_pct = 100.0 * uso.free / uso.total
        if libre_pct < self.disk_free_warning_pct and not self._disco_en_alerta:
            self._disco_en_alerta = True
            self.get_logger().warn(f'Poco espacio libre en disco: {libre_pct:.1f}%.')
            self._log_event('disco_casi_lleno', libre_pct=round(libre_pct, 1))
        elif libre_pct >= self.disk_free_warning_pct and self._disco_en_alerta:
            self._disco_en_alerta = False
            self._log_event('disco_normalizado', libre_pct=round(libre_pct, 1))

    # -- registro de rondas ("libro de rondas digital") -----------------------

    def _log_event(self, event, **fields):
        entry = {
            'ts': datetime.now().astimezone().isoformat(timespec='seconds'),
            'round_id': self.round_id,
            'event': event,
        }
        entry.update(fields)

        # intenta guardar en el JSONL, pero si el disco esta lleno no deja
        # que eso le impida publicar el evento por el topico -- si no, un
        # guardado_estado_fallido disparado desde _save_state por disco
        # lleno terminaria fallando aca tambien, sin avisarle a nadie.
        log_path = self.rounds_log_dir / f'{date.today().isoformat()}.jsonl'
        try:
            with open(log_path, 'a') as f:
                f.write(json.dumps(entry, ensure_ascii=False) + '\n')
        except OSError as e:
            self.get_logger().error(f'No se pudo escribir el registro de rondas: {e}')

        # mismo payload que la linea del log -- garantia de coherencia entre
        # lo que queda escrito y lo que sale por el topico.
        msg = String()
        msg.data = json.dumps(entry, ensure_ascii=False)
        self._events_pub.publish(msg)

    def _start_round(self):
        self.round_id = datetime.now().strftime('%Y%m%d-%H%M%S%f')[:-3]
        self._log_event('round_started')
        self._save_state()

    def _end_round(self, result):
        self._log_event('round_ended', result=result)
        self._save_state()

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
            self._register_failure(
                f'Goal #{self.current_goal + 1} rechazado por el servidor de acciones')
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
                # lo que estaba pendiente (si algo lo estaba) ya se resolvio
                # solo -- no dejar una nota vieja para una pausa futura.
                self.actividad_previa = None
                self._end_round('retorno_a_base')
                self._enter_state(PatrolState.EN_BASE)
            elif self.state == PatrolState.EN_RONDA:
                self.get_logger().info(f'Waypoint #{self.current_goal + 1} alcanzado.')
                self._log_event(
                    'waypoint_reached', waypoint_name=self.waypoint_names[self.current_goal])
                self.current_goal = (self.current_goal + 1) % len(self.waypoints)
                if self.current_goal == 0:
                    self._end_round('completada')
                    self._start_round()
                else:
                    self._save_state()
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
            self._save_state()
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

        # defensivo: cubre cualquier camino a EN_BASE que no la haya limpiado
        # ya (por ejemplo, /clear_failure tras una FALLA).
        self.actividad_previa = None
        # una ronda nueva arranca desde el principio del circuito, no desde
        # donde haya quedado current_goal de una ronda anterior -- si no,
        # un return_to_base a mitad de camino deja el indice a mitad de
        # camino para siempre, y la proxima ronda salta directo ahi.
        self.current_goal = 0
        self._enter_state(PatrolState.EN_RONDA)
        self._start_round()
        self.send_next_goal()
        response.success = True
        response.message = 'Ronda iniciada.'
        return response

    def handle_pause_patrol(self, request, response):
        if self.state not in (
                PatrolState.EN_RONDA, PatrolState.RETORNO, PatrolState.INTERRUMPIDO):
            response.success = False
            response.message = f'No se puede pausar desde {self.state.value}.'
            return response

        if self.state in (PatrolState.EN_RONDA, PatrolState.RETORNO):
            self.actividad_previa = self.state.value
        # si viene de INTERRUMPIDO, actividad_previa ya esta seteada de antes
        # -- un humano decidio pausarlo en vez de dejar que siga intentando
        # reanudarse solo. A partir de aca es una pausa deliberada como
        # cualquier otra: nunca se auto-reanuda.
        self._cancel_auto_resume()
        self._cancel_convergence_spin()
        self._enter_state(PatrolState.PAUSADO)
        self._cancel_active_goal()
        self._log_event('interrupted', reason='pausado')
        response.success = True
        response.message = 'Ronda pausada.'
        return response

    def handle_resume_patrol(self, request, response):
        if self.state not in (PatrolState.PAUSADO, PatrolState.INTERRUMPIDO):
            response.success = False
            response.message = f'No se puede reanudar desde {self.state.value}.'
            return response

        self._cancel_auto_resume()
        self._cancel_convergence_spin()
        self._resume()
        response.success = True
        response.message = 'Ronda reanudada.'
        return response

    def handle_manual_start(self, request, response):
        if self.state not in (
                PatrolState.EN_RONDA, PatrolState.PAUSADO,
                PatrolState.RETORNO, PatrolState.INTERRUMPIDO):
            response.success = False
            response.message = f'No se puede tomar control manual desde {self.state.value}.'
            return response

        if self.state in (PatrolState.EN_RONDA, PatrolState.RETORNO):
            self.actividad_previa = self.state.value
        # si viene de PAUSADO o INTERRUMPIDO, actividad_previa ya esta seteada
        # de antes.

        self._cancel_auto_resume()
        self._cancel_convergence_spin()
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
        response.message = (
            'Control manual liberado. Ronda en pausa, pedí /resume_patrol para continuar.')
        return response

    def handle_return_to_base(self, request, response):
        if self.state not in (PatrolState.EN_RONDA, PatrolState.PAUSADO, PatrolState.INTERRUMPIDO):
            response.success = False
            response.message = f'No se puede retornar a base desde {self.state.value}.'
            return response

        self._cancel_auto_resume()
        self._cancel_convergence_spin()
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
