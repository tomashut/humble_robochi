"""
Arranca Nav2 + twist_mux para el robot.

Reemplaza a nav2:=True de andino_gz.launch.py (que hay que lanzar con
nav2:=False cuando se usa este launch, para no levantar dos Nav2 a la vez).
Reusa el nav2_params.yaml de andino_gz -- no se duplica su contenido, solo
se referencia por path.

Solo cubre el caso de un robot sin namespace (el uso actual del proyecto).
Multi-robot necesitaria threadear el namespace igual que hace andino_gz.
"""

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription
from launch.conditions import IfCondition
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from launch_ros.actions import Node, SetRemap

from andino_gz.launch_tools.substitutions import TextJoin

import os


def generate_launch_description():
    pkg_robot_bringup = get_package_share_directory('robot_bringup')
    pkg_andino_gz = get_package_share_directory('andino_gz')
    pkg_nav2_bringup = get_package_share_directory('nav2_bringup')

    map_name_arg = DeclareLaunchArgument(
        'map', default_value='office',
        description='Nombre del mapa (debe coincidir con el world_name de la sim).')
    params_file_arg = DeclareLaunchArgument(
        'params_file',
        default_value=PathJoinSubstitution([pkg_andino_gz, 'config', 'nav2_params.yaml']),
        description='Parametros de Nav2 -- por default, el mismo YAML que usa andino_gz.')
    twist_mux_config_arg = DeclareLaunchArgument(
        'twist_mux_config',
        default_value=PathJoinSubstitution([pkg_robot_bringup, 'config', 'twist_mux.yaml']),
        description='Config de prioridades/timeout de twist_mux.')
    rviz_arg = DeclareLaunchArgument(
        'rviz', default_value='True',
        description=(
            'Mostrar RViz con el mapa/plan de Nav2. Si tambien la levanta '
            'andino_gz.launch.py (con rviz:=True ahi), poner una de las dos '
            'en False para no abrir dos ventanas.'))

    map_name = LaunchConfiguration('map')
    params_file = LaunchConfiguration('params_file')
    map_path = PathJoinSubstitution(
        [pkg_andino_gz, 'maps', map_name, TextJoin(substitutions=[map_name, '.yaml'])])

    # el controller_server de Nav2 publica en 'cmd_vel' por default -- lo
    # corremos a 'cmd_vel_nav' para que twist_mux pueda arbitrarlo contra
    # teleop en vez de que los dos escriban el mismo topico. Alcance
    # acotado a este launch (solo incluye nav2_bringup, nada mas), no pisa
    # el puente cmd_vel->Gazebo de andino_gz.launch.py.
    cmd_vel_remap = SetRemap(src='cmd_vel', dst='cmd_vel_nav')

    # mismos remaps de scan que usa andino_gz.launch.py para el caso de un
    # solo robot -- el YAML de nav2_params usa nombres relativos, que sin
    # esto terminan bajo global_costmap/local_costmap en vez de compartir
    # el scan real del robot.
    scan_remap_global = SetRemap(src='/global_costmap/scan', dst='/scan')
    scan_remap_local = SetRemap(src='/local_costmap/scan', dst='/scan')

    nav2 = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            os.path.join(pkg_nav2_bringup, 'launch', 'bringup_launch.py')),
        launch_arguments={
            'map': map_path,
            'autostart': 'True',
            'use_sim_time': 'True',
            'params_file': params_file,
        }.items(),
    )

    twist_mux = Node(
        package='twist_mux',
        executable='twist_mux',
        output='screen',
        parameters=[LaunchConfiguration('twist_mux_config'), {'use_sim_time': True}],
        remappings=[('cmd_vel_out', 'cmd_vel')],
    )

    # dead-man timer real -- twist_mux por si solo no tiene ningun timer que
    # publique cero por su cuenta, ver README. El timeout de aca debe
    # coincidir con el timeout de 'teleop' en twist_mux.yaml.
    teleop_watchdog = Node(
        package='robot_bringup',
        executable='teleop_watchdog.py',
        output='screen',
        parameters=[{'timeout_sec': 0.5, 'use_sim_time': True}],
    )

    # mismo rviz con mapa/plan que usaba andino_gz.launch.py con nav2:=True --
    # como ahora Nav2 lo levanta este launch y no el de andino_gz, hay que
    # traerlo aca para no perder el panel de mapa (andino_gz con nav2:=False
    # usa su config SIN nav2, que no tiene ese panel).
    rviz = Node(
        condition=IfCondition(LaunchConfiguration('rviz')),
        package='rviz2',
        executable='rviz2',
        arguments=['-d', os.path.join(pkg_andino_gz, 'rviz', 'andino_gz_nav2.rviz')],
        parameters=[{'use_sim_time': True}],
        remappings=[
            ('/tf', 'tf'),
            ('/tf_static', 'tf_static'),
        ],
    )

    ld = LaunchDescription()
    ld.add_action(map_name_arg)
    ld.add_action(params_file_arg)
    ld.add_action(twist_mux_config_arg)
    ld.add_action(rviz_arg)
    ld.add_action(cmd_vel_remap)
    ld.add_action(scan_remap_global)
    ld.add_action(scan_remap_local)
    ld.add_action(nav2)
    ld.add_action(twist_mux)
    ld.add_action(teleop_watchdog)
    ld.add_action(rviz)
    return ld
