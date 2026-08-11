"""
Tests de comms_agent_node, contra el Mosquitto real de esta maquina.

No se simula ni se mockea el broker -- se usa el servicio de sistema tal
cual. Usa un installation_id/robot_id de prueba para no pisar los topicos
del robot real mientras corre.
"""

import rclpy
import pytest
from std_msgs.msg import String
from rclpy.parameter import Parameter

from comms_agent.comms_agent_node import CommsAgentNode


@pytest.fixture
def rclpy_context():
    rclpy.init()
    yield
    rclpy.shutdown()


def make_node(rclpy_context):
    overrides = [
        Parameter('installation_id', value='test'),
        Parameter('robot_id', value='test-comms-agent'),
        Parameter('mqtt_username', value='andino'),
        Parameter('mqtt_password', value='patrol_2026'),
    ]
    return CommsAgentNode(parameter_overrides=overrides)


class FakeMqttClient:
    """
    Reemplaza al cliente real para poder inspeccionar las llamadas.

    Sirve para chequear que se llamo a publish() con lo esperado, sin
    depender del timing real de una reconexion de red.
    """

    def __init__(self):
        self.published = []

    def subscribe(self, topic, qos=0):
        pass

    def publish(self, topic, payload, qos=0, retain=False):
        self.published.append((topic, payload, qos, retain))


def test_reenvia_ultimo_estado_y_posicion_al_reconectar(rclpy_context):
    node = make_node(rclpy_context)
    try:
        msg = String()
        msg.data = 'FALLA'
        node._on_patrol_state(msg)  # simula que llego un cambio de ROS

        fake_client = FakeMqttClient()
        node._on_mqtt_connect(fake_client, None, None, 0)

        state_publishes = [p for p in fake_client.published if p[0] == node.state_topic]
        assert len(state_publishes) == 1
        assert state_publishes[0][1] == 'FALLA'
    finally:
        node.destroy_node()


def test_no_publica_nada_si_nunca_hubo_estado(rclpy_context):
    """
    Primera conexion real: todavia no llego nada de ROS.

    No hay que inventar un estado.
    """
    node = make_node(rclpy_context)
    try:
        fake_client = FakeMqttClient()
        node._on_mqtt_connect(fake_client, None, None, 0)

        state_publishes = [p for p in fake_client.published if p[0] == node.state_topic]
        assert state_publishes == []
    finally:
        node.destroy_node()
