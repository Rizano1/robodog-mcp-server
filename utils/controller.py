import rospy
import requests
import time
from threading import Thread
from geometry_msgs.msg import Twist
from actionlib import SimpleActionClient
from move_base_msgs.msg import MoveBaseAction, MoveBaseGoal
from tf.transformations import quaternion_from_euler

# URL Webhook FastAPI (Sesuaikan dengan IP host Anda)
API_CALLBACK_URL = "http://0.0.0.0:8080/api/chat_robot"

class TurtleBotController:
    def __init__(self):
        self.pub = rospy.Publisher('/cmd_vel', Twist, queue_size=10)
        
        # Navigasi Action Client
        self._action_client = SimpleActionClient('move_base', MoveBaseAction)
        rospy.loginfo("Waiting for move_base action server...")
        # self._action_client.wait_for_server() # Uncomment jika move_base sudah pasti jalan
        rospy.loginfo("TurtleBotController initialized.")

    # --- HELPER METHOD: REUSABLE WEBHOOK REPORTER ---
    def _report_event(self, session_id: str, message: str):
        """
        Fungsi reusable untuk mengirim laporan ke FastAPI Webhook.
        Bisa dipakai oleh move, navigate, arm_control, dll.
        """
        if not session_id:
            rospy.logwarn(f"Event finished but no session_id provided. Msg: {message}")
            return

        rospy.loginfo(f"📡 Reporting to Webhook: {message}")
        
        try:
            payload = {
                "session_id": session_id,
                "message": message,
            }
            # Timeout pendek agar tidak memblokir thread robot jika API down
            requests.post(API_CALLBACK_URL, json=payload, timeout=3.0)
        except Exception as e:
            rospy.logerr(f"❌ Failed to report event to API: {e}")

    # --- 1. MANUAL MOVE (OPEN LOOP) ---

    def move_async(self, linear_speed: float, angular_speed: float, duration: float, session_id: str = None):
        """
        Gerak manual. Menerima session_id untuk lapor setelah selesai.
        """
        Thread(
            target=self._move_blocking, 
            args=(linear_speed, angular_speed, duration, session_id), 
            daemon=True
        ).start()

    def _move_blocking(self, linear_speed: float, angular_speed: float, duration: float, session_id: str):
        """
        Logic gerak manual + Lapor Webhook di akhir.
        """
        msg = Twist()
        msg.linear.x = linear_speed   
        msg.angular.z = angular_speed 

        t_end = time.time() + duration
        rospy.loginfo(f"Starting manual move for {duration}s...")
        
        # Loop Gerak
        while time.time() < t_end and not rospy.is_shutdown():
            self.pub.publish(msg)
            rospy.sleep(0.1)

        # Stop Robot
        self.pub.publish(Twist())
        rospy.loginfo("Manual move finished.")

        # --- LAPOR KE WEBHOOK (REUSABLE) ---
        report_msg = (
            f"✅ [ROBOT] Gerakan manual selesai. "
            f"(Maju: {linear_speed}m/s, Putar: {angular_speed}rad/s, Durasi: {duration}s)"
        )
        self._report_event(session_id, report_msg)

    # --- 2. NAVIGATION (PATH PLANNING) ---

    def send_nav_goal_async(self, x: float, y: float, theta: float, session_id: str = None) -> bool:
        if not self._action_client.wait_for_server(timeout=rospy.Duration(5.0)):
            rospy.logerr("move_base action server not available!")
            return False

        # Setup Goal (Sama seperti sebelumnya)
        goal = MoveBaseGoal()
        goal.target_pose.header.frame_id = 'map'
        goal.target_pose.header.stamp = rospy.Time.now()
        goal.target_pose.pose.position.x = x
        goal.target_pose.pose.position.y = y
        
        q = quaternion_from_euler(0, 0, theta)
        goal.target_pose.pose.orientation.x = q[0]
        goal.target_pose.pose.orientation.y = q[1]
        goal.target_pose.pose.orientation.z = q[2]
        goal.target_pose.pose.orientation.w = q[3]

        # Callback Internal
        def done_callback(status, result):
            # Status 3 = SUCCEEDED
            is_success = (status == 3)
            status_str = "SUCCESS" if is_success else "FAILED"
            
            msg_text = (
                f"✅ [ROBOT] Sampai di titik navigasi ({x}, {y})." 
                if is_success else 
                f"⚠️ [ROBOT] Gagal mencapai titik ({x}, {y}). Ada halangan atau path invalid."
            )
            
            # --- LAPOR KE WEBHOOK (REUSABLE) ---
            self._report_event(session_id, msg_text)

        self._action_client.send_goal(goal, done_cb=done_callback)
        return True