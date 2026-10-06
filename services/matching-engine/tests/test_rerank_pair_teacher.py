"""Comparative pair schemas, salary facts, and citations use synthetic offline transport."""

import io
import json
from email.message import Message
from urllib.request import HTTPSHandler, build_opener
from urllib.response import addinfourl

import pytest

from matching_engine.rerank_gemma_format import source_citations
from matching_engine.rerank_gemma_teacher import (
    GEMMA_TEACHER_URL,
    GemmaTeacherProviders,
)
from matching_engine.rerank_hash import digest
from matching_engine.rerank_pair_teacher import (
    COMPARATIVE_MODELS,
    COMPARATIVE_PROMPT_VERSION,
    ComparativeTeacherProviders,
    comparative_teacher_identity,
    salary_comparison,
)
from matching_engine.rerank_providers import (
    GENERATION_MODEL,
    BudgetExceeded,
    SystemOneProviders,
    teacher_identity,
)

A = {"grade": "junior", "status": "self_reported", "salary_min": 80000, "duration_months": None,
     "production_support": None, "structured_facts_available": False, "levels": {"react": 3, "teamwork": 5}}
B = {"grade": "middle", "status": "confirmed", "salary_min": 95000, "duration_months": 24,
     "production_support": False, "structured_facts_available": True, "levels": {"react": 5, "teamwork": 3}}
VACANCY = {"class": "frontend", "salary": {"min": 50000, "max": 200000}}
PREFERENCES = "Prefer teamwork and economical salary within known technical fit."


@pytest.fixture
def provider(tmp_path):
    key_file = tmp_path / "synthetic-key.txt"
    key_file.write_text("sk-unit-comparative-teacher", encoding="utf-8")
    return ComparativeTeacherProviders(tmp_path / "cache", key_file=key_file)


def _answer(a=A, b=B, label="insufficient"):
    return {"label": label, "rationale": "Tradeoff priorities are unspecified; source facts differ.",
            "evidence_A": [f"A.salary_min[int]={a['salary_min']}"],
            "evidence_B": [f"B.salary_min[int]={b['salary_min']}"],
            "uncertainties": ["Relative importance of technical fit and teamwork is unknown."],
            "salary_comparison_choice": salary_comparison(a, b)["cheaper_side"]}


def _response(model, answer=None):
    return {"model": model, "choices": [{"finish_reason": "stop",
            "message": {"content": json.dumps(_answer() if answer is None else answer)}}],
            "usage": {"total_tokens": 19}}


@pytest.mark.parametrize("first,second,side,delta", [(80000, 95000, "A", 15000), (95000, 80000, "B", -15000),
                                                    (80000, 80000, "equal", 0), (None, 80000, "unknown", None),
                                                    (True, 80000, "unknown", None), (80000.0, 95000, "unknown", None),
                                                    ("80000", 95000, "unknown", None)])
def test_salary_comparison_uses_only_known_integer_facts_and_swaps_symmetrically(first, second, side, delta):
    result = salary_comparison({"salary_min": first}, {"salary_min": second})
    assert result == {"salary_min_A": first if type(first) is int else None,
                      "salary_min_B": second if type(second) is int else None,
                      "cheaper_side": side, "delta_B_minus_A": delta}
    reverse = salary_comparison({"salary_min": second}, {"salary_min": first})
    assert reverse["salary_min_A"] == result["salary_min_B"]
    assert reverse["salary_min_B"] == result["salary_min_A"]
    assert reverse["cheaper_side"] == {"A": "B", "B": "A", "equal": "equal", "unknown": "unknown"}[side]
    assert reverse["delta_B_minus_A"] == (None if delta is None else -delta)
    assert salary_comparison({}, {}) == {"salary_min_A": None, "salary_min_B": None,
                                         "cheaper_side": "unknown", "delta_B_minus_A": None}


