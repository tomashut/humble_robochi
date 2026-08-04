# patrol_control_client.py

import rclpy
from rclpy.node import Node
from std_srvs.srv import Trigger
import subprocess
import os
import signal

class PatrolClient(Node):
    def __init__(self):
        super().__init__('patrol_control_client')

        self.pause_client = self.create_client(Trigger, '/pause_patrol')
        self.resume_client = self.create_client(Trigger, '/resume_patrol')
        self.teleop_process = None

        while not self.pause_client.wait_for_service(timeout_sec=1.0):
            self.get_logger().info('Esperando /pause_patrol...')
        while not self.resume_client.wait_for_service(timeout_sec=1.0):
            self.get_logger().info('Esperando /resume_patrol...')

        self.get_logger().info('Listo. Escribí [p] para pausar y activar teleop, [r] para reanudar y matar teleop, [q] para salir.')
        self.loop()

    def loop(self):
        try:
            while rclpy.ok():
                cmd = input('> ')
                if cmd == 'p':
                    self.call_service(self.pause_client)
                    self.start_teleop()
                elif cmd == 'r':
                    self.call_service(self.resume_client)
                    self.stop_teleop()
                elif cmd == 'q':
                    self.stop_teleop()
                    break
        except KeyboardInterrupt:
            self.stop_teleop()

    def call_service(self, client):
        req = Trigger.Request()
        future = client.call_async(req)
        rclpy.spin_until_future_complete(self, future)
        if future.result():
            self.get_logger().info(future.result().message)

    import subprocess
    import signal

    def start_teleop(self):
        self.get_logger().info('Iniciando teleop. Presioná Ctrl+C para salir del modo manual.')

        # Ejecutamos el teleop en un proceso hijo que maneje su propia señal
        teleop = subprocess.Popen(
           ['ros2', 'run', 'teleop_twist_keyboard', 'teleop_twist_keyboard'],
           preexec_fn=os.setsid
        )

        try:
           teleop.wait()
        except KeyboardInterrupt:
           self.get_logger().info('Interrupción capturada, matando teleop...')
           os.killpg(os.getpgid(teleop.pid), signal.SIGINT)
           teleop.wait()

        self.get_logger().info('Teleop terminado. Podés escribir [r] para reanudar o [q] para salir.')


    def stop_teleop(self):
        if self.teleop_process is not None:
            self.get_logger().info('Finalizando teleop...')
            os.killpg(os.getpgid(self.teleop_process.pid), signal.SIGTERM)
            self.teleop_process = None

def main(args=None):
    rclpy.init(args=args)
    node = PatrolClient()
    node.destroy_node()
    rclpy.shutdown()

