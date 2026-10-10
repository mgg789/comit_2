"""Grounded pair answers preserve source evidence states without HTTP or real credentials."""

import io
import json
from copy import deepcopy

import pytest

from matching_engine.rerank_gemma_format import source_citations
from matching_engine.rerank_grounded_teacher import (
    ANSWER_FIELDS,
    GROUNDED_PROMPT_VERSION,
    REASON_CODES,
    SOURCE_STATUSES,
    GroundedTeacherProviders,
    grounded_explanation,
    grounded_teacher_identity,
)
from matching_engine.rerank_hash import digest
from matching_engine.rerank_pair_teacher import (
    COMPARATIVE_PROMPT_VERSION,
    COMPATIBLE_PROMPT_VERSION,
    ComparativeTeacherProviders,
    CompatibleTeacherProviders,
    salary_comparison,
)
from matching_engine.rerank_providers import (
    GEMMA_GENERATION_MODEL,
    GENERATION_MODEL,
    LIMITS,
    BudgetExceeded,
    teacher_identity,
)

A = {"grade": "junior", "status": "self_reported", "salary_min": 80000, "duration_months": None,
     "production_support": None, "structured_facts_available": False,
     "levels": {"python": 2, "react": 3, "sql": 2, "rest_api": 3, "teamwork": 6}}
B = {"grade": "middle", "status": "confirmed", "salary_min": 95000, "duration_months": 24,
     "production_support": False, "structured_facts_available": True,
     "levels": {"python": 3, "react": 5, "sql": 3, "rest_api": 4, "teamwork": 4}}
VACANCY = {"class": "frontend", "salary": {"min": 50000, "max": 200000}}
PREFERENCES = "Consider technical fit, salary economy, and teamwork."


def _ref(a, b, side, field):
    return next(token for token, fact in source_citations(a, b).items()
                if fact["candidate"] == side and fact["field"] == field)


def _answer(a=A, b=B, label="insufficient", reason="missing_tradeoff_priorities"):
    result = {"label": label, "reason_code": reason,
              "salary_comparison_choice": salary_comparison(a, b)["cheaper_side"]}
    for side in ("A", "B"):
        result[f"evidence_{side}"] = [_ref(a, b, side, "status"), _ref(a, b, side, "levels.react")]
        result[f"unknown_{side}"] = [token for token, fact in source_citations(a, b).items()
                                     if fact["candidate"] == side and fact["value"] is None][:3]
    return result


def _response(answer, model=GENERATION_MODEL):
    return {"model": model, "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(answer)}}],
            "usage": {"total_tokens": 20}}


def _mock(provider, monkeypatch, answer, *, a=A, b=B):
    monkeypatch.setattr(provider, "request", lambda *_args: _response(answer, provider.model))
    return provider.judge(VACANCY, a, b, PREFERENCES)


def test_payload_is_portable_identical_across_models_and_keeps_original_facts(tmp_path):
    qwen = GroundedTeacherProviders(tmp_path / "qwen")
    gemma = GroundedTeacherProviders(tmp_path / "gemma", model=GEMMA_GENERATION_MODEL)
    before = deepcopy((VACANCY, A, B))
    q_payload = qwen._pair_payload(VACANCY, A, B, PREFERENCES)
    g_payload = gemma._pair_payload(VACANCY, A, B, PREFERENCES)
    assert q_payload["chat_template_kwargs"] == {"enable_thinking": False}
    assert "chat_template_kwargs" not in g_payload
    assert {k: v for k, v in q_payload.items() if k not in ("model", "chat_template_kwargs")} == {
        k: v for k, v in g_payload.items() if k != "model"}
    assert q_payload["max_tokens"] == 700 and q_payload["temperature"] == 0
    spec = q_payload["response_format"]["json_schema"]
    assert spec["strict"] is True
    schema = spec["schema"]
    assert set(schema["properties"]) == set(schema["required"]) == set(ANSWER_FIELDS)
    assert schema["additionalProperties"] is False
    assert schema["properties"]["reason_code"]["enum"] == list(REASON_CODES)
    assert "uniqueItems" not in json.dumps(schema)
    assert schema["properties"]["unknown_B"] == {"type": "array", "maxItems": 0, "items": {"type": "string"}}
    assert schema["properties"]["unknown_A"]["items"]["enum"] == _answer()["unknown_A"]
    assert schema["properties"]["evidence_A"]["minItems"] == 2
    assert schema["properties"]["evidence_A"]["maxItems"] == 4
    state = json.loads(q_payload["messages"][1]["content"])
    assert state["A"] == A and state["B"] == B and state["vacancy"] == VACANCY
    assert state["preferences"] == PREFERENCES
    assert state["salary_comparison"] == salary_comparison(A, B)
    assert state["available_unknowns"] == {"A": _answer()["unknown_A"], "B": []}
    assert (VACANCY, A, B) == before
    assert not list(qwen.directory.glob("*.json"))
    instructions = q_payload["messages"][0]["content"]
    assert "class-only" in instructions and "numeric weights" in instructions and "technical minima" in instructions
    assert "Return JSON only with label, evidence_A" in instructions
    assert "a concise rationale" not in instructions and "Rationale is one" not in instructions


