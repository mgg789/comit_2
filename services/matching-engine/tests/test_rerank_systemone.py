"""SystemOne teacher transport and labels are validated without live HTTP calls."""

import io
import json
from copy import deepcopy
from email.message import Message
from urllib.request import HTTPSHandler, build_opener
from urllib.response import addinfourl

import pytest

from matching_engine.rerank_providers import (
    QWEN,
    SYSTEMONE_MODEL,
    SYSTEMONE_PAIR_ABSTENTION_POLICY,
    SYSTEMONE_PAIR_PROMPT_VERSION,
    SYSTEMONE_PROMPT_VERSION,
    SYSTEMONE_URL,
    BudgetExceeded,
    LocalProviders,
    SystemOneProviders,
    digest,
    teacher_identity,
)


@pytest.fixture
def provider(tmp_path):
    key_file = tmp_path / "synthetic-key.txt"
    key_file.write_text("sk-unit-test-credential\n", encoding="utf-8")
    return SystemOneProviders(tmp_path / "cache", key_file=key_file)


def test_teacher_identity_contains_only_public_contract() -> None:
    assert teacher_identity()["endpoint"] == SYSTEMONE_URL
    assert teacher_identity()["model"] == SYSTEMONE_MODEL == "kev-latest"
    assert set(teacher_identity()) == {"endpoint", "model", "prompt_version"}


def test_remote_transport_uses_fixed_endpoint_and_lazy_key_file(tmp_path, monkeypatch) -> None:
    key_file = tmp_path / "created-after-provider.txt"
    teacher = SystemOneProviders(tmp_path / "cache", key_file=key_file)
    key_file.write_text("sk-unit-test-credential\n", encoding="utf-8")
    captured = []

    def respond(request):
        captured.append(request)
        return io.BytesIO(b'{"model":"kev-latest","answers":{}}')

    monkeypatch.setattr(teacher, "_open", respond)
    payload = {"state": "{}", "questions": {}}
    teacher.request("/v1/systemone", payload, "pair")
    request = captured[0]
    assert request.full_url == SYSTEMONE_URL
    assert request.method == "POST"
    assert request.get_header("Authorization") == "Bearer sk-unit-test-credential"
    assert request.get_header("Content-type") == "application/json"
    assert json.loads(request.data) == payload
    for path in teacher.directory.glob("*.json"):
        assert "unit-test-credential" not in path.read_text()


def test_environment_key_file_is_used_lazily(tmp_path, monkeypatch) -> None:
    key_file = tmp_path / "environment-key.txt"
    monkeypatch.setenv("RERANK_SYSTEMONE_KEY_FILE", str(key_file))
    teacher = SystemOneProviders(tmp_path / "cache")
    key_file.write_text("sk-environment-test-credential", encoding="utf-8")
    captured = []

    def respond(request):
        captured.append(request)
        return io.BytesIO(b'{"model":"kev-latest","answers":{}}')

    monkeypatch.setattr(teacher, "_open", respond)
    teacher.request("/v1/systemone", {}, "grade")
    assert captured[0].get_header("Authorization") == "Bearer sk-environment-test-credential"


def test_cache_replay_requires_no_credentials_and_does_not_spend_budget(provider, monkeypatch) -> None:
    result = {"model": "kev-latest", "answers": {}, "usage": {"total_tokens": 7}}
    monkeypatch.setattr(provider, "_open", lambda _request: io.BytesIO(json.dumps(result).encode()))
    payload = {"state": "synthetic", "questions": {}}
    assert provider.request("/v1/systemone", payload, "pair") == result
    missing_key = provider.directory.parent / "missing-key.txt"
    replay = SystemOneProviders(provider.directory, key_file=missing_key)
    monkeypatch.setattr(replay, "_open", lambda _request: pytest.fail("cache replay attempted HTTP"))
    assert replay.request("/v1/systemone", payload, "pair") == result
    assert json.loads((provider.directory / "usage.json").read_text()) == {"pair": 1}
    records = [json.loads(path.read_text()) for path in provider.directory.glob("*.json") if path.name != "usage.json"]
    assert len(records) == 1
    assert records[0]["usage"] == {"total_tokens": 7}
    assert records[0]["request"]["model"] == "kev-latest"


