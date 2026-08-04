from setuptools import find_packages, setup

package_name = 'patrol_behavior'

setup(
    name=package_name,
    version='0.0.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        ('share/' + package_name + '/launch', ['launch/patrol.launch.py']),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='tomashut',
    maintainer_email='tomashut@todo.todo',
    description='TODO: Package description',
    license='TODO: License declaration',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
        	'patrol_path = patrol_behavior.patrol_path:main',
        	'patrol_path_client = patrol_behavior.patrol_path_client:main',
        ],
        'rqt_gui_plugins': [
                'PatrolPanel = patrol_behavior.rviz_plugins.patrol_panel:PatrolPanel'
    ],
    },
)
