# src/robodog_server/utils/ros_manager.py (ROS Noetic)

import rospy
from threading import Thread

# Import Node yang sudah didefinisikan (di ROS 1, Node biasanya adalah kelas Python biasa)
from utils.controller import TurtleBotController

# Inisiasi Node Controller
NODE_CONTROLLER = None

def get_controller_node() -> TurtleBotController:
    """Mengembalikan instansi TurtleBotController yang sudah diinisialisasi."""
    global NODE_CONTROLLER
    return NODE_CONTROLLER

def start_ros_execution():
    """Menginisialisasi ROS, membuat node, dan memulai loop utama."""
    global NODE_CONTROLLER

    # Periksa apakah ROS Master (roscore) sedang berjalan
    try:
        rospy.get_master().getPid()
    except:
        print("ROS Master (roscore) not running. Please start roscore.")
        return

    # Inisialisasi rospy. Ini harus dilakukan sebelum membuat node.
    rospy.init_node('ros_mcp_manager', anonymous=True)
    
    # Membuat instansi controller
    NODE_CONTROLLER = TurtleBotController()
    
    # ROS Noetic tidak menggunakan Executor. Node/Publisher/Action Client
    # akan berjalan secara asynchronous dalam thread saat diinstansiasi.
    # Kita hanya perlu menjalankan rospy.spin() di thread terpisah jika 
    # proses utama ingin terus berjalan (seperti FastMCP server).
    
    print("Starting ROS 1 spin in a separate thread...")
    # Thread(target=rospy.spin, daemon=True).start()
    # PENTING: Karena rospy.spin() memblokir, kita hanya bisa menjalankannya
    # jika proses utama tidak memblokir, atau kita jalankan node kita di thread lain.
    # Namun, karena FastMCP akan memblokir, kita biarkan saja.
    # Node/Publisher/Action Client sudah berjalan di thread dari FastMCP.
    
    # Catatan: Karena FastMCP menjalankan server di thread utama, kita tidak menjalankan
    # rospy.spin(). Fungsi-fungsi timer (jika ada) di Node akan gagal kecuali
    # Action Client dan Publisher diinisialisasi secara eksplisit di thread utama.
    # Namun, untuk Action Client dan Publisher, mereka akan berjalan asinkron 
    # tanpa memerlukan rospy.spin() selama FastMCP berjalan.
    
    # Untuk memastikan Action Client berfungsi, kita akan menjalankan spin di thread.
    Thread(target=rospy.spin, daemon=True).start()