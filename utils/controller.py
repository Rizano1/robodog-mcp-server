import rospy
import requests
import time
import cv2
import numpy as np
from threading import Thread, Event
from geometry_msgs.msg import Twist
from actionlib import SimpleActionClient
from move_base_msgs.msg import MoveBaseAction, MoveBaseGoal
from tf.transformations import quaternion_from_euler

# Import ROS 1 message types for camera
from sensor_msgs.msg import Image as RosImage

# URL Webhook FastAPI (localhost, not 0.0.0.0 — that's a listen address, not a connect address)
API_CALLBACK_URL = "http://localhost:8080/api/chat_robot"

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

    # --- 3. CAMERA IMAGE CAPTURE ---

    def capture_image(self, timeout: float = 7.0) -> bytes | None:
        """
        Mengambil satu frame dari stream RTSP secara langsung.
        Mengembalikan None jika gagal atau timeout.
        """
        rospy.loginfo("📸 Capturing frame from RTSP stream...")
        try:
            # Buka stream RTSP
            cap = cv2.VideoCapture("rtsp://10.7.101.231:8554/front_facing", cv2.CAP_FFMPEG)
            
            start_time = time.time()
            # Tunggu sampai stream bisa dibuka atau timeout
            while not cap.isOpened():
                if time.time() - start_time > timeout:
                    rospy.logwarn("⚠️ Timeout waiting for RTSP stream to open.")
                    return None
                rospy.sleep(0.1)
                
            # Kurangi ukuran buffer untuk mendapatkan frame paling update
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            
            # Buang beberapa frame pertama (membasuh buffer)
            for _ in range(3):
                success, frame = cap.read()
                if not success:
                    break
                    
            if not success or frame is None:
                rospy.logwarn("⚠️ Failed to read frame from RTSP stream.")
                cap.release()
                return None
                
            # OpenCV menggunakan format BGR, encode langsung ke JPEG
            success_encode, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
            cap.release()
            
            if success_encode:
                return buffer.tobytes()
            else:
                rospy.logwarn("⚠️ Failed to encode frame to JPEG.")
                return None
                
        except Exception as e:
            rospy.logerr(f"❌ Error during RTSP capture: {e}")
            return None