@pytest.mark.parametrize("model", [GENERATION_MODEL, GEMMA_GENERATION_MODEL])
def test_actual_mock_transport_raw_cache_replay_and_identity_are_safe(tmp_path, monkeypatch, model):
    provider = GroundedTeacherProviders(tmp_path / "cache", model=model)
    monkeypatch.setattr(provider, "_api_key", lambda: "sk-unit-grounded")
    answer = _answer()
    raw = _response(answer, model)
    requests = []

    def respond(request):
        requests.append(request)
        assert request.get_header("Authorization") == "Bearer sk-unit-grounded"
        return io.BytesIO(json.dumps(raw).encode())

    monkeypatch.setattr(provider, "_open", respond)
    assert provider.judge(VACANCY, A, B, PREFERENCES) == answer
    payload = provider._pair_payload(VACANCY, A, B, PREFERENCES)
    key = digest(provider._identity("/v1/chat/completions", payload, "pair"))
    stored = json.loads((provider.directory / f"{key}.json").read_text())
    assert stored["request"]["prompt_version"] == GROUNDED_PROMPT_VERSION == "rerank-pair-grounded-v5"
    assert stored["response"] == raw and stored["response_hash"] == digest(raw)
    assert requests[0].full_url == "https://ai.sverk.tech/v1/chat/completions"
    assert json.loads((provider.directory / "usage.json").read_text()) == {"pair": 1}
    before = {path.name: path.read_bytes() for path in provider.directory.glob("*.json")}
    monkeypatch.setattr(provider, "_open", lambda *_args: pytest.fail("cache replay called HTTP"))
    monkeypatch.setattr(provider, "_api_key", lambda: pytest.fail("cache replay read a secret"))
    assert provider.judge(VACANCY, A, B, PREFERENCES) == answer
    assert provider.pair(VACANCY, A, B, PREFERENCES) == "insufficient"
    assert {path.name: path.read_bytes() for path in provider.directory.glob("*.json")} == before
    assert all(b"sk-unit" not in data for data in before.values())


@pytest.mark.parametrize("status", SOURCE_STATUSES)
def test_all_original_confirmation_states_survive_judgement_and_explanation(tmp_path, monkeypatch, status):
    provider = GroundedTeacherProviders(tmp_path / "cache")
    a = {**A, "status": status}
    answer = _answer(a, B)
    assert _mock(provider, monkeypatch, answer, a=a) == answer
    before = deepcopy((VACANCY, a, B, answer))
    explained = grounded_explanation(VACANCY, a, B, answer)
    assert explained["reason_code"] == "missing_tradeoff_priorities"
    assert explained["salary_comparison_choice"] == "A"
    assert explained["evidence_A"] == [
        {"candidate": "A", "field": "status", "value": status, "status": status},
        {"candidate": "A", "field": "levels.react", "value": 3, "status": status},
    ]
    assert explained["unknown_A"] == [
        {"candidate": "A", "field": "duration_months", "value": None, "status": status},
        {"candidate": "A", "field": "production_support", "value": None, "status": status},
    ]
    assert all(fact["status"] == "confirmed" for fact in explained["evidence_B"])
    assert explained["unknown_B"] == []
    assert not ({"score", "confidence", "probabilities", "rationale", "priority"} & set(explained))
    explained["vacancy"]["salary"]["min"] = 1
    assert (VACANCY, a, B, answer) == before


