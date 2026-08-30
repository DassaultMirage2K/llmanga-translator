"""Global app settings endpoints (key -> value strings).

The store is intentionally generic: any string key can be added later without
schema or endpoint changes. The frontend renders one section per known key;
unknown keys are simply ignored there until a UI item exists for them.
"""

from typing import Dict

from fastapi import APIRouter, Request

from . import get_state

router = APIRouter()


@router.get("/settings")
async def get_settings(request: Request):
    """All stored settings as a key -> value object (empty {} on first run)."""
    state = get_state(request)
    return state.db.get_all_settings()


@router.put("/settings")
async def update_settings(body: Dict[str, str], request: Request):
    """Upsert the given keys; returns the full settings map afterwards."""
    state = get_state(request)
    for key, value in body.items():
        state.db.set_setting(key, value)
    return state.db.get_all_settings()
