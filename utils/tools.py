import os
import math
from fastmcp import FastMCP
from fastmcp.server.context import Context
from supabase import create_client, Client
from typing import List, Dict, Any, Optional
from dotenv import load_dotenv
from langfuse import observe, propagate_attributes, get_client
from utils.ros_manager import get_controller_node 

load_dotenv()
langfuse_client = get_client()

mcp = FastMCP("robot_api_mcp")

# --- Konfigurasi Supabase ---
SUPABASE_URL: str = os.getenv("SUPABASE_URL")
SUPABASE_KEY: str = os.getenv("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

BUCKET_NAME = "robotics-prata"

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
def async_move(ctx: Context, linear_speed: float = 0.0, angular_speed: float = 0.0, duration: float = 5.0) -> dict:
    """
    Memerintahkan robot untuk bergerak manual (open-loop).
    """
    metadata = ctx.request_context.meta
    session_id = metadata.session_id
    trace_id = metadata.trace_id
    observation_id = metadata.observation_id
    model_name = getattr(metadata, 'model_name', None)
    t_ctx = {"trace_id": trace_id, "parent_span_id": observation_id} if trace_id else None

    with langfuse_client.start_as_current_observation(
        as_type="span",
        name="mcp-tool: move",
        trace_context=t_ctx,
        input={"linear_speed": linear_speed, "angular_speed": angular_speed, "duration": duration}
    ) as span:
        with propagate_attributes(tags=["mcp-server"]):
            try:
                controller = get_controller_node()

                if controller is None:
                    result = create_response(
                        type="robot_action",
                        status="error",
                        message="Controller ROS 2 belum siap."
                    )
                    span.update(output=result)
                    return result

                # Memanggil metode async pada controller ROS 2
                controller.move_async(linear_speed, angular_speed, duration, session_id, model_name)
                
                result = create_response(
                    type="robot_action",
                    status="running",
                    message=f"Perintah dikirim. Robot sedang bergerak: Linear={linear_speed}m/s, Angular={angular_speed}rad/s. Jangan lakukan perintah apapun hingga robot selesai bergerak.",
                    data={
                        "linear_speed": linear_speed,
                        "angular_speed": angular_speed,
                        "duration": duration
                    }
                )
                span.update(output=result)
                return result
            except Exception as e:
                raise e

@mcp.tool
def async_navigate_to_waypoint(ctx: Context, x: float, y: float, theta_deg: float) -> dict:
    """
    Mengirimkan tujuan navigasi ke stack Nav2 (ROS 2).
    Args:
        x: Target X position in meters (map frame)
        y: Target Y position in meters (map frame)
        theta_deg: Target orientation in degrees (0=East, 90=North, 180=West, -90=South)
    """
    metadata = ctx.request_context.meta
    session_id = metadata.session_id
    trace_id = metadata.trace_id
    parent_span_id = metadata.observation_id
    model_name = getattr(metadata, 'model_name', None)
    t_ctx = {"trace_id": trace_id, "parent_span_id": parent_span_id} if trace_id else None

    with langfuse_client.start_as_current_observation(
        as_type="span",
        name="mcp-tool: navigate_to_waypoint",
        trace_context=t_ctx,
        input={"x": x, "y": y, "theta_deg": theta_deg}
    ) as span:
        with propagate_attributes(tags=["mcp-server"]):
            try:
                controller = get_controller_node()

                if controller is None:
                    result = create_response(
                        type="robot_action",
                        status="error",
                        message="Controller ROS 2 tidak tersedia."
                    )
                    span.update(output=result)
                    return result

                # Convert degrees to radians for Nav2
                theta_rad = math.radians(theta_deg)

                # Mengirim goal ke Action Server Nav2
                goal_sent = controller.send_nav_goal_async(x, y, theta_rad, session_id, model_name)
                
                if goal_sent:
                    result = create_response(
                        type="robot_action",
                        status="running",
                        message=f"Perintah dikirim. Robot sedang menuju ({x:.2f}, {y:.2f}) arah {theta_deg:.0f}°. Jangan lakukan perintah apapun hingga robot selesai bergerak.",
                        data={"target_x": x, "target_y": y, "target_theta_deg": theta_deg}
                    )
                else:
                    result = create_response(
                        type="robot_action",
                        status="error",
                        message="Gagal mengirim goal. Pastikan action server 'navigate_to_pose' sudah berjalan."
                    )
                
                span.update(output=result)
                return result
            except Exception as e:
                raise e

# --- TOOLS: DATABASE QUERY ---

@mcp.tool
def get_object_waypoints(ctx: Context, query: str, location: Optional[str] = None) -> dict:
    """
    Mencari objek inspeksi dan koordinat waypoint-nya di database.
    Mengembalikan data hierarkis: Map → Location path → Object → Waypoint.

    Args:
        query: Kata kunci pencarian (nama objek, keywords, atau nama lokasi).
               Contoh: "pressure tank", "valve", "pompa".
        location: (Opsional) Filter berdasarkan nama lokasi tertentu.
                  Contoh: "Boiler Room", "Floor 1".
    """
    metadata = ctx.request_context.meta
    session_id = metadata.session_id
    trace_id = metadata.trace_id
    parent_span_id = metadata.observation_id
    model_name = getattr(metadata, 'model_name', None)
    t_ctx = {"trace_id": trace_id, "parent_span_id": parent_span_id} if trace_id else None

    with langfuse_client.start_as_current_observation(
        as_type="span",
        name="mcp-tool: get_object_waypoints",
        trace_context=t_ctx,
        input={"query": query, "location": location}
    ) as span:
        with propagate_attributes(tags=["mcp-server"]):
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
                    result = create_response(
                        type="navigation_query",
                        status="empty",
                        message=f"Tidak ditemukan objek dengan kata kunci '{query}'.",
                        data=[]
                    )
                    span.update(output=result)
                    return result

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
                        result = create_response(
                            type="navigation_query",
                            status="empty",
                            message=f"Tidak ditemukan '{query}' di lokasi '{location}'.",
                            data=[]
                        )
                        span.update(output=result)
                        return result

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

                result = create_response(
                    type="navigation_query",
                    status="success",
                    message=f"Ditemukan {len(formatted_data)} waypoint yang cocok untuk '{query}'.",
                    data=formatted_data
                )
                span.update(output=result)
                return result

            except Exception as e:
                result = create_response(
                    type="navigation_query",
                    status="error",
                    message=f"Terjadi kesalahan saat query database: {str(e)}"
                )
                span.update(output=result)
                return result
            except Exception as e:
                raise e

# --- TOOLS: FILE RETRIEVAL (DOCUMENT) ---

@mcp.tool
def get_documents(ctx: Context) -> dict:
    """
    Mengambil daftar file dokumen dalam folder 'raisa' di bucket Supabase.
    """
    metadata = ctx.request_context.meta
    session_id = metadata.session_id
    trace_id = metadata.trace_id
    parent_span_id = metadata.observation_id
    model_name = getattr(metadata, 'model_name', None)
    t_ctx = {"trace_id": trace_id, "parent_span_id": parent_span_id} if trace_id else None

    with langfuse_client.start_as_current_observation(
        as_type="span",
        name="mcp-tool: get_documents",
        trace_context=t_ctx,
        input={}
    ) as span:
        with propagate_attributes(tags=["mcp-server"]):
            try:
                # Ambil daftar file di folder 'raisa'
                files = supabase.storage.from_(BUCKET_NAME).list("raisa")
                
                # Filter out empty placeholder files
                filtered_files = [f for f in files if f.get("name") != ".emptyFolderPlaceholder"]
                
                formatted = []
                for file in filtered_files:
                    file_name = file.get("name")
                    file_path = f"raisa/{file_name}"
                    
                    # Buat URL publik manual sesuai pola Supabase Local/Cloud
                    public_url = f"{SUPABASE_URL}/storage/v1/object/public/{BUCKET_NAME}/{file_path}"
                    
                    formatted.append({
                        "name": file_name,
                        "url": public_url,
                        "size": file.get("metadata", {}).get("size", 0) if file.get("metadata") else 0,
                        "created_at": file.get("created_at", ""),
                        "type": file.get("metadata", {}).get("mimetype", "application/octet-stream") if file.get("metadata") else "application/octet-stream",
                    })

                result = create_response(
                    type="document_query",
                    status="success",
                    message=f"Ditemukan {len(formatted)} dokumen di folder 'raisa'.",
                    data=formatted
                )
                span.update(output=result)
                return result
            except Exception as e:
                result = create_response(
                    type="document_query",
                    status="error",
                    message=f"Gagal mengambil dokumen dari folder 'raisa': {str(e)}"
                )
                span.update(output=result)
                return result
