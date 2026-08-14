import os
import signal
import subprocess

import rclpy
from rclpy.node import Node
from std_srvs.srv import Trigger


class PatrolClient(Node):

    def __init__(self):
        super().__init__('patrol_control_client')

        self.trigger_clients = {
            's': self.create_client(Trigger, '/start_patrol'),
            'p': self.create_client(Trigger, '/pause_patrol'),
            'r': self.create_client(Trigger, '/resume_patrol'),
            'b': self.create_client(Trigger, '/return_to_base'),
            'c': self.create_client(Trigger, '/clear_failure'),
        }
        self.manual_start_client = self.create_client(Trigger, '/manual_start')
        self.manual_stop_client = self.create_client(Trigger, '/manual_stop')

        for name, client in list(self.trigger_clients.items()) + [
                ('manual_start', self.manual_start_client),
                ('manual_stop', self.manual_stop_client)]:
            while not client.wait_for_service(timeout_sec=1.0):
                self.get_logger().info(f'Esperando servicio de {name}...')

        self.teleop_process = None

        self.get_logger().info(
            'Listo. Comandos: '
            '[s] iniciar, [p] pausar, [r] reanudar, '
            '[m] manual (teleop, Ctrl+C para salir), '
            '[b] retornar a base, [c] limpiar falla, [q] salir.')
        self.loop()

    def loop(self):
        try:
            while rclpy.ok():
                cmd = input('> ')
                if cmd in self.trigger_clients:
                    self.call_service(self.trigger_clients[cmd])
                elif cmd == 'm':
                    self.call_service(self.manual_start_client)
                    self.run_teleop()
                    self.call_service(self.manual_stop_client)
                elif cmd == 'q':
                    break
                else:
                    self.get_logger().info(f'Comando desconocido: {cmd}')
        except KeyboardInterrupt:
            pass

    def call_service(self, client):
        req = Trigger.Request()
        future = client.call_async(req)
        rclpy.spin_until_future_complete(self, future)
        if future.result():
            self.get_logger().info(future.result().message)

    def run_teleop(self):
        self.get_logger().info('Iniciando teleop. Ctrl+C para volver a pausa.')
        # remapeado a cmd_vel_teleop, no al /cmd_vel final -- lo arbitra
        # twist_mux (robot_bringup) contra Nav2, con dead-man timer.
        teleop = subprocess.Popen(
            ['ros2', 'run', 'teleop_twist_keyboard', 'teleop_twist_keyboard',
             '--ros-args', '--remap', 'cmd_vel:=cmd_vel_teleop'],
            preexec_fn=os.setsid,
        )
        try:
            teleop.wait()
        except KeyboardInterrupt:
            os.killpg(os.getpgid(teleop.pid), signal.SIGINT)
            teleop.wait()
        self.get_logger().info('Teleop terminado.')


def main(args=None):
    rclpy.init(args=args)
    node = PatrolClient()
    node.destroy_node()
    rclpy.shutdown()


if __name__ == '__main__':
    main()
