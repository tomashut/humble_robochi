"""
Tests de la logica de patrol_node, sin necesitar Gazebo/Nav2 corriendo.

Se construye el nodo con wait_for_nav2=False (salta la espera del servidor
de accion) y con archivos de estado/rondas/waypoints propios en un
directorio temporal, para no tocar nunca los reales del usuario ni
depender de una simulacion levantada.
"""

import json
from datetime import date, datetime, timedelta

import yaml
import pytest

import rclpy
from rclpy.parameter import Parameter
from action_msgs.msg import GoalStatus
from geometry_msgs.msg import PoseWithCovarianceStamped
from std_msgs.msg import Bool
from std_srvs.srv import Trigger

import patrol_fsm.patrol_node as patrol_node_module
from patrol_fsm.patrol_node import PatrolNode, PatrolState


TEST_WAYPOINTS = [
    {'name': 'base', 'x': 0.0, 'y': 0.0, 'yaw': 0.0},
    {'name': 'wp1', 'x': 1.0, 'y': 0.0, 'yaw': 0.0},
    {'name': 'wp2', 'x': 1.0, 'y': 1.0, 'yaw': 0.0},
]


@pytest.fixture
def rclpy_context():
    rclpy.init()
    yield
    rclpy.shutdown()


def make_node(tmp_path, rclpy_context, waypoints=None, **param_overrides):
    """Crea un PatrolNode de prueba, con archivos propios en tmp_path."""
    waypoints_file = tmp_path / 'waypoints.yaml'
    waypoints_file.write_text(yaml.dump({'waypoints': waypoints or TEST_WAYPOINTS}))

    defaults = {
        'waypoints_file': str(waypoints_file),
        'state_file': str(tmp_path / 'state.json'),
        'rounds_log_dir': str(tmp_path / 'rondas'),
        'auto_resume_timeout_sec': 0,
    }
    defaults.update(param_overrides)

    overrides = [Parameter(name, value=value) for name, value in defaults.items()]
    return PatrolNode(wait_for_nav2=False, parameter_overrides=overrides)


def write_estado(
        tmp_path, state, actividad_previa=None, current_goal=1, waypoints=None,
        current_waypoint=None, saved_at=None):
    """
    Simula un state.json dejado por un corte, en el estado que sea.

    current_waypoint tiene prioridad si se pasa (para simular directamente
    un nombre que ya no existe en la lista actual); si no, se resuelve a
    partir de current_goal contra TEST_WAYPOINTS (o waypoints, si se pasa).
    saved_at ausente por default -- simula un state.json de antes de que
    ese campo existiera.
    """
    state_file = tmp_path / 'state.json'
    if current_waypoint is None:
        current_waypoint = (waypoints or TEST_WAYPOINTS)[current_goal]['name']
    payload = {
        'state': state,
        'current_waypoint': current_waypoint,
        'fail_count': 0,
        'round_id': '20260811-000000000',
    }
    if actividad_previa is not None:
        payload['actividad_previa'] = actividad_previa
    if saved_at is not None:
        payload['saved_at'] = saved_at
    state_file.write_text(json.dumps(payload))


def ultimo_evento(tmp_path):
    """Lee la ultima linea del JSONL de rondas de hoy."""
    log_path = tmp_path / 'rondas' / f'{date.today().isoformat()}.jsonl'
    return json.loads(log_path.read_text().strip().splitlines()[-1])


def amcl_pose_con_covarianza(var_x, var_y, var_yaw):
    msg = PoseWithCovarianceStamped()
    msg.pose.covariance[0] = var_x
    msg.pose.covariance[7] = var_y
    msg.pose.covariance[35] = var_yaw
    return msg


class FakeGoalHandle:
    """Reemplaza a un goal handle real solo para poder cancelarlo sin Nav2."""

    def cancel_goal_async(self):
        pass


class FakeDiskUsage:
    """Reemplaza el resultado de shutil.disk_usage() sin tocar el disco real."""

    def __init__(self, total, free):
        self.total = total
        self.free = free


class FakeResultFuture:
    """Simula el future que get_result_callback recibe de una accion real."""

    def __init__(self, status):
        self.status = status

    def result(self):
        return self


# -- arranque limpio (sin state.json previo) ---------------------------------

def test_arranca_en_base_sin_estado_previo(tmp_path, rclpy_context):
    node = make_node(tmp_path, rclpy_context)
    try:
        assert node.state == PatrolState.EN_BASE
        assert node.current_goal == 0
        assert node.fail_count == 0
    finally:
        node.destroy_node()


# -- aterrizaje de reinicio: a donde cae cada estado persistido --------------

