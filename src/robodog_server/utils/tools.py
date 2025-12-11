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
    Memerintahkan robot untuk bergerak secara manual (open-loop).
    Cocok untuk pergerakan sederhana tanpa planning jalur.
    
    :param linear_speed: Kecepatan maju/mundur (m/s). Positif untuk maju.
    :param angular_speed: Kecepatan rotasi (rad/s). Positif untuk kiri (counter-clockwise).
    :param duration: Berapa lama robot harus bergerak (detik).
    """
    controller = get_controller_node()

    if controller is None:
        return "Gagal: Controller ROS Noetic belum siap atau roscore tidak terdeteksi."

    # Memanggil metode async pada controller Noetic
    controller.move_async(linear_speed, angular_speed, duration)
    
    return (
        f"Perintah gerakan dikirim: Maju={linear_speed}m/s, "
        f"Belok={angular_speed}rad/s selama {duration}s."
    )

@mcp.tool
def navigate_to_waypoint(x: float, y: float, theta: float = 0.0) -> str:
    """
    Mengirimkan tujuan navigasi (waypoint) ke stack move_base (ROS 1).
    Robot akan merencanakan jalur (path planning) untuk menghindari rintangan.
    
    :param x: Koordinat X target pada peta (meter).
    :param y: Koordinat Y target pada peta (meter).
    :param theta: Sudut orientasi akhir (Yaw) dalam radian (misal: 1.57 untuk 90 derajat).
    """
    controller = get_controller_node()

    if controller is None:
        return "Gagal: Controller ROS Noetic tidak tersedia."

    # Mengirim goal ke Action Server move_base
    goal_sent = controller.send_nav_goal_async(x, y, theta)
    
    if goal_sent:
        return (
            f"Goal navigasi berhasil dikirim ke koordinat ({x:.2f}, {y:.2f}). "
            "Robot sedang merencanakan jalur melalui move_base."
        )
    else:
        return "Gagal mengirim goal. Pastikan node 'move_base' di robot sudah berjalan."
    

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