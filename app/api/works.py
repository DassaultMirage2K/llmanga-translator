"""Works & images CRUD + multipart upload."""

import io
import re
import zipfile
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from PIL import Image as PILImage

from ..schemas import (
    WorkCreate, WorkUpdate, ImageUpdate, ReorderRequest,
)
from . import get_state

router = APIRouter()

_EXT_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".tiff": "image/tiff",
}


def _safe_filename(original: str) -> str:
    """Reduce an uploaded filename to a safe on-disk name, keeping extension."""
    p = Path(original or "image")
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", p.stem).strip("._") or "image"
    ext = p.suffix.lower()
    return f"{stem}{ext}"


@router.post("/works", status_code=201)
async def create_work(body: WorkCreate, request: Request):
    state = get_state(request)
    work_id = state.db.create_work(body.name, body.description)
    work = state.db.get_work(work_id)
    return work


@router.get("/works")
async def list_works(request: Request):
    state = get_state(request)
    return state.db.list_works()


@router.get("/works/{work_id}")
async def get_work(work_id: int, request: Request):
    state = get_state(request)
    work = state.db.get_work(work_id)
    if not work:
        raise HTTPException(404, "Work not found")
    return work


@router.put("/works/{work_id}")
async def update_work(work_id: int, body: WorkUpdate, request: Request):
    state = get_state(request)
    existing = state.db.get_work(work_id)
    if not existing:
        raise HTTPException(404, "Work not found")
    name = body.name if body.name is not None else existing["name"]
    description = (body.description if body.description is not None
                   else existing["description"])
    state.db.update_work(work_id, name, description)
    return state.db.get_work(work_id)


@router.delete("/works/{work_id}", status_code=204)
async def delete_work(work_id: int, request: Request):
    state = get_state(request)
    if not state.db.get_work(work_id):
        raise HTTPException(404, "Work not found")
    # Remove stored files first (best-effort), then cascade-delete rows.
    for img in state.db.list_images(work_id):
        state.storage.delete_file(img["file_path"])
    state.storage.delete_work_dir(work_id)
    state.db.delete_work(work_id)


# ---- images -----------------------------------------------------------------
@router.post("/works/{work_id}/images", status_code=201)
async def upload_images(
    work_id: int, request: Request, files: List[UploadFile] = File(...),
):
    state = get_state(request)
    if not state.db.get_work(work_id):
        raise HTTPException(404, "Work not found")

    created = []
    for f in files:
        data = await f.read()
        original_name = Path(f.filename or "image").name
        ext = Path(original_name).suffix.lower()

        if ext == ".zip":
            # Import images from the archive (root, or a single top-level folder).
            for entry_name, entry_data in _iter_zip_images(data):
                created.append(_store_image(state, work_id, entry_name, entry_data))
        else:
            if ext not in _EXT_MIME:
                raise HTTPException(400, f"Unsupported image type: {ext}")
            created.append(_store_image(state, work_id, original_name, data))

    return created


def _store_image(state, work_id: int, original_name: str, data: bytes):
    """Persist one image (from a loose upload or extracted from a zip)."""
    ext = Path(original_name).suffix.lower()
    rel_path = state.storage.save_upload(work_id, _safe_filename(original_name), data)
    abs_path = state.storage.get_file_path(rel_path)
    stored_filename = Path(rel_path).name

    width = height = None
    try:
        with PILImage.open(abs_path) as im:
            width, height = im.size
    except Exception:
        pass  # dimensions are optional; don't fail the upload

    sort_order = state.db.next_sort_order(work_id)
    image_id = state.db.add_image(
        work_id=work_id,
        filename=stored_filename,
        original_name=original_name,
        sort_order=sort_order,
        file_path=rel_path,
        width=width,
        height=height,
        mime_type=_EXT_MIME[ext],
        file_size=len(data),
    )
    return state.db.get_image(image_id)


def _natural_key(name: str):
    """Case-insensitive natural-order sort key (so page2 sorts before page10)."""
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", name.lower())]


def _iter_zip_images(data: bytes):
    """Yield (original_name, file_bytes) for each image to import from a zip.

    Imports the images at the archive root; if there are none at the root and all
    images live under exactly one top-level folder, imports those instead."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, ValueError) as e:
        raise HTTPException(400, f"Not a valid zip archive: {e}")

    with zf:
        entries = []  # (name_for_read, normalized_path, basename)
        for n in zf.namelist():
            if n.endswith("/"):
                continue  # directory entry
            nn = n.replace("\\", "/")
            if Path(nn).suffix.lower() in _EXT_MIME:
                entries.append((n, nn, Path(nn).name))

        if not entries:
            raise HTTPException(400, "No images found in archive")

        top_level = [e for e in entries if "/" not in e[1]]
        if top_level:
            chosen = top_level
        else:
            folders = {}
            for orig, nn, base in entries:
                folders.setdefault(nn.split("/", 1)[0], []).append((orig, nn, base))
            if len(folders) != 1:
                raise HTTPException(400,
                                    "Archive must contain images at the top level or in a single folder")
            chosen = list(folders.values())[0]

        for orig, nn, base in sorted(chosen, key=lambda e: _natural_key(e[1])):
            yield base, zf.read(orig)


@router.get("/works/{work_id}/images")
async def list_images(work_id: int, request: Request):
    state = get_state(request)
    if not state.db.get_work(work_id):
        raise HTTPException(404, "Work not found")
    return state.db.list_images(work_id)


@router.put("/works/{work_id}/images/reorder")
async def reorder_images(work_id: int, body: ReorderRequest, request: Request):
    state = get_state(request)
    if not state.db.get_work(work_id):
        raise HTTPException(404, "Work not found")
    # Only accept ids that actually belong to this work.
    existing_ids = {i["id"] for i in state.db.list_images(work_id)}
    ordered = [i for i in body.ordered_ids if i in existing_ids]
    state.db.reorder_images(work_id, ordered)
    return state.db.list_images(work_id)


@router.put("/images/{image_id}")
async def update_image(image_id: int, body: ImageUpdate, request: Request):
    state = get_state(request)
    if not state.db.get_image(image_id):
        raise HTTPException(404, "Image not found")
    if body.context is not None:
        state.db.update_image_context(image_id, body.context)
    return state.db.get_image(image_id)


@router.delete("/images/{image_id}", status_code=204)
async def delete_image(image_id: int, request: Request):
    state = get_state(request)
    img = state.db.get_image(image_id)
    if not img:
        raise HTTPException(404, "Image not found")
    state.storage.delete_file(img["file_path"])
    state.db.delete_image(image_id)


@router.get("/images/{image_id}/file")
async def get_image_file(image_id: int, request: Request):
    from fastapi.responses import FileResponse

    state = get_state(request)
    img = state.db.get_image(image_id)
    if not img:
        raise HTTPException(404, "Image not found")
    abs_path = state.storage.get_file_path(img["file_path"])
    if not abs_path.exists():
        raise HTTPException(410, "File missing on disk")
    return FileResponse(str(abs_path), media_type=img.get("mime_type") or "image/jpeg")
