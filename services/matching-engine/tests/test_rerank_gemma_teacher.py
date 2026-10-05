"""Pair-only Gemma evidence, caching, and budgets are verified without live HTTP."""

import io
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from email.message import Message
from urllib.request import HTTPSHandler, build_opener
from urllib.response import addinfourl

import pytest

from matching_engine.rerank_gemma_teacher import (
    EVIDENCE_FIELDS,
    GEMMA_TEACHER_MODEL,
    GEMMA_TEACHER_PROMPT_VERSION,
    GEMMA_TEACHER_URL,
    GemmaTeacherProviders,
    gemma_teacher_identity,
)
from matching_engine.rerank_hash import digest
from matching_engine.rerank_providers import (
    BudgetExceeded,
    SystemOneProviders,
    teacher_identity,
)

A = {"grade": "junior", "status": "self_reported", "salary_min": 80000, "duration_months": None,
     "production_support": None, "structured_facts_available": False,
     "levels": {"python": 2, "react": 3, "sql": 2, "rest_api": 3, "teamwork": 4}}
B = {"grade": "middle", "status": "confirmed", "salary_min": 95000, "duration_months": 24,
     "production_support": False, "structured_facts_available": True,
     "levels": {"python": 3, "react": 5, "sql": 3, "rest_api": 4, "teamwork": 4}}
VACANCY = {"class": "frontend", "stacks": {"react": 5}, "salary": {"min": 50000, "max": 200000}}


@pytest.fixture
def provider(tmp_path):
    key_file = tmp_path / "synthetic-key.txt"
    key_file.write_text("sk-unit-gemma-teacher", encoding="utf-8")
    return GemmaTeacherProviders(tmp_path / "cache", key_file=key_file)


def _judgement(label="B"):
    return {"label": label, "rationale": "B declares stronger React skill; A's experience is unknown.",
            "evidence": [{"candidate": "A", "field": "levels.react", "value": 3, "reason": "A declares level 3."},
                         {"candidate": "B", "field": "levels.react", "value": 5, "reason": "B declares level 5."}],
            "uncertainties": ["A's duration and production support are unknown."]}


def _response(judgement=None, *, finish_reason="stop", model=GEMMA_TEACHER_MODEL):
    return {"model": model, "choices": [{"finish_reason": finish_reason,
            "message": {"content": json.dumps(_judgement() if judgement is None else judgement)}}],
            "usage": {"total_tokens": 21}}


def test_gemma_judge_uses_fixed_endpoint_schema_and_shared_pair_receipt(provider, monkeypatch):
    captured = []

    def respond(request):
        captured.append(request)
        return io.BytesIO(json.dumps(_response()).encode())

    monkeypatch.setattr(provider, "_open", respond)
    assert provider.judge(VACANCY, A, B, "Prefer React fit") == _judgement()
    request = captured[0]
    assert request.full_url == GEMMA_TEACHER_URL == "https://ai.sverk.tech/v1/chat/completions"
    assert request.get_header("Authorization") == "Bearer sk-unit-gemma-teacher"
    payload = json.loads(request.data)
    assert payload["model"] == GEMMA_TEACHER_MODEL == "gemma4-vlm"
    assert payload["temperature"] == 0 and payload["max_tokens"] == 1400
    assert "chat_template_kwargs" not in payload and "enable_thinking" not in payload
    state = json.loads(payload["messages"][1]["content"])
    assert list(state) == ["vacancy", "preferences", "A", "B"]
    assert state == {"vacancy": VACANCY, "preferences": "Prefer React fit", "A": A, "B": B}
    assert "both candidates" in payload["messages"][0]["content"]
    schema = payload["response_format"]["json_schema"]
    assert schema["strict"] is True and schema["schema"]["additionalProperties"] is False
    assert set(schema["schema"]["properties"]) == {"label", "rationale", "evidence", "uncertainties"}
    assert set(schema["schema"]["properties"]["evidence"]["items"]["properties"]["field"]["enum"]) == set(EVIDENCE_FIELDS)
    assert json.loads((provider.directory / "usage.json").read_text()) == {"pair": 1}
    key = digest(provider._identity("/v1/chat/completions", payload, "pair"))
    record = json.loads((provider.directory / f"{key}.json").read_text())
    assert record["request"]["kind"] == "pair"
    assert record["request"]["prompt_version"] == GEMMA_TEACHER_PROMPT_VERSION == "rerank-gemma-pair-v1"
    assert record["response"] == _response()
    assert all("sk-unit" not in path.read_text() for path in provider.directory.glob("*.json"))
    assert teacher_identity()["model"] == "kev-latest"


