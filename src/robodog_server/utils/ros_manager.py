import rclpy
from rclpy.executors import MultiThreadedExecutor
from threading import Thread

from .controller import TurtleBotController

NODE_CONTROLLER = None
ROS_EXECUTOR = None

def get_controller_node() -> TurtleBotController:
    """Mengembalikan instansi TurtleBotController yang sudah diinisialisasi."""
    global NODE_CONTROLLER
    return NODE_CONTROLLER

def start_ros2_execution():
    """Menginisialisasi ROS 2, membuat node, dan memulai eksekutor."""
    global NODE_CONTROLLER, ROS_EXECUTOR

    if rclpy.ok():
        print("ROS 2 already initialized. Skipping init.")
    else:
        rclpy.init()

    NODE_CONTROLLER = TurtleBotController()

    # Setup Executor
    ROS_EXECUTOR = MultiThreadedExecutor()
    ROS_EXECUTOR.add_node(NODE_CONTROLLER)
    
    print("Starting ROS 2 MultiThreadedExecutor spin...")
    Thread(target=ROS_EXECUTOR.spin, daemon=True).start()