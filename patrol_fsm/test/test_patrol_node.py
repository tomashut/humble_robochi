"""Tests de la logica de patrol_node, sin necesitar Gazebo/Nav2 corriendo.

Se construye el nodo con wait_for_nav2=False (salta la espera del
servidor de accion) y con archivos de estado/rondas/waypoints propios
en un directorio temporal, para no tocar nunca los reales del usuario
ni depender de una simulacion levantada.
"""

import json

import yaml
import pytest

import rclpy
from rclpy.parameter import Parameter
from geometry_msgs.msg import PoseWithCovarianceStamped
from std_srvs.srv import Trigger

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


# -- arranque limpio (sin state.json previo) ---------------------------------

def test_arranca_en_base_sin_estado_previo(tmp_path, rclpy_context):
    node = make_node(tmp_path, rclpy_context)
    try:
        assert node.state == PatrolState.EN_BASE
        assert node.current_goal == 0
        assert node.fail_count == 0
    finally:
        node.destroy_node()


# -- auto-resume solo debe reanudar con localizacion confiable ---------------

def write_estado_pausado_por_reinicio(tmp_path):
    """Simula un state.json dejado por un corte a mitad de ronda."""
    state_file = tmp_path / 'state.json'
    state_file.write_text(json.dumps({
        'state': 'EN_RONDA',
        'current_goal': 1,
        'fail_count': 0,
        'round_id': '20260811-000000000',
    }))


def amcl_pose_con_covarianza(var_x, var_y, var_yaw):
    msg = PoseWithCovarianceStamped()
    msg.pose.covariance[0] = var_x
    msg.pose.covariance[7] = var_y
    msg.pose.covariance[35] = var_yaw
    return msg


def test_fire_auto_resume_arranca_un_giro_no_reanuda_directo(tmp_path, rclpy_context):
    """_fire_auto_resume ya no decide nada por si sola -- primero gira, y
    recien la decision real pasa por _on_spin_result (proximos tests)."""
    write_estado_pausado_por_reinicio(tmp_path)
    node = make_node(tmp_path, rclpy_context)
    try:
        node._on_amcl_pose(amcl_pose_con_covarianza(0.001, 0.001, 0.001))  # bien ubicado
        node._fire_auto_resume()
        assert node.state == PatrolState.PAUSADO  # no reanuda antes de girar
        assert node._spin_in_progress is True
    finally:
        node.destroy_node()


def test_no_reanuda_tras_girar_sin_ningun_dato_de_amcl(tmp_path, rclpy_context):
    write_estado_pausado_por_reinicio(tmp_path)
    node = make_node(tmp_path, rclpy_context)
    try:
        assert node.state == PatrolState.PAUSADO  # aterrizo pausado, por el reinicio
        node._on_spin_result(None)  # simula que el giro termino
        assert node.state == PatrolState.PAUSADO  # sin dato de AMCL, no se mueve
    finally:
        node.destroy_node()


def test_no_reanuda_tras_girar_con_localizacion_mala(tmp_path, rclpy_context):
    write_estado_pausado_por_reinicio(tmp_path)
    node = make_node(tmp_path, rclpy_context)
    try:
        node._on_amcl_pose(amcl_pose_con_covarianza(1.0, 1.0, 1.0))  # bien perdido
        node._on_spin_result(None)
        assert node.state == PatrolState.PAUSADO
    finally:
        node.destroy_node()


def test_reanuda_tras_girar_con_localizacion_buena(tmp_path, rclpy_context):
    write_estado_pausado_por_reinicio(tmp_path)
    node = make_node(tmp_path, rclpy_context)
    try:
        node._on_amcl_pose(amcl_pose_con_covarianza(0.001, 0.001, 0.001))  # bien ubicado
        node._on_spin_result(None)
        assert node.state == PatrolState.EN_RONDA
    finally:
        node.destroy_node()


def test_manual_start_cancela_un_giro_en_curso(tmp_path, rclpy_context):
    """Si un humano toma control mientras el robot esta girando para
    converger, no tiene que quedar girando solo por su cuenta."""
    write_estado_pausado_por_reinicio(tmp_path)
    node = make_node(tmp_path, rclpy_context)
    try:
        node._fire_auto_resume()
        assert node._spin_in_progress is True

        response = node.handle_manual_start(Trigger.Request(), Trigger.Response())

        assert response.success is True
        assert node._spin_in_progress is False
    finally:
        node.destroy_node()


# -- vigilancia continua: si se pierde la localizacion YA navegando ----------

def test_se_va_a_falla_si_se_pierde_localizacion_navegando(tmp_path, rclpy_context):
    node = make_node(tmp_path, rclpy_context)
    try:
        node.handle_start_patrol(Trigger.Request(), Trigger.Response())
        assert node.state == PatrolState.EN_RONDA

        node._on_amcl_pose(amcl_pose_con_covarianza(5.0, 5.0, 2.0))  # se perdio

        assert node.state == PatrolState.FALLA
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
    """El chequeo continuo es solo mientras navega -- en PAUSADO, por ejemplo,
    no tiene que disparar nada (ese caso lo cubre el giro de convergencia)."""
    node = make_node(tmp_path, rclpy_context)
    try:
        assert node.state == PatrolState.EN_BASE

        node._on_amcl_pose(amcl_pose_con_covarianza(5.0, 5.0, 2.0))

        assert node.state == PatrolState.EN_BASE
    finally:
        node.destroy_node()
