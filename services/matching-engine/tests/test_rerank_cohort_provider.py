"""Offline contracts for uniform cohort labels, receipts and bounded repairs."""

import io
import json
from copy import deepcopy

import pytest

from matching_engine.rerank_cohort_provider import (
    CANDIDATE_FIELDS,
    GENERATION_DURATIONS,
    GENERATION_SALARIES,
    PROMPT_VERSIONS,
    STATUSES,
    CohortProviders,
    source_citations,
    teacher_identity,
    validate_candidate,
)
from matching_engine.rerank_hash import digest
from matching_engine.rerank_providers import (
    GEMMA_GENERATION_MODEL,
    GENERATION_MODEL,
    GENERATION_URL,
    LIMITS,
    BudgetExceeded,
    SystemOneProviders,
    generation_identity,
)
from matching_engine.rerank_providers import (
    teacher_identity as legacy_teacher_identity,
)

KEYS = ("python", "sql", "teamwork")
POLICY = {"id": "technical", "ordered_priorities": ["Compare declared Python level first."]}
VACANCY = {"class": "backend", "salary": {"min": 50000, "max": 200000}}
A = {"grade": "junior", "status": "partial", "levels": dict.fromkeys(KEYS, 4),
     "skill_statuses": {"python": "confirmed", "sql": "pending", "teamwork": "rejected"},
     "salary_min": 80000, "description": "Разрабатываю API на Python.",
     "structured_facts_available": True, "duration_months": 0, "duration_status": "self_reported",
     "production_support": False, "production_status": "partial", "role": "Разработчик",
     "role_status": "pending", "project_domains": [{"key": "rest_api", "status": "confirmed"}],
     "project_stacks": [{"key": "python", "status": "rejected"}]}
B = {**deepcopy(A), "salary_min": 100000, "status": "self_reported"}


def _provider(tmp_path, **kwargs):
    return CohortProviders(tmp_path / "provider-cache", cohort_id="cohort-20261006-v1", **kwargs)


def _ref(candidate, field, side="A"):
    citations = source_citations(candidate) if side == "A" else source_citations(A, candidate)
    return next(token for token, fact in citations.items()
                if fact["candidate"] == side and fact["field"] == field)


def _pair_answer(a=A, b=B, label="A"):
    return {"label": label, "reason_code": {"A": "prefer_known_facts", "B": "prefer_known_facts",
            "tie": "comparable_fit", "insufficient": "insufficient_source_facts"}[label],
            "salary_comparison_choice": "A", "status_A": a["status"], "status_B": b["status"],
            "evidence_A": [_ref(a, "status"), _ref(a, "levels.python")],
            "evidence_B": [_ref(b, "status", "B"), _ref(b, "levels.python", "B")]}


def _grade_answer(candidate=A, grade=3):
    return {"grade": grade, "reason_code": "insufficient" if grade is None else "assessed",
            "status": candidate["status"],
            "evidence": [_ref(candidate, "status"), _ref(candidate, "levels.python")]}


def _response(answer, model=GENERATION_MODEL):
    return {"model": model, "choices": [{"finish_reason": "stop", "message": {
        "content": json.dumps(answer, ensure_ascii=False)}}], "usage": {"total_tokens": 10}}


def test_candidate_statuses_and_unknowns_are_independent():
    validate_candidate(A, KEYS)
    value = deepcopy(A)
    for status in STATUSES:
        value["status"] = status
        value["duration_status"] = status
        value["skill_statuses"]["python"] = status
        validate_candidate(value, KEYS)
    value.update(structured_facts_available=False, duration_months=None, duration_status=None,
                 production_support=None, production_status=None, role=None, role_status=None,
                 project_domains=[], project_stacks=[])
    validate_candidate(value, KEYS)
    value["duration_status"] = "pending"
    with pytest.raises(ValueError, match="null statuses"):
        validate_candidate(value, KEYS)


