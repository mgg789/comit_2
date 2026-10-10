#!/usr/bin/env python3
"""Verify a disposable QGramm container's encrypted files and durable restart.

Requires CHAT_TEST_SERVICE_URL, CHAT_TEST_SERVICE_TOKEN and
CHAT_TEST_CONTAINER. Use only a dedicated synthetic container: this test
creates a conversation, copies its storage for inspection, and restarts it.
No credentials or message contents are printed.
"""

import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import urllib.request
import uuid


def main() -> None:
    """Exercise the actual HTTP façade, storage volume and Docker restart."""
    url = os.environ["CHAT_TEST_SERVICE_URL"].rstrip("/")
    token = os.environ["CHAT_TEST_SERVICE_TOKEN"]
    container = os.environ["CHAT_TEST_CONTAINER"]
    if not container.startswith("comit-qgramm-chat-"):
        raise ValueError("Use an explicitly dedicated comit-qgramm-chat-* container")
    context = {
        "process_id": str(uuid.uuid4()),
        "owner_id": str(uuid.uuid4()),
        "candidate_id": str(uuid.uuid4()),
    }
    context["actor_id"] = context["owner_id"]

    def call(operation: str, fields: dict) -> dict:
        request = urllib.request.Request(
            f"{url}/internal/chat/{operation}",
            data=json.dumps({**context, **fields}).encode(),
            headers={"Content-Type": "application/json", "X-Internal-Token": token},
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)

    marker = f"synthetic-private-{uuid.uuid4()}"
    filename = f"private-name-{uuid.uuid4()}.txt"
    data = (marker.encode() * 40000) + b"final chunk"
    file = call("upload", {
        "idempotency_key": str(uuid.uuid4()), "name": filename,
        "media_type": "text/plain", "data_base64": base64.b64encode(data).decode(),
    })
    assert file["size"] == len(data)
    assert file["sha256"] == hashlib.sha256(data).hexdigest()
    operation = str(uuid.uuid4())
    message = call("send", {"idempotency_key": operation, "text": marker, "files": [file]})
    subprocess.run(["docker", "restart", "--time", "10", container], check=True, capture_output=True)
    deadline = time.monotonic() + 30
    while True:
        try:
            with urllib.request.urlopen(f"{url}/readyz", timeout=2) as response:
                assert json.load(response)["status"] == "ok"
            break
        except (OSError, AssertionError):
            if time.monotonic() >= deadline:
                raise RuntimeError("Chat did not become ready after restart") from None
            time.sleep(0.2)
    context["actor_id"] = context["candidate_id"]
    page = call("history", {"after_sequence": 0, "limit": 100})
    assert page["messages"][0] == message
    events = call("events", {"after_sequence": 0, "limit": 100})
    assert any(event.get("message") == message for event in events["events"])
    resumed = call("events", {"after_sequence": events["next_sequence"], "limit": 100})
    assert resumed["events"] == []
    downloaded = call("download", {"file_id": file["id"]})
    assert base64.b64decode(downloaded["data_base64"], validate=True) == data
    assert downloaded["name"] == filename
    context["actor_id"] = context["owner_id"]
    assert call("send", {"idempotency_key": operation, "text": marker, "files": [file]}) == message
    # Read a copy of synthetic storage, never host/production data or keys.
    with tempfile.TemporaryDirectory(prefix="comit-chat-ciphertext-") as directory:
        subprocess.run(["docker", "cp", f"{container}:/data/.", directory], check=True, capture_output=True)
        files = [path for path in Path(directory).rglob("*") if path.is_file()]
        assert files, "Missing persisted storage"
        for path in files:
            stored = path.read_bytes()
            assert marker.encode() not in stored, "Plaintext content found in storage"
            assert filename.encode() not in stored, "Plaintext filename found in storage"
    print(json.dumps({"status": "passed", "restart": True, "file_round_trip": True,
                      "encrypted_storage": True, "durable_idempotency": True, "event_replay": True}))


if __name__ == "__main__":
    main()
