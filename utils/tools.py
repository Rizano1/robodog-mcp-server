import os
import io
import math
import base64
from datetime import datetime
from fastmcp import FastMCP
from supabase import create_client, Client
from typing import List, Dict, Any, Optional
from io import BytesIO
from pypdf import PdfReader
from dotenv import load_dotenv
from docx import Document

# Import Controller ROS Noetic Anda
from utils.ros_manager import get_controller_node 

load_dotenv()

mcp = FastMCP("robot_api_mcp")

# --- Konfigurasi Supabase ---
SUPABASE_URL: str = os.getenv("SUPABASE_URL")
SUPABASE_KEY: str = os.getenv("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

BUCKET_NAME = "robotics-prata"       
FOLDER_NAME = "sop"   

# --- Helper Function untuk Format Standar ---
def create_response(type: str, status: str, message: str, data: Any = None) -> dict:
    """
    Mengembalikan dictionary standar.
    FastMCP akan otomatis mengonversinya menjadi JSON saat dikirim ke client.
    """
    return {
        "type": type,
        "status": status,
        "message": message,
        "data": data
    }

# --- TOOLS: ROBOT ACTION ---

@mcp.tool
def move(linear_speed: float = 0.0, angular_speed: float = 0.0, duration: float = 5.0, session_id: Optional[str] = None) -> dict:
    """
    Memerintahkan robot untuk bergerak manual (open-loop).
    """
    controller = get_controller_node()

    if controller is None:
        return create_response(
            type="robot_action",
            status="error",
            message="Controller ROS Noetic belum siap atau roscore tidak terdeteksi."
        )

    # Memanggil metode async pada controller Noetic
    controller.move_async(linear_speed, angular_speed, duration, session_id)
    
    return create_response(
        type="robot_action",
        status="running",
        message=f"Robot bergerak: Linear={linear_speed}m/s, Angular={angular_speed}rad/s.",
        data={
            "linear_speed": linear_speed,
            "angular_speed": angular_speed,
            "duration": duration
        }
    )

@mcp.tool
def navigate_to_waypoint(x: float, y: float, theta_deg: float = 0.0, session_id: Optional[str] = None) -> dict:
    """
    Mengirimkan tujuan navigasi ke stack move_base (ROS 1).
    Args:
        x: Target X position in meters (map frame)
        y: Target Y position in meters (map frame)
        theta_deg: Target orientation in degrees (0=East, 90=North, 180=West, -90=South)
        session_id: Chat session ID (auto-injected by client)
    """
    controller = get_controller_node()

    if controller is None:
        return create_response(
            type="robot_action",
            status="error",
            message="Controller ROS Noetic tidak tersedia."
        )

    # Convert degrees to radians for move_base
    theta_rad = math.radians(theta_deg)

    # Mengirim goal ke Action Server move_base
    goal_sent = controller.send_nav_goal_async(x, y, theta_rad, session_id)
    
    if goal_sent:
        return create_response(
            type="robot_action",
            status="running",
            message=f"Goal navigasi berhasil dikirim. Robot menuju ({x:.2f}, {y:.2f}) arah {theta_deg:.0f}°.",
            data={"target_x": x, "target_y": y, "target_theta_deg": theta_deg}
        )
    else:
        return create_response(
            type="robot_action",
            status="error",
            message="Gagal mengirim goal. Pastikan node 'move_base' di robot sudah berjalan."
        )

# --- TOOLS: DATABASE QUERY ---

@mcp.tool
def get_object_waypoints(query: str, session_id: Optional[str] = None) -> dict:
    """
    Mencari koordinat objek atau lokasi di database Supabase (tabel 'waypoints').
    Dengan query :
    supabase.table("object-waypoints").select("*").or_(
            f"slug.ilike.{search_term},display_name.ilike.{search_term},group_tag.ilike.{search_term}"
    ).execute()
    """
    try:
        search_term = f"%{query}%"
        
        response = supabase.table("object-waypoints").select("*").or_(
            f"slug.ilike.{search_term},display_name.ilike.{search_term},group_tag.ilike.{search_term}"
        ).execute()
        
        data = response.data
        
        if not data:
            return create_response(
                type="navigation_query",
                status="empty",
                message=f"Tidak ditemukan objek atau lokasi dengan kata kunci '{query}'.",
                data=[]
            )
            
        formatted_data = []
        for item in data:
            # Convert view_yaw from radians (DB) to degrees for the LLM
            
            formatted_data.append({
                "id": item.get("slug"),
                "name": item.get("display_name"),
                "group": item.get("group_tag"),
                "nav_target": {
                    "x": item.get("view_x"),
                    "y": item.get("view_y"),
                    "theta_deg": item.get("view_yaw")
                },
                "object_pos": {
                    "x": item.get("obj_x"), 
                    "y": item.get("obj_y")
                }
            })

        return create_response(
            type="navigation_query",
            status="success",
            message=f"Ditemukan {len(data)} lokasi yang cocok.",
            data=formatted_data
        )

    except Exception as e:
        return create_response(
            type="navigation_query",
            status="error",
            message=f"Terjadi kesalahan saat query database: {str(e)}"
        )

# --- TOOLS: FILE RETRIEVAL (SOP) ---

@mcp.tool
def list_sop_files(session_id: Optional[str] = None) -> dict:
    """
    Mengambil daftar file dalam bucket Supabase 'SOP'.
    """
    try:
        result = supabase.storage.from_(BUCKET_NAME).list(
            FOLDER_NAME,
            {
                "limit": 200,
                "offset": 0,
                "sortBy": {"column": "name", "order": "asc"},
            }
        )
        
        if isinstance(result, list):
            file_names = [f["name"] for f in result]
            return create_response(
                type="file_list",
                status="success",
                message=f"Ditemukan {len(file_names)} file SOP.",
                data=file_names
            )
        else:
            return create_response(
                type="file_list",
                status="error",
                message="Format respon dari storage tidak dikenali.",
                data=str(result)
            )
            
    except Exception as e:
        return create_response(
            type="file_list",
            status="error",
            message=f"Gagal mengambil list file: {str(e)}"
        )

@mcp.tool
def get_sop_file(file_name: str, session_id: Optional[str] = None) -> dict:
    """
    Mengambil filepath
    args:
        file_name: Nama full file SOP yang akan diambil berdasarkan list_sop_files.
    """

    return create_response(
        type="file_retrieve",
        status="success",
        message=f"File '{file_name}' berhasil diambil.",
        data={
            "filename": file_name,
            "folder": FOLDER_NAME,
        }
    )

# --- TOOLS: CAMERA CAPTURE & UPLOAD ---

@mcp.tool
def capture_and_upload_image(session_id: Optional[str] = None) -> dict:
    """
    Mengambil gambar dari kamera robot via go2rtc snapshot API,
    menguploadnya ke Supabase Storage bucket 'robotics-prata' folder 'captured',
    dan mengembalikan public URL untuk diakses langsung oleh client.
    """
    controller = get_controller_node()

    if controller is None:
        return create_response(
            type="image_capture",
            status="error",
            message="Controller ROS Noetic belum siap. Pastikan roscore sudah berjalan."
        )

    # 1. Capture image dari kamera
    jpeg_bytes = controller.capture_image(timeout=10.0)

    if jpeg_bytes is None:
        return create_response(
            type="image_capture",
            status="error",
            message="Gagal mengambil gambar dari go2rtc snapshot API (timeout atau stream tidak tersedia)."
        )

    # 2. Generate nama file dengan timestamp
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filepath = f"captured/capture_{timestamp}.jpg"

    try:
        # 3. Upload ke Supabase Storage
        supabase.storage.from_(BUCKET_NAME).upload(
            path=filepath,
            file=jpeg_bytes,
            file_options={"content-type": "image/jpeg"}
        )

        # 4. Buat public URL
        public_url = f"{SUPABASE_URL}/storage/v1/object/public/{BUCKET_NAME}/{filepath}"

        return create_response(
            type="image_capture",
            status="success",
            message=f"Gambar berhasil diambil dan diupload.",
            data={
                "filepath": filepath,
                "public_url": public_url
            }
        )

    except Exception as e:
        return create_response(
            type="image_capture",
            status="error",
            message=f"Gagal mengupload gambar ke Supabase: {str(e)}"
        )
