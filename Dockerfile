# 1. Gunakan Base Image ROS Noetic
FROM osrf/ros:noetic-desktop-full

ENV LANG=C.UTF-8
ENV LC_ALL=C.UTF-8
ENV DEBIAN_FRONTEND=noninteractive

# 2. Install Dependensi Awal & Setup PPA
# Kita install software-properties-common dulu agar command add-apt-repository ada
RUN apt-get update && apt-get install -y \
    software-properties-common \
    curl \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# 3. Tambahkan PPA Deadsnakes
RUN add-apt-repository -y ppa:deadsnakes/ppa

# 4. Install Python 3.10 (Komponen Terpisah)
# PERBAIKAN: Gunakan nama paket eksplisit, BUKAN python3.10-full
RUN apt-get update && apt-get install -y \
    python3.10 \
    python3.10-dev \
    python3.10-venv \
    && rm -rf /var/lib/apt/lists/*

# 5. Install UV (Package Manager)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# 6. Konfigurasi Project
WORKDIR /app
COPY . /app

# --- KONFIGURASI ENV ---
# Trik agar Python 3.10 bisa melihat library ROS (rospy, geometry_msgs, dll)
ENV PYTHONPATH=/opt/ros/noetic/lib/python3/dist-packages:${PYTHONPATH}
# Set UV untuk menggunakan Python 3.10
ENV UV_PYTHON=python3.10

# 7. Install Dependensi Project
RUN uv pip install --system .

# 8. Setup Bash (Agar env variable ROS termuat)
RUN echo "source /opt/ros/noetic/setup.bash" >> ~/.bashrc

# Pastikan semua dependensi terinstal
RUN uv sync

# 8. Jalankan Aplikasi menggunakan UV dengan Python 3.10
CMD ["uv", "run", "--python", "python3.10", "robodog-server"]

