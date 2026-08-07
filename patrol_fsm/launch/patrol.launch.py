import os

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, ExecuteProcess
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue
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
    default_state_file = os.path.join(
        os.path.expanduser('~'), '.local', 'share', 'patrol_fsm', 'state.json')
    state_file_arg = DeclareLaunchArgument(
        'state_file',
        default_value=default_state_file,
        description='Archivo donde se persiste el estado de la maquina de estados '
                     '(sobrevive reinicios del nodo).'
    )
    auto_resume_timeout_arg = DeclareLaunchArgument(
        'auto_resume_timeout_sec',
        default_value='300',
        description='Segundos en PAUSADO tras un reinicio no planeado antes de reanudar '
                     'la ronda sola, si nadie mando un comando antes. 0 desactiva esto '
                     '(queda pausado indefinidamente, requiere intervencion humana).'
    )
    waypoints_file = LaunchConfiguration('waypoints_file')
    rounds_log_dir = LaunchConfiguration('rounds_log_dir')
    state_file = LaunchConfiguration('state_file')
    auto_resume_timeout_sec = LaunchConfiguration('auto_resume_timeout_sec')

    return LaunchDescription([
        waypoints_file_arg,
        rounds_log_dir_arg,
        state_file_arg,
        auto_resume_timeout_arg,

        Node(
            package='patrol_fsm',
            executable='patrol_node',
            name='patrol_node',
            output='screen',
            parameters=[{
                'waypoints_file': waypoints_file,
                'rounds_log_dir': rounds_log_dir,
                'state_file': state_file,
                'auto_resume_timeout_sec': ParameterValue(auto_resume_timeout_sec, value_type=int),
            }],
        ),

        ExecuteProcess(
            cmd=['xterm', '-hold', '-e', 'ros2 run patrol_fsm patrol_client'],
            name='patrol_client_terminal',
            output='screen'
        )
    ])
