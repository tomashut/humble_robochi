from setuptools import find_packages, setup

package_name = 'comms_agent'

setup(
    name=package_name,
    version='0.0.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        ('share/' + package_name + '/launch', ['launch/comms_agent.launch.py']),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='tomashut',
    maintainer_email='tomashut@todo.todo',
    description='Agente de comunicaciones: puente entre MQTT y los servicios ROS de patrol_fsm',
    license='TODO: License declaration',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            'comms_agent_node = comms_agent.comms_agent_node:main',
        ],
    },
)
