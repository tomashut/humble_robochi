cmake_minimum_required(VERSION 3.7)
project(robot_bringup)

find_package(ament_cmake REQUIRED)

install(
  DIRECTORY
    config
    launch
  DESTINATION
    share/${PROJECT_NAME}/
)

install(
  PROGRAMS
    scripts/teleop_watchdog.py
  DESTINATION
    lib/${PROJECT_NAME}
)

ament_package()
