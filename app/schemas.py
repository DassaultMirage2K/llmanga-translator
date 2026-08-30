"""Pydantic request/response models."""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ---- Works ------------------------------------------------------------------
class WorkCreate(BaseModel):
    name: str = Field(..., min_length=1)
    description: Optional[str] = None


class WorkUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None


class WorkOut(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    image_count: Optional[int] = 0


# ---- Images -----------------------------------------------------------------
class ImageUpdate(BaseModel):
    context: Optional[str] = None


class ReorderRequest(BaseModel):
    # Ordered list of image ids in the desired order.
    ordered_ids: List[int]


class ImageOut(BaseModel):
    id: int
    work_id: int
    filename: str
    original_name: str
    sort_order: int
    context: Optional[str] = ""
    width: Optional[int] = None
    height: Optional[int] = None
    mime_type: Optional[str] = None
    file_size: Optional[int] = None


# ---- Translation ------------------------------------------------------------
class TranslateRequest(BaseModel):
    target_language: str = "English"
    system_prompt: str = ""  # optional extra instructions; prepended to the built-in prompt
    image_resize_enabled: bool = True
    image_resize_px: int = Field(1048, ge=64, le=16384)  # biggest side, when enabled
    glossary: Optional[Dict[str, str]] = None


class JobOut(BaseModel):
    id: int
    work_id: int
    status: str
    progress: int = 0
    current_image_id: Optional[int] = None
    error_message: Optional[str] = None
    settings: Optional[Dict[str, Any]] = None
    created_at: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None


class ResultOut(BaseModel):
    image_id: int
    job_id: int
    result_data: Any
    context_after: Optional[str] = None