def test_en_ronda_persistido_aterriza_en_interrumpido_y_agenda_timer(tmp_path, rclpy_context):
    write_estado(tmp_path, 'EN_RONDA')
    node = make_node(tmp_path, rclpy_context, auto_resume_timeout_sec=15)
    try:
        assert node.state == PatrolState.INTERRUMPIDO
        assert node.actividad_previa == 'EN_RONDA'
        assert node._auto_resume_timer is not None
    finally:
        node.destroy_node()


def test_retorno_persistido_aterriza_en_interrumpido_con_esa_actividad(tmp_path, rclpy_context):
    write_estado(tmp_path, 'RETORNO')
    node = make_node(tmp_path, rclpy_context, auto_resume_timeout_sec=15)
    try:
        assert node.state == PatrolState.INTERRUMPIDO
        assert node.actividad_previa == 'RETORNO'
        assert node._auto_resume_timer is not None
    finally:
        node.destroy_node()


def test_manual_persistido_aterriza_en_pausado_sin_timer(tmp_path, rclpy_context):
    """
    El caso que marco la revision de Fable.

    Nadie retoma el control manual solo -- ni siquiera pasa por INTERRUMPIDO.
    """
    write_estado(tmp_path, 'MANUAL')
    node = make_node(tmp_path, rclpy_context, auto_resume_timeout_sec=15)
    try:
        assert node.state == PatrolState.PAUSADO
        assert node._auto_resume_timer is None
    finally:
        node.destroy_node()


def test_pausado_persistido_se_mantiene_pausado_sin_timer(tmp_path, rclpy_context):
    """
    PAUSADO es puro: solo se llega por decision humana.

    Nunca se auto-reanuda -- ni siquiera despues de un reinicio.
    """
    write_estado(tmp_path, 'PAUSADO')
    node = make_node(tmp_path, rclpy_context, auto_resume_timeout_sec=15)
    try:
        assert node.state == PatrolState.PAUSADO
        assert node._auto_resume_timer is None
    finally:
        node.destroy_node()


def test_doble_reinicio_en_interrumpido_conserva_la_actividad_previa(tmp_path, rclpy_context):
    """
    Si ya estaba en INTERRUMPIDO y se reinicia de nuevo, conserva la actividad.

    No hay que perderla ni confundirla con 'INTERRUMPIDO' como si fuera la
    actividad en si.
    """
    write_estado(tmp_path, 'INTERRUMPIDO', actividad_previa='RETORNO')
    node = make_node(tmp_path, rclpy_context, auto_resume_timeout_sec=15)
    try:
        assert node.state == PatrolState.INTERRUMPIDO
        assert node.actividad_previa == 'RETORNO'
        assert node._auto_resume_timer is not None
    finally:
        node.destroy_node()


# -- timestamp de state.json: hace cuanto se guardo lo que se retoma --------

def test_save_state_incluye_saved_at(tmp_path, rclpy_context):
    node = make_node(tmp_path, rclpy_context)
    try:
        node._save_state()
        payload = json.loads((tmp_path / 'state.json').read_text())
        # no revienta al parsearlo -- alcanza como chequeo, el valor exacto
        # depende del reloj real.
        datetime.fromisoformat(payload['saved_at'])
    finally:
        node.destroy_node()


def test_reinicio_calcula_segundos_desde_guardado(tmp_path, rclpy_context):
    hace_un_rato = (datetime.now().astimezone() - timedelta(hours=3)).isoformat(
        timespec='seconds')
    write_estado(tmp_path, 'EN_RONDA', saved_at=hace_un_rato)
    node = make_node(tmp_path, rclpy_context)
    try:
        evento = ultimo_evento(tmp_path)
        assert evento['event'] == 'reiniciado'
        # ~3 horas, con margen por el tiempo que tarda el test en correr.
        assert 10770 <= evento['segundos_desde_guardado'] <= 10830
    finally:
        node.destroy_node()


def test_reinicio_sin_saved_at_no_rompe_ni_agrega_el_campo(tmp_path, rclpy_context):
    """state.json de antes de que este campo existiera -- debe seguir andando."""
    write_estado(tmp_path, 'EN_RONDA')  # sin saved_at, default de write_estado
    node = make_node(tmp_path, rclpy_context)
    try:
        assert node.state == PatrolState.INTERRUMPIDO
        evento = ultimo_evento(tmp_path)
        assert 'segundos_desde_guardado' not in evento
    finally:
        node.destroy_node()