def test_corrupted_cache_is_rejected_without_a_new_attempt(provider, monkeypatch) -> None:
    monkeypatch.setattr(provider, "_open", lambda _request: io.BytesIO(b'{"model":"kev-latest","answers":{}}'))
    provider.request("/v1/systemone", {}, "pair")
    record_path = next(path for path in provider.directory.glob("*.json") if path.name != "usage.json")
    record = json.loads(record_path.read_text())
    record["response"]["model"] = "changed-after-cache-write"
    record_path.write_text(json.dumps(record), encoding="utf-8")
    monkeypatch.setattr(provider, "_open", lambda _request: pytest.fail("corrupted cache attempted HTTP"))
    with pytest.raises(ValueError, match="cache integrity"):
        provider.request("/v1/systemone", {}, "pair")
    assert json.loads((provider.directory / "usage.json").read_text()) == {"pair": 1}


@pytest.mark.parametrize("kind,ceiling", [("pair", 3000), ("grade", 600)])
def test_remote_budget_is_reserved_before_http(provider, monkeypatch, kind, ceiling) -> None:
    (provider.directory / "usage.json").write_text(json.dumps({kind: ceiling}))
    monkeypatch.setattr(provider, "_open", lambda _request: pytest.fail("exhausted budget attempted HTTP"))
    with pytest.raises(BudgetExceeded):
        provider.request("/v1/systemone", {}, kind)


def test_remote_failure_consumes_attempt_and_sanitizes_exception(provider, monkeypatch) -> None:
    def fail(_request):
        raise TimeoutError("Authorization: Bearer unit-test-credential")

    monkeypatch.setattr(provider, "_open", fail)
    with pytest.raises(RuntimeError) as caught:
        provider.request("/v1/systemone", {}, "pair")
    assert "unit-test-credential" not in str(caught.value)
    assert "TimeoutError" in str(caught.value)
    assert json.loads((provider.directory / "usage.json").read_text()) == {"pair": 1}
    failures = list(provider.directory.glob("*.failure-1.json"))
    assert len(failures) == 1
    failure = json.loads(failures[0].read_text())
    assert failure["error_type"] == "TimeoutError"
    assert "unit-test-credential" not in failures[0].read_text()


@pytest.mark.parametrize("body", [b"{broken JSON", b"[]"])
def test_malformed_http_response_records_a_failure_without_success_cache(provider, monkeypatch, body) -> None:
    monkeypatch.setattr(provider, "_open", lambda _request: io.BytesIO(body))
    with pytest.raises(RuntimeError):
        provider.request("/v1/systemone", {}, "pair")
    assert json.loads((provider.directory / "usage.json").read_text()) == {"pair": 1}
    assert len(list(provider.directory.glob("*.failure-1.json"))) == 1
    assert len(list(provider.directory.glob("*.json"))) == 2


def test_missing_credential_fails_before_http_without_exposing_private_path(tmp_path, monkeypatch) -> None:
    teacher = SystemOneProviders(tmp_path / "cache", key_file=tmp_path / "private-credential-file.txt")
    monkeypatch.setattr(teacher, "_open", lambda _request: pytest.fail("missing key attempted HTTP"))
    with pytest.raises(RuntimeError) as caught:
        teacher.request("/v1/systemone", {}, "pair")
    assert "private-credential-file" not in str(caught.value)
    assert json.loads((teacher.directory / "usage.json").read_text()) == {"pair": 1}


@pytest.mark.parametrize("path,kind", [("/v1/choice", "pair"), ("/v1/score", "grade"), ("/v1/systemone", "generation")])
def test_endpoint_cannot_be_switched_to_an_unapproved_target(provider, monkeypatch, path, kind) -> None:
    monkeypatch.setattr(provider, "_open", lambda _request: pytest.fail("invalid endpoint attempted HTTP"))
    with pytest.raises(ValueError):
        provider.request(path, {}, kind)
    assert not (provider.directory / "usage.json").exists()


