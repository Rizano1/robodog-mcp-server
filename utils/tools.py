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

@mcp.tool
def toggle_sit_stand(session_id: Optional[str] = None) -> dict:
    """
    Memerintahkan robot untuk mengganti state antara duduk (sit) dan berdiri (stand).
    Perintah ini menggunakan SimpleCMD dengan kode 0x21010202.
    """
    controller = get_controller_node()

    if controller is None:
        return create_response(
            type="robot_action",
            status="error",
            message="Controller ROS Noetic tidak tersedia."
        )

    # 0x21010202 adalah command untuk switch antara duduk dan berdiri
    controller.send_simple_cmd(cmd_code=0x21010202, cmd_value=0, cmd_type=0, session_id=session_id)

    return create_response(
        type="robot_action",
        status="success",
        message="Perintah Sit/Stand berhasil dikirim.",
        data={"cmd_code": "0x21010202"}
    )

@mcp.tool
def look_up_down(angle_value: int, session_id: Optional[str] = None) -> dict:
    """
    Memerintahkan robot untuk menunduk (look down) atau menengadah (look up) dengan mengatur pitch angle.
    Args:
        angle_value: Nilai antara -6553 sampai 6553. Positif (>0) untuk menunduk, negatif (<0) untuk menengadah. 0 untuk netral.
        session_id: Chat session ID (auto-injected by client)
    """
    controller = get_controller_node()

    if controller is None:
        return create_response(
            type="robot_action",
            status="error",
            message="Controller ROS Noetic tidak tersedia."
        )

    # 0x21010130 adalah command untuk Adjust Pitch Angle
    # Batasi nilai agar sesuai dengan spesifikasi [-6553, 6553]
    clamped_value = max(-6553, min(6553, angle_value))
    controller.send_simple_cmd(cmd_code=0x21010130, cmd_value=clamped_value, cmd_type=0, session_id=session_id)

    return create_response(
        type="robot_action",
        status="success",
        message=f"Perintah Look Up/Down berhasil dikirim dengan nilai pitch {clamped_value}.",
        data={"cmd_code": "0x21010130", "cmd_value": clamped_value}
    )

# --- TOOLS: DATABASE QUERY ---

