import base64
import io
import math
import os
from datetime import datetime
from io import BytesIO
from typing import Any, Dict, List, Optional

import cv2
import httpx
import numpy as np
from dotenv import load_dotenv
from fastmcp import FastMCP
from fastmcp.server.context import Context
from google import genai
from google.genai import types
from pydantic import BaseModel

# Import Controller ROS Noetic Anda
from utils.ros_manager import get_controller_node

from langfuse import get_client, observe, propagate_attributes
from supabase import Client, create_client

load_dotenv()
langfuse_client = get_client()


class ObjectDetectionResult(BaseModel):
    is_detected: bool
    ymin: int
    xmin: int
    ymax: int
    xmax: int


class InspectionResult(BaseModel):
    is_detected: bool
    ymin: int
    xmin: int
    ymax: int
    xmax: int
    analysis: str
    findings: list[str]
    status: str  # "normal", "abnormal", "inconclusive"


mcp = FastMCP("robot_api_mcp")

# --- Konfigurasi Supabase ---
SUPABASE_URL: str = os.getenv("SUPABASE_URL")
SUPABASE_KEY: str = os.getenv("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

BUCKET_NAME = "robotics-prata"

# --- Konfigurasi Model Routing untuk Vision ---
OLLAMA_HOST: str = os.getenv("OLLAMA_HOST", "http://localhost:11434")
OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY")
OLLAMA_MODELS = {"qwen3.5:27b"}
OPENAI_MODELS = {"gpt-4o", "gpt-4o-mini"}


# --- Helper Function untuk Format Standar ---
def create_response(type: str, status: str, message: str, data: Any = None) -> dict:
    """
    Mengembalikan dictionary standar.
    FastMCP akan otomatis mengonversinya menjadi JSON saat dikirim ke client.
    """
    return {"type": type, "status": status, "message": message, "data": data}


def _search_objects_by_query(query: str) -> list:
    """
    Mencari objek berdasarkan nama (ilike) DAN keywords (partial match).
    PostgREST `.cs.` hanya mendukung exact match pada array elements,
    sehingga untuk partial/fuzzy keyword search, kita fetch semua objek
    dan filter keywords secara manual di Python (case-insensitive substring).
    """
    search_term = f"%{query}%"
    query_lower = query.lower()

    # 1. Cari berdasarkan nama (ilike - partial match di DB)
    name_response = (
        supabase.table("objects").select("*").ilike("name", search_term).execute()
    )
    name_matched = {obj["id"]: obj for obj in (name_response.data or [])}

    # 2. Fetch semua objek untuk keyword partial matching di Python
    all_response = supabase.table("objects").select("*").execute()
    for obj in all_response.data or []:
        if obj["id"] in name_matched:
            continue  # sudah ditemukan via nama
        keywords = obj.get("keywords") or []
        for kw in keywords:
            # Partial match: query adalah substring dari keyword, ATAU keyword adalah substring dari query
            if query_lower in kw.lower() or kw.lower() in query_lower:
                name_matched[obj["id"]] = obj
                break

    return list(name_matched.values())


def _fetch_sop_text(object_name: str) -> Optional[str]:
    """
    Mencari objek berdasarkan nama, lalu download dan ekstrak isi SOP file-nya.
    Mendukung format PDF, DOCX, dan plain text.
    Mengembalikan teks SOP atau None jika tidak ditemukan.
    """
    import mimetypes

    import requests as req_lib

    try:
        # 1. Cari objek berdasarkan nama
        matched_objects = _search_objects_by_query(object_name)
        if not matched_objects:
            print(f"   ℹ️ SOP auto-fetch: No object found for '{object_name}'")
            return None

        # Ambil objek pertama yang punya sop_url
        sop_url = None
        matched_name = None
        for obj in matched_objects:
            if obj.get("sop_url"):
                sop_url = obj["sop_url"]
                matched_name = obj.get("name", object_name)
                break

        if not sop_url:
            print(
                f"   ℹ️ SOP auto-fetch: Object '{object_name}' found but has no SOP URL"
            )
            return None

        # 2. Download file dari URL
        print(
            f"   📥 SOP auto-fetch: Downloading SOP for '{matched_name}' from {sop_url}"
        )
        resp = req_lib.get(sop_url, timeout=30)
        resp.raise_for_status()
        file_bytes = resp.content

        if not file_bytes:
            print(f"   ⚠️ SOP auto-fetch: File empty from URL: {sop_url}")
            return None

        # 3. Detect content type
        content_type = resp.headers.get("Content-Type", "")
        if ";" in content_type:
            content_type = content_type.split(";")[0].strip()
        if not content_type or content_type == "application/octet-stream":
            mime_type, _ = mimetypes.guess_type(sop_url)
            if mime_type:
                content_type = mime_type

        # 4. Extract text berdasarkan tipe file
        if (
            content_type
            == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        ):
            # DOCX
            import docx

            doc_stream = io.BytesIO(file_bytes)
            doc = docx.Document(doc_stream)
            full_text = [para.text for para in doc.paragraphs]
            extracted = "\n".join(full_text)
            print(f"   ✅ SOP auto-fetch: Extracted DOCX text ({len(extracted)} chars)")
            return extracted

        elif content_type == "application/pdf":
            # PDF
            import pypdf

            pdf_reader = pypdf.PdfReader(io.BytesIO(file_bytes))
            pages_text = []
            for page in pdf_reader.pages:
                page_text = page.extract_text()
                if page_text:
                    pages_text.append(page_text)
            extracted = "\n\n".join(pages_text) if pages_text else None
            if extracted:
                print(
                    f"   ✅ SOP auto-fetch: Extracted PDF text ({len(extracted)} chars)"
                )
            else:
                print(f"   ⚠️ SOP auto-fetch: No extractable text found in PDF")
            return extracted

        else:
            # Plain text atau format lain, coba decode sebagai text
            try:
                extracted = file_bytes.decode("utf-8")
                print(
                    f"   ✅ SOP auto-fetch: Read as plain text ({len(extracted)} chars)"
                )
                return extracted
            except UnicodeDecodeError:
                print(
                    f"   ⚠️ SOP auto-fetch: Cannot extract text from content type '{content_type}'"
                )
                return None

    except Exception as e:
        print(f"   ⚠️ SOP auto-fetch error: {e}")
        return None


# --- TOOLS: ROBOT ACTION ---


@mcp.tool
def async_move(
    ctx: Context,
    linear_speed: float = 0.0,
    angular_speed: float = 0.0,
    duration: float = 5.0,
) -> dict:
    """
    Memerintahkan robot untuk bergerak manual (open-loop).
    """
    metadata = ctx.request_context.meta
    session_id = metadata.session_id
    trace_id = metadata.trace_id
    observation_id = metadata.observation_id
    model_name = getattr(metadata, "model_name", None)
    t_ctx = (
        {"trace_id": trace_id, "parent_span_id": observation_id} if trace_id else None
    )

    with langfuse_client.start_as_current_observation(
        as_type="span",
        name="mcp-tool: move",
        trace_context=t_ctx,
        input={
            "linear_speed": linear_speed,
            "angular_speed": angular_speed,
            "duration": duration,
        },
    ) as span:
        with propagate_attributes(tags=["mcp-server"]):
            try:
                controller = get_controller_node()

                if controller is None:
                    result = create_response(
                        type="robot_action",
                        status="error",
                        message="Controller ROS Noetic belum siap atau roscore tidak terdeteksi.",
                    )
                    span.update(output=result)
                    return result

                # Memanggil metode async pada controller Noetic
                controller.move_async(
                    linear_speed, angular_speed, duration, session_id, model_name
                )

                result = create_response(
                    type="robot_action",
                    status="running",
                    message=f"Perintah dikirim. Robot sedang bergerak: Linear={linear_speed}m/s, Angular={angular_speed}rad/s. Jangan lakukan perintah apapun hingga robot selesai bergerak.",
                    data={
                        "linear_speed": linear_speed,
                        "angular_speed": angular_speed,
                        "duration": duration,
                    },
                )
                span.update(output=result)
                return result
            except Exception as e:
                raise e


@mcp.tool
def async_navigate_to_waypoint(
    ctx: Context, x: float, y: float, theta_deg: float
) -> dict:
    """
    Mengirimkan tujuan navigasi ke stack move_base (ROS 1).
    Args:
        x: Target X position in meters (map frame)
        y: Target Y position in meters (map frame)
        theta_deg: Target orientation in degrees (0=East, 90=North, 180=West, -90=South)
    """
    metadata = ctx.request_context.meta
    session_id = metadata.session_id
    trace_id = metadata.trace_id
    parent_span_id = metadata.observation_id
    model_name = getattr(metadata, "model_name", None)
    t_ctx = (
        {"trace_id": trace_id, "parent_span_id": parent_span_id} if trace_id else None
    )

    with langfuse_client.start_as_current_observation(
        as_type="span",
        name="mcp-tool: navigate_to_waypoint",
        trace_context=t_ctx,
        input={"x": x, "y": y, "theta_deg": theta_deg},
    ) as span:
        with propagate_attributes(tags=["mcp-server"]):
            try:
                controller = get_controller_node()

                if controller is None:
                    result = create_response(
                        type="robot_action",
                        status="error",
                        message="Controller ROS Noetic tidak tersedia.",
                    )
                    span.update(output=result)
                    return result

                # Convert degrees to radians for move_base
                theta_rad = math.radians(theta_deg)

                # Mengirim goal ke Action Server move_base
                goal_sent = controller.send_nav_goal_async(
                    x, y, theta_rad, session_id, model_name
                )

                if goal_sent:
                    result = create_response(
                        type="robot_action",
                        status="running",
                        message=f"Perintah dikirim. Robot sedang menuju ({x:.2f}, {y:.2f}) arah {theta_deg:.0f}°. Jangan lakukan perintah apapun hingga robot selesai bergerak.",
                        data={
                            "target_x": x,
                            "target_y": y,
                            "target_theta_deg": theta_deg,
                        },
                    )
                else:
                    result = create_response(
                        type="robot_action",
                        status="error",
                        message="Gagal mengirim goal. Pastikan node 'move_base' di robot sudah berjalan.",
                    )

                span.update(output=result)
                return result
            except Exception as e:
                raise e


@mcp.tool
def toggle_sit_stand(ctx: Context) -> dict:
    """
    Memerintahkan robot untuk mengganti state antara duduk (sit) dan berdiri (stand).
    Perintah ini menggunakan SimpleCMD dengan kode 0x21010202.
    """
    metadata = ctx.request_context.meta
    session_id = metadata.session_id
    trace_id = metadata.trace_id
    parent_span_id = metadata.observation_id
    model_name = getattr(metadata, "model_name", None)
    t_ctx = (
        {"trace_id": trace_id, "parent_span_id": parent_span_id} if trace_id else None
    )

    with langfuse_client.start_as_current_observation(
        as_type="span", name="mcp-tool: toggle_sit_stand", trace_context=t_ctx, input={}
    ) as span:
        with propagate_attributes(tags=["mcp-server"]):
            try:
                controller = get_controller_node()

                if controller is None:
                    result = create_response(
                        type="robot_action",
                        status="error",
                        message="Controller ROS Noetic tidak tersedia.",
                    )
                    span.update(output=result)
                    return result

                # 0x21010202 adalah command untuk switch antara duduk dan berdiri
                controller.send_simple_cmd(
                    cmd_code=0x21010202, cmd_value=0, cmd_type=0, session_id=session_id
                )

                result = create_response(
                    type="robot_action",
                    status="success",
                    message="Robot sudah dalam posisi duduk/berdiri.",
                    data={"cmd_code": "0x21010202"},
                )
                span.update(output=result)
                return result
            except Exception as e:
                raise e


@mcp.tool
def say_hello(ctx: Context) -> dict:
    """
    Memerintahkan robot untuk melakukan aksi 'Hello' (melambaikan tangan).
    Perintah ini menggunakan SimpleCMD dengan kode 0x21010507.
    """
    metadata = ctx.request_context.meta
    session_id = metadata.session_id
    trace_id = metadata.trace_id
    parent_span_id = metadata.observation_id
    model_name = getattr(metadata, "model_name", None)
    t_ctx = (
        {"trace_id": trace_id, "parent_span_id": parent_span_id} if trace_id else None
    )

    with langfuse_client.start_as_current_observation(
        as_type="span", name="mcp-tool: say_hello", trace_context=t_ctx, input={}
    ) as span:
        with propagate_attributes(tags=["mcp-server"]):
            try:
                controller = get_controller_node()

                if controller is None:
                    result = create_response(
                        type="robot_action",
                        status="error",
                        message="Controller ROS Noetic tidak tersedia.",
                    )
                    span.update(output=result)
                    return result

                # 0x21010507 adalah command untuk aksi Hello (lambaikan tangan)
                controller.send_simple_cmd(
                    cmd_code=0x21010507, cmd_value=0, cmd_type=0, session_id=session_id
                )

                result = create_response(
                    type="robot_action",
                    status="success",
                    message="Robot sedang melakukan aksi Hello (melambaikan tangan). Pastikan robot dalam keadaan duduk (sitting state).",
                    data={"cmd_code": "0x21010507"},
                )
                span.update(output=result)
                return result
            except Exception as e:
                raise e


@mcp.tool
def look_up_down(ctx: Context, angle_value: int, duration: float = 3.0) -> dict:
    """
    Memerintahkan robot untuk menunduk (look down) atau menengadah (look up) dengan mengatur pitch angle, berjalan secara asinkron.
    Args:
        angle_value: Nilai antara -32767 sampai 32767.
                     PENTING: Nilai di antara [-6553, 6553] adalah DEAD ZONE dan akan diabaikan (dianggap 0).
                     Gunakan nilai yang lebih besar (misal: 20000 untuk menunduk, -20000 untuk menengadah).
        duration: Lama waktu (dalam detik) robot menahan pose ini sebelum kembali normal. Default: 3.0.
    """
    metadata = ctx.request_context.meta
    session_id = metadata.session_id
    trace_id = metadata.trace_id
    parent_span_id = metadata.observation_id
    model_name = getattr(metadata, "model_name", None)
    t_ctx = (
        {"trace_id": trace_id, "parent_span_id": parent_span_id} if trace_id else None
    )

    with langfuse_client.start_as_current_observation(
        as_type="span",
        name="mcp-tool: look_up_down",
        trace_context=t_ctx,
        input={"angle_value": angle_value, "duration": duration},
    ) as span:
        with propagate_attributes(tags=["mcp-server"]):
            try:
                controller = get_controller_node()

                if controller is None:
                    result = create_response(
                        type="robot_action",
                        status="error",
                        message="Controller ROS Noetic tidak tersedia.",
                    )
                    span.update(output=result)
                    return result

                # Batasi nilai agar sesuai dengan batas maksimal joystick [-32767, 32767]
                clamped_value = max(-32767, min(32767, angle_value))

                # Gunakan pose_async yang baru kita buat
                controller.pose_async(
                    pitch_angle=clamped_value,
                    duration=duration,
                    session_id=session_id,
                    model_name=model_name,
                )

                result = create_response(
                    type="robot_action",
                    status="success",
                    message=f"Robot sedang look up/down sesuai perintah, dan akan menahan pose selama {duration} detik.",
                    data={
                        "cmd_code": "pose_async",
                        "cmd_value": clamped_value,
                        "duration": duration,
                    },
                )
                span.update(output=result)
                return result
            except Exception as e:
                raise e


# --- TOOLS: DATABASE QUERY ---


@mcp.tool
def get_object_waypoints(
    ctx: Context, query: str, location: Optional[str] = None
) -> dict:
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
    model_name = getattr(metadata, "model_name", None)
    t_ctx = (
        {"trace_id": trace_id, "parent_span_id": parent_span_id} if trace_id else None
    )

    with langfuse_client.start_as_current_observation(
        as_type="span",
        name="mcp-tool: get_object_waypoints",
        trace_context=t_ctx,
        input={"query": query, "location": location},
    ) as span:
        with propagate_attributes(tags=["mcp-server"]):
            try:
                search_term = f"%{query}%"

                # --- 1. Search objects by name & keywords (partial match) ---
                matched_objects = _search_objects_by_query(query)
                matched_object_ids = [obj["id"] for obj in matched_objects]

                # --- 2. Search waypoints by display_name OR matching object_id ---
                if matched_object_ids:
                    # Build filter: waypoints whose object_id matches OR display_name matches
                    obj_id_filter = ",".join(str(i) for i in matched_object_ids)
                    wp_response = (
                        supabase.table("object-waypoints")
                        .select("*")
                        .or_(
                            f"display_name.ilike.{search_term},object_id.in.({obj_id_filter})"
                        )
                        .execute()
                    )
                else:
                    wp_response = (
                        supabase.table("object-waypoints")
                        .select("*")
                        .ilike("display_name", search_term)
                        .execute()
                    )

                waypoints = wp_response.data or []

                if not waypoints:
                    result = create_response(
                        type="navigation_query",
                        status="empty",
                        message=f"Tidak ditemukan objek dengan kata kunci '{query}'.",
                        data=[],
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
                    for loc in loc_response.data or []:
                        all_locations[loc["id"]] = loc

                # --- 5. Fetch referenced objects ---
                objects_map = {}
                if object_ids:
                    obj_ids_str = ",".join(str(i) for i in object_ids)
                    obj_detail = (
                        supabase.table("objects")
                        .select("*")
                        .in_("id", list(object_ids))
                        .execute()
                    )
                    for obj in obj_detail.data or []:
                        objects_map[obj["id"]] = obj

                # --- 6. Fetch all maps ---
                maps_map = {}
                map_response = supabase.table("maps").select("*").execute()
                for m in map_response.data or []:
                    maps_map[m["id"]] = m

                # --- 7. Helper: build location path (walk up parent chain) ---
                def build_location_path(loc_id: int) -> List[Dict]:
                    """Returns list from root to leaf: [map_name, loc1, loc2, ...]"""
                    chain = []
                    visited = set()
                    current_id = loc_id
                    while (
                        current_id
                        and current_id in all_locations
                        and current_id not in visited
                    ):
                        visited.add(current_id)
                        loc = all_locations[current_id]
                        chain.append(
                            {
                                "name": loc.get("name"),
                                "type": loc.get("type"),
                                "id": loc["id"],
                            }
                        )
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
                                    if (
                                        child.get("parent_id") == pid
                                        and child_id not in matching_loc_ids
                                    ):
                                        matching_loc_ids.add(child_id)
                                        queue.append(child_id)

                    waypoints = [
                        wp
                        for wp in waypoints
                        if wp.get("parent_id") in matching_loc_ids
                    ]

                    if not waypoints:
                        result = create_response(
                            type="navigation_query",
                            status="empty",
                            message=f"Tidak ditemukan '{query}' di lokasi '{location}'.",
                            data=[],
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

                    formatted_data.append(
                        {
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
                                    [
                                        (
                                            f"{l['name']} ({l['type']})"
                                            if l.get("type")
                                            else l["name"]
                                        )
                                        for l in location_path
                                    ]
                                ),
                                "location_name": (
                                    location_path[-1]["name"] if location_path else None
                                ),
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
                        }
                    )

                result = create_response(
                    type="navigation_query",
                    status="success",
                    message=f"Ditemukan {len(formatted_data)} waypoint yang cocok untuk '{query}'.",
                    data=formatted_data,
                )
                span.update(output=result)
                return result

            except Exception as e:
                result = create_response(
                    type="navigation_query",
                    status="error",
                    message=f"Terjadi kesalahan saat query database: {str(e)}",
                )
                span.update(output=result)
                return result
            except Exception as e:
                raise e


# --- TOOLS: FILE RETRIEVAL (SOP) ---


@mcp.tool
def get_sop_file(ctx: Context, query: str) -> dict:
    """
    Mencari dan mengambil file SOP berdasarkan nama objek atau keywords dari database.
    Tool ini mencari di tabel 'objects' dan mengembalikan sop_url yang terkait.

    Args:
        query: Kata kunci pencarian (nama objek atau keyword).
               Contoh: "pressure tank", "valve", "pompa".
    """
    metadata = ctx.request_context.meta
    session_id = metadata.session_id
    trace_id = metadata.trace_id
    parent_span_id = metadata.observation_id
    model_name = getattr(metadata, "model_name", None)
    t_ctx = (
        {"trace_id": trace_id, "parent_span_id": parent_span_id} if trace_id else None
    )

    with langfuse_client.start_as_current_observation(
        as_type="span",
        name="mcp-tool: get_sop_file",
        trace_context=t_ctx,
        input={"query": query},
    ) as span:
        with propagate_attributes(tags=["mcp-server"]):
            try:
                # Search objects by name & keywords (partial match)
                objects = _search_objects_by_query(query)

                if not objects:
                    result = create_response(
                        type="sop_query",
                        status="empty",
                        message=f"Tidak ditemukan objek/SOP dengan kata kunci '{query}'.",
                        data=[],
                    )
                    span.update(output=result)
                    return result

                # Format results
                formatted = []
                for obj in objects:
                    formatted.append(
                        {
                            "object_id": obj.get("id"),
                            "name": obj.get("name"),
                            "keywords": obj.get("keywords", []),
                            "sop_url": obj.get("sop_url"),
                        }
                    )

                result = create_response(
                    type="sop_query",
                    status="success",
                    message=f"Ditemukan {len(formatted)} SOP yang cocok untuk '{query}'.",
                    data=formatted,
                )
                span.update(output=result)
                return result

            except Exception as e:
                result = create_response(
                    type="sop_query",
                    status="error",
                    message=f"Terjadi kesalahan saat query database: {str(e)}",
                )
                span.update(output=result)
                return result


# --- VISION INSPECTION HELPERS ---


def _build_detection_prompt(inspected_object: str) -> str:
    """Prompt untuk object detection saja (tanpa SOP analysis)."""
    return (
        f"Tolong deteksi objek: {inspected_object} apakah ada atau tidak pada gambar. "
        f"Jika ada, set is_detected ke true dan berikan bounding box coordinates "
        f"(ymin, xmin, ymax, xmax) sebagai normalized integers antara 0 dan 1000, "
        f"dimana 0 adalah bagian atas/kiri dan 1000 adalah bagian bawah/kanan.\n\n"
        f"Respond ONLY with valid JSON in this exact format:\n"
        f'{{"is_detected": true/false, "ymin": 0, "xmin": 0, "ymax": 0, "xmax": 0}}'
    )


def _build_inspection_prompt(inspected_object: str, sop_context: str) -> str:
    """Prompt untuk object detection + SOP inspection analysis."""
    return (
        f"Kamu adalah inspektur visual profesional.\n"
        f"1. Deteksi objek: '{inspected_object}' pada gambar. "
        f"Jika ada, set is_detected=true dan berikan bounding box (ymin, xmin, ymax, xmax) "
        f"sebagai normalized integers 0-1000 (0=atas/kiri, 1000=bawah/kanan).\n"
        f"2. Analisis kondisi visual objek berdasarkan SOP berikut:\n"
        f"--- SOP START ---\n{sop_context}\n--- SOP END ---\n"
        f"3. Di field 'analysis', berikan analisis detail kondisi objek berdasarkan poin-poin SOP.\n"
        f"4. Di field 'findings', list temuan spesifik (baik normal maupun abnormal).\n"
        f"5. Di field 'status', set 'normal' jika semua sesuai SOP, 'abnormal' jika ada ketidaksesuaian, "
        f"atau 'inconclusive' jika gambar kurang jelas untuk menilai.\n\n"
        f"Respond ONLY with valid JSON in this exact format:\n"
        f'{{"is_detected": true/false, "ymin": 0, "xmin": 0, "ymax": 0, "xmax": 0, '
        f'"analysis": "...", "findings": ["..."], "status": "normal/abnormal/inconclusive"}}'
    )


def _inspect_with_gemini(
    jpeg_bytes: bytes,
    inspected_object: str,
    sop_context: Optional[str] = None,
    model_name: str = "gemini-2.5-flash",
) -> tuple[dict | None, dict | None]:
    """
    Inspect image menggunakan Gemini SDK (native structured output).

    Returns:
        (detection_result, analysis_data) — detection_result berisi bbox coords,
        analysis_data berisi SOP findings (atau None jika tanpa SOP).
    """
    client = genai.Client()

    if sop_context:
        prompt = (
            f"Kamu adalah inspektur visual profesional.\n"
            f"1. Deteksi objek: '{inspected_object}' pada gambar. "
            f"Jika ada, set is_detected=true dan berikan bounding box (ymin, xmin, ymax, xmax) "
            f"sebagai normalized integers 0-1000 (0=atas/kiri, 1000=bawah/kanan).\n"
            f"2. Analisis kondisi visual objek berdasarkan SOP berikut:\n"
            f"--- SOP START ---\n{sop_context}\n--- SOP END ---\n"
            f"3. Di field 'analysis', berikan analisis detail kondisi objek berdasarkan poin-poin SOP.\n"
            f"4. Di field 'findings', list temuan spesifik (baik normal maupun abnormal).\n"
            f"5. Di field 'status', set 'normal' jika semua sesuai SOP, 'abnormal' jika ada ketidaksesuaian, "
            f"atau 'inconclusive' jika gambar kurang jelas untuk menilai."
        )
        schema = InspectionResult
    else:
        prompt = (
            f"Tolong deteksi objek: {inspected_object} apakah ada atau tidak pada gambar. "
            f"Jika ada, set is_detected ke true dan berikan bounding box coordinates "
            f"(ymin, xmin, ymax, xmax) sebagai normalized integers antara 0 dan 1000, "
            f"dimana 0 adalah bagian atas/kiri dan 1000 adalah bagian bawah/kanan."
        )
        schema = ObjectDetectionResult

    # Disable thinking untuk model 2.5 — known issue: thinking mode
    # merusak akurasi bounding box secara signifikan pada Gemini 2.5 Flash
    thinking_config = None
    if "2.5" in model_name:
        thinking_config = types.ThinkingConfig(thinking_budget=0)
        print(f"   ⚙️ Thinking disabled for {model_name} (bbox accuracy fix)")

    response = client.models.generate_content(
        model=model_name,
        contents=[
            prompt,
            types.Part.from_bytes(data=jpeg_bytes, mime_type="image/jpeg"),
        ],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=schema,
            temperature=0.0,
            thinking_config=thinking_config,
        ),
    )

    result = response.parsed
    if result is None:
        return None, None

    analysis_data = None
    if sop_context and isinstance(result, InspectionResult):
        analysis_data = {
            "analysis": result.analysis,
            "findings": result.findings,
            "inspection_status": result.status,
        }

    detection = {
        "is_detected": result.is_detected,
        "ymin": result.ymin,
        "xmin": result.xmin,
        "ymax": result.ymax,
        "xmax": result.xmax,
    }
    return detection, analysis_data


import json as json_module


def _inspect_with_openai_compatible(
    jpeg_bytes: bytes,
    inspected_object: str,
    sop_context: Optional[str] = None,
    model_name: str = "qwen2.5:7b",
) -> tuple[dict | None, dict | None]:
    """
    Inspect image menggunakan OpenAI-compatible API (Ollama/Qwen atau OpenAI GPT).
    Mengirim gambar sebagai base64 data URL dalam message content.

    Returns:
        (detection_result, analysis_data)
    """
    import requests as req_lib

    # Build prompt
    if sop_context:
        prompt = _build_inspection_prompt(inspected_object, sop_context)
    else:
        prompt = _build_detection_prompt(inspected_object)

    # Encode image ke base64
    img_b64 = base64.b64encode(jpeg_bytes).decode("utf-8")

    # Build messages dengan image (OpenAI vision format)
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"},
                },
            ],
        }
    ]

    # Determine endpoint and headers
    if model_name in OPENAI_MODELS:
        url = "https://api.openai.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json",
        }
    else:
        # Ollama
        url = f"{OLLAMA_HOST}/v1/chat/completions"
        headers = {"Content-Type": "application/json"}

    payload = {
        "model": model_name,
        "messages": messages,
        "temperature": 0.0,
    }

    resp = req_lib.post(url, json=payload, headers=headers, timeout=300)
    resp.raise_for_status()
    resp_json = resp.json()

    # Extract text content from response
    raw_text = resp_json.get("choices", [{}])[0].get("message", {}).get("content", "")

    # Parse JSON dari response text
    # Model kadang membungkus JSON dalam markdown code block
    cleaned = raw_text.strip()
    if cleaned.startswith("```"):
        # Remove markdown code fences
        lines = cleaned.split("\n")
        # Remove first line (```json or ```) and last line (```)
        lines = [l for l in lines if not l.strip().startswith("```")]
        cleaned = "\n".join(lines).strip()

    try:
        parsed = json_module.loads(cleaned)
    except json_module.JSONDecodeError:
        print(f"⚠️ Failed to parse vision response as JSON: {raw_text[:200]}")
        return None, None

    analysis_data = None
    if sop_context and "analysis" in parsed:
        analysis_data = {
            "analysis": parsed.get("analysis", ""),
            "findings": parsed.get("findings", []),
            "inspection_status": parsed.get("status", "inconclusive"),
        }

    detection = {
        "is_detected": parsed.get("is_detected", False),
        "ymin": parsed.get("ymin", 0),
        "xmin": parsed.get("xmin", 0),
        "ymax": parsed.get("ymax", 0),
        "xmax": parsed.get("xmax", 0),
    }
    return detection, analysis_data