def test_reinicio_con_saved_at_invalido_no_rompe_el_arranque(tmp_path, rclpy_context):
    """Un saved_at corrupto (state.json editado a mano) no debe tumbar el nodo."""
    write_estado(tmp_path, 'EN_RONDA', saved_at='esto-no-es-una-fecha')
    node = make_node(tmp_path, rclpy_context)
    try:
        assert node.state == PatrolState.INTERRUMPIDO
        evento = ultimo_evento(tmp_path)
        assert 'segundos_desde_guardado' not in evento
    finally:
        node.destroy_node()


# -- persistencia por nombre de waypoint, no por indice crudo ----------------

def test_reinicio_con_waypoints_reordenados_retoma_el_waypoint_correcto(tmp_path, rclpy_context):
    """
    El indice como current_goal no es estable entre reinicios.

    Si waypoints.yaml cambia entre el guardado y la carga (agregar/sacar
    una parada corre a los demas), guardar el NOMBRE del waypoint evita que
    el robot retome en el lugar fisico equivocado sin ningun error visible.
    """
    write_estado(tmp_path, 'EN_RONDA', current_goal=2)  # 'wp2' en TEST_WAYPOINTS

    # entre el guardado y el reinicio, alguien inserta una parada nueva
    # ANTES de wp2 -- wp2 ya no esta en la posicion 2, se corrio a la 3.
    waypoints_reordenados = [
        {'name': 'base', 'x': 0.0, 'y': 0.0, 'yaw': 0.0},
        {'name': 'wp1', 'x': 1.0, 'y': 0.0, 'yaw': 0.0},
        {'name': 'parada_nueva', 'x': 5.0, 'y': 5.0, 'yaw': 0.0},
        {'name': 'wp2', 'x': 1.0, 'y': 1.0, 'yaw': 0.0},
    ]
    node = make_node(tmp_path, rclpy_context, waypoints=waypoints_reordenados)
    try:
        assert node.waypoint_names[node.current_goal] == 'wp2'
    finally:
        node.destroy_node()


def test_reinicio_con_waypoint_pendiente_borrado_descarta_el_estado(tmp_path, rclpy_context):
    """
    Sin el waypoint pendiente en la lista actual, no hay forma segura de resolverlo.

    Se descarta como estado corrupto, mismo camino que cualquier otro dato
    invalido, en vez de adivinar.
    """
    write_estado(tmp_path, 'EN_RONDA', current_waypoint='wp_que_ya_no_existe')
    node = make_node(tmp_path, rclpy_context)
    try:
        assert node.state == PatrolState.EN_BASE
        assert node.current_goal == 0
    finally:
        node.destroy_node()


# -- INTERRUMPIDO: giro de convergencia antes de decidir ----------------------

def test_fire_auto_resume_arranca_un_giro_no_reanuda_directo(tmp_path, rclpy_context):
    """
    _fire_auto_resume ya no decide nada por si sola: primero gira.

    La decision real pasa por _on_spin_result (ver los proximos tests).
    """
    write_estado(tmp_path, 'EN_RONDA')
    node = make_node(tmp_path, rclpy_context)
    try:
        node._on_amcl_pose(amcl_pose_con_covarianza(0.001, 0.001, 0.001))  # bien ubicado
        node._fire_auto_resume()
        assert node.state == PatrolState.INTERRUMPIDO  # no reanuda antes de girar
        assert node._spin_in_progress is True
    finally:
        node.destroy_node()


def test_no_reanuda_tras_girar_sin_ningun_dato_de_amcl(tmp_path, rclpy_context):
    write_estado(tmp_path, 'EN_RONDA')
    node = make_node(tmp_path, rclpy_context)
    try:
        assert node.state == PatrolState.INTERRUMPIDO  # aterrizo aca, por el reinicio
        node._on_spin_result(None)  # simula que el giro termino
        assert node.state == PatrolState.INTERRUMPIDO  # sin dato de AMCL, no se mueve
    finally:
        node.destroy_node()


def test_no_reanuda_tras_girar_con_localizacion_mala(tmp_path, rclpy_context):
    write_estado(tmp_path, 'EN_RONDA')
    node = make_node(tmp_path, rclpy_context)
    try:
        node._on_amcl_pose(amcl_pose_con_covarianza(1.0, 1.0, 1.0))  # bien perdido
        node._on_spin_result(None)
        assert node.state == PatrolState.INTERRUMPIDO
    finally:
        node.destroy_node()


def test_reanuda_a_en_ronda_tras_girar_con_localizacion_buena(tmp_path, rclpy_context):
    write_estado(tmp_path, 'EN_RONDA')
    node = make_node(tmp_path, rclpy_context)
    try:
        node._on_amcl_pose(amcl_pose_con_covarianza(0.001, 0.001, 0.001))  # bien ubicado
        node._on_spin_result(None)
        assert node.state == PatrolState.EN_RONDA
    finally:
        node.destroy_node()