@pytest.mark.parametrize("field,value", [
    ("levels", {"python": True, "sql": 2, "teamwork": 1}),
    ("levels", {"python": 4, "react": 4, "teamwork": 4}),
    ("skill_statuses", {"python": "confirmed"}), ("salary_min", True),
    ("duration_months", 120.0), ("duration_months", -1), ("production_support", 1),
    ("role", "x" * 81), ("description", "not Russian"),
    ("project_domains", [{"key": "python", "status": "confirmed"}]),
    ("project_stacks", [{"key": "python", "status": "invalid"}]),
    ("project_stacks", [{"key": "python", "status": "confirmed", "level": 3}]),
    ("production_status", None), ("grade", "staff"),
])
def test_candidate_rejects_wrong_types_extra_or_unregistered_facts(field, value):
    candidate = deepcopy(A)
    candidate[field] = value
    with pytest.raises(ValueError):
        validate_candidate(candidate, KEYS)


def test_candidate_exact_contract_and_unavailable_facts():
    assert set(A) == set(CANDIDATE_FIELDS)
    for bad in ({**A, "confidence": 1}, {key: value for key, value in A.items() if key != "status"},
                {**A, "structured_facts_available": False}):
        with pytest.raises(ValueError):
            validate_candidate(bad)


def test_recursive_sources_cover_all_typed_fields_and_exclude_prose():
    candidate = {**deepcopy(A), "duration_months": None, "duration_status": None}
    citations = source_citations(candidate, B)
    fields = {fact["field"]: fact for fact in citations.values() if fact["candidate"] == "A"}
    assert fields["duration_months"]["value"] is None
    assert fields["duration_status"]["value"] is None
    assert fields["production_support"]["value"] is False
    assert fields["skill_statuses.teamwork"]["value"] == "rejected"
    assert fields["project_stacks.0.status"]["value"] == "rejected"
    assert fields["project_domains.0.key"]["value"] == "rest_api"
    assert not any("description" in token for token in citations)
    assert _ref(candidate, "duration_months").endswith("[null]=null")
    assert _ref(candidate, "production_support").endswith("[bool]=false")
    assert _ref(candidate, "status") == "A.status[str]=partial"
    assert _ref(candidate, "skill_statuses.teamwork") == "A.skill_statuses.teamwork[str]=rejected"
    assert source_citations(candidate, B) == citations


def test_payloads_are_portable_score_blind_and_deterministic(tmp_path):
    provider = _provider(tmp_path)
    before = deepcopy((VACANCY, A, B, POLICY))
    pair = provider.pair_payload(VACANCY, A, B, POLICY)
    grade = provider.suitability_payload(VACANCY, A, POLICY)
    generation = provider.generation_payload("backend", "backend-01", KEYS)
    for kind, payload in (("pair", pair), ("grade", grade), ("generation", generation)):
        schema = payload["response_format"]["json_schema"]["schema"]
        assert "uniqueItems" not in json.dumps(schema)
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) == set(schema["properties"])
        identity = provider._identity("/v1/chat/completions", payload, kind)
        assert identity["prompt_version"] == PROMPT_VERSIONS[kind]
        assert identity["cohort_id"] == provider.cohort_id and identity["attempt"] == 0
    state = json.loads(pair["messages"][1]["content"])
    assert state["policy"] == POLICY and state["A"] == A and state["B"] == B
    assert state["source_citations"] == source_citations(A, B)
    assert state["original_statuses"] == {"A": A["status"], "B": B["status"]}
    assert pair["response_format"]["json_schema"]["schema"]["properties"]["status_A"]["enum"] == [A["status"]]
    assert pair["response_format"]["json_schema"]["schema"]["properties"]["status_B"]["enum"] == [B["status"]]
    assert grade["response_format"]["json_schema"]["schema"]["properties"]["status"]["enum"] == [A["status"]]
    assert pair == provider.pair_payload(VACANCY, A, B, POLICY)
    assert pair["temperature"] == grade["temperature"] == 0
    assert pair["max_tokens"] == grade["max_tokens"] == 1000
    assert pair["chat_template_kwargs"] == {"enable_thinking": False}
    assert pair["model"] == grade["model"] == GENERATION_MODEL
    assert generation["model"] == GEMMA_GENERATION_MODEL
    assert generation["max_tokens"] == 8192 and generation["temperature"] == .7
    assert "chat_template_kwargs" not in generation
    gen_state = json.loads(generation["messages"][1]["content"])
    assert gen_state["required_keys"] == list(KEYS) and gen_state["count"] == 7
    assert "B" not in json.loads(grade["messages"][1]["content"])
    assert "never infer typed facts" in pair["messages"][0]["content"]
    assert "strict exclusive criteria" in pair["messages"][0]["content"]
    assert "no generic identical sentence templates" in generation["messages"][0]["content"]
    assert "not text to append mechanically" in generation["messages"][0]["content"]
    assert (VACANCY, A, B, POLICY) == before
    assert not list(provider.directory.glob("*.json"))