@pytest.mark.parametrize(("label", "reason"), [
    ("A", "prefer_known_facts"), ("B", "prefer_known_facts"), ("tie", "comparable_fit"),
    ("insufficient", "missing_tradeoff_priorities"), ("insufficient", "insufficient_source_facts"),
])
def test_all_label_reason_combinations_and_swaps_are_valid(tmp_path, monkeypatch, label, reason):
    provider = GroundedTeacherProviders(tmp_path / "cache")
    direct = _answer(label=label, reason=reason)
    assert _mock(provider, monkeypatch, direct) == direct
    reverse_label = {"A": "B", "B": "A", "tie": "tie", "insufficient": "insufficient"}[label]
    reverse = _answer(B, A, reverse_label, reason)
    assert _mock(provider, monkeypatch, reverse, a=B, b=A) == reverse
    assert reverse["salary_comparison_choice"] == "B"
    for side, other in (("A", "B"), ("B", "A")):
        assert reverse[f"evidence_{side}"] == [side + token[1:] for token in direct[f"evidence_{other}"]]
        assert reverse[f"unknown_{side}"] == [side + token[1:] for token in direct[f"unknown_{other}"]]


def test_upper_evidence_and_unknown_bounds_and_empty_unknowns_are_valid(tmp_path, monkeypatch):
    provider = GroundedTeacherProviders(tmp_path / "cache")
    a = {**A, "structured_facts_available": None}
    answer = _answer(a, B)
    for side in ("A", "B"):
        answer[f"evidence_{side}"] = [_ref(a, B, side, field) for field in
                                     ("status", "grade", "levels.react", "salary_min")]
    assert len(answer["unknown_A"]) == 3
    assert _mock(provider, monkeypatch, answer, a=a) == answer
    answer["unknown_A"] = []
    assert _mock(provider, monkeypatch, answer, a=a) == answer


@pytest.mark.parametrize(("label", "reason"), [
    ("A", "comparable_fit"), ("B", "insufficient_source_facts"),
    ("tie", "missing_tradeoff_priorities"), ("tie", "prefer_known_facts"),
    ("insufficient", "comparable_fit"),
])
def test_reason_codes_cannot_relabel_decisive_tie_or_insufficient_answers(tmp_path, monkeypatch, label, reason):
    provider = GroundedTeacherProviders(tmp_path / "cache")
    with pytest.raises(ValueError):
        _mock(provider, monkeypatch, _answer(label=label, reason=reason))


@pytest.mark.parametrize("mutation", [
    "wrong_status", "missing_status", "wrong_side", "evidence_duplicate", "unknown_duplicate", "unknown_nonnull",
    "unknown_wrongside", "unknown_absent", "too_few", "too_many", "unknown_many", "extra_rationale",
    "extra_confidence", "missing_field", "label_type", "reason_type", "reason_label", "wrong_salary",
    "evidence_type", "unknown_type", "reference_type", "edited_numeric_type", "extra_unknown_B",
])
def test_invalid_references_types_bounds_and_fields_are_rejected(tmp_path, monkeypatch, mutation):
    provider = GroundedTeacherProviders(tmp_path / "cache")
    answer = _answer()
    if mutation == "wrong_status":
        answer["evidence_A"][0] = "A.status[str]=confirmed"
    elif mutation == "missing_status":
        answer["evidence_A"][0] = _ref(A, B, "A", "grade")
    elif mutation == "wrong_side":
        answer["evidence_A"][1] = _ref(A, B, "B", "levels.react")
    elif mutation == "evidence_duplicate":
        answer["evidence_A"] *= 2
    elif mutation == "unknown_duplicate":
        answer["unknown_A"] = [answer["unknown_A"][0]] * 2
    elif mutation == "unknown_nonnull":
        answer["unknown_A"] = [_ref(A, B, "A", "salary_min")]
    elif mutation == "unknown_wrongside":
        answer["unknown_B"] = answer["unknown_A"][:1]
    elif mutation == "unknown_absent":
        answer["unknown_A"] = ["A.levels.rust[null]=null"]
    elif mutation == "too_few":
        answer["evidence_A"] = answer["evidence_A"][:1]
    elif mutation == "too_many":
        answer["evidence_A"] = [_ref(A, B, "A", field) for field in
                                ("status", "grade", "salary_min", "levels.react", "levels.teamwork")]
    elif mutation == "unknown_many":
        answer["unknown_A"] *= 2
    elif mutation in ("extra_rationale", "extra_confidence"):
        answer[mutation.removeprefix("extra_")] = "unsupported"
    elif mutation == "missing_field":
        answer.pop("unknown_B")
    elif mutation == "label_type":
        answer["label"] = ["insufficient"]
    elif mutation == "reason_type":
        answer["reason_code"] = 1
    elif mutation == "reason_label":
        answer["reason_code"] = "prefer_known_facts"
    elif mutation == "wrong_salary":
        answer["salary_comparison_choice"] = "B"
    elif mutation == "evidence_type":
        answer["evidence_B"] = {"ref": answer["evidence_B"][0]}
    elif mutation == "unknown_type":
        answer["unknown_B"] = None
    elif mutation == "reference_type":
        answer["evidence_B"][1] = {"field": "levels.react", "value": 5}
    elif mutation == "edited_numeric_type":
        answer["evidence_B"][1] = "B.levels.react[float]=5.0"
    else:
        answer["unknown_B"] = [_ref(A, B, "B", "production_support")]
    with pytest.raises(ValueError):
        _mock(provider, monkeypatch, answer)
    with pytest.raises(ValueError):
        grounded_explanation(VACANCY, A, B, answer)