def test_cached_judgement_and_pair_replay_without_key_or_http(provider, monkeypatch):
    monkeypatch.setattr(provider, "_open", lambda _request: io.BytesIO(json.dumps(_response()).encode()))
    assert provider.judge(VACANCY, A, B, "Prefer React fit")["label"] == "B"
    before = {path.name: path.read_bytes() for path in provider.directory.glob("*.json")}
    provider.key_file.unlink()
    monkeypatch.setattr(provider, "_api_key", lambda: pytest.fail("replay read a credential"))
    monkeypatch.setattr(provider, "_open", lambda _request: pytest.fail("replay attempted HTTP"))
    assert provider.judge(VACANCY, A, B, "Prefer React fit") == _judgement()
    assert provider.pair(VACANCY, A, B, "Prefer React fit") == "B"
    assert {path.name: path.read_bytes() for path in provider.directory.glob("*.json")} == before


@pytest.mark.parametrize("field", EVIDENCE_FIELDS)
def test_evidence_can_quote_each_approved_exact_primitive_field(provider, monkeypatch, field):
    value = A
    for key in field.split("."):
        value = value[key]
    judgement = _judgement("tie")
    judgement["evidence"] = [{"candidate": "A", "field": field, "value": value, "reason": "Exact source fact."}]
    monkeypatch.setattr(provider, "request", lambda *_args: _response(judgement))
    assert provider.judge(VACANCY, A, B, "synthetic") == judgement


@pytest.mark.parametrize("mutation", ["wrong_value", "wrong_type", "bool_as_int", "unknown_candidate",
                                     "unapproved_path", "missing_path", "extra_evidence_field", "only_one_candidate",
                                     "nested_value", "empty_reason", "empty_rationale", "confidence", "invalid_label",
                                     "invalid_uncertainty", "too_many_uncertainties", "too_many_evidence"])
def test_hallucinations_and_invalid_judgement_contract_are_rejected(provider, monkeypatch, mutation):
    judgement = _judgement()
    a = deepcopy(A)
    if mutation == "wrong_value":
        judgement["evidence"][0]["value"] = 4
    elif mutation == "wrong_type":
        judgement["evidence"][0]["value"] = 3.0
    elif mutation == "bool_as_int":
        judgement["evidence"][1].update(field="production_support", value=0)
    elif mutation == "unknown_candidate":
        judgement["evidence"][0]["candidate"] = "C"
    elif mutation == "unapproved_path":
        judgement["evidence"][0]["field"] = "metadata.score"
    elif mutation == "missing_path":
        a["levels"].pop("react")
    elif mutation == "extra_evidence_field":
        judgement["evidence"][0]["confidence"] = .9
    elif mutation == "only_one_candidate":
        judgement["evidence"].pop()
    elif mutation == "nested_value":
        a["grade"] = {"role": "junior"}
        judgement["evidence"][0].update(field="grade", value={"role": "junior"})
    elif mutation == "empty_reason":
        judgement["evidence"][0]["reason"] = " "
    elif mutation == "empty_rationale":
        judgement["rationale"] = ""
    elif mutation == "confidence":
        judgement["confidence"] = .9
    elif mutation == "invalid_label":
        judgement["label"] = "hire"
    elif mutation == "invalid_uncertainty":
        judgement["uncertainties"] = [None]
    elif mutation == "too_many_uncertainties":
        judgement["uncertainties"] = ["Unknown"] * 9
    else:
        judgement["evidence"] *= 5
    monkeypatch.setattr(provider, "request", lambda *_args: _response(judgement))
    with pytest.raises(ValueError):
        provider.judge(VACANCY, a, B, "synthetic")