def test_explicit_suitability_rubric_overrides_generic_prompt_examples(tmp_path):
    provider = _provider(tmp_path)
    policy = {**POLICY, "suitability_rubric": "Known sole declared Python level >=4 means grade3."}
    payload = provider.suitability_payload(VACANCY, A, policy)
    instructions = payload["messages"][0]["content"]
    assert "explicit ordinal rules override these generic examples" in instructions
    assert "do not introduce additional unknown facts" in instructions
    assert json.loads(payload["messages"][1]["content"])["policy"] == policy


def test_identity_separates_kind_cohort_repair_and_preserves_defaults(tmp_path):
    provider = _provider(tmp_path)
    other = CohortProviders(provider.directory, cohort_id="another-cohort")
    initial = provider.pair_payload(VACANCY, A, B, POLICY)
    repaired = provider.pair_payload(VACANCY, A, B, POLICY, attempt=1)
    identity = provider._identity("/v1/chat/completions", initial, "pair")
    repair_identity = provider._identity("/v1/chat/completions", repaired, "pair")
    assert repair_identity["attempt"] == 1 and repair_identity["prompt_version"].endswith("-repair1")
    other_identity = other._identity("/v1/chat/completions", other.pair_payload(VACANCY, A, B, POLICY), "pair")
    assert len({digest(identity), digest(repair_identity), digest(other_identity)}) == 3
    assert teacher_identity(provider.cohort_id) == provider.teacher_identity()
    assert provider.teacher_identity("grade") != provider.teacher_identity("pair")
    assert legacy_teacher_identity()["prompt_version"] == "rerank-systemone-v1"
    assert PROMPT_VERSIONS["generation"] == "rerank-uniform-cohort-generation-v3"
    assert PROMPT_VERSIONS["pair"] == "rerank-uniform-cohort-pair-v2"
    assert PROMPT_VERSIONS["grade"] == "rerank-uniform-cohort-suitability-v2"
    assert generation_identity()["prompt_version"] == "rerank-sverk-generation-v2"
    legacy = SystemOneProviders(provider.directory)
    assert legacy._endpoint("pair") == "/v1/systemone"
    with pytest.raises(AttributeError):
        provider.cohort_id = "changed"
    for invalid in (-1, 2, True, "1"):
        with pytest.raises(ValueError):
            provider.pair_payload(VACANCY, A, B, POLICY, attempt=invalid)


@pytest.mark.parametrize("count", [0, 8, True, 7.0])
def test_generation_count_bounds(tmp_path, count):
    with pytest.raises(ValueError):
        _provider(tmp_path).generation_payload("backend", "backend-01", KEYS, count)


def test_generation_approved_qwen_and_sparse_registry(tmp_path, monkeypatch):
    provider = _provider(tmp_path, generator_model=GENERATION_MODEL)
    calls = []

    def request(path, payload, kind):
        calls.append((path, payload, kind))
        return _response({"profiles": [A]})

    monkeypatch.setattr(provider, "request", request)
    assert provider.generate_family("backend", "backend-01", KEYS, 1) == [A]
    assert calls[0][0] == "/v1/chat/completions" and calls[0][2] == "generation"
    assert calls[0][1]["chat_template_kwargs"] == {"enable_thinking": False}
    with pytest.raises(ValueError, match="sparse keys"):
        provider.generate_family("backend", "backend-01", ["python", "react"], 1)
    for keys in (("python", "python"), ("unregistered",), (), "python"):
        with pytest.raises(ValueError):
            provider.generation_payload("backend", "backend-01", keys)
    with pytest.raises(ValueError):
        _provider(tmp_path, generator_model="unauthorized")