@pytest.mark.parametrize("salary", [None, 80000.0, "80000", True, "absent"])
def test_unknown_or_noninteger_salary_is_not_coerced(tmp_path, monkeypatch, salary):
    provider = GroundedTeacherProviders(tmp_path / "cache")
    a = {**A, "salary_min": salary}
    if salary == "absent":
        a.pop("salary_min")
    answer = _answer(a, B)
    assert answer["salary_comparison_choice"] == "unknown"
    assert _mock(provider, monkeypatch, answer, a=a) == answer
    explained = grounded_explanation(VACANCY, a, B, answer)
    assert explained["salary_comparison"]["salary_min_A"] is None
    assert explained["salary_comparison"]["delta_B_minus_A"] is None


@pytest.mark.parametrize("status", [None, True, "verified", 1])
def test_invalid_source_status_rejected_before_budget_or_secret(tmp_path, monkeypatch, status):
    provider = GroundedTeacherProviders(tmp_path / "cache")
    monkeypatch.setattr(provider, "request", lambda *_args: pytest.fail("invalid source reached transport"))
    with pytest.raises(ValueError):
        provider.judge(VACANCY, {**A, "status": status}, B, PREFERENCES)
    assert not (provider.directory / "usage.json").exists()


def test_missing_nonstatus_facts_and_missing_status_are_rejected(tmp_path):
    provider = GroundedTeacherProviders(tmp_path / "cache")
    for a in ({"status": "partial"}, {"grade": "junior"}):
        with pytest.raises(ValueError):
            provider._pair_payload(VACANCY, a, B, PREFERENCES)


@pytest.mark.parametrize("mutation", ["model", "missing_model", "length", "missing_stop", "truncated", "choices",
                                     "choice_type", "content_type", "nonobject", "duplicate_fields"])
def test_invalid_chat_envelope_and_duplicate_json_fields_are_rejected(tmp_path, monkeypatch, mutation):
    provider = GroundedTeacherProviders(tmp_path / "cache")
    response = _response(_answer())
    if mutation == "model":
        response["model"] = GEMMA_GENERATION_MODEL
    elif mutation == "missing_model":
        response.pop("model")
    elif mutation in ("length", "missing_stop"):
        response["choices"][0]["finish_reason"] = "length" if mutation == "length" else None
    elif mutation == "truncated":
        response["truncated"] = True
    elif mutation == "choices":
        response["choices"] *= 2
    elif mutation == "choice_type":
        response["choices"] = ["stop"]
    elif mutation == "content_type":
        response["choices"][0]["message"]["content"] = _answer()
    elif mutation == "nonobject":
        response["choices"][0]["message"]["content"] = "[]"
    else:
        content = json.dumps(_answer())
        response["choices"][0]["message"]["content"] = '{"label":"A",' + content[1:]
    monkeypatch.setattr(provider, "request", lambda *_args: response)
    with pytest.raises(ValueError):
        provider.judge(VACANCY, A, B, PREFERENCES)


