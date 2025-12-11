import os
from fastmcp import FastMCP
from supabase import create_client, Client
from typing import List
from io import BytesIO
from pypdf import PdfReader
from dotenv import load_dotenv

load_dotenv()

def main():
    SUPABASE_URL: str = os.getenv("SUPABASE_URL")
    SUPABASE_KEY: str = os.getenv("SUPABASE_KEY")
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

    BUCKET_NAME = "robotics-prata"       
    FOLDER_NAME = "sop"   

    print(SUPABASE_KEY)
    print(SUPABASE_URL)
    response = supabase.storage.list_buckets()
    print(response)
    result = supabase.storage.from_(BUCKET_NAME).list(
        FOLDER_NAME,
        {
        "limit": 200,
        "offset": 0,
        "sortBy": {"column": "name", "order": "asc"},
        }
    )

    print(f"responses: {result}")
