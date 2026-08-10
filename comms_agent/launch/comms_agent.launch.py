from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue


def generate_launch_description():
    mqtt_host_arg = DeclareLaunchArgument(
        'mqtt_host', default_value='localhost',
        description='Host del broker Mosquitto.')
    mqtt_port_arg = DeclareLaunchArgument(
        'mqtt_port', default_value='1883',
        description='Puerto del broker Mosquitto.')
    mqtt_username_arg = DeclareLaunchArgument(
        'mqtt_username', default_value='',
        description='Usuario para autenticarse contra Mosquitto. Vacio = sin autenticacion '
                     '(falla si Mosquitto tiene allow_anonymous en false).')
    mqtt_password_arg = DeclareLaunchArgument(
        'mqtt_password', default_value='',
        description='Contrasena para mqtt_username. No tiene default real a proposito, '
                     'para no dejar una credencial hardcodeada en el repo.')
    installation_id_arg = DeclareLaunchArgument(
        'installation_id', default_value='default',
        description='Identificador de la instalacion (para topicos MQTT multi-sitio).')
    robot_id_arg = DeclareLaunchArgument(
        'robot_id', default_value='andino',
        description='Identificador del robot (para topicos MQTT multi-robot).')

    return LaunchDescription([
        mqtt_host_arg,
        mqtt_port_arg,
        mqtt_username_arg,
        mqtt_password_arg,
        installation_id_arg,
        robot_id_arg,

        Node(
            package='comms_agent',
            executable='comms_agent_node',
            name='comms_agent_node',
            output='screen',
            parameters=[{
                'mqtt_host': LaunchConfiguration('mqtt_host'),
                'mqtt_port': ParameterValue(LaunchConfiguration('mqtt_port'), value_type=int),
                'mqtt_username': LaunchConfiguration('mqtt_username'),
                'mqtt_password': LaunchConfiguration('mqtt_password'),
                'installation_id': LaunchConfiguration('installation_id'),
                'robot_id': LaunchConfiguration('robot_id'),
            }],
        ),
    ])
