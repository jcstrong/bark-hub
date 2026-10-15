"""Management & push REST API (token-protected)."""
from __future__ import annotations

import datetime as dt
import json
import re

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from . import __version__, db, notify
from .auth import require_token

router = APIRouter(prefix="/api")

KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
TIME_RE = re.compile(r"^\d{2}:\d{2}$")


class EndpointIn(BaseModel):
    name: str = ""
    server_url: str
    device_key: str
    params: dict = Field(default_factory=dict)
    enabled: bool = True


class ItemIn(BaseModel):
    key: str
    name: str
    description: str = ""
    bypass_quiet: bool = False
    enabled: bool = True
    # 非空时全量替换端点列表；省略则保留现有端点
    endpoints: list[EndpointIn] = []


class NotifyIn(BaseModel):
    title: str
    body: str = ""
    group: str = ""
    level: str | None = None
    force: bool = False
    source: str = ""


class SettingsIn(BaseModel):
    quiet_start: str | None = None
    quiet_end: str | None = None
    retention_days: int | None = Field(default=None, ge=1, le=3650)
    dedup_seconds: int | None = Field(default=None, ge=0, le=86400)


def _load_params(s) -> dict:
    try:
        v = json.loads(s or "{}")
        return v if isinstance(v, dict) else {}
    except Exception:
        return {}


def _item_out(c, row, with_stats: bool = True) -> dict:
    eps = c.execute("SELECT * FROM endpoints WHERE item_id=? ORDER BY id", (row["id"],)).fetchall()
    out = {k: row[k] for k in row.keys()}
    out["bypass_quiet"] = bool(out["bypass_quiet"])
    out["enabled"] = bool(out["enabled"])
    out["endpoints"] = [
        {
            "id": e["id"],
            "name": e["name"],
            "server_url": e["server_url"],
            "device_key": e["device_key"],
            "params": _load_params(e["params"]),
            "enabled": bool(e["enabled"]),
        }
        for e in eps
    ]
    if with_stats:
        last = c.execute(
            "SELECT created_at FROM notifications WHERE item_id=? ORDER BY id DESC LIMIT 1",
            (row["id"],),
        ).fetchone()
        cnt = c.execute(
            "SELECT COUNT(*) n FROM notifications WHERE item_id=? "
            "AND created_at >= date('now', 'localtime')",
            (row["id"],),
        ).fetchone()
        out["last_notification_at"] = last["created_at"] if last else None
        out["today_count"] = cnt["n"]
    return out


def _get_item(c, key: str):
    row = c.execute("SELECT * FROM items WHERE key=?", (key,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="item not found")
    return row


@router.get("/status")
def status():
    """Open endpoint: current quiet state and counters."""
    s = db.get_settings()
    with db.conn() as c:
        items = c.execute("SELECT COUNT(*) n FROM items").fetchone()["n"]
        eps = c.execute("SELECT COUNT(*) n FROM endpoints WHERE enabled=1").fetchone()["n"]
        today = c.execute(
            "SELECT COUNT(*) n FROM notifications WHERE created_at >= date('now', 'localtime')"
        ).fetchone()["n"]
        total = c.execute("SELECT COUNT(*) n FROM notifications").fetchone()["n"]
    return {
        "version": __version__,
        "now": dt.datetime.now().strftime("%H:%M"),
        "quiet": notify.is_quiet(),
        "window": {"start": s["quiet_start"], "end": s["quiet_end"]},
        "items": items,
        "endpoints": eps,
        "today_count": today,
        "total_count": total,
        "retention_days": int(s["retention_days"]),
        "dedup_seconds": int(s["dedup_seconds"]),
    }


@router.get("/items")
def list_items(_: None = Depends(require_token)):
    with db.conn() as c:
        rows = c.execute("SELECT * FROM items ORDER BY id").fetchall()
        return [_item_out(c, r) for r in rows]


@router.post("/items")
def upsert_item(item: ItemIn, _: None = Depends(require_token)):
    """Create or update an item by key (upsert). Used by remote agents
    (e.g. the MacPro monitoring agent) to register items centrally."""
    if not KEY_RE.match(item.key):
        raise HTTPException(status_code=400, detail="key must match ^[a-z0-9][a-z0-9_-]{0,63}$")
    if not item.name.strip():
        raise HTTPException(status_code=400, detail="name is required")
    for ep in item.endpoints:
        if not ep.server_url.startswith(("http://", "https://")):
            raise HTTPException(status_code=400, detail=f"invalid server_url: {ep.server_url}")
        if not ep.device_key.strip():
            raise HTTPException(status_code=400, detail="device_key is required")
    with db.conn() as c:
        row = c.execute("SELECT id FROM items WHERE key=?", (item.key,)).fetchone()
        if row:
            c.execute(
                "UPDATE items SET name=?, description=?, bypass_quiet=?, enabled=?, "
                "updated_at=datetime('now', 'localtime') WHERE id=?",
                (item.name, item.description, int(item.bypass_quiet), int(item.enabled), row["id"]),
            )
            item_id = row["id"]
        else:
            cur = c.execute(
                "INSERT INTO items(key, name, description, bypass_quiet, enabled) VALUES (?,?,?,?,?)",
                (item.key, item.name, item.description, int(item.bypass_quiet), int(item.enabled)),
            )
            item_id = cur.lastrowid
        if item.endpoints:
            c.execute("DELETE FROM endpoints WHERE item_id=?", (item_id,))
            for ep in item.endpoints:
                c.execute(
                    "INSERT INTO endpoints(item_id, name, server_url, device_key, params, enabled) "
                    "VALUES (?,?,?,?,?,?)",
                    (item_id, ep.name, ep.server_url, ep.device_key,
                     json.dumps(ep.params, ensure_ascii=False), int(ep.enabled)),
                )
        return _item_out(c, _get_item(c, item.key))


@router.delete("/items/{key}")
def delete_item(key: str, _: None = Depends(require_token)):
    with db.conn() as c:
        row = _get_item(c, key)
        c.execute("DELETE FROM items WHERE id=?", (row["id"],))
    return {"ok": True}


@router.post("/items/{key}/endpoints")
def add_endpoint(key: str, ep: EndpointIn, _: None = Depends(require_token)):
    if not ep.server_url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="invalid server_url")
    with db.conn() as c:
        row = _get_item(c, key)
        c.execute(
            "INSERT INTO endpoints(item_id, name, server_url, device_key, params, enabled) "
            "VALUES (?,?,?,?,?,?)",
            (row["id"], ep.name, ep.server_url, ep.device_key,
             json.dumps(ep.params, ensure_ascii=False), int(ep.enabled)),
        )
        return _item_out(c, row)