def test_old_versions_unchanged_and_both_models_share_pair_budget(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    v3 = ComparativeTeacherProviders(cache)
    v4 = CompatibleTeacherProviders(cache)
    qwen = GroundedTeacherProviders(cache)
    gemma = GroundedTeacherProviders(cache, model=GEMMA_GENERATION_MODEL)
    providers = [(v3, COMPARATIVE_PROMPT_VERSION), (v4, COMPATIBLE_PROMPT_VERSION),
                 (qwen, GROUNDED_PROMPT_VERSION), (gemma, GROUNDED_PROMPT_VERSION)]
    keys = []
    for provider, version in providers:
        payload = provider._pair_payload(VACANCY, A, B, PREFERENCES)
        identity = provider._identity("/v1/chat/completions", payload, "pair")
        assert identity["prompt_version"] == version
        keys.append(digest(identity))
    assert len(set(keys)) == 4 and teacher_identity()["prompt_version"] == "rerank-systemone-v1"
    assert grounded_teacher_identity()["model"] == GENERATION_MODEL
    (cache / "usage.json").write_text(json.dumps({"pair": LIMITS["pair"] - 1}))
    monkeypatch.setattr(qwen, "_api_key", lambda: "sk-unit-grounded")
    monkeypatch.setattr(qwen, "_open", lambda _request: io.BytesIO(json.dumps(_response(_answer())).encode()))
    assert qwen.judge(VACANCY, A, B, PREFERENCES) == _answer()
    monkeypatch.setattr(gemma, "_api_key", lambda: pytest.fail("exhausted budget read a key"))
    with pytest.raises(BudgetExceeded):
        gemma.judge(VACANCY, A, B, PREFERENCES)
    assert json.loads((cache / "usage.json").read_text()) == {"pair": LIMITS["pair"]}


def test_model_whitelist_and_pair_only_methods_do_not_reserve_budget(tmp_path):
    with pytest.raises(ValueError):
        GroundedTeacherProviders(tmp_path / "invalid", model="unapproved")
    with pytest.raises(ValueError):
        grounded_teacher_identity("unapproved")
    assert not (tmp_path / "invalid").exists()
    provider = GroundedTeacherProviders(tmp_path / "cache")
    payload = provider._pair_payload(VACANCY, A, B, PREFERENCES)
    for path, kind in (("/v1/chat/completions", "generation"), ("/v1/chat/completions", "grade"),
                       ("/v1/systemone", "pair")):
        with pytest.raises(ValueError):
            provider.request(path, payload, kind)
    with pytest.raises(ValueError):
        provider.generate("frontend", "synthetic")
    with pytest.raises(ValueError):
        provider.grade(VACANCY, A, PREFERENCES)
    assert not (provider.directory / "usage.json").exists()


def test_cached_request_identity_tampering_is_rejected_without_transport(tmp_path, monkeypatch):
    provider = GroundedTeacherProviders(tmp_path / "cache")
    monkeypatch.setattr(provider, "_api_key", lambda: "sk-unit-grounded")
    monkeypatch.setattr(provider, "_open", lambda _request: io.BytesIO(json.dumps(_response(_answer())).encode()))
    assert provider.judge(VACANCY, A, B, PREFERENCES) == _answer()
    payload = provider._pair_payload(VACANCY, A, B, PREFERENCES)
    key = digest(provider._identity("/v1/chat/completions", payload, "pair"))
    path = provider.directory / f"{key}.json"
    record = json.loads(path.read_text())
    record["request"]["prompt_version"] = "changed"
    path.write_text(json.dumps(record))
    monkeypatch.setattr(provider, "_api_key", lambda: pytest.fail("invalid cache read a secret"))
    monkeypatch.setattr(provider, "_open", lambda *_args: pytest.fail("invalid cache called HTTP"))
    with pytest.raises(ValueError, match="cache integrity"):
        provider.judge(VACANCY, A, B, PREFERENCES)
    assert json.loads((provider.directory / "usage.json").read_text()) == {"pair": 1}
