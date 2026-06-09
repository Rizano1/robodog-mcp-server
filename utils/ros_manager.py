# [TESTING/NO-ROS] Seluruh file ini dinonaktifkan untuk testing tanpa environment ROS.
# File asli mengandung inisialisasi ROS node dan controller.

# import rospy
# from threading import Thread
# from utils.controller import TurtleBotController

# Inisiasi Node Controller
NODE_CONTROLLER = None

def get_controller_node():
    """
    [TESTING/NO-ROS] Mengembalikan None karena ROS tidak tersedia.
    Di mode produksi, ini mengembalikan instansi TurtleBotController.
    """
    global NODE_CONTROLLER
    return NODE_CONTROLLER

def start_ros_execution():
    """
    [TESTING/NO-ROS] Tidak melakukan apa-apa karena ROS tidak tersedia.
    Di mode produksi, ini menginisialisasi ROS node dan memulai spin thread.
    """
    print("[TESTING/NO-ROS] ROS execution dinonaktifkan. Server berjalan tanpa ROS.")
    # global NODE_CONTROLLER
    # try:
    #     rospy.get_master().getPid()
    # except:
    #     print("ROS Master (roscore) not running. Please start roscore.")
    #     return
    # rospy.init_node('ros_mcp_manager', anonymous=True)
    # NODE_CONTROLLER = TurtleBotController()
    # print("Starting ROS 1 spin in a separate thread...")
    # Thread(target=rospy.spin, daemon=True).start()