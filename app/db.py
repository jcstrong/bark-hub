"""SQLite storage layer for bark-hub.

One thread-local connection per worker thread; connections are re-created
automatically when BARKHUB_DB changes (keeps tests isolated).
"""
from __future__ import annotations

import os
import sqlite3
import threading
from contextlib import contextmanager

DEFAULT_SETTINGS = {
    "quiet_start": "00:30",
    "quiet_end": "07:30",
    "retention_days": "90",
    "dedup_seconds": "60",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    bypass_quiet INTEGER NOT NULL DEFAULT 0,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS endpoints (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    name TEXT NOT NULL DEFAULT '',
    server_url TEXT NOT NULL,
    device_key TEXT NOT NULL,
    params TEXT NOT NULL DEFAULT '{}',
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    title TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL DEFAULT '',
    grp TEXT NOT NULL DEFAULT '',
    level_requested TEXT NOT NULL DEFAULT 'active',
    level_effective TEXT NOT NULL DEFAULT 'active',
    forced INTEGER NOT NULL DEFAULT 0,
    quiet INTEGER NOT NULL DEFAULT 0,
    source TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS deliveries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    notification_id INTEGER NOT NULL REFERENCES notifications(id) ON DELETE CASCADE,
    endpoint_id INTEGER NOT NULL,
    endpoint_name TEXT NOT NULL DEFAULT '',
    ok INTEGER NOT NULL DEFAULT 0,
    status_code INTEGER NOT NULL DEFAULT 0,
    response TEXT NOT NULL DEFAULT '',
    latency_ms INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS settings (
    k TEXT PRIMARY KEY,
    v TEXT NOT NULL
);
"""

_local = threading.local()


def db_path() -> str:
    default = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "bark-hub.db"
    )
    return os.environ.get("BARKHUB_DB", default)


def _connect(path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    c = sqlite3.connect(path, timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA foreign_keys=ON")
    c.executescript(SCHEMA)
    for k, v in DEFAULT_SETTINGS.items():
        c.execute("INSERT OR IGNORE INTO settings(k, v) VALUES (?, ?)", (k, v))
    c.commit()
    return c


def _get() -> sqlite3.Connection:
    path = db_path()
    c = getattr(_local, "conn", None)
    if c is None or getattr(_local, "path", None) != path:
        if c is not None:
            c.close()
        c = _connect(path)
        _local.conn = c
        _local.path = path
    return c


@contextmanager
def conn():
    c = _get()
    try:
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise


def reset() -> None:
    """Drop cached connections (used by tests)."""
    c = getattr(_local, "conn", None)
    if c is not None:
        c.close()
    _local.conn = None
    _local.path = None


def get_settings() -> dict:
    with conn() as c:
        rows = c.execute("SELECT k, v FROM settings").fetchall()
    return {r["k"]: r["v"] for r in rows}


def set_setting(k: str, v: str) -> None:
    with conn() as c:
        c.execute(
            "INSERT INTO settings(k, v) VALUES (?, ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, v)
        )
