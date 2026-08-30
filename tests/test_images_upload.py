"""Multipart image upload, listing order, per-image context, reorder, file."""
import io

from conftest import make_image_bytes


def test_upload_returns_stored_images_with_dims(client, make_work):
    wid = make_work("with-images")
    data = make_image_bytes("JPEG", size=(32, 16))
    r = client.post(
        f"/api/works/{wid}/images",
        files=[("files", ("a.jpg", io.BytesIO(data), "image/jpeg")),
               ("files", ("b.png", io.BytesIO(make_image_bytes("PNG")), "image/png"))],
    )
    assert r.status_code == 201, r.text
    imgs = r.json()
    assert len(imgs) == 2
    # Dimensions are read by PIL on upload.
    first = next(i for i in imgs if i["original_name"] == "a.jpg")
    assert (first["width"], first["height"]) == (32, 16)
    assert first["mime_type"] == "image/jpeg"
    # sort_order is assigned sequentially from 1.
    orders = sorted(i["sort_order"] for i in imgs)
    assert orders == [1, 2]


def test_upload_unknown_work_404(client):
    r = client.post(
        "/api/works/999999/images",
        files={"files": ("a.jpg", io.BytesIO(make_image_bytes()), "image/jpeg")},
    )
    assert r.status_code == 404


def test_upload_rejects_unsupported_extension(client, make_work):
    wid = make_work("bad-ext")
    r = client.post(
        f"/api/works/{wid}/images",
        files={"files": ("notes.txt", io.BytesIO(b"not an image"), "text/plain")},
    )
    assert r.status_code == 400


def test_list_images_in_sort_order(client, make_work):
    wid = make_work("ordered")
    ids = []
    for name in ("one.jpg", "two.jpg", "three.jpg"):
        r = client.post(
            f"/api/works/{wid}/images",
            files={"files": (name, io.BytesIO(make_image_bytes()), "image/jpeg")},
        )
        assert r.status_code == 201, r.text
        ids.append(r.json()[0]["id"])
    listed = client.get(f"/api/works/{wid}/images").json()
    assert [i["id"] for i in listed] == ids


def test_update_image_context(client, make_work):
    wid = make_work("ctx")
    img = client.post(
        f"/api/works/{wid}/images",
        files={"files": ("a.jpg", io.BytesIO(make_image_bytes()), "image/jpeg")},
    ).json()[0]
    r = client.put(f"/api/images/{img['id']}", json={"context": "hello context"})
    assert r.status_code == 200
    assert r.json()["context"] == "hello context"


def test_update_image_context_unknown_404(client):
    assert client.put("/api/images/999999", json={"context": "x"}).status_code == 404


def test_reorder_images(client, make_work):
    wid = make_work("reorder")
    ids = []
    for name in ("a.jpg", "b.jpg", "c.jpg"):
        r = client.post(
            f"/api/works/{wid}/images",
            files={"files": (name, io.BytesIO(make_image_bytes()), "image/jpeg")},
        )
        ids.append(r.json()[0]["id"])
    a, b, c = ids

    # Reverse the order.
    r = client.put(f"/api/works/{wid}/images/reorder", json={"ordered_ids": [c, a, b]})
    assert r.status_code == 200
    listed = [i["id"] for i in r.json()]
    assert listed == [c, a, b]

    # Persisted: a fresh list reflects the new order.
    listed2 = [i["id"] for i in client.get(f"/api/works/{wid}/images").json()]
    assert listed2 == [c, a, b]


def test_reorder_ignores_foreign_ids(client, make_work):
    wid = make_work("reorder-foreign")
    r = client.post(
        f"/api/works/{wid}/images",
        files=[("files", ("a.jpg", io.BytesIO(make_image_bytes()), "image/jpeg")),
               ("files", ("b.jpg", io.BytesIO(make_image_bytes()), "image/jpeg"))],
    ).json()
    a, b = r[0]["id"], r[1]["id"]
    # 999999 is not in this work -> filtered out; order of the two real ids kept.
    client.put(f"/api/works/{wid}/images/reorder", json={"ordered_ids": [b, a, 999999]})
    listed = [i["id"] for i in client.get(f"/api/works/{wid}/images").json()]
    assert listed == [b, a]


def test_delete_image(client, make_work):
    wid = make_work("del-img")
    img = client.post(
        f"/api/works/{wid}/images",
        files={"files": ("a.jpg", io.BytesIO(make_image_bytes()), "image/jpeg")},
    ).json()[0]
    r = client.delete(f"/api/images/{img['id']}")
    assert r.status_code == 204
    listed = client.get(f"/api/works/{wid}/images").json()
    assert [i["id"] for i in listed] == []


def test_delete_image_unknown_404(client):
    assert client.delete("/api/images/999999").status_code == 404


def test_get_image_file_roundtrip(client, make_work):
    from PIL import Image as _PILImage

    wid = make_work("file")
    data = make_image_bytes("JPEG", size=(24, 12))
    img = client.post(
        f"/api/works/{wid}/images",
        files={"files": ("a.jpg", io.BytesIO(data), "image/jpeg")},
    ).json()[0]

    r = client.get(f"/api/images/{img['id']}/file")
    assert r.status_code == 200
    ct = r.headers["content-type"].lower()
    assert ct.startswith("image/")
    # The served bytes must still be a valid image of the right size.
    with _PILImage.open(io.BytesIO(r.content)) as im:
        assert im.size == (24, 12)


def test_get_image_file_unknown_404(client):
    assert client.get("/api/images/999999/file").status_code == 404
