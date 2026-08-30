"""ZIP archive import: root images, single top-level folder, and error cases."""
import io
import zipfile

from conftest import make_image_bytes


def _zip(entries):
    """Build an in-memory zip from {path_in_zip: bytes}."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _upload_zip(client, work_id, zip_bytes, name="archive.zip"):
    return client.post(
        f"/api/works/{work_id}/images",
        files={"files": (name, io.BytesIO(zip_bytes), "application/zip")},
    )


def test_zip_root_images_natural_sort(client, make_work):
    wid = make_work("zip-root")
    png = lambda: make_image_bytes("PNG")  # noqa: E731
    z = _zip({
        "page1.png": png(),
        "page2.png": png(),
        "page10.png": png(),
        "notes.txt": b"ignore me",  # non-image at root -> ignored
    })
    r = _upload_zip(client, wid, z)
    assert r.status_code == 201, r.text
    imgs = r.json()
    # Natural sort: page1 < page2 < page10 (not lexicographic).
    assert [i["original_name"] for i in imgs] == ["page1.png", "page2.png", "page10.png"]
    # Dimensions were read from the extracted images.
    assert all(i["width"] and i["height"] for i in imgs)


def test_zip_single_top_level_folder(client, make_work):
    wid = make_work("zip-folder")
    png = lambda: make_image_bytes("PNG")  # noqa: E731
    z = _zip({
        "MyManga/page1.png": png(),
        "MyManga/page3.png": png(),
        "MyManga/readme.txt": b"ignore",
    })
    r = _upload_zip(client, wid, z)
    assert r.status_code == 201, r.text
    # Basenames are used as the display names.
    assert [i["original_name"] for i in r.json()] == ["page1.png", "page3.png"]


def test_zip_no_images_400(client, make_work):
    wid = make_work("zip-empty")
    r = _upload_zip(client, wid, _zip({"readme.txt": b"no images here"}))
    assert r.status_code == 400


def test_zip_multiple_folders_no_root_400(client, make_work):
    wid = make_work("zip-multifolder")
    png = lambda: make_image_bytes("PNG")  # noqa: E731
    z = _zip({"a/1.png": png(), "b/2.png": png()})
    r = _upload_zip(client, wid, z)
    assert r.status_code == 400


def test_zip_root_images_take_precedence_over_folders(client, make_work):
    # If there are images at the root, only those are imported (folders ignored).
    wid = make_work("zip-precedence")
    png = lambda: make_image_bytes("PNG")  # noqa: E731
    z = _zip({"root.png": png(), "sub/other.png": png()})
    r = _upload_zip(client, wid, z)
    assert r.status_code == 201, r.text
    assert [i["original_name"] for i in r.json()] == ["root.png"]


def test_zip_unknown_work_404(client):
    r = _upload_zip(client, 999999, _zip({"a.png": make_image_bytes("PNG")}))
    assert r.status_code == 404
