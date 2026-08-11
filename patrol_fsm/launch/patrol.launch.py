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
        description=(
            'Ruta al YAML de waypoints. Pensado para poder pasar un archivo '
            'distinto por robot/instalacion en el futuro sin tocar codigo.'
        )
    )
    default_rounds_log_dir = os.path.join(
        os.path.expanduser('~'), '.local', 'share', 'patrol_fsm', 'rondas')
    rounds_log_dir_arg = DeclareLaunchArgument(
        'rounds_log_dir',
        default_value=default_rounds_log_dir,
        description=(
            'Carpeta donde se escribe el registro de ejecuciones (un JSONL por dia).'
        )
    )
    default_state_file = os.path.join(
        os.path.expanduser('~'), '.local', 'share', 'patrol_fsm', 'state.json')
    state_file_arg = DeclareLaunchArgument(
        'state_file',
        default_value=default_state_file,
        description=(
            'Archivo donde se persiste el estado de la maquina de estados '
            '(sobrevive reinicios del nodo).'
        )
    )
    auto_resume_timeout_arg = DeclareLaunchArgument(
        'auto_resume_timeout_sec',
        default_value='300',
        description=(
            'Segundos en INTERRUMPIDO (aterrizaje de un reinicio a media ronda '
            'o retorno) antes de intentar reanudar la ronda sola, si nadie mando '
            'un comando antes -- siempre con el giro de convergencia y el chequeo '
            'de localizacion de por medio. 0 desactiva esto (queda esperando '
            'intervencion humana indefinidamente). No afecta a PAUSADO, que nunca '
            'se auto-reanuda bajo ninguna circunstancia.'
        )
    )
    amcl_position_covariance_threshold_arg = DeclareLaunchArgument(
        'amcl_position_covariance_threshold',
        default_value='0.5',
        description=(
            'Umbral de varianza de posicion (x/y) de AMCL para considerar la '
            'localizacion confiable antes de auto-reanudar. Medido en vivo en el '
            'mapa depot -- si cambia el mapa o el sensor, hay que volver a medir.'
        )
    )
    amcl_orientation_covariance_threshold_arg = DeclareLaunchArgument(
        'amcl_orientation_covariance_threshold',
        default_value='0.3',
        description=(
            'Umbral de varianza de orientacion (yaw) de AMCL, mismo criterio que '
            'el de posicion.'
        )
    )
    localizacion_perdida_confirmaciones_arg = DeclareLaunchArgument(
        'localizacion_perdida_confirmaciones',
        default_value='3',
        description=(
            'Cuantas lecturas de AMCL malas SEGUIDAS, mientras navega, hacen '
            'falta para dar la localizacion por perdida y pasar a FALLA. Evita '
            'que un solo pico transitorio dispare una falla.'
        )
    )
    waypoints_file = LaunchConfiguration('waypoints_file')
    rounds_log_dir = LaunchConfiguration('rounds_log_dir')
    state_file = LaunchConfiguration('state_file')
    auto_resume_timeout_sec = LaunchConfiguration('auto_resume_timeout_sec')
    amcl_position_covariance_threshold = LaunchConfiguration(
        'amcl_position_covariance_threshold')
    amcl_orientation_covariance_threshold = LaunchConfiguration(
        'amcl_orientation_covariance_threshold')
    localizacion_perdida_confirmaciones = LaunchConfiguration(
        'localizacion_perdida_confirmaciones')

    return LaunchDescription([
        waypoints_file_arg,
        rounds_log_dir_arg,
        state_file_arg,
        auto_resume_timeout_arg,
        amcl_position_covariance_threshold_arg,
        amcl_orientation_covariance_threshold_arg,
        localizacion_perdida_confirmaciones_arg,

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
                'amcl_position_covariance_threshold': ParameterValue(
                    amcl_position_covariance_threshold, value_type=float),
                'amcl_orientation_covariance_threshold': ParameterValue(
                    amcl_orientation_covariance_threshold, value_type=float),
                'localizacion_perdida_confirmaciones': ParameterValue(
                    localizacion_perdida_confirmaciones, value_type=int),
            }],
        ),

        ExecuteProcess(
            cmd=['xterm', '-hold', '-e', 'ros2 run patrol_fsm patrol_client'],
            name='patrol_client_terminal',
            output='screen'
        )
    ])
