from launch import LaunchDescription
from launch.actions import ExecuteProcess
from launch_ros.actions import Node


def generate_launch_description():
    return LaunchDescription([
        Node(
            package='patrol_fsm',
            executable='patrol_node',
            name='patrol_node',
            output='screen'
        ),

        ExecuteProcess(
            cmd=['xterm', '-hold', '-e', 'ros2 run patrol_fsm patrol_client'],
            name='patrol_client_terminal',
            output='screen'
        )
    ])