@mcp.tool
def get_object_waypoints(query: str, location: Optional[str] = None, session_id: Optional[str] = None) -> dict:
    """
    Mencari objek inspeksi dan koordinat waypoint-nya di database.
    Mengembalikan data hierarkis: Map → Location path → Object → Waypoint.

    Args:
        query: Kata kunci pencarian (nama objek, keywords, atau nama lokasi).
               Contoh: "pressure tank", "valve", "pompa".
        location: (Opsional) Filter berdasarkan nama lokasi tertentu.
                  Contoh: "Boiler Room", "Floor 1".
        session_id: Chat session ID (auto-injected by client).
    """
    try:
        search_term = f"%{query}%"

        # --- 1. Search objects by name & keywords ---
        obj_response = supabase.table("objects").select("*").or_(
            f"name.ilike.{search_term},keywords.cs.{{{query}}}"
        ).execute()
        matched_object_ids = [obj["id"] for obj in (obj_response.data or [])]

        # --- 2. Search waypoints by display_name OR matching object_id ---
        if matched_object_ids:
            # Build filter: waypoints whose object_id matches OR display_name matches
            obj_id_filter = ",".join(str(i) for i in matched_object_ids)
            wp_response = supabase.table("object-waypoints").select("*").or_(
                f"display_name.ilike.{search_term},object_id.in.({obj_id_filter})"
            ).execute()
        else:
            wp_response = supabase.table("object-waypoints").select("*").ilike(
                "display_name", search_term
            ).execute()

        waypoints = wp_response.data or []

        if not waypoints:
            return create_response(
                type="navigation_query",
                status="empty",
                message=f"Tidak ditemukan objek dengan kata kunci '{query}'.",
                data=[]
            )

        # --- 3. Collect all referenced IDs for batch lookup ---
        location_ids = set()
        object_ids = set()
        for wp in waypoints:
            if wp.get("parent_id"):
                location_ids.add(wp["parent_id"])
            if wp.get("object_id"):
                object_ids.add(wp["object_id"])

        # --- 4. Fetch all locations (for building hierarchy chain) ---
        all_locations = {}
        if location_ids:
            loc_response = supabase.table("locations").select("*").execute()
            for loc in (loc_response.data or []):
                all_locations[loc["id"]] = loc

        # --- 5. Fetch referenced objects ---
        objects_map = {}
        if object_ids:
            obj_ids_str = ",".join(str(i) for i in object_ids)
            obj_detail = supabase.table("objects").select("*").in_(
                "id", list(object_ids)
            ).execute()
            for obj in (obj_detail.data or []):
                objects_map[obj["id"]] = obj

        # --- 6. Fetch all maps ---
        maps_map = {}
        map_response = supabase.table("maps").select("*").execute()
        for m in (map_response.data or []):
            maps_map[m["id"]] = m

        # --- 7. Helper: build location path (walk up parent chain) ---
        def build_location_path(loc_id: int) -> List[Dict]:
            """Returns list from root to leaf: [map_name, loc1, loc2, ...]"""
            chain = []
            visited = set()
            current_id = loc_id
            while current_id and current_id in all_locations and current_id not in visited:
                visited.add(current_id)
                loc = all_locations[current_id]
                chain.append({"name": loc.get("name"), "type": loc.get("type"), "id": loc["id"]})
                current_id = loc.get("parent_id")
            chain.reverse()  # root → leaf
            return chain

        # --- 8. Filter by location name if specified ---
        if location:
            location_lower = location.lower()
            matching_loc_ids = set()
            for loc_id, loc in all_locations.items():
                if location_lower in (loc.get("name") or "").lower():
                    # Include this location and all its descendants
                    matching_loc_ids.add(loc_id)
                    # Add children recursively
                    queue = [loc_id]
                    while queue:
                        pid = queue.pop()
                        for child_id, child in all_locations.items():
                            if child.get("parent_id") == pid and child_id not in matching_loc_ids:
                                matching_loc_ids.add(child_id)
                                queue.append(child_id)

            waypoints = [wp for wp in waypoints if wp.get("parent_id") in matching_loc_ids]

            if not waypoints:
                return create_response(
                    type="navigation_query",
                    status="empty",
                    message=f"Tidak ditemukan '{query}' di lokasi '{location}'.",
                    data=[]
                )

        # --- 9. Format hierarchical results ---
        formatted_data = []
        for wp in waypoints:
            obj = objects_map.get(wp.get("object_id"), {})
            loc_id = wp.get("parent_id")
            location_path = build_location_path(loc_id) if loc_id else []

            # Resolve map name from the location's map_id
            map_name = None
            if loc_id and loc_id in all_locations:
                map_id = all_locations[loc_id].get("map_id")
                if map_id and map_id in maps_map:
                    map_name = maps_map[map_id].get("name")

            formatted_data.append({
                "waypoint_id": wp.get("id"),
                "display_name": wp.get("display_name"),
                "object": {
                    "id": obj.get("id"),
                    "name": obj.get("name"),
                    "keywords": obj.get("keywords", []),
                    "sop_url": obj.get("sop_url"),
                },
                "spatial_context": {
                    "map": map_name,
                    "location_path": " > ".join(
                        [f"{l['name']} ({l['type']})" if l.get("type") else l["name"]
                         for l in location_path]
                    ),
                    "location_name": location_path[-1]["name"] if location_path else None,
                },
                "coordinates": {
                    "nav_target": {
                        "x": wp.get("view_x"),
                        "y": wp.get("view_y"),
                        "theta_deg": wp.get("view_yaw"),
                    },
                    "object_position": {
                        "x": wp.get("obj_x"),
                        "y": wp.get("obj_y"),
                    },
                    "camera": {
                        "pan": wp.get("camera_pan"),
                        "tilt": wp.get("camera_tilt"),
                        "zoom": wp.get("camera_zoom"),
                    },
                },
            })

        return create_response(
            type="navigation_query",
            status="success",
            message=f"Ditemukan {len(formatted_data)} waypoint yang cocok untuk '{query}'.",
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