@router.delete("/items/{key}/endpoints/{endpoint_id}")
def delete_endpoint(key: str, endpoint_id: int, _: None = Depends(require_token)):
    with db.conn() as c:
        row = _get_item(c, key)
        c.execute(
            "DELETE FROM endpoints WHERE id=? AND item_id=?", (endpoint_id, row["id"])
        )
        return _item_out(c, row)


@router.post("/notify/{key}")
def api_notify(key: str, payload: NotifyIn, _: None = Depends(require_token)):
    """Token-protected push (for scripts that already hold the token)."""
    return notify.send(
        key, payload.title, payload.body, group=payload.group,
        level=payload.level, force=payload.force, source=payload.source or "api",
    )


@router.post("/items/{key}/test")
def test_item(key: str, _: None = Depends(require_token)):
    return notify.send(
        key, "bark-hub 测试", f"来自 webui 的测试推送 · item={key} · {dt.datetime.now():%F %T}",
        group="bark-hub", level="active", force=True, source="test",
    )


@router.get("/notifications")
def list_notifications(
    item_key: str | None = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    _: None = Depends(require_token),
):
    with db.conn() as c:
        if item_key:
            total = c.execute(
                "SELECT COUNT(*) n FROM notifications n JOIN items i ON n.item_id=i.id WHERE i.key=?",
                (item_key,),
            ).fetchone()["n"]
            rows = c.execute(
                "SELECT n.*, i.key item_key, i.name item_name FROM notifications n "
                "JOIN items i ON n.item_id=i.id WHERE i.key=? ORDER BY n.id DESC LIMIT ? OFFSET ?",
                (item_key, limit, offset),
            ).fetchall()
        else:
            total = c.execute("SELECT COUNT(*) n FROM notifications").fetchone()["n"]
            rows = c.execute(
                "SELECT n.*, i.key item_key, i.name item_name FROM notifications n "
                "JOIN items i ON n.item_id=i.id ORDER BY n.id DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        ids = [r["id"] for r in rows]
        by_nid: dict[int, list] = {}
        if ids:
            marks = ",".join("?" * len(ids))
            dels = c.execute(
                f"SELECT * FROM deliveries WHERE notification_id IN ({marks}) ORDER BY id", ids
            ).fetchall()
            for d in dels:
                out = {k: d[k] for k in d.keys()}
                out["ok"] = bool(out["ok"])
                by_nid.setdefault(d["notification_id"], []).append(out)
        out_rows = []
        for r in rows:
            o = {k: r[k] for k in r.keys()}
            o["quiet"] = bool(o["quiet"])
            o["forced"] = bool(o["forced"])
            o["deliveries"] = by_nid.get(r["id"], [])
            out_rows.append(o)
    return {"total": total, "rows": out_rows}


@router.get("/settings")
def get_settings_api(_: None = Depends(require_token)):
    return db.get_settings()


@router.put("/settings")
def put_settings(payload: SettingsIn, _: None = Depends(require_token)):
    for field in ("quiet_start", "quiet_end"):
        val = getattr(payload, field)
        if val is not None and not TIME_RE.match(val):
            raise HTTPException(status_code=400, detail=f"{field} must be HH:MM")
    updates = {
        k: str(v)
        for k, v in payload.model_dump(exclude_none=True).items()
    }
    for k, v in updates.items():
        db.set_setting(k, v)
    return db.get_settings()
