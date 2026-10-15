"""Fan-out notification engine: quiet hours, dedup, per-endpoint delivery."""
from __future__ import annotations

import datetime as dt
import json
import time

import requests

from . import db


def _to_minutes(hhmm: str) -> int:
    try:
        h, m = str(hhmm).split(":")
        v = int(h) * 60 + int(m)
        if 0 <= v < 1440:
            return v
    except Exception:
        pass
    return -1


def is_quiet(now: dt.datetime | None = None) -> bool:
    s = db.get_settings()
    qs = _to_minutes(s.get("quiet_start", "00:30"))
    qe = _to_minutes(s.get("quiet_end", "07:30"))
    if qs < 0 or qe < 0:
        return False
    now = now or dt.datetime.now()
    cur = now.hour * 60 + now.minute
    if qs == qe:
        return False
    if qs < qe:
        return qs <= cur < qe
    return cur >= qs or cur < qe  # window crosses midnight


def effective_level(
    requested: str | None, force: bool, bypass_quiet: bool, now: dt.datetime | None = None
) -> tuple[str, bool]:
    """Return (effective_level, is_quiet). Quiet hours downgrade to passive
    unless the push is forced or the item bypasses quiet hours."""
    quiet = is_quiet(now)
    level = requested or "active"
    if quiet and not force and not bypass_quiet and level != "passive":
        level = "passive"
    return level, quiet


def push_endpoint(ep, title: str, body: str, group: str, level: str, extra: dict | None = None) -> dict:
    """POST one Bark-style endpoint. ep is an sqlite Row from endpoints."""
    url = ep["server_url"].rstrip("/") + "/" + ep["device_key"]
    data = {"title": title, "body": body, "group": group, "level": level}
    try:
        params = json.loads(ep["params"] or "{}")
        if isinstance(params, dict):
            data.update(params)
    except Exception:
        pass
    if extra:
        data.update({k: v for k, v in extra.items() if v not in ("", None)})
    t0 = time.time()
    try:
        r = requests.post(url, data=data, timeout=10)
        ok = 200 <= r.status_code < 300
        try:
            ok = ok and r.json().get("code") == 200
        except Exception:
            pass
        return {
            "ok": bool(ok),
            "status_code": r.status_code,
            "response": r.text[:300],
            "latency_ms": int((time.time() - t0) * 1000),
        }
    except Exception as e:
        return {
            "ok": False,
            "status_code": 0,
            "response": str(e)[:300],
            "latency_ms": int((time.time() - t0) * 1000),
        }


def _dedup_hit(c, item_id: int, title: str, body: str, seconds: int) -> bool:
    row = c.execute(
        "SELECT id FROM notifications WHERE item_id=? AND title=? AND body=? "
        "AND created_at >= datetime('now', 'localtime', ?)",
        (item_id, title, body, f"-{seconds} seconds"),
    ).fetchone()
    return row is not None


def send(
    item_key: str,
    title: str,
    body: str = "",
    group: str = "",
    level: str | None = None,
    force: bool = False,
    source: str = "",
    extra: dict | None = None,
) -> dict:
    """Fan out one notification to every enabled endpoint of the item.

    Returns a bark-style dict: {"code": 200, "message": "success", "data": {...}}.
    """
    with db.conn() as c:
        item = c.execute("SELECT * FROM items WHERE key=?", (item_key,)).fetchone()
        if item is None:
            return {"code": 404, "message": "unknown item key"}
        if not item["enabled"]:
            return {"code": 403, "message": "item disabled"}
        try:
            dedup = int(db.get_settings().get("dedup_seconds", "60"))
        except ValueError:
            dedup = 60
        if dedup > 0 and _dedup_hit(c, item["id"], title, body, dedup):
            return {"code": 200, "message": "deduplicated", "data": {"deduplicated": True}}

        lvl, quiet = effective_level(level, force, bool(item["bypass_quiet"]))
        cur = c.execute(
            "INSERT INTO notifications(item_id, title, body, grp, level_requested, "
            "level_effective, forced, quiet, source) VALUES (?,?,?,?,?,?,?,?,?)",
            (item["id"], title, body, group, level or "active", lvl,
             1 if force else 0, 1 if quiet else 0, source),
        )
        nid = cur.lastrowid
        endpoints = c.execute(
            "SELECT * FROM endpoints WHERE item_id=? AND enabled=1", (item["id"],)
        ).fetchall()

    delivered = failed = 0
    for ep in endpoints:
        res = push_endpoint(ep, title, body, group, lvl, extra=extra)
        with db.conn() as c:
            c.execute(
                "INSERT INTO deliveries(notification_id, endpoint_id, endpoint_name, "
                "ok, status_code, response, latency_ms) VALUES (?,?,?,?,?,?,?)",
                (nid, ep["id"], ep["name"] or ep["server_url"], 1 if res["ok"] else 0,
                 res["status_code"], res["response"], res["latency_ms"]),
            )
        if res["ok"]:
            delivered += 1
        else:
            failed += 1

    if not endpoints:
        return {"code": 500, "message": "no enabled endpoints", "data": {"notification_id": nid}}
    if delivered == 0:
        return {"code": 500, "message": "all endpoints failed",
                "data": {"notification_id": nid, "failed": failed}}
    return {
        "code": 200,
        "message": "success",
        "data": {
            "notification_id": nid,
            "delivered": delivered,
            "failed": failed,
            "level_effective": lvl,
            "quiet": bool(quiet),
        },
    }


def cleanup_old() -> int:
    """Delete notifications older than retention_days. Returns rows removed."""
    try:
        days = int(db.get_settings().get("retention_days", "90"))
    except ValueError:
        days = 90
    if days <= 0:
        return 0
    with db.conn() as c:
        cur = c.execute(
            "DELETE FROM notifications WHERE created_at < datetime('now', 'localtime', ?)",
            (f"-{days} days",),
        )
        return cur.rowcount
