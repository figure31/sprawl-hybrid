"""HTTP helpers for Lambda responses and request parsing."""

from __future__ import annotations

import json
from typing import Any, Optional

from . import db


def response(status: int, body: Any, cache_seconds: int = 0) -> dict:
    headers = {
        "Content-Type": "application/json",
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type",
    }
    if cache_seconds > 0:
        headers["Cache-Control"] = f"public, max-age={cache_seconds}"
    return {
        "statusCode": status,
        "headers": headers,
        "body": db.to_json(body) if not isinstance(body, str) else body,
    }


def ok(body: Any, cache_seconds: int = 0) -> dict:
    return response(200, body, cache_seconds)


def bad_request(code: str, detail: str = "") -> dict:
    return response(400, {"error": code, "detail": detail})


def unauthorized(code: str = "unauthorized", detail: str = "") -> dict:
    return response(401, {"error": code, "detail": detail})


def forbidden(code: str = "forbidden", detail: str = "") -> dict:
    return response(403, {"error": code, "detail": detail})


def not_found(code: str = "not_found", detail: str = "") -> dict:
    return response(404, {"error": code, "detail": detail})


def server_error(detail: str = "") -> dict:
    return response(500, {"error": "internal_error", "detail": detail})


def parse_body(event: dict) -> dict:
    raw = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        import base64
        raw = base64.b64decode(raw).decode("utf-8")
    try:
        return json.loads(raw)
    except Exception:
        return {}


def path_param(event: dict, name: str) -> Optional[str]:
    params = event.get("pathParameters") or {}
    return params.get(name)


def query_param(event: dict, name: str, default: Optional[str] = None) -> Optional[str]:
    q = event.get("queryStringParameters") or {}
    return q.get(name, default)