@pytest.mark.parametrize("mutation", ["truncated", "model", "choices", "message", "nonobject", "extra_fields"])
def test_chat_response_truncation_model_and_structure_are_rejected(provider, monkeypatch, mutation):
    response = _response()
    if mutation == "truncated":
        response["choices"][0]["finish_reason"] = "length"
    elif mutation == "model":
        response["model"] = "qwen3.8-27b"
    elif mutation == "choices":
        response["choices"] = []
    elif mutation == "message":
        response["choices"][0]["message"]["content"] = None
    elif mutation == "nonobject":
        response["choices"][0]["message"]["content"] = "[]"
    else:
        judgement = _judgement()
        judgement["raw_score"] = 100
        response = _response(judgement)
    monkeypatch.setattr(provider, "request", lambda *_args: response)
    with pytest.raises((ValueError, TypeError)):
        provider.judge(VACANCY, A, B, "synthetic")


def test_missing_optional_model_metadata_and_insufficient_without_evidence_are_supported(provider, monkeypatch):
    judgement = {"label": "insufficient", "rationale": "No justified preference is supported.",
                 "evidence": [], "uncertainties": ["Relevant experience is unknown."]}
    response = _response(judgement)
    response.pop("model")
    monkeypatch.setattr(provider, "request", lambda *_args: response)
    assert provider.judge(VACANCY, A, B, "synthetic") == judgement


@pytest.mark.parametrize("path,kind", [("/v1/systemone", "pair"), ("/v1/chat/completions", "grade"),
                                      ("/v1/chat/completions", "generation")])
def test_endpoint_and_kind_isolation_reject_before_budget_or_key(provider, monkeypatch, path, kind):
    monkeypatch.setattr(provider, "_api_key", lambda: pytest.fail("invalid endpoint read a key"))
    monkeypatch.setattr(provider, "_open", lambda _request: pytest.fail("invalid endpoint attempted HTTP"))
    with pytest.raises(ValueError):
        provider.request(path, provider._pair_payload(VACANCY, A, B, "synthetic"), kind)
    assert not (provider.directory / "usage.json").exists()


def test_public_generation_grade_and_other_models_are_rejected_before_budget(provider, monkeypatch):
    monkeypatch.setattr(provider, "_api_key", lambda: pytest.fail("unsupported method read a key"))
    monkeypatch.setattr(provider, "_open", lambda _request: pytest.fail("unsupported method attempted HTTP"))
    with pytest.raises(ValueError, match="only pair"):
        provider.generate("frontend", "synthetic", 1)
    with pytest.raises(ValueError, match="only pair"):
        provider.grade(VACANCY, A, "synthetic")
    payload = provider._pair_payload(VACANCY, A, B, "synthetic")
    payload["model"] = "kev-latest"
    with pytest.raises(ValueError, match="model mismatch"):
        provider.request("/v1/chat/completions", payload, "pair")
    assert not (provider.directory / "usage.json").exists()


def test_pair_budget_is_shared_with_systemone_and_cannot_reset(provider, monkeypatch):
    (provider.directory / "usage.json").write_text(json.dumps({"pair": 2999, "grade": 7, "generation": 2}))
    monkeypatch.setattr(provider, "_open", lambda _request: io.BytesIO(json.dumps(_response()).encode()))
    assert provider.judge(VACANCY, A, B, "synthetic")["label"] == "B"
    assert json.loads((provider.directory / "usage.json").read_text()) == {"pair": 3000, "grade": 7, "generation": 2}
    systemone = SystemOneProviders(provider.directory, key_file=provider.key_file)
    monkeypatch.setattr(systemone, "_open", lambda _request: pytest.fail("SystemOne bypassed shared pair ceiling"))
    with pytest.raises(BudgetExceeded):
        systemone.pair(VACANCY, A, B, "synthetic")
    with pytest.raises(BudgetExceeded):
        provider.judge(VACANCY, B, A, "synthetic")


