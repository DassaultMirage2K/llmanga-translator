"""Global app settings (key -> value strings).

There is no DELETE endpoint for settings; cleanup here is provided by the
session-level temp data dir (the whole DB is thrown away at session end), so
these tests only assert on the specific keys they set.
"""


def test_get_settings_returns_object(client):
    r = client.get("/api/settings")
    assert r.status_code == 200
    assert isinstance(r.json(), dict)


def test_put_upserts_and_persists(client):
    key, value = "test_language", "Japanese"
    r = client.put("/api/settings", json={key: value})
    assert r.status_code == 200
    # Response is the full map after upsert.
    assert r.json()[key] == value

    got = client.get("/api/settings").json()
    assert got[key] == value


def test_put_multiple_keys_at_once(client):
    payload = {"test_a": "1", "test_b": "2"}
    r = client.put("/api/settings", json=payload)
    assert r.status_code == 200
    body = r.json()
    for k, v in payload.items():
        assert body[k] == v


def test_put_overwrites_existing_key(client):
    key = "test_overwrite"
    client.put("/api/settings", json={key: "first"})
    r = client.put("/api/settings", json={key: "second"})
    assert r.json()[key] == "second"
    assert client.get("/api/settings").json()[key] == "second"
