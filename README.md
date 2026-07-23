# Robodog MCP Server (`robodog-mcp-server`)

FastMCP Server yang menyediakan antarmuka **Model Context Protocol (MCP)** untuk sistem robot quadruped **Robodog**. Server ini mengekspos berbagai tool terstandarisasi untuk eksekusi pergerakan robot (via ROS 1 / Noetic), pencarian data spasial & waypoint dari database Supabase, pembacaan dokumen SOP (*Standard Operating Procedure*), serta analisis inspeksi visual menggunakan Vision AI (Gemini / Qwen / GPT-4o).

---

## 🚀 Fitur Utama & Daftar MCP Tools

Server mengekspos tool MCP berikut yang dapat dipanggil oleh LLM Client (seperti `robodog-mcp-client`):

### 🤖 Robot Control & Actions
- **`async_move`**: Mengontrol pergerakan manual robot secara open-loop (`linear_speed`, `angular_speed`, `duration`).
- **`async_navigate_to_waypoint`**: Mengirimkan tujuan navigasi otonom ke stack ROS `move_base` (`x`, `y`, `theta_deg`).
- **`toggle_sit_stand`**: Mengubah posisi robot antara duduk (*sit*) dan berdiri (*stand*).
- **`say_hello`**: Memerintahkan robot melakukan aksi melambaikan tangan.
- **`look_up_down`**: Mengatur sudut *pitch* kamera/badan robot (menunduk/menengadah) dengan durasi tertentu.

### 🗄️ Database & Spatial Queries
- **`get_object_waypoints`**: Mencari objek inspeksi dan rute navigasinya. Mengembalikan hirarki spasial lengkap: `Map -> Location Path -> Object -> Waypoint` beserta parameter kamera (pan, tilt, zoom).

### 📄 Document & SOP Retrieval
- **`get_sop_file`**: Mencari dan mengunduh URL dokumen SOP berdasarkan nama objek atau kata kunci.

### 👁️ Vision Inspection Engine
- Analisis deteksi objek dan pemenuhan kriteria SOP pada gambar inspeksi menggunakan:
  - **Google Gemini SDK** (Native Structured Output via `InspectionResult` / `ObjectDetectionResult`)
  - **OpenAI-Compatible Vision API** (Qwen 3.5 / GPT-4o)

---

## 📁 Struktur Proyek

```text
robodog-mcp-server/
├── utils/
│   ├── controller.py       # Controller ROS Noetic & publisher topic CMD/Nav
│   ├── ros_manager.py      # Pengelola thread eksekusi ROS node
│   └── tools.py            # Registrasi seluruh tool FastMCP, logika DB & Vision AI
├── src/
│   └── robodog_server/     # Package source root
├── main.py                 # Titik masuk utama HTTP FastMCP server (Port 8001)
├── pyproject.toml          # Manifest proyek Python & UV package manager
├── requirements.txt        # Dependensi standar Python
├── pdf_doc.txt             # Data rujukan dokumen pendukung
└── README.md
```

---

## 🛠️ Persyaratan Sistem

- **Python**: `>= 3.10`
- **Package Manager**: `uv` (Direkomendasikan) atau `pip`
- **ROS Environment (Opsional)**: ROS 1 Noetic (Untuk mode kontrol robot fisik/simulasi Gazebo). Tersedia mode **SIMULATED / NO-ROS** untuk pengembangan tanpa dependensi ROS.

---

## ⚙️ Variabel Lingkungan (`.env`)

Buat file `.env` di root direktori `robodog-mcp-server/`:

```env
# FastMCP Server Host & Port
HOST=0.0.0.0
PORT=8001

# Supabase Credentials
SUPABASE_URL=https://your-supabase-project.supabase.co
SUPABASE_KEY=your_supabase_anon_or_service_key

# AI Vision Providers
OPENAI_API_KEY=your_openai_api_key
OLLAMA_HOST=http://localhost:11434

# Langfuse Observability
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
LANGFUSE_HOST=https://cloud.langfuse.com
```

---

## 💻 Cara Memulai

### Menggunakan `uv` (Rekomendasi)

```bash
# Synchronize environment & dependensi
uv sync

# Jalankan server
uv run robodog-server
# atau
python main.py
```

### Menggunakan `pip` Standar

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

python main.py
```

Server FastMCP HTTP akan berjalan di `http://0.0.0.0:8001`.

---

## 🧪 Mode Pengujian (NO-ROS Mode)

Jika dijalankan di luar environment ROS, server secara otomatis berada dalam **NO-ROS Testing Mode**. Perintah kontrol robot akan disimulasikan dan mengembalikan response JSON sukses berlabel `[SIMULATED]` tanpa menghentikan sistem.