def test_models_share_prompt_and_schema_only_qwen_has_a_thinking_setting(provider):
    gemma = provider
    qwen = ComparativeTeacherProviders(provider.directory, model=GENERATION_MODEL, key_file=provider.key_file)
    first = gemma._pair_payload(VACANCY, A, B, PREFERENCES)
    second = qwen._pair_payload(VACANCY, A, B, PREFERENCES)
    assert first["messages"] == second["messages"]
    assert first["response_format"] == second["response_format"]
    assert first["temperature"] == second["temperature"] == 0
    assert first["max_tokens"] == second["max_tokens"] == 1400
    assert "chat_template_kwargs" not in first
    assert second["chat_template_kwargs"] == {"enable_thinking": False}
    state = json.loads(first["messages"][1]["content"])
    assert state["salary_comparison"] == salary_comparison(A, B)
    assert state["A"] == A and state["B"] == B
    citations = source_citations(A, B)
    schema = first["response_format"]["json_schema"]["schema"]
    assert schema["additionalProperties"] is False
    assert set(schema["properties"]) == set(_answer())
    for side in ("A", "B"):
        evidence = schema["properties"][f"evidence_{side}"]
        assert evidence["minItems"] == 1 and evidence["maxItems"] == 3 and evidence["uniqueItems"] is True
        assert evidence["items"]["type"] == "string"
        assert evidence["items"]["enum"] == state["available_citations"][side]
        assert all(citations[ref]["candidate"] == side for ref in evidence["items"]["enum"])
    instructions = first["messages"][0]["content"]
    for rule in ("class-only", "numeric weights", "insufficient", "Formal grade", "explicitly mentions",
                 "not a score", "even for tie or insufficient", "never reconstruct"):
        assert rule in instructions


@pytest.mark.parametrize("model", COMPARATIVE_MODELS)
def test_model_payload_authentication_exact_citations_and_replay(provider, monkeypatch, model):
    teacher = ComparativeTeacherProviders(provider.directory, model=model, key_file=provider.key_file)
    captured = []

    def respond(request):
        captured.append(request)
        return io.BytesIO(json.dumps(_response(model)).encode())

    monkeypatch.setattr(teacher, "_open", respond)
    assert teacher.judge(VACANCY, A, B, PREFERENCES) == _answer()
    request = captured[0]
    assert request.full_url == GEMMA_TEACHER_URL
    assert request.get_header("Authorization") == "Bearer sk-unit-comparative-teacher"
    payload = json.loads(request.data)
    assert payload["model"] == model
    key = digest(teacher._identity("/v1/chat/completions", payload, "pair"))
    record = json.loads((teacher.directory / f"{key}.json").read_text())
    assert record["request"]["prompt_version"] == COMPARATIVE_PROMPT_VERSION == "rerank-pair-comparative-v3"
    assert record["response"] == _response(model)
    before = {path.name: path.read_bytes() for path in teacher.directory.glob("*.json")}
    teacher.key_file.unlink()
    monkeypatch.setattr(teacher, "_open", lambda _request: pytest.fail("replay attempted HTTP"))
    monkeypatch.setattr(teacher, "_api_key", lambda: pytest.fail("replay read a key"))
    assert teacher.judge(VACANCY, A, B, PREFERENCES) == _answer()
    assert teacher.pair(VACANCY, A, B, PREFERENCES) == "insufficient"
    assert {path.name: path.read_bytes() for path in teacher.directory.glob("*.json")} == before


@pytest.mark.parametrize("label", ["A", "B", "tie", "insufficient"])
def test_all_labels_require_exact_evidence_from_both_sides(provider, monkeypatch, label):
    answer = _answer(label=label)
    monkeypatch.setattr(provider, "request", lambda *_args: _response(provider.model, answer))
    assert provider.judge(VACANCY, A, B, PREFERENCES) == answer
    answer["evidence_B"] = []
    with pytest.raises(ValueError, match="both candidates"):
        provider.judge(VACANCY, A, B, PREFERENCES)


@pytest.mark.parametrize("mutation", ["wrong_salary", "wrong_type", "edited_fact", "wrong_side", "duplicate",
                                     "too_many_refs", "invalid_label", "confidence", "missing_field", "long_rationale",
                                     "empty_rationale", "invalid_uncertainties", "too_many_uncertainties", "long_uncertainty"])
