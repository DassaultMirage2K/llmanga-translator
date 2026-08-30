"""Translation job lifecycle + reload-restore endpoints.

No real LLM calls: the happy path runs against ``fake_process``; the restore /
409 cases build DB rows directly and (for the "live" case) a sentinel task that
only needs to answer ``.done() -> False``. Everything is deterministic.
"""
import time


class _LiveTask:
    """Stand-in for an in-flight asyncio.Task; active-job only calls .done()."""

    def done(self):
        return False


def test_start_translation_requires_images(client, make_work):
    wid = make_work("no-images")
    r = client.post(f"/api/works/{wid}/translate", json={"target_language": "English"})
    assert r.status_code == 400


def test_start_translation_unknown_work_404(client):
    r = client.post("/api/works/999999/translate", json={})
    assert r.status_code == 404


def test_concurrent_job_returns_409(client, make_work, upload_image, app_state):
    wid = make_work("busy")
    upload_image(wid)
    # A 'processing' job already exists -> starting another must be rejected.
    jid = app_state.db.create_job(wid, {"target_language": "English"})
    app_state.db.update_job_status(jid, "processing", 10)
    r = client.post(f"/api/works/{wid}/translate", json={})
    assert r.status_code == 409


def test_active_job_null_when_none(client, make_work, upload_image):
    wid = make_work("no-job")
    upload_image(wid)
    r = client.get(f"/api/works/{wid}/active-job")
    assert r.status_code == 200 and r.json() is None


def test_stale_processing_marked_failed(client, make_work, app_state):
    # A 'processing' row with no live task behind it (e.g. after a restart) must
    # be marked failed and reported as null so clients recover instead of polling.
    wid = make_work("stale")
    jid = app_state.db.create_job(wid, {})
    app_state.db.update_job_status(jid, "processing", 50)

    r = client.get(f"/api/works/{wid}/active-job")
    assert r.status_code == 200 and r.json() is None
    assert app_state.db.get_job(jid)["status"] == "failed"


def test_live_job_returned(client, make_work, upload_image, app_state):
    wid = make_work("live")
    upload_image(wid)
    jid = app_state.db.create_job(wid, {"target_language": "English"})
    app_state.db.update_job_status(jid, "processing", 25)

    # Pretend a live task is behind this job.
    sentinel = _LiveTask()
    app_state.jobs.current_jobs[jid] = sentinel
    try:
        r = client.get(f"/api/works/{wid}/active-job")
        assert r.status_code == 200
        body = r.json()
        assert body is not None and body["id"] == jid
        assert body["status"] in ("pending", "processing")
    finally:
        app_state.jobs.current_jobs.pop(jid, None)


def test_active_job_unknown_work_404(client):
    assert client.get("/api/works/999999/active-job").status_code == 404


def test_start_translation_creates_and_completes_job(
    client, make_work, upload_image, fake_process
):
    wid = make_work("run")
    upload_image(wid)

    r = client.post(f"/api/works/{wid}/translate", json={"target_language": "English"})
    assert r.status_code == 202, r.text
    job = r.json()
    assert job["status"] in ("pending", "processing")

    # The background task (fake_process) should run to completion.
    deadline = time.time() + 5.0
    last = None
    while time.time() < deadline:
        last = client.get(f"/api/jobs/{job['id']}").json()
        if last["status"] == "completed":
            break
        time.sleep(0.05)
    assert last is not None and last["status"] == "completed", last


def test_get_job_unknown_404(client):
    assert client.get("/api/jobs/999999").status_code == 404


def test_work_results_and_image_result(client, make_work, upload_image, app_state):
    wid = make_work("results")
    img = upload_image(wid)

    # No results yet.
    assert client.get(f"/api/works/{wid}/results").json() == []
    assert client.get(f"/api/images/{img['id']}/result").status_code == 404

    # Simulate a finished job with one saved result (no LLM).
    jid = app_state.db.create_job(wid, {"target_language": "English"})
    app_state.db.update_job_status(jid, "completed", 100)
    regions = [{"bbox": [0, 0, 1, 1], "original_text": "a", "translated_text": "b"}]
    app_state.db.save_result(img["id"], jid, regions, "ctx-after")

    # Work-level restore: the image id now has a result.
    assert client.get(f"/api/works/{wid}/results").json() == [img["id"]]

    # Per-image result payload round-trips.
    r = client.get(f"/api/images/{img['id']}/result")
    assert r.status_code == 200
    body = r.json()
    assert body["image_id"] == img["id"]
    assert body["context_after"] == "ctx-after"
    assert body["result_data"] == regions


def test_work_results_unknown_404(client):
    assert client.get("/api/works/999999/results").status_code == 404
