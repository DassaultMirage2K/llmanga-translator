"""Shared pytest fixtures for the llmanga-translator test suite.

Isolation strategy
------------------
The app reads ``MANGA_DATA_DIR`` at import time (see ``app/config.py``). We set
it to a fresh temp directory *before* importing anything from ``app``, so the
whole database + uploads live in a throwaway location that is deleted when the
session ends. That means tests never touch real user data and clean up after
themselves automatically; individual works are additionally removed by the
``make_work`` fixture for hygiene.

No test needs network/LLM access: translation jobs run against a fake
``process_job`` (see ``fake_process``), and images are generated in memory with
PIL rather than relying on committed sample files.
"""
import io
import os
import shutil
import sys
import tempfile
from pathlib import Path

# --- make the project root importable regardless of how pytest is invoked ----
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# --- point the app at a temp data dir BEFORE importing anything from `app` ----
_TMP_ROOT = Path(tempfile.mkdtemp(prefix="manga_test_"))
os.environ["MANGA_DATA_DIR"] = str(_TMP_ROOT)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image as _PILImage  # noqa: E402
from app.main import create_app  # noqa: E402


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def make_image_bytes(fmt="JPEG", size=(16, 16), color=(200, 30, 30)) -> bytes:
    """Generate a tiny valid image in memory (no dependency on sample files)."""
    buf = io.BytesIO()
    _PILImage.new("RGB", size, color).save(buf, format=fmt)
    return buf.getvalue()


# --------------------------------------------------------------------------- #
# session fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="session")
def client():
    """One FastAPI app + TestClient for the whole session (isolated data dir)."""
    app = create_app()
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="session")
def app_state(client):
    """The AppState singletons (db, storage, ws, jobs) behind the test client."""
    return client.app.state.app_state


@pytest.fixture(scope="session", autouse=True)
def _cleanup_tmp_dir():
    """Remove the temp data dir when the session ends (suite cleans up after itself)."""
    yield
    shutil.rmtree(_TMP_ROOT, ignore_errors=True)


# --------------------------------------------------------------------------- #
# function fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture
def make_work(client):
    """Factory: create a work, yield its id, delete it on teardown.

    Deleting the work cascades to its images, jobs and results (FK ON DELETE
    CASCADE), so anything created under the work is cleaned up automatically.
    """
    created = []

    def _make(name="test-work", description=None):
        r = client.post("/api/works", json={"name": name, "description": description})
        assert r.status_code == 201, r.text
        work_id = r.json()["id"]
        created.append(work_id)
        return work_id

    yield _make
    for wid in reversed(created):
        client.delete(f"/api/works/{wid}")


@pytest.fixture
def upload_image(client):
    """Upload one generated image to a work; returns the stored image dict."""

    def _upload(work_id, name="page.jpg", fmt="JPEG"):
        data = make_image_bytes(fmt)
        r = client.post(
            f"/api/works/{work_id}/images",
            files={"files": (name, io.BytesIO(data), "image/jpeg")},
        )
        assert r.status_code == 201, r.text
        return r.json()[-1]

    yield _upload


@pytest.fixture
def fake_process(app_state):
    """Shadow JobRunner.process_job so translation jobs run without an LLM.

    The real method is a bound classmethod; assigning an instance attribute
    shadows it for the duration of the test, then we delete it to fall back to
    the original. No network/LLM calls happen.
    """
    import asyncio

    async def _fast(job_id):
        app_state.db.update_job_status(job_id, "processing", 0)
        await asyncio.sleep(0)  # let the event loop turn over once
        app_state.db.update_job_status(job_id, "completed", 100)

    app_state.jobs.process_job = _fast
    yield
    del app_state.jobs.process_job  # restore the class method


@pytest.fixture
def sample_image_file(tmp_path):
    """A real image file on disk (for service-level tests that read/encode it)."""
    p = tmp_path / "sample.jpg"
    p.write_bytes(make_image_bytes("JPEG"))
    return str(p)
