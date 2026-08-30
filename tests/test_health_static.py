"""Health check + SPA shell + static asset serving (incl. cache-control)."""


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_index_serves_shell(client):
    r = client.get("/")
    assert r.status_code == 200
    ct = r.headers["content-type"].lower()
    assert "text/html" in ct
    body = r.text.lower()
    assert "<!doctype html>" in body
    # The shell must reference the JS entry point.
    assert "/static/js/main.js" in body


def test_static_js_served_with_no_cache(client):
    r = client.get("/static/js/main.js")
    assert r.status_code == 200
    ct = r.headers["content-type"].lower()
    assert "javascript" in ct or "ecmascript" in ct
    # StaticCacheControlMiddleware forces revalidation for JS/CSS.
    assert r.headers.get("cache-control", "").lower().startswith("no-cache")


def test_static_css_served_with_no_cache(client):
    r = client.get("/static/css/app.css")
    assert r.status_code == 200
    ct = r.headers["content-type"].lower()
    assert "text/css" in ct or "css" in ct
    assert r.headers.get("cache-control", "").lower().startswith("no-cache")