def test_reanuda_a_retorno_si_la_actividad_previa_era_retorno(tmp_path, rclpy_context):
    """
    La razon de ser de actividad_previa.

    No perder que estaba volviendo a base, en vez de simplemente volver a
    patrullar.
    """
    write_estado(tmp_path, 'RETORNO')
    node = make_node(tmp_path, rclpy_context)
    try:
        node._on_amcl_pose(amcl_pose_con_covarianza(0.001, 0.001, 0.001))
        node._on_spin_result(None)
        assert node.state == PatrolState.RETORNO
    finally:
        node.destroy_node()


def test_manual_start_cancela_un_giro_en_curso(tmp_path, rclpy_context):
    """
    Si un humano toma control mientras gira para converger, se cancela.

    No tiene que quedar girando solo por su cuenta.
    """
    write_estado(tmp_path, 'EN_RONDA')
    node = make_node(tmp_path, rclpy_context)
    try:
        node._fire_auto_resume()
        assert node._spin_in_progress is True

        response = node.handle_manual_start(Trigger.Request(), Trigger.Response())

        assert response.success is True
        assert node.state == PatrolState.MANUAL
        assert node._spin_in_progress is False
    finally:
        node.destroy_node()


# -- salidas de INTERRUMPIDO por decision humana ------------------------------

def test_pause_patrol_desde_interrumpido_pasa_a_pausado_y_mata_el_timer(tmp_path, rclpy_context):
    write_estado(tmp_path, 'EN_RONDA')
    node = make_node(tmp_path, rclpy_context, auto_resume_timeout_sec=15)
    try:
        assert node.state == PatrolState.INTERRUMPIDO
        assert node._auto_resume_timer is not None

        response = node.handle_pause_patrol(Trigger.Request(), Trigger.Response())

        assert response.success is True
        assert node.state == PatrolState.PAUSADO
        assert node._auto_resume_timer is None
    finally:
        node.destroy_node()


def test_resume_patrol_desde_interrumpido_reanuda_directo_sin_esperar_el_gate(
        tmp_path, rclpy_context):
    """
    Un humano pidiendo resume es una decision informada.

    No pasa por el chequeo de covarianza, igual que un resume normal desde
    PAUSADO.
    """
    write_estado(tmp_path, 'EN_RONDA')
    node = make_node(tmp_path, rclpy_context, auto_resume_timeout_sec=15)
    try:
        response = node.handle_resume_patrol(Trigger.Request(), Trigger.Response())

        assert response.success is True
        assert node.state == PatrolState.EN_RONDA
        assert node._auto_resume_timer is None
    finally:
        node.destroy_node()


def test_return_to_base_desde_interrumpido(tmp_path, rclpy_context):
    write_estado(tmp_path, 'EN_RONDA')
    node = make_node(tmp_path, rclpy_context, auto_resume_timeout_sec=15)
    try:
        response = node.handle_return_to_base(Trigger.Request(), Trigger.Response())

        assert response.success is True
        assert node.state == PatrolState.RETORNO
        assert node._auto_resume_timer is None
    finally:
        node.destroy_node()


# -- vigilancia continua: si se pierde la localizacion YA navegando ----------

def test_se_va_a_falla_si_se_pierde_localizacion_navegando(tmp_path, rclpy_context):
    """Con el debounce default (3), hacen falta 3 lecturas malas SEGUIDAS."""
    node = make_node(tmp_path, rclpy_context)
    try:
        node.handle_start_patrol(Trigger.Request(), Trigger.Response())
        assert node.state == PatrolState.EN_RONDA

        node._on_amcl_pose(amcl_pose_con_covarianza(5.0, 5.0, 2.0))
        node._on_amcl_pose(amcl_pose_con_covarianza(5.0, 5.0, 2.0))
        assert node.state == PatrolState.EN_RONDA  # todavia no, faltan confirmaciones
        node._on_amcl_pose(amcl_pose_con_covarianza(5.0, 5.0, 2.0))

        assert node.state == PatrolState.FALLA
    finally:
        node.destroy_node()


def test_una_lectura_mala_sola_no_alcanza(tmp_path, rclpy_context):
    """
    Un pico transitorio no tiene que mandar a FALLA.

    Es justo lo que senalo Fable como riesgo de falsos positivos.
    """
    node = make_node(tmp_path, rclpy_context)
    try:
        node.handle_start_patrol(Trigger.Request(), Trigger.Response())

        node._on_amcl_pose(amcl_pose_con_covarianza(5.0, 5.0, 2.0))

        assert node.state == PatrolState.EN_RONDA
    finally:
        node.destroy_node()