def test_legacy_local_generation_remains_replayable_without_credentials(tmp_path, monkeypatch) -> None:
    import matching_engine.rerank_providers as module

    traits = {"grade": "junior", "levels": {"python": 2, "react": 1, "sql": 2, "rest_api": 2, "teamwork": 3},
              "status": "confirmed", "duration_months": 12, "production_support": False,
              "salary_min": 80000, "description": "Synthetic profile"}
    captured = []

    def respond(request, timeout):
        captured.append(request)
        return io.BytesIO(json.dumps({"choices": [{"message": {"content": json.dumps([traits])}}]}).encode())

    monkeypatch.setattr(module, "urlopen", respond)
    teacher = LocalProviders(tmp_path / "cache")
    assert teacher.generate("backend", "synthetic scenario", count=1) == [traits]
    assert SystemOneProviders.generate is LocalProviders.generate
    assert captured[0].full_url == "http://127.0.0.1:12444/api/v1/chat/completions"
    assert captured[0].get_header("Authorization") is None
    assert json.loads(captured[0].data)["model"] == QWEN
    assert json.loads((teacher.directory / "usage.json").read_text()) == {"generation": 1}


def test_multiservice_key_file_selects_only_sverk_credential(tmp_path, monkeypatch) -> None:
    key_file = tmp_path / "provider-keys.txt"
    key_file.write_text("qwen token\nsk-test-other\n\nsverk token\nsk-test-authorized\n", encoding="utf-8")
    teacher = SystemOneProviders(tmp_path / "cache", key_file=key_file)
    captured = []

    def respond(request):
        captured.append(request)
        return io.BytesIO(b'{"model":"kev-latest","answers":{}}')

    monkeypatch.setattr(teacher, "_open", respond)
    teacher.request("/v1/systemone", {}, "pair")
    assert captured[0].get_header("Authorization") == "Bearer sk-test-authorized"
    assert all("sk-test" not in path.read_text() for path in teacher.directory.glob("*.json"))


@pytest.mark.parametrize("text", ["sk-test-one\nsk-test-two", "qwen token\nsk-test-other", "sverk token\nsk-test-one\nsk-test-two"])
def test_ambiguous_or_unapproved_key_file_never_attempts_http(tmp_path, monkeypatch, text) -> None:
    key_file = tmp_path / "invalid-keys.txt"
    key_file.write_text(text, encoding="utf-8")
    teacher = SystemOneProviders(tmp_path / "cache", key_file=key_file)
    monkeypatch.setattr(teacher, "_open", lambda _request: pytest.fail("invalid credential attempted HTTP"))
    with pytest.raises(RuntimeError) as caught:
        teacher.request("/v1/systemone", {}, "pair")
    assert "sk-test" not in str(caught.value)


def test_redirect_handler_refuses_to_forward_authorization(provider, monkeypatch) -> None:
    import matching_engine.rerank_providers as module

    captured = []

    class RedirectResponse(HTTPSHandler):
        def https_open(self, request):
            captured.append(request)
            assert request.full_url == SYSTEMONE_URL
            assert request.get_header("Authorization") == "Bearer sk-unit-test-credential"
            headers = Message()
            headers["Location"] = "https://unapproved.example/v1/systemone"
            response = addinfourl(io.BytesIO(), headers, request.full_url, 302)
            response.msg = "Found"
            return response

    def opener(handler):
        return build_opener(handler, RedirectResponse())

    monkeypatch.setattr(module, "build_opener", opener)
    with pytest.raises(RuntimeError, match="HTTPError"):
        provider.request("/v1/systemone", {}, "pair")
    assert len(captured) == 1
    assert len(list(provider.directory.glob("*.failure-1.json"))) == 1


def test_pair_http_contract_contains_raw_synthetic_facts_and_choice_question(provider, monkeypatch) -> None:
    captured = []
    result = {"model": "kev-latest", "answers": {"preference": {
        "choice": "B", "confidence": .7,
        "probabilities": {"A": .1, "B": .7, "tie": .1, "insufficient": .1}}}}

    def respond(request):
        captured.append(json.loads(request.data))
        return io.BytesIO(json.dumps(result).encode())

    monkeypatch.setattr(provider, "_open", respond)
    vacancy, a, b = {"class": "backend"}, {"levels": {"python": 2}}, {"levels": {"python": 4}}
    assert provider.pair(vacancy, a, b, "Prefer Python") == "B"
    payload = captured[0]
    assert payload["model"] == "kev-latest"
    assert isinstance(payload["state"], str)
    assert json.loads(payload["state"]) == {"vacancy": vacancy, "A": a, "B": b, "preferences": "Prefer Python"}
    assert set(payload["questions"]) == {"preference"}
    assert payload["questions"]["preference"]["type"] == "choice"
    assert set(payload["questions"]["preference"]["criteria"]) == {"A", "B", "tie", "insufficient"}


