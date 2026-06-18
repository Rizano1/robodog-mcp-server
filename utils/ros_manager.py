# src/robodog_server/utils/ros_manager.py (ROS 2)

import rclpy
import threading
from utils.controller import TurtleBotController

# Inisiasi Node Controller
NODE_CONTROLLER = None

def get_controller_node() -> TurtleBotController:
    """Mengembalikan instansi TurtleBotController yang sudah diinisialisasi."""
    global NODE_CONTROLLER
    return NODE_CONTROLLER

def start_ros_execution():
    """Menginisialisasi ROS 2, membuat node, dan memulai loop executor."""
    global NODE_CONTROLLER

    # Periksa apakah rclpy sudah diinisialisasi
    if not rclpy.ok():
        rclpy.init()

    # Membuat instansi controller (yang merupakan Node ROS 2)
    NODE_CONTROLLER = TurtleBotController()
    
    # ROS 2 menggunakan Executor untuk menjalankan spin
    executor = rclpy.executors.SingleThreadedExecutor()
    executor.add_node(NODE_CONTROLLER)
    
    print("Starting ROS 2 spin in a separate thread...")
    # Menjalankan executor.spin() di thread terpisah agar FastMCP server tidak memblokir ROS
    threading.Thread(target=executor.spin, daemon=True).start()