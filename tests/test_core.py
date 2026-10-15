"""Core behaviour tests: quiet hours, dedup, fan-out, API auth, retention."""
import datetime as dt
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from fastapi.testclient import TestClient  # noqa: E402

from app import db, notify  # noqa: E402
from app.main import create_app  # noqa: E402

CALLS: list = []
AUTH = {"X-API-Token": "testtoken"}


class FakeResp:
    status_code = 200
    text = '{"code":200}'

    def json(self):
        return {"code": 200}


def fake_post_ok(url, data=None, timeout=None):
    CALLS.append((url, data))
    return FakeResp()


def fake_post_fail(url, data=None, timeout=None):
    raise ConnectionError("boom")


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("BARKHUB_DB", str(tmp_path / "t.db"))
    monkeypatch.setenv("BARKHUB_TOKEN", "testtoken")
    db.reset()
    app = create_app()
    with TestClient(app) as c:
        yield c
    db.reset()


def make_item(client, key="demo", endpoints=2, bypass=False):
    eps = [
        {
            "name": f"ep{i}",
            "server_url": "https://api.example.com",
            "device_key": f"K{i}",
            "params": {},
            "enabled": True,
        }
        for i in range(endpoints)
    ]
    r = client.post(
        "/api/items",
        json={"key": key, "name": key, "endpoints": eps, "bypass_quiet": bypass},
        headers=AUTH,
    )
    assert r.status_code == 200
    return r.json()


def last_row(client):
    return client.get("/api/notifications", headers=AUTH).json()["rows"][0]


def test_status_is_open(client):
    r = client.get("/api/status")
    assert r.status_code == 200
    assert r.json()["window"] == {"start": "00:30", "end": "07:30"}
    assert r.json()["items"] == 0


def test_items_require_token(client):
    assert client.get("/api/items").status_code == 401
    make_item(client)
    assert len(client.get("/api/items", headers=AUTH).json()) == 1


def test_bark_form_push_fanout_and_deliveries(client, monkeypatch):
    make_item(client, endpoints=2)
    monkeypatch.setattr(notify.requests, "post", fake_post_ok)
    body = client.post("/demo", data={"title": "hello", "body": "world", "group": "t"}).json()
    assert body["code"] == 200 and body["data"]["delivered"] == 2
    assert len(CALLS) == 2
    hist = client.get("/api/notifications", headers=AUTH).json()
    assert hist["total"] == 1
    row = hist["rows"][0]
    assert row["title"] == "hello" and len(row["deliveries"]) == 2
    assert all(d["ok"] for d in row["deliveries"])


def test_bark_path_style_route(client, monkeypatch):
    make_item(client)
    monkeypatch.setattr(notify.requests, "post", fake_post_ok)
    r = client.get("/demo/%E6%A0%87%E9%A2%98/%E5%86%85%E5%AE%B9?sound=minuet")
    assert r.json()["code"] == 200
    assert CALLS[-1][1]["sound"] == "minuet"


def test_dedup_window(client, monkeypatch):
    make_item(client)
    monkeypatch.setattr(notify.requests, "post", fake_post_ok)
    client.post("/demo", data={"title": "x", "body": "y"})
    r2 = client.post("/demo", data={"title": "x", "body": "y"})
    assert r2.json()["message"] == "deduplicated"
    assert client.get("/api/notifications", headers=AUTH).json()["total"] == 1


def test_unknown_and_disabled_item(client):
    assert client.post("/nope", data={"title": "a"}).json()["code"] == 404


def test_quiet_downgrade_and_force(client, monkeypatch):
    make_item(client)
    monkeypatch.setattr(notify, "is_quiet", lambda now=None: True)
    monkeypatch.setattr(notify.requests, "post", fake_post_ok)
    client.post("/demo", data={"title": "q", "body": "1"})
    row = last_row(client)
    assert row["level_effective"] == "passive" and row["quiet"] is True
    client.post("/demo", data={"title": "f", "body": "2", "force": "1"})
    row2 = last_row(client)
    assert row2["title"] == "f" and row2["level_effective"] == "active" and row2["forced"] is True


def test_item_bypass_quiet(client, monkeypatch):
    make_item(client, key="crit", bypass=True)
    monkeypatch.setattr(notify, "is_quiet", lambda now=None: True)
    monkeypatch.setattr(notify.requests, "post", fake_post_ok)
    client.post("/crit", data={"title": "b", "body": ""})
    assert last_row(client)["level_effective"] == "active"


def test_all_endpoints_fail(client, monkeypatch):
    make_item(client, endpoints=1)
    monkeypatch.setattr(notify.requests, "post", fake_post_fail)
    body = client.post("/demo", data={"title": "z", "body": ""}).json()
    assert body["code"] == 500
    row = last_row(client)
    assert row["deliveries"][0]["ok"] is False


def test_quiet_window_cross_midnight(client):
    client.put("/api/settings", json={"quiet_start": "23:00", "quiet_end": "08:00"}, headers=AUTH)
    assert notify.is_quiet(dt.datetime(2026, 1, 1, 23, 30)) is True
    assert notify.is_quiet(dt.datetime(2026, 1, 1, 12, 0)) is False
    assert notify.is_quiet(dt.datetime(2026, 1, 1, 7, 59)) is True


def test_retention_cleanup(client):
    make_item(client)
    with db.conn() as c:
        c.execute(
            "INSERT INTO notifications(item_id, title, created_at) "
            "SELECT id, 'old', datetime('now', 'localtime', '-100 days') FROM items WHERE key='demo'"
        )
    assert notify.cleanup_old() == 1


def test_notify_api_with_token(client, monkeypatch):
    make_item(client)
    monkeypatch.setattr(notify.requests, "post", fake_post_ok)
    r = client.post("/api/notify/demo", json={"title": "api", "body": "x"}, headers=AUTH)
    assert r.json()["code"] == 200


def test_item_upsert_replaces_endpoints(client):
    make_item(client, endpoints=1)
    make_item(client, endpoints=3)
    items = client.get("/api/items", headers=AUTH).json()
    assert len(items) == 1
    assert len(items[0]["endpoints"]) == 3
