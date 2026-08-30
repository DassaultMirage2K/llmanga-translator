"""In-process background job runner + per-work WebSocket fan-out.

Translation jobs run as asyncio tasks inside the FastAPI process. The blocking
LLM work (synchronous OpenAI client) is offloaded to a thread via
``asyncio.to_thread`` so the event loop stays responsive and WebSockets keep
streaming progress while pages translate.
"""

import asyncio
from typing import Any, Dict, List, Optional

from .service import MangaTranslator

from .database import Database
from .storage import StorageManager


class WebSocketManager:
    """Tracks live WebSocket connections grouped by work id."""

    def __init__(self):
        self.connections: Dict[int, set] = {}

    async def connect(self, work_id: int, websocket) -> None:
        await websocket.accept()
        self.connections.setdefault(work_id, set()).add(websocket)

    async def disconnect(self, work_id: int, websocket) -> None:
        conns = self.connections.get(work_id)
        if conns is not None:
            conns.discard(websocket)
            if not conns:
                self.connections.pop(work_id, None)

    async def send_to_work(self, work_id: int, message: Dict[str, Any]) -> None:
        for ws in list(self.connections.get(work_id, ())):
            try:
                await ws.send_json(message)
            except Exception:
                self.connections.get(work_id, set()).discard(ws)


class JobRunner:
    def __init__(self, db: Database, storage: StorageManager, ws: WebSocketManager):
        self.db = db
        self.storage = storage
        self.ws = ws
        self.current_jobs: Dict[int, asyncio.Task] = {}

    # ---- scheduling --------------------------------------------------------
    def schedule(self, job_id: int) -> None:
        """Run a job in the background on the current event loop."""
        task = asyncio.create_task(self.process_job(job_id))
        self.current_jobs[job_id] = task
        task.add_done_callback(lambda _t, j=job_id: self.current_jobs.pop(j, None))

    def active_job_for_work(self, work_id: int) -> Optional[Dict[str, Any]]:
        """Return a pending/processing job for this work, if any."""
        rows = self.db.query_all(
            "SELECT * FROM translation_jobs WHERE work_id = ? AND status IN ('pending','processing') ORDER BY id DESC",
            (work_id,),
        )
        return rows[0] if rows else None

    # ---- cancellation -------------------------------------------------------
    def cancel_all_jobs(self) -> None:
        """Cancel every in-flight job task (called on app shutdown)."""
        for job_id, task in list(self.current_jobs.items()):
            if not task.done():
                task.cancel()
                self.db.update_job_status(job_id, "cancelled", error_message="Server shutting down")
        self.current_jobs.clear()

    # ---- job body ----------------------------------------------------------
    async def process_job(self, job_id: int) -> None:
        job = self.db.get_job(job_id)
        if not job:
            return
        work_id = job["work_id"]
        images: List[Dict[str, Any]] = self.db.list_images(work_id)

        settings = job.get("settings") or {}
        target_language = settings.get("target_language", "English")
        system_prompt = settings.get("system_prompt", "")
        glossary = settings.get("glossary")
        resize_enabled = bool(settings.get("image_resize_enabled", True))
        try:
            resize_px = int(settings.get("image_resize_px", 1048))
        except (TypeError, ValueError):
            resize_px = 1048

        translator = MangaTranslator(
            target_language=target_language,
            extra_system_prompt=system_prompt,
            resize_max_side=resize_px if resize_enabled else None,
        )

        translator.begin_chapter(glossary=glossary)

        self.db.update_job_status(job_id, "processing", 0)

        # Shared with the heartbeat below so it can re-broadcast fresh state.
        status_state: Dict[str, Any] = {
            "progress": 0,
            "current_image_id": None,
            "current_image_name": None,
        }

        async def heartbeat() -> None:
            # Re-broadcast current job state every ~10s so clients always get
            # fresh info even while a single page's LLM call is in flight.
            while True:
                await asyncio.sleep(10)
                await self.ws.send_to_work(work_id, {
                    "type": "job_status",
                    "data": {
                        "job_id": job_id,
                        "status": "processing",
                        "progress": status_state["progress"],
                        "current_image_id": status_state["current_image_id"],
                        "current_image_name": status_state["current_image_name"],
                    },
                })

        heartbeat_task = asyncio.create_task(heartbeat())
        try:
            for index, image in enumerate(images):
                progress = int((index / len(images)) * 100) if images else 0
                status_state.update({
                    "progress": progress,
                    "current_image_id": image["id"],
                    "current_image_name": image["original_name"],
                })
                self.db.update_job_status(
                    job_id, "processing", progress, current_image_id=image["id"]
                )
                await self.ws.send_to_work(work_id, {
                    "type": "job_status",
                    "data": {
                        "job_id": job_id,
                        "status": "processing",
                        "progress": progress,
                        "current_image_id": image["id"],
                        "current_image_name": image["original_name"],
                    },
                })

                try:
                    abs_path = str(self.storage.get_file_path(image["file_path"]))
                    result = await asyncio.to_thread(
                        self._process_single_image,
                        translator,
                        abs_path,
                        image.get("context") or "",
                    )

                    text_regions = result["text_regions"]
                    new_context = result["context_after"]

                    self.db.save_result(image["id"], job_id, text_regions, new_context)

                    await self.ws.send_to_work(work_id, {
                        "type": "translation_complete",
                        "data": {
                            "image_id": image["id"],
                            "job_id": job_id,
                            "result": text_regions,
                            "context_after": new_context,
                        },
                    })

                except Exception as e:  # keep going; record the failure and continue
                    self.db.update_job_status(job_id, "processing", error_message=str(e))
                    await self.ws.send_to_work(work_id, {
                        "type": "error",
                        "data": {"job_id": job_id, "image_id": image["id"], "error": str(e)},
                    })

            # Mark complete even if individual pages failed (they're recorded).
            self.db.update_job_status(job_id, "completed", 100)
            await self.ws.send_to_work(work_id, {
                "type": "job_complete",
                "data": {"job_id": job_id, "status": "completed", "total_images": len(images)},
            })
        finally:
            heartbeat_task.cancel()

    # ---- blocking per-image work (runs in a thread) ------------------------
    @staticmethod
    def _process_single_image(
        translator: MangaTranslator,
        abs_path: str,
        user_context: str,
    ) -> Dict[str, Any]:
        """Translate one page in the shared conversation. Blocking; called via asyncio.to_thread."""
        return translator.process_page(abs_path, user_context=user_context)
