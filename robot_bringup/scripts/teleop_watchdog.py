#!/usr/bin/env python3
"""
Dead-man timer real para el teleop.

twist_mux NO tiene ningun timer propio que publique cero por su cuenta --
su 'timeout' solo se revisa de forma pasiva, cuando llega un mensaje
nuevo en algun topico (ver robot_bringup/README.md para el detalle). Si
la fuente de teleop se queda callada del todo (se solto la tecla, se
corto el link) y nada mas esta publicando, twist_mux nunca vuelve a
evaluar nada y el robot sigue con la ultima orden para siempre.

Este nodo es el que realmente mira el reloj: si pasan mas de timeout_sec
sin un mensaje no-cero en cmd_vel_teleop, publica el un Twist en cero ahi
mismo -- como si el joystick se hubiera centrado solo -- para que
twist_mux lo reciba y lo relaye con su logica de siempre, sin duplicar
nada de prioridades aca.
"""

import rclpy
from rclpy.node import Node
from geometry_msgs.msg import Twist


class TeleopWatchdog(Node):

    def __init__(self):
        super().__init__('teleop_watchdog')

        self.declare_parameter('timeout_sec', 0.5)
        self.declare_parameter('check_period_sec', 0.1)
        self.timeout_sec = self.get_parameter('timeout_sec').get_parameter_value().double_value
        check_period_sec = (
            self.get_parameter('check_period_sec').get_parameter_value().double_value)

        self._last_nonzero_at = None
        self._ya_avise = True  # no hay nada pendiente de avisar hasta el primer mensaje real

        self._pub = self.create_publisher(Twist, 'cmd_vel_teleop', 10)
        self.create_subscription(Twist, 'cmd_vel_teleop', self._on_cmd_vel_teleop, 10)
        self.create_timer(check_period_sec, self._check_timeout)

    def _on_cmd_vel_teleop(self, msg):
        # ignora los ceros (incluido el que publicamos nosotros mismos al
        # vencer el timeout) -- no hay nada que vigilar si ya esta quieto.
        if msg == Twist():
            return
        self._last_nonzero_at = self.get_clock().now()
        self._ya_avise = False

    def _check_timeout(self):
        if self._last_nonzero_at is None or self._ya_avise:
            return
        elapsed_sec = (self.get_clock().now() - self._last_nonzero_at).nanoseconds / 1e9
        if elapsed_sec > self.timeout_sec:
            self.get_logger().warn(
                f'Sin ordenes de teleop hace {elapsed_sec:.2f}s -- publicando velocidad cero.')
            self._pub.publish(Twist())
            self._ya_avise = True


def main(args=None):
    rclpy.init(args=args)
    node = TeleopWatchdog()
    rclpy.spin(node)
    rclpy.shutdown()


if __name__ == '__main__':
    main()