def test_generation_v3_instructs_exact_nullable_pairs_and_compact_output(tmp_path):
    provider = _provider(tmp_path, generator_model=GENERATION_MODEL)
    payload = provider.generation_payload("fullstack", "fullstack-01", KEYS)
    instructions = payload["messages"][0]["content"]
    assert "compact JSON object only" in instructions
    assert "no indentation, formatting whitespace, newlines, markdown" in instructions
    assert "Stop immediately after the final closing brace" in instructions
    for value, status in (("duration_months", "duration_status"),
                          ("production_support", "production_status"), ("role", "role_status")):
        assert f"{value} -> {status}" in instructions
        assert f"{status} must be exactly one of confirmed/partial/self_reported/pending/rejected, never null" in instructions
        assert f"{value}=null, {status}=null" in instructions
    assert "including 0" in instructions and "including false" in instructions
    assert "If a paired value is null, its own status must also be null" in instructions
    assert "independent evidence status" in instructions and "evidence5" not in instructions
    assert "uniqueItems" not in json.dumps(payload["response_format"])
    assert provider._identity("/v1/chat/completions", payload, "generation")["prompt_version"] == (
        "rerank-uniform-cohort-generation-v3")


def test_generation_discrete_numbers_and_two_unique_project_references(tmp_path):
    provider = _provider(tmp_path)
    payload = provider.generation_payload("backend", "backend-01", KEYS)
    fields = payload["response_format"]["json_schema"]["schema"]["properties"]["profiles"]["items"]["properties"]
    assert GENERATION_SALARIES == (50000, 60000, 70000, 80000, 90000, 100000, 120000, 140000, 160000, 180000)
    assert GENERATION_DURATIONS == (None, 0, 6, 12, 24, 36, 48, 60, 96, 120)
    assert fields["salary_min"] == {"type": "integer", "enum": list(GENERATION_SALARIES)}
    assert fields["duration_months"] == {"type": ["integer", "null"], "enum": list(GENERATION_DURATIONS)}
    assert fields["project_domains"]["maxItems"] == fields["project_stacks"]["maxItems"] == 2
    instructions = payload["messages"][0]["content"]
    assert "50000,60000,70000,80000,90000,100000,120000,140000,160000,180000" in instructions
    assert "null,0,6,12,24,36,48,60,96,120" in instructions
    assert "at most 2 references with unique keys" in instructions
    assert "uniqueItems" not in json.dumps(payload["response_format"])


def test_legacy_source_validation_keeps_ranges_and_more_than_two_refs():
    candidate = deepcopy(A)
    candidate.update(salary_min=87321, duration_months=53,
                     project_domains=[{"key": key, "status": "partial"}
                                      for key in ("sql", "rest_api", "algorithms")],
                     project_stacks=[{"key": key, "status": "self_reported"}
                                     for key in ("python", "node", "react")])
    validate_candidate(candidate)


def test_generation_accepts_valid_compact_qwen_receipt_with_zero_false_and_null(tmp_path, monkeypatch):
    provider = _provider(tmp_path, generator_model=GENERATION_MODEL)
    profiles = []
    for index in range(7):
        profile = deepcopy(A)
        profile["description"] = f"Задача проекта: разработка API, этап {index + 1}."
        if index % 2:
            profile.update(structured_facts_available=False, duration_months=None, duration_status=None,
                           production_support=None, production_status=None, role=None, role_status=None,
                           project_domains=[], project_stacks=[])
        profiles.append(profile)
    compact = json.dumps({"profiles": profiles}, ensure_ascii=False, separators=(",", ":"))
    response = _response({})
    response["choices"][0]["message"]["content"] = compact
    calls = []

    def request(path, payload, kind):
        calls.append((path, payload, kind))
        return response

    monkeypatch.setattr(provider, "request", request)
    assert provider.generate_family("fullstack", "fullstack-01", KEYS) == profiles
    assert len(calls) == 1 and calls[0][2] == "generation"
    assert calls[0][1]["max_tokens"] == 8192
    assert profiles[0]["duration_months"] == 0 and profiles[0]["duration_status"] == "self_reported"
    assert profiles[0]["production_support"] is False and profiles[0]["production_status"] == "partial"
    assert profiles[1]["duration_months"] is None and profiles[1]["duration_status"] is None


