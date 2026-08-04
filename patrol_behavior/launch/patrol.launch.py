from launch import LaunchDescription
from launch.actions import ExecuteProcess
from launch_ros.actions import Node

def generate_launch_description():
    return LaunchDescription([
        # patrol_path sin logs molestos
        Node(
            package='patrol_behavior',
            executable='patrol_path',
            name='patrol_path',
            output='log'  # manda logs al archivo
        ),

        # cliente en nueva terminal con input
        ExecuteProcess(
            cmd=['xterm', '-hold', '-e', 'ros2 run patrol_behavior patrol_path_client'],
            name='patrol_client_terminal',
            output='screen'
        )
    ])

