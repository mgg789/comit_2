"""Synthetic HTTP acceptance against CODE_JUDGE_URL; never executes code locally."""

import json
import os
import ssl
import urllib.request
import uuid


def main() -> None:
    """Verify authenticated readiness, correct/wrong code and identical replay."""
    base = os.environ["CODE_JUDGE_URL"].rstrip("/")
    token = os.environ["CODE_JUDGE_TOKEN"]
    context = ssl.create_default_context()
    private_ca = os.getenv("CODE_JUDGE_CA_PEM", "").replace("\\n", "\n")
    if private_ca:
        context.load_verify_locations(cadata=private_ca)

    def request(path: str, body: dict | None = None) -> dict:
        call = urllib.request.Request(base + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"content-type": "application/json", "x-internal-token": token})
        with urllib.request.urlopen(call, timeout=60, context=context) as response:
            return json.loads(response.read())

    assert request("/ready")["runner"] == "docker-isolated"
    base_body = {"owner_id": "synthetic-acceptance", "item_id": "synthetic-double",
                 "problem": {"function_name": "solve", "tests": [
                     {"args": [3], "expected": 6}, {"args": [7], "expected": 14}]}}
    for code, expected in (("def solve(n): return n * 2", True), ("def solve(n): return n", False)):
        body = {**base_body, "code": code, "idempotency_key": uuid.uuid4().hex}
        first = request("/submissions", body)
        assert first["correct"] is expected
        assert request("/submissions", body) == first
    print("PASS: ready, correct, wrong, durable identical replay")


if __name__ == "__main__":
    main()
