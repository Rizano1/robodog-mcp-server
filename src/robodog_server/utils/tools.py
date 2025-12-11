import os
import io
from fastmcp import FastMCP
from supabase import create_client, Client
from typing import List
from io import BytesIO
from pypdf import PdfReader
from dotenv import load_dotenv
from .ros_manager import get_controller_node 
from docx import Document

load_dotenv()

mcp = FastMCP("robot_api_mcp")

SUPABASE_URL: str = os.getenv("SUPABASE_URL")
SUPABASE_KEY: str = os.getenv("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

BUCKET_NAME = "robotics-prata"       
FOLDER_NAME = "sop"   

@mcp.tool
def move(linear_speed: float = 0.0, angular_speed: float = 0.0, duration: float = 5.0) -> str:
    """
    Memerintahkan TurtleBot untuk bergerak (linear) dan/atau berputar (angular) 
    selama durasi tertentu.
    
    :param linear_speed: Kecepatan linear (m/s). Positif untuk maju, negatif untuk mundur. Default: 0.0.
    :param angular_speed: Kecepatan angular (rad/s). Positif untuk berputar ke kiri, negatif untuk ke kanan. Default: 0.0.
    :param duration: Durasi pergerakan (detik). Default: 5.0.
    :return: Status perintah.
    """
    controller = get_controller_node()

    if controller is None:
        return "ROS2 Controller Node is not initialized. Please ensure ROS 2 is running."

    controller.move_async(linear_speed, angular_speed, duration)
    
    return (
        f"Moving: Linear={linear_speed} m/s, Angular={angular_speed} rad/s "
        f"for {duration} seconds."
    )

@mcp.tool
def navigate_to_waypoint(x: float, y: float, theta: float = 0.0) -> str:
    """
    Mengirim tujuan navigasi (waypoint) ke stack Navigasi ROS 2 (Nav2).
    Robot akan mencari jalur dan bergerak ke lokasi tersebut.
    
    :param x: Koordinat X tujuan (meter) di frame 'map'. (Wajib)
    :param y: Koordinat Y tujuan (meter) di frame 'map'. (Wajib)
    :param theta: Orientasi akhir yang diinginkan (Yaw) dalam radian. Default: 0.0.
    :return: Status perintah navigasi.
    """
    controller = get_controller_node()

    if controller is None:
        return "ROS2 Controller Node is not initialized. Please ensure ROS 2 is running."

    goal_sent = controller.send_nav_goal_async(x, y, theta)
    
    if goal_sent:
        return (
            f"Navigation goal sent to coordinates ({x:.2f}m, {y:.2f}m) "
            f"with orientation {theta:.2f} rad. Robot is now navigating."
        )
    else:
        return "Failed to send navigation goal. The Nav2 action server 'navigate_to_pose' is not available."
    

def extract_pdf_text(pdf_bytes: bytes) -> str:
    try:
        reader = PdfReader(BytesIO(pdf_bytes))
        text = ""
        for page in reader.pages:
            text += page.extract_text() + "\n"
        return text
    except Exception as e:
        return f"[PDF_EXTRACT_ERROR] {e}"


@mcp.tool
def list_sop_files() -> List[object]:
    """
    Mengambil daftar file dalam bucket Supabase 'SOP'.
    """
    result = supabase.storage.from_(BUCKET_NAME).list(
        FOLDER_NAME,
        {
        "limit": 200,
        "offset": 0,
        "sortBy": {"column": "name", "order": "asc"},
        }
    )

    print(f"responses: {result}")

    if not isinstance(result, list):
        return ["Error: Could not fetch list of files"]

    return result


@mcp.tool
def get_sop_file(file_name: str) -> str:
    """
    Mengunduh dan mengembalikan isi file SOP dalam bentuk teks.
    Mendukung TXT, MD, PDF, DOCX.
    """

    file_path = f"{FOLDER_NAME}/{file_name}" if FOLDER_NAME else file_name

    try:
        file_bytes = supabase.storage.from_(BUCKET_NAME).download(file_path)
    except Exception as e:
        return f"Error downloading file: {e}"

    if file_bytes is None:
        return "Error: File not found."

    lower = file_name.lower()

    if lower.endswith(".txt") or lower.endswith(".md"):
        try:
            return file_bytes.decode("utf-8", errors="ignore")
        except Exception as e:
            return f"[TXT_DECODE_ERROR] {e}"

    if lower.endswith(".pdf"):
        return extract_pdf_text(file_bytes)

    if lower.endswith(".docx"):
        try:
            file_stream = io.BytesIO(file_bytes)
            doc = Document(file_stream)

            # Gabungkan semua paragraf jadi teks
            paragraphs = [p.text for p in doc.paragraphs]
            return "\n".join(paragraphs)

        except Exception as e:
            return f"[DOCX_READ_ERROR] {e}"

    return "Unsupported file type. Only .txt, .md, .pdf, .docx are allowed."