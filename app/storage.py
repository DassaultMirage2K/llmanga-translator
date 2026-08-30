"""Local filesystem storage manager for uploaded images."""

import shutil
from pathlib import Path
from typing import Optional

from .config import Config


class StorageManager:
    def __init__(self, base_path: str | Path = Config.DATA_DIR):
        self.base_path = Path(base_path)
        self.uploads_path = self.base_path / "uploads"
        self.results_path = self.base_path / "results"
        self.uploads_path.mkdir(parents=True, exist_ok=True)
        self.results_path.mkdir(parents=True, exist_ok=True)

    def save_upload(self, work_id: int, filename: str, content: bytes) -> str:
        """Save uploaded file under uploads/<work_id>/ and return path relative to base."""
        work_dir = self.uploads_path / str(work_id)
        work_dir.mkdir(parents=True, exist_ok=True)
        # Avoid overwriting existing files by appending a counter if needed.
        target = work_dir / filename
        stem, suffix = Path(filename).stem, Path(filename).suffix
        n = 1
        while target.exists():
            target = work_dir / f"{stem}_{n}{suffix}"
            n += 1
        target.write_bytes(content)
        return str(target.relative_to(self.base_path))

    def get_file_path(self, relative_path: str) -> Path:
        """Resolve a stored relative path to an absolute path (guarded)."""
        p = self.base_path / relative_path
        # Guard against path traversal.
        if not p.resolve().is_relative_to(self.base_path.resolve()):
            raise ValueError("Invalid file path")
        return p

    def delete_file(self, relative_path: str) -> None:
        """Delete a single stored file."""
        try:
            p = self.get_file_path(relative_path)
            if p.exists():
                p.unlink()
        except (ValueError, OSError):
            pass

    def delete_work_dir(self, work_id: int) -> None:
        """Remove the whole uploads/<work_id> directory."""
        work_dir = self.uploads_path / str(work_id)
        if work_dir.exists():
            shutil.rmtree(work_dir)