def inspect_image(
    jpeg_bytes: bytes,
    inspected_object: str,
    sop_context: Optional[str] = None,
    model_name: Optional[str] = None,
) -> tuple[dict | None, dict | None]:
    """
    Dispatcher: route vision inspection ke handler yang sesuai berdasarkan model_name.

    Args:
        jpeg_bytes: Raw JPEG image bytes dari kamera.
        inspected_object: Nama objek yang ingin dideteksi.
        sop_context: (Opsional) SOP text untuk analisis visual.
        model_name: Model yang dipilih user. Menentukan handler mana yang dipakai.

    Returns:
        (detection_result, analysis_data) — detection_result dict dengan is_detected + bbox,
        analysis_data dict dengan SOP findings atau None.
    """
    model_name = "gemini-3.1-pro-preview"
    # Default ke Gemini jika tidak ada model_name
    if not model_name or model_name.startswith("gemini"):
        # Untuk Gemini, gunakan model vision terbaik yang tersedia
        gemini_vision_model = model_name if model_name else "gemini-2.5-flash"
        print(f"   🔍 Inspecting with Gemini ({gemini_vision_model})...")
        return _inspect_with_gemini(
            jpeg_bytes, inspected_object, sop_context, gemini_vision_model
        )

    elif model_name in OLLAMA_MODELS or model_name in OPENAI_MODELS:
        provider = "OpenAI GPT" if model_name in OPENAI_MODELS else "Ollama"
        print(f"   🔍 Inspecting with {provider} ({model_name})...")
        return _inspect_with_openai_compatible(
            jpeg_bytes, inspected_object, sop_context, model_name
        )

    else:
        # Fallback: model tidak dikenal, gunakan Gemini default
        print(f"   ⚠️ Unknown model '{model_name}' for vision. Falling back to Gemini.")
        return _inspect_with_gemini(
            jpeg_bytes, inspected_object, sop_context, "gemini-2.5-flash"
        )


