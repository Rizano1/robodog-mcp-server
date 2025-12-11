# src/robodog_server/utils/controller.py
import rclpy
from rclpy.node import Node
from geometry_msgs.msg import Twist
from threading import Thread
from geometry_msgs.msg import Twist, PoseStamped 
from rclpy.action import ActionClient             
from nav2_msgs.action import NavigateToPose
import time

class TurtleBotController(Node):
    """
    ROS 2 Node untuk mengontrol pergerakan TurtleBot.
    """
    def __init__(self):
        super().__init__("turtlebot_controller")
        self.pub = self.create_publisher(Twist, "/cmd_vel", 10)

        self._action_client = ActionClient(
            self,
            NavigateToPose,
            'navigate_to_pose' 
        )
        self.get_logger().info("TurtleBotController Node initialized.")
        

    def move_async(self, linear_speed: float, angular_speed: float, duration: float):
        """
        Memulai pergerakan umum (linear dan angular) non-blocking 
        dalam thread terpisah.
        """
        Thread(target=self._move_blocking, args=(linear_speed, angular_speed, duration), daemon=True).start()

    def _move_blocking(self, linear_speed: float, angular_speed: float, duration: float):
        """
        Fungsi blocking yang mengirim perintah Twist secara berkala.
        """
        msg = Twist()
        msg.linear.x = linear_speed   
        msg.angular.z = angular_speed 

        t_end = time.time() + duration
        
        self.get_logger().info(
            f"Starting move: Linear={linear_speed} m/s, Angular={angular_speed} rad/s for {duration}s"
        )
        
        while time.time() < t_end:
            self.pub.publish(msg)
            time.sleep(0.1)

        stop_msg = Twist()
        self.pub.publish(stop_msg)
        
        self.get_logger().info("Finished move.")

    def send_nav_goal_async(self, x: float, y: float, theta: float = 0.0) -> bool:
        """
        Mengirim tujuan navigasi (x, y, theta) ke stack Nav2 secara asynchronous.
        
        :param x: Koordinat X tujuan.
        :param y: Koordinat Y tujuan.
        :param theta: Orientasi akhir yang diinginkan (Yaw) dalam radian.
        :return: True jika goal berhasil dikirim, False jika server Nav2 tidak tersedia.
        """
        if not self._action_client.wait_for_server(timeout_sec=5.0):
            self.get_logger().error("Navigation action server 'navigate_to_pose' not available!")
            return False

        goal_msg = NavigateToPose.Goal()
        
        goal_msg.pose.header.frame_id = 'map'
        goal_msg.pose.header.stamp = self.get_clock().now().to_msg()
        
        goal_msg.pose.pose.position.x = x
        goal_msg.pose.pose.position.y = y
        goal_msg.pose.pose.position.z = 0.0
        

        goal_msg.pose.pose.orientation.x = 0.0
        goal_msg.pose.pose.orientation.y = 0.0
        goal_msg.pose.pose.orientation.z = 0.0
        goal_msg.pose.pose.orientation.w = 1.0 

        self.get_logger().info(f"Sending Nav2 goal to ({x:.2f}, {y:.2f}) in 'map' frame.")
        
        self._action_client.send_goal_async(goal_msg)
        
        return True 