# src/robodog_server/utils/controller.py (ROS Noetic)

import rospy
from threading import Thread
import time

# Import ROS 1 message types
from geometry_msgs.msg import Twist, PoseStamped, Quaternion 
from actionlib import SimpleActionClient          # <-- ROS 1 Action Client
from move_base_msgs.msg import MoveBaseAction, MoveBaseGoal # <-- ROS 1 Nav Goal (MoveBase)

class TurtleBotController: # <-- Bukan turunan dari rospy.AbstractNode
    """
    ROS Noetic Controller untuk mengontrol pergerakan dan navigasi TurtleBot.
    """
    def __init__(self):
        # ROS 1 Node tidak perlu super().__init__. rospy.init_node() ada di ros_manager.
        
        # ROS 1 Publisher
        self.pub = rospy.Publisher('/cmd_vel', Twist, queue_size=10)
        
        # --- ROS 1 Action Client Setup (move_base) ---
        # Action Client ROS 1 (untuk Navigasi)
        self._action_client = SimpleActionClient('move_base', MoveBaseAction)
        rospy.loginfo("Waiting for move_base action server...")
        self._action_client.wait_for_server()
        
        rospy.loginfo("TurtleBotController initialized. move_base server connected.")
        
    # Perubahan: 'self.get_logger().info' diganti dengan 'rospy.loginfo'
        
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
        
        rospy.loginfo(
            f"Starting move: Linear={linear_speed} m/s, Angular={angular_speed} rad/s for {duration}s"
        )
        
        # ROS 1 menggunakan rospy.is_shutdown() untuk memeriksa loop utama
        while time.time() < t_end and not rospy.is_shutdown():
            self.pub.publish(msg)
            rospy.sleep(0.1) # Gunakan rospy.sleep untuk waktu yang lebih baik

        stop_msg = Twist()
        self.pub.publish(stop_msg)
        
        rospy.loginfo("Finished move.")

    def send_nav_goal_async(self, x: float, y: float, theta: float = 0.0) -> bool:
        """
        Mengirim tujuan navigasi (x, y, theta) ke stack Navigasi ROS 1 (move_base).
        
        :param x: Koordinat X tujuan.
        :param y: Koordinat Y tujuan.
        :param theta: Orientasi akhir yang diinginkan (Yaw) dalam radian.
        :return: True jika goal berhasil dikirim.
        """
        if not self._action_client.wait_for_server(timeout=5.0):
            rospy.logerr("move_base action server not available!")
            return False

        # ROS 1 Navigasi menggunakan MoveBaseGoal
        goal_msg = MoveBaseGoal()
        
        # Header (Penting untuk Navigasi)
        goal_msg.target_pose.header.frame_id = 'map'
        goal_msg.target_pose.header.stamp = rospy.Time.now()
        
        # Posisi Tujuan
        goal_msg.target_pose.pose.position.x = x
        goal_msg.target_pose.pose.position.y = y
        goal_msg.target_pose.pose.position.z = 0.0
        
        # Orientasi Tujuan (Quaternion)
        # Catatan: Sama seperti ROS 2, konversi Yaw ke Quaternion sangat penting.
        # Karena kita tidak memiliki pustaka konversi, kita gunakan identitas 
        # dan mencatat bahwa ini mungkin tidak menginisiasi orientasi yang benar 
        # tanpa pustaka tambahan (misalnya, tf.transformations).
        goal_msg.target_pose.pose.orientation.x = 0.0
        goal_msg.target_pose.pose.orientation.y = 0.0
        goal_msg.target_pose.pose.orientation.z = 0.0
        goal_msg.target_pose.pose.orientation.w = 1.0 

        rospy.loginfo(f"Sending move_base goal to ({x:.2f}, {y:.2f}) in 'map' frame.")
        
        # Mengirim goal dan mendapatkan hasil secara asynchronous
        # Tidak memblokir karena kita di dalam FastMCP loop
        self._action_client.send_goal(goal_msg) 
        
        return True