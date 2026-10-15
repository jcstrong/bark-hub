#!/usr/bin/env python3
"""Import bark-hub v1 legacy data (config.json + bark-history.jsonl) into v2 DB.

Usage:
  python scripts/import_legacy.py --db data/bark-hub.db \
      --legacy-dir /legacy --server-url https://api.day.app --device-key <KEY>

Creates the standard seed items, carries over the quiet-hours window,
and imports v1 push history (mapped by group -> item key, source='legacy').
Idempotent: history import is skipped if already done (use --force to redo).
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from app.db import DEFAULT_SETTINGS, SCHEMA  # noqa: E402

SEED_ITEMS = [
    ("bambu", "Bambu 打印监控", "打印报警 critical，夜间自动 passive", 0),
    ("daily", "每日日报", "10:00 Bambu 日报", 0),
    ("openclaw", "OpenClaw 动态", "PR/issue/bug watchers", 0),
    ("homarr", "Homarr 动态", "issue/PR watchers", 0),
    ("vultr", "vultr-clash", "节点守卫/管理操作通知", 0),
    ("github_mail", "GitHub 邮件监控 (Mac)", "github-mail-watch.zsh → bark", 0),
    ("manual", "手动/默认", "webui 手动发送与未匹配分组", 0),
    ("legacy", "历史导入", "v1 JSONL 导入的旧记录", 0),
]

GROUP_MAP = {
    "Bambu Lab": "bambu",
    "日报": "daily",
    "openclaw": "openclaw",
    "homarr": "homarr",
    "vultr-clash": "vultr",
    "GitHub": "github_mail",
    "小U": "manual",
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--legacy-dir", required=True)
    ap.add_argument("--server-url", required=True)
    ap.add_argument("--device-key", required=True)
    ap.add_argument("--endpoint-name", default="iPhone (official)")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    os.makedirs(os.path.dirname(os.path.abspath(args.db)), exist_ok=True)
    c = sqlite3.connect(args.db)
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    for k, v in DEFAULT_SETTINGS.items():
        c.execute("INSERT OR IGNORE INTO settings(k, v) VALUES (?, ?)", (k, v))

    cfg = os.path.join(args.legacy_dir, "config.json")
    if os.path.isfile(cfg):
        try:
            with open(cfg, encoding="utf-8") as f:
                lc = json.load(f)
            if lc.get("quiet_start"):
                c.execute("UPDATE settings SET v=? WHERE k='quiet_start'", (str(lc["quiet_start"]),))
            if lc.get("quiet_end"):
                c.execute("UPDATE settings SET v=? WHERE k='quiet_end'", (str(lc["quiet_end"]),))
        except Exception as e:  # noqa: BLE001
            print(f"config.json skipped: {e}")

    ids: dict[str, int] = {}
    for key, name, desc, bq in SEED_ITEMS:
        r = c.execute("SELECT id FROM items WHERE key=?", (key,)).fetchone()
        if r:
            ids[key] = r["id"]
            continue
        cur = c.execute(
            "INSERT INTO items(key, name, description, bypass_quiet, enabled) VALUES (?,?,?,?,1)",
            (key, name, desc, bq),
        )
        ids[key] = cur.lastrowid
        c.execute(
            "INSERT INTO endpoints(item_id, name, server_url, device_key, params, enabled) "
            "VALUES (?,?,?,?,?,1)",
            (ids[key], args.endpoint_name, args.server_url, args.device_key, "{}"),
        )
    c.commit()

    have = c.execute("SELECT COUNT(*) n FROM notifications WHERE source='legacy'").fetchone()["n"]
    imported = 0
    jsonl = os.path.join(args.legacy_dir, "bark-history.jsonl")
    if os.path.isfile(jsonl) and (have == 0 or args.force):
        with open(jsonl, encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    row = json.loads(ln)
                except Exception:
                    continue
                key = GROUP_MAP.get(str(row.get("group", "")), "legacy")
                c.execute(
                    "INSERT INTO notifications(item_id, title, body, grp, level_requested, "
                    "level_effective, forced, quiet, source, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        ids[key],
                        str(row.get("title", "")),
                        str(row.get("body", "")),
                        str(row.get("group", "")),
                        str(row.get("level", "active")),
                        str(row.get("level", "active")),
                        1 if row.get("forced") else 0,
                        1 if row.get("quiet") else 0,
                        "legacy",
                        str(row.get("ts", "")),
                    ),
                )
                imported += 1
        c.commit()

    settings = {r["k"]: r["v"] for r in c.execute("SELECT k, v FROM settings")}
    print(f"items={len(ids)} legacy_imported={imported} (already_had={have})")
    print(f"settings={settings}")
    c.close()


if __name__ == "__main__":
    main()
