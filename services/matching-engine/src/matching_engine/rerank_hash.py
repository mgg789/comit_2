"""Canonical identities shared by offline experiments and runtime artifacts."""

import hashlib
import json


def digest(value: object) -> str:
    """SHA-256 of strict canonical JSON; nonfinite parameters are rejected."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False, allow_nan=False).encode()).hexdigest()
