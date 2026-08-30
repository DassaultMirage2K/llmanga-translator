"""Works create / read / update / delete."""


def test_create_work(client, make_work):
    wid = make_work("My Manga", "a description")
    r = client.get(f"/api/works/{wid}")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == wid
    assert body["name"] == "My Manga"
    assert body["description"] == "a description"


def test_create_work_requires_name(client):
    # name has min_length=1 -> empty string is rejected by the schema.
    r = client.post("/api/works", json={"name": ""})
    assert r.status_code == 422


def test_list_includes_new_work_with_zero_images(client, make_work):
    wid = make_work("listed-work")
    r = client.get("/api/works")
    assert r.status_code == 200
    rows = {w["id"]: w for w in r.json()}
    assert wid in rows
    assert rows[wid]["image_count"] == 0
    assert rows[wid]["cover_image_id"] is None


def test_get_unknown_work_404(client):
    assert client.get("/api/works/999999").status_code == 404


def test_update_name_and_description(client, make_work):
    wid = make_work("old name", "old desc")
    r = client.put(f"/api/works/{wid}", json={"name": "new name", "description": "new desc"})
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "new name"
    assert body["description"] == "new desc"


def test_partial_update_keeps_other_fields(client, make_work):
    wid = make_work("keep me", "old desc")
    # Only send description; name must be preserved.
    r = client.put(f"/api/works/{wid}", json={"description": "changed"})
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "keep me"
    assert body["description"] == "changed"


def test_update_unknown_work_404(client):
    assert client.put("/api/works/999999", json={"name": "x"}).status_code == 404


def test_delete_work_then_gone(client, make_work):
    wid = make_work("doomed")
    r = client.delete(f"/api/works/{wid}")
    assert r.status_code == 204
    assert client.get(f"/api/works/{wid}").status_code == 404


def test_delete_unknown_work_404(client):
    assert client.delete("/api/works/999999").status_code == 404
