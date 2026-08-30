"""Application configuration."""

import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env from the project root (one level above this app/ package) so the
# values below are populated at import time, regardless of the working directory.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env")


class Config:
    # Data directory (auto-created on first run)
    DATA_DIR = Path(os.getenv("MANGA_DATA_DIR", "data"))
    DATABASE_PATH = DATA_DIR / "manga.db"
    UPLOADS_DIR = DATA_DIR / "uploads"
    RESULTS_DIR = DATA_DIR / "results"

    # Upload limits
    MAX_UPLOAD_SIZE = 50 * 1024 * 1024  # 50MB
    ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff"}

    # LLM Configuration (overridable via env)
    OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "http://localhost:11434/v1")
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "sk-no-key-required")
    VISION_MODEL = os.getenv("VISION_MODEL", "qwen3.8-27b")
