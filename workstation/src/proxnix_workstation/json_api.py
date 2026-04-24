from __future__ import annotations

import json
from typing import Any


def ok(data: object, *, warnings: list[str] | None = None) -> dict[str, object]:
    return {
        "ok": True,
        "data": data,
        "warnings": warnings or [],
        "error": None,
    }


def error(code: str, message: str, *, details: dict[str, object] | None = None) -> dict[str, object]:
    return {
        "ok": False,
        "data": None,
        "warnings": [],
        "error": {
            "code": code,
            "message": message,
            "details": details or {},
        },
    }


def dumps(payload: object) -> str:
    return json.dumps(payload, indent=2, sort_keys=True)


def print_json(payload: object) -> None:
    print(dumps(payload))


JsonObject = dict[str, Any]
