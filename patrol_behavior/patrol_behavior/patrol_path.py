import rclpy
from rclpy.node import Node
from geometry_msgs.msg import PoseStamped
from nav2_msgs.action import NavigateToPose
from rclpy.action import ActionClient
from std_srvs.srv import Trigger
import time

class PatrolNode(Node):
    def __init__(self):
        super().__init__('patrol_path')
        
        self._action_client = ActionClient(self, NavigateToPose, 'navigate_to_pose')

        self.waypoints = [
            self.create_pose(0.0, 0.0, 0.0),
            self.create_pose(6.910, 6.125, 0.0),
            self.create_pose(10.801, 4.965, -1.476),
            self.create_pose(14.693, 1.859, 1.310),
            self.create_pose(18.810, 7.585, -1.450),
            self.create_pose(21.317, -2.519, 3.117),
            self.create_pose(14.331, -2.313, 3.142),
            self.create_pose(11.887, -2.482, 3.102),
            self.create_pose(8.706, -2.407, 3.142),
            self.create_pose(2.531, -3.193, 1.773)
        ]

        self.current_goal = 0
        self.retry_count = 0
        self.max_retries = 5
        
        self.interrupt_patrol = False
        self.manual_active = False
        self.last_manual_time = self.get_clock().now()
        
        self._current_goal_handle = None  # <--- para cancelar goals activos

        self.create_service(Trigger, '/pause_patrol', self.handle_pause_patrol)
        self.create_service(Trigger, '/resume_patrol', self.handle_resume_patrol)

        self.get_logger().info('Esperando al servidor de acción NavigateToPose...')
        self._action_client.wait_for_server()

        #self.create_timer(1.0, self.check_resume_patrol)

        self.get_logger().info('Servidor listo, empezamos patrulla')
        self.send_next_goal()

    def manual_result_callback(self, future):
        status = future.result().status
        if status == 3:
            self.get_logger().info('Goal manual completado.')
        else:
            self.get_logger().warn(f'Goal manual falló con status {status}')

        self.manual_active = False
        self.last_manual_time = self.get_clock().now()

    def check_resume_patrol(self):
        if self.interrupt_patrol and not self.manual_active:
            time_diff = (self.get_clock().now() - self.last_manual_time).nanoseconds / 1e9
            if time_diff > 5.0:
                self.get_logger().info('No hay orden manual hace 5s. Retomando patrulla.')
                self.interrupt_patrol = False
                self.send_next_goal()

    def create_pose(self, x, y, yaw):
        pose = PoseStamped()
        pose.header.frame_id = 'map'
        pose.header.stamp = self.get_clock().now().to_msg()
        pose.pose.position.x = x
        pose.pose.position.y = y

        import math
        from tf_transformations import quaternion_from_euler
        q = quaternion_from_euler(0, 0, yaw)
        pose.pose.orientation.x = q[0]
        pose.pose.orientation.y = q[1]
        pose.pose.orientation.z = q[2]
        pose.pose.orientation.w = q[3]
        return pose

    def send_next_goal(self):
        if self.interrupt_patrol:
           self.get_logger().info('Patrulla pausada, no se envía goal.')
           return

        if self.current_goal >= len(self.waypoints):
           self.get_logger().info('Patrulla terminada, reiniciando...')
           self.current_goal = 0

        goal_msg = NavigateToPose.Goal()
        goal_msg.pose = self.waypoints[self.current_goal]

        self.get_logger().info(f'Enviando goal #{self.current_goal + 1}')
        send_goal_future = self._action_client.send_goal_async(goal_msg, feedback_callback=self.feedback_callback)
        send_goal_future.add_done_callback(self.goal_response_callback)


    def goal_response_callback(self, future):
        goal_handle = future.result()
        if not goal_handle.accepted:
            self.get_logger().warn(f'Goal #{self.current_goal + 1} fue rechazado.')
            self.retry_count += 1
            if self.retry_count >= self.max_retries:
                self.get_logger().warn('Demasiados intentos fallidos. Pasando al siguiente goal.')
                self.retry_count = 0
                self.current_goal += 1
            time.sleep(1.0)
            self.send_next_goal()
            return

        self.get_logger().info('Goal aceptado, esperando resultado...')
        self._current_goal_handle = goal_handle  # Guardar handle para cancelar
        self._get_result_future = goal_handle.get_result_async()
        self._get_result_future.add_done_callback(self.get_result_callback)

    def handle_pause_patrol(self, request, response):
        self.get_logger().info('Servicio recibido: PAUSAR patrulla')
        self.interrupt_patrol = True

        if self._current_goal_handle is not None:
           cancel_future = self._current_goal_handle.cancel_goal_async()
           self.get_logger().info('Cancelando goal actual...')
           cancel_future.add_done_callback(lambda f: self.get_logger().info('Goal cancelado.'))

        response.success = True
        response.message = 'Patrulla pausada.'
        return response

    def handle_resume_patrol(self, request, response):
        if not self.interrupt_patrol:
            response.success = False
            response.message = 'La patrulla ya estaba activa.'
        else:
            self.get_logger().info('Servicio recibido: REANUDAR patrulla')
            self.interrupt_patrol = False
            self.send_next_goal()
            response.success = True
            response.message = 'Patrulla reanudada.'
        return response

    def feedback_callback(self, feedback_msg):
        feedback = feedback_msg.feedback
        self.get_logger().info(f'Feedback recibido: {feedback.current_pose.pose.position.x:.2f}, {feedback.current_pose.pose.position.y:.2f}')

    def get_result_callback(self, future):
        result = future.result().result
        status = future.result().status

        if status == 4:
            self.get_logger().info('Goal abortado')
        elif status == 5:
            self.get_logger().info('Goal rechazado')
        elif status == 3:
            self.get_logger().info('Goal alcanzado!')

        self._current_goal_handle = None  # limpiar goal handle
        self.retry_count = 0

        if self.interrupt_patrol:
            self.get_logger().info('Patrulla pausada. Esperando retomar...')
            return
        else:
            self.current_goal += 1
            time.sleep(1.0)
            self.send_next_goal()

def main(args=None):
    rclpy.init(args=args)
    node = PatrolNode()
    rclpy.spin(node)
    rclpy.shutdown()

if __name__ == '__main__':
    main()