def test_una_lectura_buena_en_el_medio_resetea_el_contador(tmp_path, rclpy_context):
    node = make_node(tmp_path, rclpy_context)
    try:
        node.handle_start_patrol(Trigger.Request(), Trigger.Response())

        node._on_amcl_pose(amcl_pose_con_covarianza(5.0, 5.0, 2.0))
        node._on_amcl_pose(amcl_pose_con_covarianza(5.0, 5.0, 2.0))
        node._on_amcl_pose(amcl_pose_con_covarianza(0.01, 0.01, 0.01))  # se recupero
        node._on_amcl_pose(amcl_pose_con_covarianza(5.0, 5.0, 2.0))
        node._on_amcl_pose(amcl_pose_con_covarianza(5.0, 5.0, 2.0))

        assert node.state == PatrolState.EN_RONDA  # el contador arranco de nuevo
    finally:
        node.destroy_node()


def test_sigue_en_ronda_si_la_localizacion_sigue_bien(tmp_path, rclpy_context):
    node = make_node(tmp_path, rclpy_context)
    try:
        node.handle_start_patrol(Trigger.Request(), Trigger.Response())
        assert node.state == PatrolState.EN_RONDA

        node._on_amcl_pose(amcl_pose_con_covarianza(0.01, 0.01, 0.01))  # bien ubicado

        assert node.state == PatrolState.EN_RONDA
    finally:
        node.destroy_node()


def test_localizacion_mala_no_afecta_si_no_esta_navegando(tmp_path, rclpy_context):
    """
    El chequeo continuo es solo mientras navega.

    En PAUSADO, por ejemplo, no tiene que disparar nada.
    """
    node = make_node(tmp_path, rclpy_context)
    try:
        assert node.state == PatrolState.EN_BASE

        node._on_amcl_pose(amcl_pose_con_covarianza(5.0, 5.0, 2.0))

        assert node.state == PatrolState.EN_BASE
    finally:
        node.destroy_node()


# -- pausa deliberada: nunca se auto-reanuda, sin importar el origen --------

def test_pause_patrol_desde_en_ronda_marca_actividad_previa(tmp_path, rclpy_context):
    node = make_node(tmp_path, rclpy_context)
    try:
        node.handle_start_patrol(Trigger.Request(), Trigger.Response())
        node.handle_pause_patrol(Trigger.Request(), Trigger.Response())

        assert node.state == PatrolState.PAUSADO
        assert node.actividad_previa == 'EN_RONDA'

        saved = json.loads((tmp_path / 'state.json').read_text())
        assert saved['state'] == 'PAUSADO'
    finally:
        node.destroy_node()


# -- pausar/tomar manual durante RETORNO ------------------------------------

def test_pause_patrol_desde_retorno_marca_actividad_previa(tmp_path, rclpy_context):
    node = make_node(tmp_path, rclpy_context)
    try:
        node.handle_start_patrol(Trigger.Request(), Trigger.Response())
        node.handle_return_to_base(Trigger.Request(), Trigger.Response())

        response = node.handle_pause_patrol(Trigger.Request(), Trigger.Response())

        assert response.success is True
        assert node.state == PatrolState.PAUSADO
        assert node.actividad_previa == 'RETORNO'
    finally:
        node.destroy_node()


def test_resume_tras_pausar_retorno_vuelve_a_retorno(tmp_path, rclpy_context):
    node = make_node(tmp_path, rclpy_context)
    try:
        node.handle_start_patrol(Trigger.Request(), Trigger.Response())
        node.handle_return_to_base(Trigger.Request(), Trigger.Response())
        node.handle_pause_patrol(Trigger.Request(), Trigger.Response())

        node.handle_resume_patrol(Trigger.Request(), Trigger.Response())

        assert node.state == PatrolState.RETORNO
    finally:
        node.destroy_node()


def test_manual_start_desde_retorno_marca_actividad_previa(tmp_path, rclpy_context):
    node = make_node(tmp_path, rclpy_context)
    try:
        node.handle_start_patrol(Trigger.Request(), Trigger.Response())
        node.handle_return_to_base(Trigger.Request(), Trigger.Response())

        response = node.handle_manual_start(Trigger.Request(), Trigger.Response())

        assert response.success is True
        assert node.state == PatrolState.MANUAL
        assert node.actividad_previa == 'RETORNO'
    finally:
        node.destroy_node()