def test_invalid_salary_codes_and_citations_never_become_labels(provider, monkeypatch, mutation):
    answer = _answer()
    if mutation == "wrong_salary":
        answer["salary_comparison_choice"] = "B"
    elif mutation == "wrong_type":
        answer["evidence_A"][0] = "A.salary_min[str]=80000"
    elif mutation == "edited_fact":
        answer["evidence_A"][0] += "0"
    elif mutation == "wrong_side":
        answer["evidence_B"][0] = answer["evidence_A"][0]
    elif mutation == "duplicate":
        answer["evidence_A"] *= 2
    elif mutation == "too_many_refs":
        answer["evidence_A"] = list(source_citations(A, B))[:4]
    elif mutation == "invalid_label":
        answer["label"] = "hire"
    elif mutation == "confidence":
        answer["confidence"] = .95
    elif mutation == "missing_field":
        answer.pop("salary_comparison_choice")
    elif mutation == "long_rationale":
        answer["rationale"] = "x" * 241
    elif mutation == "empty_rationale":
        answer["rationale"] = " "
    elif mutation == "invalid_uncertainties":
        answer["uncertainties"] = [None]
    elif mutation == "too_many_uncertainties":
        answer["uncertainties"] = ["Unknown"] * 4
    else:
        answer["uncertainties"] = ["x" * 181]
    monkeypatch.setattr(provider, "request", lambda *_args: _response(provider.model, answer))
    with pytest.raises(ValueError):
        provider.judge(VACANCY, A, B, PREFERENCES)


@pytest.mark.parametrize("rationale", ["A asks for a cheaper salary.", "У A более низкая зарплата."])
def test_economy_rationale_requires_explicit_economy_preferences(provider, monkeypatch, rationale):
    answer = _answer()
    answer["rationale"] = rationale
    monkeypatch.setattr(provider, "request", lambda *_args: _response(provider.model, answer))
    with pytest.raises(ValueError, match="explicit employer preferences"):
        provider.judge(VACANCY, A, B, "Prefer React fit and teamwork.")
    assert provider.judge(VACANCY, A, B, "Prefer economical salary.") == answer


@pytest.mark.parametrize("mutation", ["model", "length", "missing_finish", "missing_model", "truncated", "content", "choices"])
def test_model_completion_and_content_contract_must_be_valid(provider, monkeypatch, mutation):
    response = _response(provider.model)
    if mutation == "model":
        response["model"] = "unapproved"
    elif mutation == "length":
        response["choices"][0]["finish_reason"] = "length"
    elif mutation == "missing_finish":
        response["choices"][0].pop("finish_reason")
    elif mutation == "missing_model":
        response.pop("model")
    elif mutation == "truncated":
        response["truncated"] = True
    elif mutation == "content":
        response["choices"][0]["message"]["content"] = None
    else:
        response["choices"] = []
    monkeypatch.setattr(provider, "request", lambda *_args: response)
    with pytest.raises((ValueError, TypeError)):
        provider.judge(VACANCY, A, B, PREFERENCES)


@pytest.mark.parametrize("model", ["gemma4", "qwen3.8", "kev-latest", "external-model"])
def test_only_exact_approved_models_are_constructible(tmp_path, model):
    with pytest.raises(ValueError, match="unknown comparative pair teacher model"):
        comparative_teacher_identity(model)
    with pytest.raises(ValueError, match="unknown comparative pair teacher model"):
        ComparativeTeacherProviders(tmp_path / "invalid-cache", model=model)
    assert not (tmp_path / "invalid-cache").exists()


def test_models_share_budget_but_use_separate_cache_keys_and_preserve_old_versions(provider, monkeypatch):
    qwen = ComparativeTeacherProviders(provider.directory, model=GENERATION_MODEL, key_file=provider.key_file)
    old = GemmaTeacherProviders(provider.directory, key_file=provider.key_file)
    systemone = SystemOneProviders(provider.directory, key_file=provider.key_file)
    old_payload = old._pair_payload(VACANCY, A, B, PREFERENCES)
    old_identity = old._identity("/v1/chat/completions", old_payload, "pair")
    for teacher in (provider, qwen):
        monkeypatch.setattr(teacher, "_open", lambda request: io.BytesIO(json.dumps(
            _response(json.loads(request.data)["model"])).encode()))
        assert teacher.judge(VACANCY, A, B, PREFERENCES) == _answer()
    assert json.loads((provider.directory / "usage.json").read_text()) == {"pair": 2}
    records = [json.loads(path.read_text()) for path in provider.directory.glob("*.json") if path.name != "usage.json"]
    assert {record["request"]["model"] for record in records} == set(COMPARATIVE_MODELS)
    assert len({record["request_hash"] for record in records}) == 2
    assert all(record["request"]["prompt_version"] == COMPARATIVE_PROMPT_VERSION for record in records)
    assert old._pair_payload(VACANCY, A, B, PREFERENCES) == old_payload
    assert old._identity("/v1/chat/completions", old_payload, "pair") == old_identity
    assert teacher_identity()["prompt_version"] == "rerank-systemone-v1"
    (provider.directory / "usage.json").write_text(json.dumps({"pair": 3000, "generation": 4, "grade": 9}))
    monkeypatch.setattr(systemone, "_api_key", lambda: pytest.fail("SystemOne bypassed shared budget"))
    with pytest.raises(BudgetExceeded):
        systemone.pair(VACANCY, A, B, PREFERENCES)
    for teacher in (provider, qwen):
        with pytest.raises(BudgetExceeded):
            teacher.judge(VACANCY, B, A, PREFERENCES)


