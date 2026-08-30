"""Translation job endpoints."""

from fastapi import APIRouter, HTTPException, Request

from ..schemas import TranslateRequest
from . import get_state

router = APIRouter()


@router.post("/works/{work_id}/translate", status_code=202)
async def start_translation(work_id: int, body: TranslateRequest, request: Request):
    state = get_state(request)
    work = state.db.get_work(work_id)
    if not work:
        raise HTTPException(404, "Work not found")

    images = state.db.list_images(work_id)
    if not images:
        raise HTTPException(400, "Work has no images to translate")

    # One active job per work.
    if state.jobs.active_job_for_work(work_id):
        raise HTTPException(409, "A translation is already running for this work")

    settings = {
        "target_language": body.target_language,
        "system_prompt": body.system_prompt,
        "image_resize_enabled": body.image_resize_enabled,
        "image_resize_px": body.image_resize_px,
        "glossary": body.glossary,
    }
    job_id = state.db.create_job(work_id, settings)
    state.jobs.schedule(job_id)  # runs in background on the event loop
    return state.db.get_job(job_id)


@router.get("/works/{work_id}/active-job")
async def get_active_job(work_id: int, request: Request):
    """Return the work's in-flight job (pending/processing), or null.

    Used by the frontend to restore progress state after a page reload:
    without this it would wait for the next live WebSocket event (i.e. until
    the current LLM call finishes) before showing anything.
    """
    state = get_state(request)
    if not state.db.get_work(work_id):
        raise HTTPException(404, "Work not found")
    job = state.jobs.active_job_for_work(work_id)
    if not job:
        return None
    # A row can be stale after a server restart/crash: the DB says processing
    # but no live task is behind it. Mark it failed so clients recover instead
    # of polling a dead job forever.
    task = state.jobs.current_jobs.get(job["id"])
    if task is None or task.done():
        state.db.update_job_status(
            job["id"], "failed", error_message="Interrupted by server restart"
        )
        return None
    return job


@router.get("/works/{work_id}/results")
async def get_work_results(work_id: int, request: Request):
    """Image ids in this work that already have a translation result.

    Lets the frontend restore per-page 'done' marks after a reload without
    fetching every page's full result payload.
    """
    state = get_state(request)
    if not state.db.get_work(work_id):
        raise HTTPException(404, "Work not found")
    rows = state.db.query_all(
        """SELECT tr.image_id FROM translation_results tr
           JOIN images i ON i.id = tr.image_id
           WHERE i.work_id = ?""",
        (work_id,),
    )
    return [r["image_id"] for r in rows]


@router.get("/jobs/{job_id}")
async def get_job(job_id: int, request: Request):
    state = get_state(request)
    job = state.db.get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return job


@router.get("/jobs/{job_id}/results")
async def get_job_results(job_id: int, request: Request):
    state = get_state(request)
    if not state.db.get_job(job_id):
        raise HTTPException(404, "Job not found")
    return state.db.get_job_results(job_id)


@router.get("/images/{image_id}/result")
async def get_image_result(image_id: int, request: Request):
    state = get_state(request)
    if not state.db.get_image(image_id):
        raise HTTPException(404, "Image not found")
    result = state.db.get_result_for_image(image_id)
    if not result:
        raise HTTPException(404, "No translation result yet")
    return result
