"""Bounded keyset cursors tied to one authorized query and database snapshot."""
import base64
import json

from fastapi import HTTPException


def encode_cursor(scope: str, snapshot: str, key: list) -> str:
    """Serialize a stable SQL key, never an offset or a grant of authority."""
    return base64.urlsafe_b64encode(json.dumps({"v": 1, "scope": scope, "snapshot": snapshot, "key": key}, separators=(",", ":")).encode()).decode().rstrip("=")


def decode_cursor(cursor: str | None, scope: str, snapshot: str) -> list | None:
    """Reject malformed or drifted continuations before querying their SQL key."""
    if cursor is None:
        return None
    try:
        if len(cursor) > 1024:
            raise ValueError
        value = json.loads(base64.b64decode(cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True))
        if set(value) != {"v", "scope", "snapshot", "key"} or value["v"] != 1 or value["scope"] != scope or value["snapshot"] != snapshot or not isinstance(value["key"], list):
            raise ValueError
        return value["key"]
    except (ValueError, TypeError, UnicodeError):
        raise HTTPException(409, "page cursor changed or invalid; restart listing") from None
