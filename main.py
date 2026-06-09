# [TESTING/NO-ROS] ROS imports dinonaktifkan untuk testing tanpa environment ROS
# from utils.ros_manager import start_ros_execution
from utils.tools import mcp 

def main():
    """
    Titik masuk utama program.
    """
    # [TESTING/NO-ROS] start_ros_execution dinonaktifkan
    # start_ros_execution()
    
    print("\nStarting FastMCP HTTP server (NO-ROS testing mode)...")
    mcp.run("http", host="0.0.0.0", port=8001)

if __name__ == "__main__":
    main()