def test_grade_http_contract_uses_independent_score_distribution(provider, monkeypatch) -> None:
    captured = []
    result = {"model": "kev-latest", "answers": {"suitability": {
        "score": 2.2, "confidence": .7, "probabilities": {"0": 0., "1": .05, "2": .7, "3": .25}}}}

    def respond(request):
        captured.append(json.loads(request.data))
        return io.BytesIO(json.dumps(result).encode())

    monkeypatch.setattr(provider, "_open", respond)
    assert provider.grade({"class": "backend"}, {"grade": "junior"}, "Prefer exact fit") == 2
    payload = captured[0]
    assert json.loads(payload["state"]) == {"vacancy": {"class": "backend"}, "candidate": {"grade": "junior"}, "preferences": "Prefer exact fit"}
    assert payload["questions"]["suitability"]["type"] == "score"
    assert len(payload["questions"]["suitability"]["criteria"]) == 4


@pytest.mark.parametrize("label", ["A", "B", "tie", "insufficient"])
def test_pair_accepts_each_valid_unique_winner(provider, monkeypatch, label) -> None:
    probabilities = dict.fromkeys(("A", "B", "tie", "insufficient"), .1)
    probabilities[label] = .7
    response = {"model": "kev-latest", "answers": {"preference": {
        "choice": label, "confidence": .7, "probabilities": probabilities}}}
    monkeypatch.setattr(provider, "request", lambda *_args: response)
    assert provider.pair({}, {}, {}, "synthetic") == label


@pytest.mark.parametrize("probabilities", [
    {"A": .5, "B": .5, "tie": 0., "insufficient": 0.},
    {"A": .25, "B": .25, "tie": .25, "insufficient": .25},
])
def test_pair_ambiguous_distribution_never_emits_binary_label(provider, monkeypatch, probabilities) -> None:
    response = {"model": "kev-latest", "answers": {"preference": {
        "choice": "A", "confidence": .5, "probabilities": probabilities}}}
    monkeypatch.setattr(provider, "request", lambda *_args: response)
    assert provider.pair({}, {}, {}, "synthetic") == "tie"


@pytest.mark.parametrize("confidence,probabilities,expected", [
    (.199, {"0": .1, "1": .1, "2": .7, "3": .1}, None),
    (.2, {"0": .1, "1": .1, "2": .7, "3": .1}, 2),
    (.7, {"0": 0., "1": 0., "2": .5, "3": .5}, None),
])
def test_grade_preserves_uncertainty_and_confidence_boundary(provider, monkeypatch, confidence, probabilities, expected) -> None:
    response = {"model": "kev-latest", "answers": {"suitability": {
        "score": 2.5, "confidence": confidence, "probabilities": probabilities}}}
    monkeypatch.setattr(provider, "request", lambda *_args: response)
    assert provider.grade({}, {}, "synthetic") == expected


@pytest.mark.parametrize("question,answer", [
    ("preference", {"choice": "A", "confidence": .7, "probabilities": {"A": .7, "B": .1, "tie": .1, "insufficient": .1}}),
    ("suitability", {"score": 2.2, "confidence": .7, "probabilities": {"0": 0., "1": .05, "2": .7, "3": .25}}),
])
@pytest.mark.parametrize("mutation", ["model", "truncated", "wrong_id", "extra_id", "nonobject", "nan", "infinity", "boolean", "negative", "sum", "keys", "confidence"])
def test_malformed_teacher_answers_are_rejected(provider, monkeypatch, question, answer, mutation) -> None:
    answer = deepcopy(answer)
    response = {"model": "kev-latest", "answers": {question: answer}}
    if mutation == "model":
        response["model"] = "unapproved-model"
    elif mutation == "truncated":
        response["truncated"] = True
    elif mutation == "wrong_id":
        response["answers"] = {"other": answer}
    elif mutation == "extra_id":
        response["answers"]["other"] = answer
    elif mutation == "nonobject":
        response["answers"][question] = []
    elif mutation == "confidence":
        answer["confidence"] = float("nan")
    elif mutation == "keys":
        answer["probabilities"].pop(next(iter(answer["probabilities"])))
    else:
        first = next(iter(answer["probabilities"]))
        answer["probabilities"][first] = {"nan": float("nan"), "infinity": float("inf"),
                                          "boolean": True, "negative": -.1, "sum": 1.}[mutation]
    monkeypatch.setattr(provider, "request", lambda *_args: response)
    with pytest.raises((ValueError, TypeError)):
        if question == "preference":
            provider.pair({}, {}, {}, "synthetic")
        else:
            provider.grade({}, {}, "synthetic")