def test_valid_pair_and_independent_grade(tmp_path, monkeypatch):
    provider = _provider(tmp_path)
    response = _response(_pair_answer())
    monkeypatch.setattr(provider, "request", lambda *_args: response)
    assert provider.judge(VACANCY, A, B, POLICY) == _pair_answer()
    for grade in (0, 1, 2, 3, None):
        response = _response(_grade_answer(grade=grade))
        assert provider.suitability(VACANCY, A, POLICY)["grade"] == grade


@pytest.mark.parametrize("mutation", [
    lambda answer: answer.update(label="A", reason_code="comparable_fit"),
    lambda answer: answer.update(salary_comparison_choice="B"),
    lambda answer: answer.update(confidence=.8),
    lambda answer: answer.update(evidence_A=[_ref(B, "status", "B"), _ref(A, "levels.python")]),
    lambda answer: answer.update(evidence_A=[_ref(A, "status")] * 2),
    lambda answer: answer.update(status_A="confirmed"),
    lambda answer: answer.update(status_B="rejected"),
    lambda answer: answer.pop("status_A"),
    lambda answer: answer.update(evidence_A=['A.status[str]=\\"partial\\"', _ref(A, "levels.python")]),
    lambda answer: answer.update(evidence_B=["B.levels.python[int]=999", _ref(B, "status", "B")]),
])
def test_pair_rejects_reason_salary_extra_wrong_side_duplicate_or_wrong_status(tmp_path, monkeypatch, mutation):
    answer = _pair_answer()
    mutation(answer)
    provider = _provider(tmp_path)
    monkeypatch.setattr(provider, "request", lambda *_args: _response(answer))
    with pytest.raises(ValueError):
        provider.judge(VACANCY, A, B, POLICY)


def test_status_is_preserved_separately_without_requiring_root_status_evidence(tmp_path, monkeypatch):
    provider = _provider(tmp_path)
    answer = _pair_answer()
    for side, candidate in (("A", A), ("B", B)):
        answer[f"evidence_{side}"] = [_ref(candidate, "levels.python", side),
                                     _ref(candidate, "skill_statuses.python", side)]
    response = _response(answer)
    monkeypatch.setattr(provider, "request", lambda *_args: response)
    assert provider.judge(VACANCY, A, B, POLICY) == answer
    grade = {**_grade_answer(), "evidence": [_ref(A, "levels.python"), _ref(A, "skill_statuses.python")]}
    response = _response(grade)
    assert provider.suitability(VACANCY, A, POLICY) == grade
    grade["status"] = "confirmed"
    response = _response(grade)
    with pytest.raises(ValueError, match="original status"):
        provider.suitability(VACANCY, A, POLICY)
    grade.pop("status")
    response = _response(grade)
    with pytest.raises(ValueError, match="suitability fields"):
        provider.suitability(VACANCY, A, POLICY)


@pytest.mark.parametrize("grade,reason", [(True, "assessed"), (4, "assessed"), (2.0, "assessed"),
                                         (None, "assessed"), (3, "insufficient")])
def test_grade_rejects_coercions_and_inconsistent_reasons(tmp_path, monkeypatch, grade, reason):
    provider = _provider(tmp_path)
    answer = {**_grade_answer(), "grade": grade, "reason_code": reason}
    monkeypatch.setattr(provider, "request", lambda *_args: _response(answer))
    with pytest.raises(ValueError):
        provider.suitability(VACANCY, A, POLICY)


def test_exclusive_missing_measurements_are_executable_abstention(tmp_path, monkeypatch):
    provider = _provider(tmp_path)
    policy = {**POLICY, "exclusive_criterion": True, "required_measurements": ["uptime_percentage"]}
    response = _response(_pair_answer())
    monkeypatch.setattr(provider, "request", lambda *_args: response)
    with pytest.raises(ValueError, match="exclusive measurement"):
        provider.judge(VACANCY, A, B, policy)
    response = _response(_pair_answer(label="insufficient"))
    assert provider.judge(VACANCY, A, B, policy)["label"] == "insufficient"
    response = _response(_grade_answer())
    with pytest.raises(ValueError, match="exclusive measurement"):
        provider.suitability(VACANCY, A, policy)
    response = _response(_grade_answer(grade=None))
    assert provider.suitability(VACANCY, A, policy)["grade"] is None
    missing = {**A, "duration_months": None, "duration_status": None}
    policy["required_measurements"] = ["duration_months"]
    response = _response(_pair_answer(missing, B))
    with pytest.raises(ValueError, match="exclusive measurement"):
        provider.judge(VACANCY, missing, B, policy)
    response = _response(_pair_answer(missing, B, label="insufficient"))
    assert provider.judge(VACANCY, missing, B, policy)["label"] == "insufficient"