def test_manual_stop_y_resume_desde_retorno_vuelve_a_retorno(tmp_path, rclpy_context):
    """El escenario original del bug, pero con RETORNO en vez de EN_RONDA."""
    node = make_node(tmp_path, rclpy_context)
    try:
        node.handle_start_patrol(Trigger.Request(), Trigger.Response())
        node.handle_return_to_base(Trigger.Request(), Trigger.Response())
        node.handle_manual_start(Trigger.Request(), Trigger.Response())
        node.handle_manual_stop(Trigger.Request(), Trigger.Response())

        node.handle_resume_patrol(Trigger.Request(), Trigger.Response())

        assert node.state == PatrolState.RETORNO
    finally:
        node.destroy_node()


def test_manual_start_desde_en_ronda_sobreescribe_actividad_previa_vieja(tmp_path, rclpy_context):
    """
    El bug original: una nota vieja no debe sobrevivir a un ciclo nuevo.

    Si actividad_previa quedo con un valor de un incidente anterior,
    tomar control manual desde EN_RONDA lo tiene que pisar con el valor
    correcto, no dejarlo como estaba -- si no, el resume posterior manda
    el robot a la base en vez de seguir la ronda.
    """
    node = make_node(tmp_path, rclpy_context)
    try:
        node.handle_start_patrol(Trigger.Request(), Trigger.Response())
        node.actividad_previa = 'RETORNO'  # simula una nota vieja pegada

        node.handle_manual_start(Trigger.Request(), Trigger.Response())
        node.handle_manual_stop(Trigger.Request(), Trigger.Response())
        node.handle_resume_patrol(Trigger.Request(), Trigger.Response())

        assert node.state == PatrolState.EN_RONDA
    finally:
        node.destroy_node()


def test_pausar_retorno_no_registra_fallo_espurio(tmp_path, rclpy_context):
    """
    Pausar un RETORNO en curso cancela el goal, no cuenta como un fallo.

    El mismo mecanismo (_expected_cancel) que ya evita esto en EN_RONDA
    tiene que cubrir tambien RETORNO.
    """
    node = make_node(tmp_path, rclpy_context)
    try:
        node.handle_start_patrol(Trigger.Request(), Trigger.Response())
        node.handle_return_to_base(Trigger.Request(), Trigger.Response())
        node._current_goal_handle = FakeGoalHandle()

        node.handle_pause_patrol(Trigger.Request(), Trigger.Response())
        assert node._expected_cancel is True

        node.get_result_callback(FakeResultFuture(GoalStatus.STATUS_CANCELED))

        assert node.fail_count == 0
        assert node._expected_cancel is False
    finally:
        node.destroy_node()


# -- limpieza de actividad_previa cuando ya no hay nada pendiente -----------

def test_actividad_previa_se_limpia_al_llegar_a_base(tmp_path, rclpy_context):
    """
    Completar el retorno a base borra actividad_previa.

    Si no se limpia, un dato viejo puede sobrevivir a una ronda entera y
    reaparecer en un resume mucho despues -- el bug original. El campo
    solo queda seteado si hubo una pausa/manual de por medio (ida directa
    a RETORNO sin pausar no lo toca), asi que ese es el camino que hace
    falta probar.
    """
    node = make_node(tmp_path, rclpy_context)
    try:
        node.handle_start_patrol(Trigger.Request(), Trigger.Response())
        node.handle_return_to_base(Trigger.Request(), Trigger.Response())
        node.handle_pause_patrol(Trigger.Request(), Trigger.Response())
        node.handle_resume_patrol(Trigger.Request(), Trigger.Response())
        assert node.actividad_previa == 'RETORNO'

        node.get_result_callback(FakeResultFuture(GoalStatus.STATUS_SUCCEEDED))

        assert node.state == PatrolState.EN_BASE
        assert node.actividad_previa is None
    finally:
        node.destroy_node()


def test_actividad_previa_se_limpia_al_iniciar_ronda_nueva(tmp_path, rclpy_context):
    """
    /start_patrol limpia actividad_previa aunque haya quedado sucia.

    Defensivo: cubre cualquier camino a EN_BASE que no la haya limpiado
    (por ejemplo, /clear_failure tras una FALLA).
    """
    write_estado(tmp_path, 'EN_BASE', actividad_previa='RETORNO')
    node = make_node(tmp_path, rclpy_context)
    try:
        assert node.actividad_previa == 'RETORNO'  # arranca con el dato viejo

        node.handle_start_patrol(Trigger.Request(), Trigger.Response())

        assert node.actividad_previa is None
    finally:
        node.destroy_node()


# -- canal de eventos (patrol_events) ----------------------------------------