def test_pair_label_must_match_distribution(provider, monkeypatch) -> None:
    response = {"model": "kev-latest", "answers": {"preference": {
        "choice": "B", "confidence": .7, "probabilities": {"A": .7, "B": .1, "tie": .1, "insufficient": .1}}}}
    monkeypatch.setattr(provider, "request", lambda *_args: response)
    with pytest.raises(ValueError):
        provider.pair({}, {}, {}, "synthetic")


@pytest.mark.parametrize("score", [float("nan"), float("inf"), True, -.1, 3.1, "2"])
def test_grade_requires_finite_bounded_numeric_score(provider, monkeypatch, score) -> None:
    response = {"model": "kev-latest", "answers": {"suitability": {
        "score": score, "confidence": .7, "probabilities": {"0": 0., "1": .05, "2": .7, "3": .25}}}}
    monkeypatch.setattr(provider, "request", lambda *_args: response)
    with pytest.raises(ValueError):
        provider.grade({}, {}, "synthetic")


def _pair_v2(provider):
    return SystemOneProviders(provider.directory, key_file=provider.key_file,
                              pair_prompt_version=SYSTEMONE_PAIR_PROMPT_VERSION)


def _pair_response(label="A", confidence=.7, probabilities=None):
    if probabilities is None:
        probabilities = dict.fromkeys(("A", "B", "tie", "insufficient"), .1)
        probabilities[label] = .7
    return {"model": SYSTEMONE_MODEL, "answers": {"preference": {
        "choice": label, "confidence": confidence, "probabilities": probabilities}}}


def test_v1_pair_payload_and_cache_hash_remain_exactly_compatible(provider):
    vacancy, a, b = {"class": "backend"}, {"levels": {"python": 2}}, {"levels": {"python": 4}}
    payload = provider._pair_payload(vacancy, a, b, "Prefer Python")
    assert provider.pair_prompt_version == SYSTEMONE_PROMPT_VERSION
    assert payload == {"state": (
        '{"A": {"levels": {"python": 2}}, "B": {"levels": {"python": 4}}, '
        '"preferences": "Prefer Python", "vacancy": {"class": "backend"}}'),
        "model": "kev-latest", "questions": {"preference": {"type": "choice",
            "instructions": "Which candidate better fits these employer preferences? Consider stated facts and uncertainty.",
            "criteria": {"A": "A preferable to B", "B": "B preferable to A",
                         "tie": "Comparable overall suitability", "insufficient": "Insufficient facts to compare"}}}}
    assert digest(provider._identity("/v1/systemone", payload, "pair")) == (
        "d66f8aaaed0981208acb1f3fb44e415bd0543841ba445d73ec4b2d172869db67")
    assert teacher_identity() == {"endpoint": SYSTEMONE_URL, "model": SYSTEMONE_MODEL,
                                  "prompt_version": "rerank-systemone-v1"}
    assert set(teacher_identity(SYSTEMONE_PAIR_PROMPT_VERSION)) == {"endpoint", "model", "prompt_version"}


