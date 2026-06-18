import rclpy
from rclpy.node import Node
from rclpy.action import ActionClient
from geometry_msgs.msg import Twist
from nav2_msgs.action import NavigateToPose
import requests
import time
import math
import threading

# URL Webhook FastAPI
API_CALLBACK_URL = "http://localhost:8082/api/chat_robot"

class TurtleBotController(Node):
    def __init__(self):
        super().__init__('turtlebot_controller')
        
        # Publisher for manual movement
        self.pub = self.create_publisher(Twist, '/cmd_vel', 10)
        
        # Action Client for Nav2 Navigation
        self._action_client = ActionClient(self, NavigateToPose, 'navigate_to_pose')
        
        self.get_logger().info("Waiting for navigate_to_pose action server...")
        # self._action_client.wait_for_server()
        self.get_logger().info("TurtleBotController ROS2 initialized.")

    # --- HELPER METHOD: REUSABLE WEBHOOK REPORTER ---
    def _report_event(self, session_id: str, message: str, model_name: str = None):
        """
        Fungsi reusable untuk mengirim laporan ke FastAPI Webhook.
        """
        if not session_id:
            self.get_logger().warn(f"Event finished but no session_id provided. Msg: {message}")
            return

        self.get_logger().info(f"📡 Reporting to Webhook: {message}")
        
        try:
            payload = {
                "session_id": int(session_id),
                "user_prompt": message,
            }
            if model_name:
                payload["model_name"] = model_name
                
            resp = requests.post(API_CALLBACK_URL, json=payload, timeout=5.0)
            self.get_logger().info(f"📡 Webhook response: {resp.status_code}")
        except Exception as e:
            self.get_logger().error(f"❌ Failed to report event to API: {e}")

    # --- 1. MANUAL MOVE (OPEN LOOP) ---

    def move_async(self, linear_speed: float, angular_speed: float, duration: float, session_id: str = None, model_name: str = None):
        """
        Gerak manual secara asinkron.
        """
        threading.Thread(
            target=self._move_blocking, 
            args=(linear_speed, angular_speed, duration, session_id, model_name), 
            daemon=True
        ).start()

    def _move_blocking(self, linear_speed: float, angular_speed: float, duration: float, session_id: str, model_name: str = None):
        """
        Logic gerak manual + Lapor Webhook di akhir.
        """
        msg = Twist()
        msg.linear.x = linear_speed   
        msg.angular.z = angular_speed 

        t_end = time.time() + duration
        self.get_logger().info(f"Starting manual move for {duration}s...")
        
        while time.time() < t_end and rclpy.ok():
            self.pub.publish(msg)
            time.sleep(0.1)

        # Stop Robot
        self.pub.publish(Twist())
        self.get_logger().info("Manual move finished.")

        # --- LAPOR KE WEBHOOK ---
        report_msg = (
            f"✅ [ROBOT_FEEDBACK] Gerakan manual selesai. "
            f"(Maju: {linear_speed}m/s, Putar: {angular_speed}rad/s, Durasi: {duration}s)"
        )
        self._report_event(session_id, report_msg, model_name)

    # --- 2. NAVIGATION (PATH PLANNING) ---

    def send_nav_goal_async(self, x: float, y: float, theta: float, session_id: str = None, model_name: str = None) -> bool:
        """
        Mengirimkan tujuan navigasi ke stack Nav2 (ROS 2).
        """
        if not self._action_client.wait_for_server(timeout_sec=5.0):
            self.get_logger().error("navigate_to_pose action server not available!")
            return False

        # Setup Goal
        goal_msg = NavigateToPose.Goal()
        goal_msg.pose.header.frame_id = 'map'
        goal_msg.pose.header.stamp = self.get_clock().now().to_msg()
        goal_msg.pose.pose.position.x = x
        goal_msg.pose.pose.position.y = y
        
        # Calculate quaternion manually from yaw (theta)
        cy = math.cos(theta * 0.5)
        sy = math.sin(theta * 0.5)
        goal_msg.pose.pose.orientation.x = 0.0
        goal_msg.pose.pose.orientation.y = 0.0
        goal_msg.pose.pose.orientation.z = sy
        goal_msg.pose.pose.orientation.w = cy

        self.get_logger().info(f"Sending navigate_to_pose goal to ({x:.2f}, {y:.2f}, {theta:.2f})")

        # Callback goal acceptance
        def goal_response_callback(future):
            goal_handle = future.result()
            if not goal_handle.accepted:
                self.get_logger().info("Goal rejected by server.")
                self._report_event(
                    session_id, 
                    f"⚠️ [ROBOT_FEEDBACK] Perintah navigasi ke ({x}, {y}) ditolak oleh robot.", 
                    model_name
                )
                return

            self.get_logger().info("Goal accepted by server, waiting for result...")
            result_future = goal_handle.get_result_async()
            
            # Callback goal completion
            def get_result_callback(result_future_res):
                status = result_future_res.result().status
                # In ROS 2 action_msgs/msg/GoalStatus, STATUS_SUCCEEDED = 4
                is_success = (status == 4)
                
                if is_success:
                    msg_text = f"✅ [ROBOT_FEEDBACK] Sampai di titik navigasi ({x}, {y})."
                else:
                    msg_text = f"⚠️ [ROBOT_FEEDBACK] Gagal mencapai titik ({x}, {y}). Ada halangan atau path invalid. Status: {status}"
                
                self.get_logger().info(msg_text)
                self._report_event(session_id, msg_text, model_name)

            result_future.add_done_callback(get_result_callback)

        send_goal_future = self._action_client.send_goal_async(goal_msg)
        send_goal_future.add_done_callback(goal_response_callback)
        return True