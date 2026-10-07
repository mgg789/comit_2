"""Cursor query binding and malformed input never become an offset or a grant."""
import pytest
from fastapi import HTTPException
from matching_engine.page_cursor import decode_cursor, encode_cursor


def test_cursor_scope_snapshot_and_key_roundtrip():
    cursor = encode_cursor("pool", "snapshot", ["backend", 2, "candidate-id"])
    assert decode_cursor(cursor, "pool", "snapshot") == ["backend", 2, "candidate-id"]
    for scope, snapshot in (("other", "snapshot"), ("pool", "new")):
        with pytest.raises(HTTPException) as error:
            decode_cursor(cursor, scope, snapshot)
        assert error.value.status_code == 409
    for value in ("!", "x" * 1025, "W10"):
        with pytest.raises(HTTPException):
            decode_cursor(value, "pool", "snapshot")