def test_opt_in_pair_v2_does_not_change_grade_payload_identity_or_cache(provider, monkeypatch):
    corrected = _pair_v2(provider)
    captured = []
    response = {"model": SYSTEMONE_MODEL, "answers": {"suitability": {
        "score": 2, "confidence": .7, "probabilities": {"0": .1, "1": .1, "2": .7, "3": .1}}}}

    def respond(request):
        captured.append(json.loads(request.data))
        return io.BytesIO(json.dumps(response).encode())

    monkeypatch.setattr(provider, "_open", respond)
    assert provider.grade({"class": "backend"}, {"grade": "junior"}, "Prefer exact fit") == 2
    payload = captured[0]
    expected = provider._identity("/v1/systemone", payload, "grade")
    assert expected["prompt_version"] == SYSTEMONE_PROMPT_VERSION
    assert corrected._identity("/v1/systemone", payload, "grade") == expected
    monkeypatch.setattr(corrected, "_open", lambda _request: pytest.fail("v2 repeated cached grade HTTP"))
    monkeypatch.setattr(corrected, "_api_key", lambda: pytest.fail("v2 read a key for cached grade"))
    assert corrected.grade({"class": "backend"}, {"grade": "junior"}, "Prefer exact fit") == 2
    assert json.loads((provider.directory / "usage.json").read_text()) == {"grade": 1}


def test_pair_v2_orders_context_first_and_preserves_unverified_unknown_facts(provider, monkeypatch):
    corrected = _pair_v2(provider)
    a = {"grade": "junior", "description": "Senior role in a personal project", "levels": {"react": 5},
         "status": "self_reported", "duration_months": None, "production_support": None}
    b = {"grade": "middle", "levels": {"react": 4}, "status": "confirmed",
         "duration_months": 12, "production_support": False}
    original_a, original_b = deepcopy(a), deepcopy(b)
    monkeypatch.setattr(corrected, "_open", lambda _request: pytest.fail("pure payload helper performed HTTP"))
    monkeypatch.setattr(corrected, "_api_key", lambda: pytest.fail("pure payload helper read credentials"))
    payload = corrected._pair_payload({"class": "frontend"}, a, b, "Prefer economical known fit")
    state = json.loads(payload["state"])
    assert list(state) == ["vacancy", "preferences", "A", "B"]
    assert state["A"] == original_a and state["B"] == original_b
    assert a == original_a and b == original_b
    question = payload["questions"]["preference"]
    instructions = question["instructions"]
    for rule in ("0..7", "separately from evidence", "formal test result", "does not reassign grade",
                 "confirmed".capitalize(), "partial", "self_reported", "pending", "unknown, never zero",
                 "do not invent technical minima", "class-only", "missing facts are not a negative qualification",
                 "Salary economy", "known technical fit", "employer explicitly mentions it", "salary overlap",
                 "minimum technical fit", "genuine", "Swapping A and B"):
        assert rule in instructions
    criteria = question["criteria"]
    assert criteria["A"].translate(str.maketrans("AB", "BA")) == criteria["B"]
    assert "comparable" in criteria["tie"] and "uncertain" in criteria["insufficient"]
    assert not (provider.directory / "usage.json").exists()


@pytest.mark.parametrize("label,confidence,probabilities,expected", [
    ("A", .199, {"A": .7, "B": .1, "tie": .1, "insufficient": .1}, "insufficient"),
    ("A", .2, {"A": .7, "B": .1, "tie": .1, "insufficient": .1}, "A"),
    ("A", .7, {"A": .4245, "B": .3755, "tie": .1, "insufficient": .1}, "insufficient"),
    ("A", .7, {"A": .45, "B": .4, "tie": .1, "insufficient": .05}, "A"),
    ("A", .7, {"A": .251, "B": .25, "tie": .25, "insufficient": .249}, "insufficient"),
    ("A", .7, {"A": .5, "B": .5, "tie": 0., "insufficient": 0.}, "insufficient"),
    ("tie", .7, {"A": .1, "B": .1, "tie": .7, "insufficient": .1}, "tie"),
    ("tie", .199, {"A": .1, "B": .1, "tie": .7, "insufficient": .1}, "insufficient"),
    ("insufficient", .7, {"A": .1, "B": .1, "tie": .1, "insufficient": .7}, "insufficient"),
])
def test_pair_v2_abstains_without_changing_raw_response(provider, monkeypatch, label, confidence, probabilities, expected):
    corrected = _pair_v2(provider)
    response = _pair_response(label, confidence, probabilities)
    original = deepcopy(response)
    monkeypatch.setattr(corrected, "request", lambda *_args: response)
    assert corrected.pair({}, {}, {}, "synthetic") == expected
    assert response == original
    assert SYSTEMONE_PAIR_ABSTENTION_POLICY == {
        "minimum_confidence": .2, "minimum_probability_gap": .05, "calibrated": False}


