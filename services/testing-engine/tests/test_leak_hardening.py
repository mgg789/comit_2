"""Leak rotation, persisted shuffling and conservative similarity credit."""
import io
import json
import urllib.error

from fastapi.testclient import TestClient
from testing_engine.judge import CodeJudgeClient
from testing_engine.main import create_testing_app
from testing_engine.store import TestingStore
from testing_engine.variants import FAMILIES, instantiate, instantiate_checked


def test_parameterized_inputs_change_computed_answer():
    for family in FAMILIES:
        first, second = instantiate(family, 2, seed=1), instantiate(family, 2, seed=2)
        assert first["prompt"] != second["prompt"]
        assert first["expected_answers"] != second["expected_answers"]
        assert first == instantiate(family, 2, seed=1)
    for family in ("algorithms", "python"):
        for level in (2, 3):
            first, second = instantiate_checked(family, level, "code"), instantiate_checked(family, level, "code")
            assert first["tests"] != second["tests"]


def test_shuffle_remaps_key_and_resume_preserves_order(tmp_path, monkeypatch):
    import secrets
    monkeypatch.setattr(secrets.SystemRandom, "shuffle", lambda self, order: order.reverse())
    client = TestClient(create_testing_app(tmp_path, allow_unauthenticated=True))
    sid = client.post("/testing/sessions", json={"candidate_key": "a", "specialization": "docker"}).json()["session_id"]
    task = client.get(f"/testing/sessions/{sid}/next").json()["task"]
    original = next(row["payload"] for row in json.loads((tmp_path / "items.json").read_text()) if row["item_id"] == task["item_id"])
    assert task["options"] == original["options"][::-1]
    assert "answer_index" not in task
    assert client.get(f"/testing/sessions/{sid}/next").json()["task"] == task
    choice = task["options"].index(original["options"][original["answer_index"]])
    assert client.post(f"/testing/sessions/{sid}/answer", json={"item_id":task["item_id"],"answer":choice}).json()["correct"] is True


def test_busy_does_not_consume_attempt_or_grade(tmp_path, monkeypatch):
    monkeypatch.setenv("TESTING_ENGINE_TOKEN", "isolated-token")
    monkeypatch.setenv("TESTING_RECORDING_MODE", "off")
    monkeypatch.setattr(CodeJudgeClient, "ready", lambda self: True)
    monkeypatch.setattr(CodeJudgeClient, "judge", lambda *args, **kwargs: (None, "runner_busy"))
    client = TestClient(create_testing_app(tmp_path), headers={"x-internal-token":"isolated-token","x-candidate-id":"owner"})
    started=client.post("/testing/sessions",json={"specialization":"python","consents":{"processing":True,"version":"testing-media-v1","screen_recording":False}})
    sid=started.json()["session_id"]
    first=client.get(f"/testing/sessions/{sid}/next").json()["task"]
    client.post(f"/testing/sessions/{sid}/answer",json={"item_id":first["item_id"],"answer":0})
    code=client.get(f"/testing/sessions/{sid}/next").json()["task"]
    assert code["type"]=="code"
    for _ in range(5):
        reply=client.post(f"/testing/sessions/{sid}/answer",json={"item_id":code["item_id"],"answer":"def flatten(x): return []"})
        assert reply.status_code==503 and reply.json()["detail"]=="runner_busy"
    session=TestingStore(tmp_path).read_session(sid)
    assert session["status"]=="active"
    assert session["state"]["answered"]==1
    assert session["items"][-1].get("judge_attempts",0)==0


def test_judge_preserves_busy_reason(monkeypatch):
    client=CodeJudgeClient("http://isolated")
    client.token="isolated"
    def busy(*args):
        raise urllib.error.HTTPError("http://isolated",503,"busy",None,io.BytesIO(b'{"detail":"runner_busy"}'))
    monkeypatch.setattr(client,"_request",busy)
    assert client.judge({"function_name":"solve","tests":[]},"code")== (None,"runner_busy")


def test_similarity_is_audit_evidence_not_plagiarism_verdict(tmp_path):
    store=TestingStore(tmp_path)
    sid=store.create_session("prior","python",None)
    session=store.read_session(sid)
    session["items"]=[{"item_id":"code-1","payload":{"type":"code"},"answer":"def solve(x): return x + 1"}]
    store.write_session(session)
    evidence=store.audit_code_similarity("next","code-1","def solve(x): return x + 1",True)
    assert evidence["maximum_similarity"]==1 and evidence["review_flag"]
    assert evidence["credited"] and evidence["credit_policy"]=="independent_judge"
    logged=(tmp_path/"code-similarity.jsonl").read_text()
    assert "def solve" not in logged and '"prior"' not in logged
    assert not store.audit_code_similarity("next","code-1","bad syntax",False)["credited"]


def test_old_leaked_instance_never_returns_next_day(tmp_path):
    store = TestingStore(tmp_path)
    old_id = store.add_generated_item(instantiate("docker", 2, seed=1))
    bank = json.loads(store.items_path.read_text())
    bank[0]["last_served_date"] = "2020-01-01"
    store.items_path.write_text(json.dumps(bank))
    assert not store.claim_item(old_id)
    assert all(row["item_id"] != old_id for row in store.list_items("docker", 2))