def crop_detected_object(jpeg_bytes: bytes, detection: dict) -> bytes:
    """
    Crop gambar berdasarkan bounding box dari detection result.
    Menambahkan 20% padding agar objek tidak terpotong terlalu mepet.

    Args:
        jpeg_bytes: Raw JPEG bytes gambar asli.
        detection: Dict berisi is_detected, ymin, xmin, ymax, xmax (normalized 0-1000).

    Returns:
        Cropped JPEG bytes, atau original bytes jika crop gagal.
    """
    try:
        nparr = np.frombuffer(jpeg_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

        if img is None:
            return jpeg_bytes

        h, w = img.shape[:2]

        # Un-normalize coordinates dari range [0, 1000] ke pixel
        ymin_px = int((detection["ymin"] / 1000.0) * h)
        ymax_px = int((detection["ymax"] / 1000.0) * h)
        xmin_px = int((detection["xmin"] / 1000.0) * w)
        xmax_px = int((detection["xmax"] / 1000.0) * w)

        # Tambahkan 20% padding
        pad_y = int((ymax_px - ymin_px) * 0.2)
        pad_x = int((xmax_px - xmin_px) * 0.2)

        # Clamp coordinates
        ymin = max(0, min(h - 1, ymin_px - pad_y))
        ymax = max(ymin + 1, min(h, ymax_px + pad_y))
        xmin = max(0, min(w - 1, xmin_px - pad_x))
        xmax = max(xmin + 1, min(w, xmax_px + pad_x))

        cropped_img = img[ymin:ymax, xmin:xmax]

        success, buffer = cv2.imencode(".jpg", cropped_img)
        if success:
            return buffer.tobytes()
    except Exception as e:
        print(f"   ⚠️ Error during cropping: {e}")

    return jpeg_bytes


# --- TOOLS: CAMERA CAPTURE & UPLOAD ---


@mcp.tool
def capture_and_inspect_image(
    ctx: Context,
    inspected_object: Optional[str] = None,
    notes: Optional[str] = None,
) -> dict:
    """
    Mengambil gambar dari kamera robot via go2rtc snapshot API.
    Jika 'inspected_object' diberikan, gambar akan diproses oleh AI model
    untuk mendeteksi objek tersebut. Jika ditemukan, gambar akan di-crop menggunakan OpenCV
    berdasarkan koordinat bounding box yang dikembalikan oleh model.
    SOP (Standard Operating Procedure) akan otomatis dicari dan diambil dari database
    berdasarkan nama objek. Jika SOP ditemukan, model akan melakukan analisis visual
    terhadap objek berdasarkan prosedur SOP tersebut dan mengembalikan temuan inspeksinya.
    Hasil gambar (asli atau hasil crop) akan diupload ke Supabase Storage bucket
    'robotics-prata' folder 'captured', lalu dikembalikan public URL-nya beserta hasil analisis.

    Args:
        inspected_object: Nama objek yang ingin dideteksi dan diinspeksi.
        notes: (Opsional) Catatan atau instruksi tambahan dari user untuk inspeksi.
               Contoh: "lakukan pengecekan SOP level 2", "fokus pada korosi",
               "tambahan SOP: periksa tekanan gauge harus di range 2-4 bar".
    """
    metadata = ctx.request_context.meta
    session_id = metadata.session_id
    trace_id = metadata.trace_id
    parent_span_id = metadata.observation_id
    model_name = getattr(metadata, "model_name", None)
    t_ctx = (
        {"trace_id": trace_id, "parent_span_id": parent_span_id} if trace_id else None
    )

    with langfuse_client.start_as_current_observation(
        as_type="span",
        name="mcp-tool: capture_and_inspect_image",
        trace_context=t_ctx,
        input={"inspected_object": inspected_object, "notes": notes},
    ) as span:
        with propagate_attributes(tags=["mcp-server"]):
            try:
                controller = get_controller_node()

                if controller is None:
                    result = create_response(
                        type="image_capture",
                        status="error",
                        message="Controller ROS Noetic belum siap. Pastikan roscore sudah berjalan.",
                    )
                    span.update(output=result)
                    return result

                # 1. Capture image dari kamera
                jpeg_bytes = controller.capture_image(timeout=10.0)

                if jpeg_bytes is None:
                    result = create_response(
                        type="image_capture",
                        status="error",
                        message="Gagal mengambil gambar dari go2rtc snapshot API (timeout atau stream tidak tersedia).",
                    )
                    span.update(output=result)
                    return result

                # 1.5 Object Detection, Cropping & SOP Analysis (Opsional)
                analysis_data = None
                if inspected_object:
                    # Auto-fetch SOP dari database berdasarkan nama objek
                    sop_context = _fetch_sop_text(inspected_object)
                    if sop_context:
                        print(
                            f"   📄 SOP found for '{inspected_object}', will include in inspection."
                        )
                    else:
                        print(
                            f"   ℹ️ No SOP found for '{inspected_object}', proceeding with detection only."
                        )

                    # Append notes ke sop_context jika ada
                    if notes:
                        print(f"   📝 Notes from LLM: {notes}")
                        notes_section = (
                            f"\n--- ADDITIONAL NOTES ---\n{notes}\n--- END NOTES ---"
                        )
                        if sop_context:
                            sop_context = sop_context + notes_section
                        else:
                            # Jika tidak ada SOP tapi ada notes, gunakan notes sebagai sop_context
                            # agar tetap masuk ke inspection prompt
                            sop_context = notes_section

                    try:
                        detection, analysis_data = inspect_image(
                            jpeg_bytes=jpeg_bytes,
                            inspected_object=inspected_object,
                            sop_context=sop_context,
                            model_name=model_name,
                        )

                        if detection and detection.get("is_detected"):
                            jpeg_bytes = crop_detected_object(jpeg_bytes, detection)
                    except Exception as e:
                        print(f"Error during detection/inspection: {e}")
                        # Lanjutkan dengan gambar original jika terjadi error

                # 2. Generate nama file dengan timestamp
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                filepath = f"captured/capture_{timestamp}_{session_id}.jpg"

                # 3. Upload ke Supabase Storage
                supabase.storage.from_(BUCKET_NAME).upload(
                    path=filepath,
                    file=jpeg_bytes,
                    file_options={"content-type": "image/jpeg"},
                )

                # 4. Buat public URL
                public_url = (
                    f"{SUPABASE_URL}/storage/v1/object/public/{BUCKET_NAME}/{filepath}"
                )

                response_data = {"filepath": filepath, "public_url": public_url}
                if analysis_data:
                    response_data["inspection"] = analysis_data

                msg = (
                    "Gambar berhasil diambil, diupload, dan dianalisis berdasarkan SOP."
                    if analysis_data
                    else "Gambar berhasil diambil dan diupload."
                )

                result = create_response(
                    type="image_capture",
                    status="success",
                    message=msg,
                    data=response_data,
                )
                span.update(output=result)
                return result

            except Exception as e:
                result = create_response(
                    type="image_capture",
                    status="error",
                    message=f"Gagal mengupload gambar ke Supabase: {str(e)}",
                )
                span.update(output=result)
                return result
            except Exception as e:
                raise e