def test_generation_grade_wrong_endpoint_and_payload_model_reject_before_reservation(provider, monkeypatch):
    monkeypatch.setattr(provider, "_open", lambda _request: pytest.fail("unsupported request attempted HTTP"))
    monkeypatch.setattr(provider, "_api_key", lambda: pytest.fail("unsupported request read a key"))
    with pytest.raises(ValueError):
        provider.generate("frontend", "synthetic", 1)
    with pytest.raises(ValueError):
        provider.grade(VACANCY, A, PREFERENCES)
    payload = provider._pair_payload(VACANCY, A, B, PREFERENCES)
    with pytest.raises(ValueError):
        provider.request("/v1/systemone", payload, "pair")
    payload["model"] = GENERATION_MODEL
    with pytest.raises(ValueError, match="model mismatch"):
        provider.request("/v1/chat/completions", payload, "pair")
    assert not (provider.directory / "usage.json").exists()


def test_cache_request_binding_is_validated_without_a_new_attempt(provider, monkeypatch):
    monkeypatch.setattr(provider, "_open", lambda _request: io.BytesIO(json.dumps(_response(provider.model)).encode()))
    provider.judge(VACANCY, A, B, PREFERENCES)
    payload = provider._pair_payload(VACANCY, A, B, PREFERENCES)
    key = digest(provider._identity("/v1/chat/completions", payload, "pair"))
    path = provider.directory / f"{key}.json"
    record = json.loads(path.read_text())
    record["request"]["payload"]["temperature"] = .9
    path.write_text(json.dumps(record))
    before = (provider.directory / "usage.json").read_bytes()
    monkeypatch.setattr(provider, "_open", lambda _request: pytest.fail("modified cache attempted HTTP"))
    monkeypatch.setattr(provider, "_api_key", lambda: pytest.fail("modified cache read a key"))
    with pytest.raises(ValueError, match="cache integrity"):
        provider.judge(VACANCY, A, B, PREFERENCES)
    assert (provider.directory / "usage.json").read_bytes() == before


def test_network_errors_are_sanitized_and_redirects_cannot_forward_credentials(provider, monkeypatch):
    def fail(_request):
        raise TimeoutError("Authorization: sk-unit-comparative-teacher")

    monkeypatch.setattr(provider, "_open", fail)
    with pytest.raises(RuntimeError) as caught:
        provider.judge(VACANCY, A, B, PREFERENCES)
    assert "sk-unit" not in str(caught.value)
    assert json.loads((provider.directory / "usage.json").read_text()) == {"pair": 1}
    monkeypatch.undo()
    import matching_engine.rerank_providers as transport
    captured = []

    class RedirectResponse(HTTPSHandler):
        def https_open(self, request):
            captured.append(request)
            headers = Message()
            headers["Location"] = "https://unapproved.example/v1/chat/completions"
            response = addinfourl(io.BytesIO(), headers, request.full_url, 302)
            response.msg = "Found"
            return response

    monkeypatch.setattr(transport, "build_opener", lambda handler: build_opener(RedirectResponse(), handler))
    with pytest.raises(RuntimeError, match="HTTPError"):
        provider.judge(VACANCY, B, A, PREFERENCES)
    assert len(captured) == 1
    assert json.loads((provider.directory / "usage.json").read_text()) == {"pair": 2}
