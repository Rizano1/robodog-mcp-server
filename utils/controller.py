import rospy
import requests
import time
from threading import Thread, Event
from geometry_msgs.msg import Twist
from actionlib import SimpleActionClient
from move_base_msgs.msg import MoveBaseAction, MoveBaseGoal
from tf.transformations import quaternion_from_euler

# URL Webhook FastAPI (localhost, not 0.0.0.0 — that's a listen address, not a connect address)
API_CALLBACK_URL = "http://localhost:8082/api/chat_robot"

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
                "session_id": int(session_id),
                "user_prompt": message,
            }
            # Timeout pendek agar tidak memblokir thread robot jika API down
            resp = requests.post(API_CALLBACK_URL, json=payload, timeout=5.0)
            rospy.loginfo(f"📡 Webhook response: {resp.status_code}")
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

        rospy.loginfo(f"Sending move_base goal to ({x:.2f}, {y:.2f}, {theta:.2f}) in 'map' frame.")

        # Callback Internal
        def done_callback(status, result):
            # Status 3 = SUCCEEDED (actionlib GoalStatus)
            is_success = (status == 3)
            
            if is_success:
                msg_text = f"✅ [ROBOT] Sampai di titik navigasi ({x}, {y})."
            else:
                msg_text = f"⚠️ [ROBOT] Gagal mencapai titik ({x}, {y}). Ada halangan atau path invalid."
            
            rospy.loginfo(msg_text)
            # --- LAPOR KE WEBHOOK (REUSABLE) ---
            self._report_event(session_id, msg_text)

        self._action_client.send_goal(goal, done_cb=done_callback)
        return True

    # --- 3. CAMERA IMAGE CAPTURE (via go2rtc HTTP snapshot) ---

    GO2RTC_SNAPSHOT_URL = "http://10.7.101.231:1984/api/frame.jpeg?src=front_facing"

    def capture_image(self, timeout: float = 7.0) -> bytes | None:
        """
        Mengambil satu frame dari go2rtc HTTP snapshot API.
        Endpoint: /api/frame.jpeg?src=front_facing
        Mengembalikan JPEG bytes atau None jika gagal.
        """
        rospy.loginfo("📸 Capturing frame from go2rtc snapshot API...")
        try:
            resp = requests.get(self.GO2RTC_SNAPSHOT_URL, timeout=timeout)

            if resp.status_code == 200 and resp.headers.get("Content-Type", "").startswith("image/"):
                rospy.loginfo(f"📸 Frame captured successfully ({len(resp.content)} bytes).")
                return resp.content
            else:
                rospy.logwarn(
                    f"⚠️ Unexpected response from go2rtc: "
                    f"status={resp.status_code}, content-type={resp.headers.get('Content-Type')}"
                )
                return None

        except requests.exceptions.Timeout:
            rospy.logwarn("⚠️ Timeout capturing frame from go2rtc snapshot API.")
            return None
        except Exception as e:
            rospy.logerr(f"❌ Error during go2rtc snapshot capture: {e}")
            return None