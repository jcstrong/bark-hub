"""Bark-compatible push routes (heimdallr-style).

Any client that can talk to a Bark server can talk to bark-hub:
swap `https://api.day.app/<device_key>` for `http://<hub>/<item_key>`.

- POST /<item_key>            form/query encoded (title, body, group, level, ...)
- GET|POST /<item_key>/<title>/<body>
"""
from __future__ import annotations

import re

from fastapi import APIRouter, Request
from starlette.concurrency import run_in_threadpool

from . import notify

router = APIRouter()

KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
LEVELS = {"active", "passive", "timeSensitive", "critical"}
RESERVED = {"title", "body", "group", "level", "force", "isForce", "source", "copy", "msg_type"}


def _bool(v) -> bool:
    return str(v).lower() in ("1", "true", "yes")


def _dispatch(item_key: str, data: dict) -> dict:
    level = str(data.get("level") or "") or None
    if level and level not in LEVELS:
        level = None
    extra = {k: str(v) for k, v in data.items() if k not in RESERVED and k not in ("title", "body")}
    return notify.send(
        item_key,
        str(data.get("title") or ""),
        str(data.get("body") or ""),
        group=str(data.get("group") or ""),
        level=level,
        force=_bool(data.get("force", data.get("isForce", 0))),
        source=str(data.get("source") or ""),
        extra=extra or None,
    )


@router.post("/{item_key}")
async def push_form(item_key: str, request: Request):
    if not KEY_RE.match(item_key):
        return {"code": 400, "message": "bad item key"}
    data = dict(request.query_params)
    form = await request.form()
    for k in form.keys():
        data[k] = form[k]
    return await run_in_threadpool(_dispatch, item_key, data)


@router.api_route("/{item_key}/{title}/{body}", methods=["GET", "POST"])
async def push_path(item_key: str, title: str, body: str, request: Request):
    if not KEY_RE.match(item_key):
        return {"code": 400, "message": "bad item key"}
    data = dict(request.query_params)
    data.setdefault("title", title)
    data.setdefault("body", body)
    if request.method == "POST":
        form = await request.form()
        for k in form.keys():
            data[k] = form[k]
    return await run_in_threadpool(_dispatch, item_key, data)
