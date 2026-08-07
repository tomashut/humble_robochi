import os

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, ExecuteProcess
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from launch_ros.actions import Node
from launch_ros.substitutions import FindPackageShare


def generate_launch_description():
    waypoints_file_arg = DeclareLaunchArgument(
        'waypoints_file',
        default_value=PathJoinSubstitution(
            [FindPackageShare('patrol_fsm'), 'config', 'waypoints.yaml']),
        description='Ruta al YAML de waypoints. Pensado para poder pasar un archivo '
                     'distinto por robot/instalacion en el futuro sin tocar codigo.'
    )
    default_rounds_log_dir = os.path.join(
        os.path.expanduser('~'), '.local', 'share', 'patrol_fsm', 'rondas')
    rounds_log_dir_arg = DeclareLaunchArgument(
        'rounds_log_dir',
        default_value=default_rounds_log_dir,
        description='Carpeta donde se escribe el registro de ejecuciones '
                     '(un JSONL por dia).'
    )
    waypoints_file = LaunchConfiguration('waypoints_file')
    rounds_log_dir = LaunchConfiguration('rounds_log_dir')

    return LaunchDescription([
        waypoints_file_arg,
        rounds_log_dir_arg,

        Node(
            package='patrol_fsm',
            executable='patrol_node',
            name='patrol_node',
            output='screen',
            parameters=[{
                'waypoints_file': waypoints_file,
                'rounds_log_dir': rounds_log_dir,
            }],
        ),

        ExecuteProcess(
            cmd=['xterm', '-hold', '-e', 'ros2 run patrol_fsm patrol_client'],
            name='patrol_client_terminal',
            output='screen'
        )
    ])