def test_log_event_publica_en_patrol_events_lo_mismo_que_el_jsonl(tmp_path, rclpy_context):
    """_log_event() saca el mismo payload por el topico que el que escribe al log."""
    node = make_node(tmp_path, rclpy_context)
    try:
        published = []
        node._events_pub.publish = published.append

        node.handle_start_patrol(Trigger.Request(), Trigger.Response())

        assert len(published) == 1
        payload = json.loads(published[0].data)
        assert payload['event'] == 'round_started'
        assert payload['round_id'] == node.round_id

        log_path = tmp_path / 'rondas' / f'{date.today().isoformat()}.jsonl'
        last_line = log_path.read_text().strip().splitlines()[-1]
        assert json.loads(last_line) == payload
    finally:
        node.destroy_node()


# -- una ronda nueva no hereda el indice de una ronda anterior ---------------

def test_ronda_nueva_no_hereda_el_current_goal_de_un_retorno_a_mitad_de_camino(
        tmp_path, rclpy_context):
    """
    Reproduce el bug real encontrado en vivo el 2026-08-12.

    Un return_to_base a mitad de camino no reseteaba current_goal, asi que
    la siguiente ronda arrancaba directo en ese waypoint intermedio en vez
    de en el primero.
    """
    node = make_node(tmp_path, rclpy_context)
    try:
        node.handle_start_patrol(Trigger.Request(), Trigger.Response())
        node.current_goal = 2  # simula haber avanzado varios waypoints

        node.handle_return_to_base(Trigger.Request(), Trigger.Response())
        assert node.state == PatrolState.RETORNO

        node._current_goal_handle = FakeGoalHandle()
        node.get_result_callback(FakeResultFuture(GoalStatus.STATUS_SUCCEEDED))
        assert node.state == PatrolState.EN_BASE

        node.handle_start_patrol(Trigger.Request(), Trigger.Response())

        assert node.current_goal == 0
    finally:
        node.destroy_node()


# -- resiliencia ante disco lleno / solo-lectura -----------------------------

def test_save_state_no_crashea_si_no_puede_escribir_en_disco(tmp_path, rclpy_context):
    """
    Simula disco lleno/solo-lectura para el archivo de estado.

    _save_state no debe dejar escapar la excepcion (mataria el nodo entero,
    ya que se llama desde un callback de accion), sino avisar via un evento.
    """
    node = make_node(tmp_path, rclpy_context)
    try:
        published = []
        node._events_pub.publish = published.append

        node.state_file.parent.chmod(0o500)
        try:
            node._save_state()
        finally:
            node.state_file.parent.chmod(0o700)

        eventos = [json.loads(p.data)['event'] for p in published]
        assert 'guardado_estado_fallido' in eventos
    finally:
        node.destroy_node()


def test_log_event_publica_igual_si_no_puede_escribir_el_jsonl(tmp_path, rclpy_context):
    """
    _log_event tiene que publicar el evento aunque el JSONL falle.

    Si ADEMAS el JSONL de rondas tampoco se puede escribir (disco lleno de
    verdad, no solo el archivo de estado), el evento tiene que llegar igual
    por el topico -- si no, guardado_estado_fallido terminaria fallando
    tambien al intentar registrarse a si mismo, sin avisarle a nadie.
    """
    node = make_node(tmp_path, rclpy_context)
    try:
        published = []
        node._events_pub.publish = published.append

        node.rounds_log_dir.chmod(0o500)
        try:
            node._log_event('evento_de_prueba')
        finally:
            node.rounds_log_dir.chmod(0o700)

        assert len(published) == 1
        assert json.loads(published[0].data)['event'] == 'evento_de_prueba'
    finally:
        node.destroy_node()


def test_avisa_cuando_el_disco_esta_por_llenarse(tmp_path, rclpy_context, monkeypatch):
    """
    Aviso preventivo antes de que el disco se llene de verdad.

    Un chequeo periodico detecta poco espacio libre y dispara un evento --
    una sola vez mientras siga por debajo del umbral, y otro cuando se
    normaliza.
    """
    node = make_node(tmp_path, rclpy_context, disk_free_warning_pct=10.0)
    try:
        published = []
        node._events_pub.publish = published.append

        monkeypatch.setattr(
            patrol_node_module.shutil, 'disk_usage',
            lambda path: FakeDiskUsage(total=100, free=5))
        node._check_disk_space()
        node._check_disk_space()  # no debe repetir el aviso mientras sigue en alerta

        eventos = [json.loads(p.data)['event'] for p in published]
        assert eventos.count('disco_casi_lleno') == 1

        monkeypatch.setattr(
            patrol_node_module.shutil, 'disk_usage',
            lambda path: FakeDiskUsage(total=100, free=50))
        node._check_disk_space()

        eventos = [json.loads(p.data)['event'] for p in published]
        assert eventos.count('disco_normalizado') == 1
    finally:
        node.destroy_node()