@pytest.mark.parametrize("mutation", [
    lambda response: response.update(model="wrong-model"),
    lambda response: response.update(truncated=True),
    lambda response: response["choices"][0].update(finish_reason="length"),
    lambda response: response["choices"][0].update(finish_reason=None),
    lambda response: response.update(choices=[]),
    lambda response: response["choices"][0]["message"].update(content="```json\n{}\n```"),
    lambda response: response["choices"][0]["message"].update(content='{"label":"A","label":"B"}'),
    lambda response: response["choices"][0]["message"].update(content='{"evidence":{"x":1,"x":2}}'),
])
def test_parse_rejects_unfinished_model_malformed_duplicate_without_retry(tmp_path, monkeypatch, mutation):
    provider = _provider(tmp_path)
    response = _response(_pair_answer())
    mutation(response)
    calls = []

    def request(*args):
        calls.append(args)
        return response

    monkeypatch.setattr(provider, "request", request)
    with pytest.raises(ValueError):
        provider.judge(VACANCY, A, B, POLICY)
    assert len(calls) == 1


def test_shared_cache_ledger_key_laziness_and_all_kind_chat_transport(tmp_path, monkeypatch):
    provider = _provider(tmp_path)
    legacy_receipt = provider.directory / "historical.json"
    historical = b'{"immutable":"legacy bytes"}\n'
    legacy_receipt.write_bytes(historical)
    ledger_path = provider.directory / "usage.json"
    before = {"generation": 70, "pair": 900, "grade": 450}
    ledger_path.write_text(json.dumps(before), encoding="utf-8")
    keys_read, opened = [], []
    monkeypatch.setattr(provider, "_api_key", lambda: keys_read.append(True) or "synthetic-test-key")

    def open_response(request):
        opened.append(request)
        payload = json.loads(request.data)
        kind = json.loads(payload["messages"][1]["content"])["request_nonce"]["kind"]
        answer = {"generation": {"profiles": [A]}, "pair": _pair_answer(), "grade": _grade_answer()}[kind]
        return io.BytesIO(json.dumps(_response(answer, payload["model"])).encode())

    monkeypatch.setattr(provider, "_open", open_response)
    assert not keys_read
    assert provider.generate_family("backend", "backend-01", KEYS, 1) == [A]
    provider.judge(VACANCY, A, B, POLICY)
    provider.suitability(VACANCY, A, POLICY)
    assert len(opened) == len(keys_read) == 3
    assert all(request.full_url == GENERATION_URL for request in opened)
    assert json.loads(ledger_path.read_text()) == {kind: number + 1 for kind, number in before.items()}
    assert legacy_receipt.read_bytes() == historical
    # Replaying requires neither a credential file nor another cumulative attempt.
    monkeypatch.setattr(provider, "_api_key", lambda: pytest.fail("cache replay read secret"))
    provider.generate_family("backend", "backend-01", KEYS, 1)
    provider.judge(VACANCY, A, B, POLICY)
    provider.suitability(VACANCY, A, POLICY)
    assert len(opened) == 3
    assert len(list(provider.directory.glob(".remote-slot-*.lock"))) >= 1
    assert "synthetic-test-key" not in "".join(path.read_text() for path in provider.directory.glob("*.json"))
    ledger_path.write_text(json.dumps({**before, "pair": LIMITS["pair"]}), encoding="utf-8")
    with pytest.raises(BudgetExceeded):
        provider.judge(VACANCY, A, B, POLICY, attempt=1)
    assert len(opened) == 3


def test_explicit_policy_validation(tmp_path):
    provider = _provider(tmp_path)
    for policy in ({}, {"ordered_priorities": []}, {"ordered_priorities": [5]},
                   {**POLICY, "required_measurements": [{}]}, {**POLICY, "exclusive_criterion": 1}):
        with pytest.raises(ValueError):
            provider.pair_payload(VACANCY, A, B, policy)