def test_pair_prompt_versions_cache_raw_probabilities_separately_with_one_shared_ledger(provider, monkeypatch):
    corrected = _pair_v2(provider)
    response = _pair_response(confidence=.1284)
    calls = []

    def respond(request):
        calls.append(request)
        return io.BytesIO(json.dumps(response).encode())

    monkeypatch.setattr(provider, "_open", respond)
    monkeypatch.setattr(corrected, "_open", respond)
    assert provider.pair({}, {"status": "self_reported"}, {}, "synthetic") == "A"
    assert corrected.pair({}, {"status": "self_reported"}, {}, "synthetic") == "insufficient"
    assert len(calls) == 2
    records = [json.loads(path.read_text()) for path in provider.directory.glob("*.json") if path.name != "usage.json"]
    assert {record["request"]["prompt_version"] for record in records} == {
        SYSTEMONE_PROMPT_VERSION, SYSTEMONE_PAIR_PROMPT_VERSION}
    assert len({record["request_hash"] for record in records}) == 2
    assert all(record["response"] == response for record in records)
    assert all(record["response"]["answers"]["preference"]["choice"] == "A" for record in records)
    assert json.loads((provider.directory / "usage.json").read_text()) == {"pair": 2}
    provider.key_file.unlink()
    for teacher, expected in ((provider, "A"), (corrected, "insufficient")):
        monkeypatch.setattr(teacher, "_open", lambda _request: pytest.fail("cached pair repeated HTTP"))
        monkeypatch.setattr(teacher, "_api_key", lambda: pytest.fail("cached pair read a key"))
        assert teacher.pair({}, {"status": "self_reported"}, {}, "synthetic") == expected
    assert json.loads((provider.directory / "usage.json").read_text()) == {"pair": 2}


def test_new_pair_prompt_does_not_reset_an_exhausted_shared_pair_budget(provider, monkeypatch):
    corrected = _pair_v2(provider)
    (provider.directory / "usage.json").write_text(json.dumps({"pair": 3000, "grade": 9, "generation": 4}))
    monkeypatch.setattr(corrected, "_open", lambda _request: pytest.fail("new prompt bypassed exhausted pair budget"))
    monkeypatch.setattr(corrected, "_api_key", lambda: pytest.fail("exhausted pair budget read a key"))
    with pytest.raises(BudgetExceeded):
        corrected.pair({}, {}, {}, "synthetic")
    assert json.loads((provider.directory / "usage.json").read_text()) == {"pair": 3000, "grade": 9, "generation": 4}


def test_v2_low_confidence_never_masks_a_malformed_teacher_choice(provider, monkeypatch):
    corrected = _pair_v2(provider)
    response = _pair_response("B", confidence=.1, probabilities={"A": .7, "B": .1, "tie": .1, "insufficient": .1})
    monkeypatch.setattr(corrected, "request", lambda *_args: response)
    with pytest.raises(ValueError, match="contradicts its distribution"):
        corrected.pair({}, {}, {}, "synthetic")


@pytest.mark.parametrize("version", ["", "rerank-systemone-v2", "rerank-systemone-pair-v3", "rerank-sverk-generation-v2"])
def test_unrecognized_pair_prompt_is_rejected_before_cache_creation_or_http(tmp_path, monkeypatch, version):
    monkeypatch.setattr(SystemOneProviders, "_open", lambda *_args: pytest.fail("invalid prompt performed HTTP"))
    monkeypatch.setattr(SystemOneProviders, "_api_key", lambda *_args: pytest.fail("invalid prompt read a key"))
    with pytest.raises(ValueError, match="unknown SystemOne teacher prompt version"):
        teacher_identity(version)
    directory = tmp_path / "invalid-prompt-cache"
    with pytest.raises(ValueError, match="unknown SystemOne teacher prompt version"):
        SystemOneProviders(directory, pair_prompt_version=version)
    assert not directory.exists()