# -- enlace comms_agent<->Mosquitto ------------------------------------------

def link_msg(ok):
    msg = Bool()
    msg.data = ok
    return msg


def test_enlace_perdido_se_loguea_y_agenda_la_gracia(tmp_path, rclpy_context):
    node = make_node(tmp_path, rclpy_context)
    try:
        published = []
        node._events_pub.publish = published.append

        node._on_link_status(link_msg(False))

        assert node._link_ok is False
        assert node._link_loss_grace_timer is not None
        eventos = [json.loads(p.data)['event'] for p in published]
        assert eventos == ['enlace_perdido']
    finally:
        node.destroy_node()


def test_enlace_restablecido_antes_de_la_gracia_cancela_el_timer(tmp_path, rclpy_context):
    node = make_node(tmp_path, rclpy_context)
    try:
        published = []
        node._events_pub.publish = published.append

        node._on_link_status(link_msg(False))
        node._on_link_status(link_msg(True))

        assert node._link_ok is True
        assert node._link_loss_grace_timer is None
        eventos = [json.loads(p.data)['event'] for p in published]
        assert eventos == ['enlace_perdido', 'enlace_restablecido']
    finally:
        node.destroy_node()


def test_gracia_vencida_con_politica_continue_solo_avisa(tmp_path, rclpy_context):
    """
    La politica 'continue' (default) no cambia el comportamiento del robot.

    Solo se dispara el evento de aviso -- pensado para que el panel lo
    marque con mas urgencia si alguien esta mirando en vivo.
    """
    node = make_node(tmp_path, rclpy_context, on_link_loss='continue')
    try:
        node.handle_start_patrol(Trigger.Request(), Trigger.Response())
        published = []
        node._events_pub.publish = published.append

        node._on_link_status(link_msg(False))
        node._on_link_loss_grace_expired()

        assert node.state == PatrolState.EN_RONDA
        eventos = [json.loads(p.data)['event'] for p in published]
        assert 'enlace_perdido_prolongado' in eventos
    finally:
        node.destroy_node()


def test_gracia_vencida_con_politica_return_to_base_retorna(tmp_path, rclpy_context):
    node = make_node(tmp_path, rclpy_context, on_link_loss='return_to_base')
    try:
        node.handle_start_patrol(Trigger.Request(), Trigger.Response())

        node._on_link_status(link_msg(False))
        node._on_link_loss_grace_expired()

        assert node.state == PatrolState.RETORNO
    finally:
        node.destroy_node()


def test_gracia_vencida_con_politica_pause_pausa(tmp_path, rclpy_context):
    node = make_node(tmp_path, rclpy_context, on_link_loss='pause')
    try:
        node.handle_start_patrol(Trigger.Request(), Trigger.Response())

        node._on_link_status(link_msg(False))
        node._on_link_loss_grace_expired()

        assert node.state == PatrolState.PAUSADO
    finally:
        node.destroy_node()


def test_gracia_vencida_fuera_de_en_ronda_no_hace_nada(tmp_path, rclpy_context):
    """
    La politica de enlace no actua fuera de EN_RONDA.

    Si el robot ya estaba PAUSADO por otro motivo cuando se cumple la
    gracia, no hay nada que "continuar" o "volver" -- no debe tocar el
    estado.
    """
    node = make_node(tmp_path, rclpy_context, on_link_loss='return_to_base')
    try:
        node.handle_start_patrol(Trigger.Request(), Trigger.Response())
        node.handle_pause_patrol(Trigger.Request(), Trigger.Response())

        node._on_link_status(link_msg(False))
        node._on_link_loss_grace_expired()

        assert node.state == PatrolState.PAUSADO
    finally:
        node.destroy_node()


def test_gracia_vencida_luego_de_recuperado_no_hace_nada(tmp_path, rclpy_context):
    """
    Corrida tardia del timer tras haberse recuperado el enlace es un no-op.

    Cubre la carrera donde el timer ya estaba agendado para correr y el
    enlace se recupera justo antes.
    """
    node = make_node(tmp_path, rclpy_context, on_link_loss='return_to_base')
    try:
        node.handle_start_patrol(Trigger.Request(), Trigger.Response())
        published = []
        node._events_pub.publish = published.append

        node._on_link_status(link_msg(False))
        node._on_link_status(link_msg(True))
        node._on_link_loss_grace_expired()

        assert node.state == PatrolState.EN_RONDA
        eventos = [json.loads(p.data)['event'] for p in published]
        assert 'enlace_perdido_prolongado' not in eventos
    finally:
        node.destroy_node()