def test_gemma_and_systemone_share_five_remote_slots(provider, monkeypatch):
    systemone = SystemOneProviders(provider.directory, key_file=provider.key_file)
    active = maximum = 0
    guard = threading.Lock()
    saturated = threading.Event()

    def respond(request):
        nonlocal active, maximum
        with guard:
            active += 1
            maximum = max(maximum, active)
            if active == 5:
                saturated.set()
        saturated.wait(timeout=.5)
        time.sleep(.02)
        with guard:
            active -= 1
        return io.BytesIO(json.dumps(_response()).encode())

    monkeypatch.setattr(provider, "_open", respond)
    monkeypatch.setattr(systemone, "_open", respond)

    def call(index):
        teacher = provider if index % 2 == 0 else systemone
        path = "/v1/chat/completions" if teacher is provider else "/v1/systemone"
        teacher.request(path, {"model": GEMMA_TEACHER_MODEL, "synthetic": index}, "pair")

    with ThreadPoolExecutor(max_workers=10) as executor:
        list(executor.map(call, range(10)))
    assert maximum == 5
    assert json.loads((provider.directory / "usage.json").read_text()) == {"pair": 10}


def test_remote_failure_is_sanitized_and_records_one_shared_attempt(provider, monkeypatch):
    def fail(_request):
        raise TimeoutError("Authorization: sk-unit-gemma-teacher")

    monkeypatch.setattr(provider, "_open", fail)
    with pytest.raises(RuntimeError, match="TimeoutError") as caught:
        provider.judge(VACANCY, A, B, "synthetic")
    assert "sk-unit" not in str(caught.value)
    assert json.loads((provider.directory / "usage.json").read_text()) == {"pair": 1}
    assert len(list(provider.directory.glob("*.failure-1.json"))) == 1
    assert all("sk-unit" not in path.read_text() for path in provider.directory.glob("*.json"))


def test_gemma_redirect_is_blocked_before_forwarding_a_bearer_key(provider, monkeypatch):
    import matching_engine.rerank_providers as transport
    captured = []

    class RedirectResponse(HTTPSHandler):
        def https_open(self, request):
            captured.append(request)
            assert request.full_url == GEMMA_TEACHER_URL
            headers = Message()
            headers["Location"] = "https://unapproved.example/v1/chat/completions"
            response = addinfourl(io.BytesIO(), headers, request.full_url, 302)
            response.msg = "Found"
            return response

    monkeypatch.setattr(transport, "build_opener", lambda handler: build_opener(RedirectResponse(), handler))
    with pytest.raises(RuntimeError, match="HTTPError"):
        provider.judge(VACANCY, A, B, "synthetic")
    assert len(captured) == 1


def test_teacher_identity_is_distinct_from_systemone_and_gemma_generation(provider):
    identity = gemma_teacher_identity()
    assert identity == {"endpoint": GEMMA_TEACHER_URL, "model": GEMMA_TEACHER_MODEL,
                        "prompt_version": "rerank-gemma-pair-v1"}
    assert set(identity) == {"endpoint", "model", "prompt_version"}
    payload = provider._pair_payload(VACANCY, A, B, "synthetic")
    systemone = SystemOneProviders(provider.directory, key_file=provider.key_file, generator_model=GEMMA_TEACHER_MODEL)
    key = digest(provider._identity("/v1/chat/completions", payload, "pair"))
    assert key != digest(systemone._identity("/v1/chat/completions", payload, "generation"))
    assert key != digest(systemone._identity("/v1/systemone", payload, "pair"))


def test_cache_request_body_tampering_is_rejected_without_http_or_budget_delta(provider, monkeypatch):
    monkeypatch.setattr(provider, "_open", lambda _request: io.BytesIO(json.dumps(_response()).encode()))
    provider.judge(VACANCY, A, B, "synthetic")
    payload = provider._pair_payload(VACANCY, A, B, "synthetic")
    key = digest(provider._identity("/v1/chat/completions", payload, "pair"))
    path = provider.directory / f"{key}.json"
    record = json.loads(path.read_text())
    record["request"]["payload"]["max_tokens"] = 10000
    path.write_text(json.dumps(record), encoding="utf-8")
    before = (provider.directory / "usage.json").read_bytes()
    monkeypatch.setattr(provider, "_api_key", lambda: pytest.fail("tampered cache read credentials"))
    monkeypatch.setattr(provider, "_open", lambda _request: pytest.fail("tampered cache attempted HTTP"))
    with pytest.raises(ValueError, match="Gemma teacher cache integrity"):
        provider.judge(VACANCY, A, B, "synthetic")
    assert (provider.directory / "usage.json").read_bytes() == before
