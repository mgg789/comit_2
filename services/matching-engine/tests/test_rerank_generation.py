"""Remote generation, durable budgets, and process slots use synthetic offline fixtures."""

import io
import json
import multiprocessing
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from email.message import Message
from urllib.request import HTTPSHandler, build_opener
from urllib.response import addinfourl

import pytest

from matching_engine.rerank_hash import digest
from matching_engine.rerank_providers import (
    GEMMA_GENERATION_MODEL,
    GENERATION_MODEL,
    GENERATION_MODELS,
    GENERATION_URL,
    QWEN,
    BudgetExceeded,
    LocalProviders,
    SystemOneProviders,
    generation_identity,
)

TRAITS = {"grade": "junior", "levels": {"python": 2, "react": 1, "sql": 2, "rest_api": 2, "teamwork": 3},
          "status": "confirmed", "duration_months": 12, "production_support": False,
          "salary_min": 80000, "description": "Синтетический профиль"}


@pytest.fixture
def provider(tmp_path):
    key_file = tmp_path / "synthetic-key.txt"
    key_file.write_text("sk-unit-test-credential", encoding="utf-8")
    return SystemOneProviders(tmp_path / "cache", key_file=key_file)


def _response(profiles=None, finish_reason="stop", model=GENERATION_MODEL):
    profiles = [TRAITS] if profiles is None else profiles
    return {"model": model, "choices": [{"finish_reason": finish_reason,
            "message": {"content": json.dumps({"profiles": profiles}, ensure_ascii=False)}}],
            "usage": {"total_tokens": 42}}


@pytest.mark.parametrize("model", GENERATION_MODELS)
def test_generation_uses_remote_authorization_and_bounded_schema(provider, monkeypatch, model):
    provider = SystemOneProviders(provider.directory, key_file=provider.key_file, generator_model=model)
    captured = []

    def respond(request):
        captured.append(request)
        return io.BytesIO(json.dumps(_response(model=model)).encode())

    monkeypatch.setattr(provider, "_open", respond)
    assert provider.generate("backend", "synthetic", 1) == [TRAITS]
    request = captured[0]
    assert request.full_url == GENERATION_URL
    assert request.get_header("Authorization") == "Bearer sk-unit-test-credential"
    payload = json.loads(request.data)
    assert payload["model"] == model
    if model == GENERATION_MODEL:
        assert payload["chat_template_kwargs"] == {"enable_thinking": False}
    else:
        assert "chat_template_kwargs" not in payload
        assert "enable_thinking" not in payload
    assert '"profiles" array' in payload["messages"][0]["content"]
    schema = payload["response_format"]["json_schema"]
    assert schema["strict"] is True
    profiles = schema["schema"]["properties"]["profiles"]
    assert profiles["minItems"] == profiles["maxItems"] == 1
    fields = profiles["items"]["properties"]
    assert set(fields) == set(TRAITS)
    assert fields["levels"]["properties"]["python"] == {"type": "integer", "minimum": 0, "maximum": 7}
    assert fields["salary_min"]["minimum"] == 50000
    assert fields["salary_min"]["maximum"] == 180000
    records = [json.loads(path.read_text()) for path in provider.directory.glob("*.json")
               if path.name != "usage.json"]
    assert records[0]["request"]["endpoint"] == GENERATION_URL
    assert records[0]["request"]["model"] == model
    assert records[0]["request"]["prompt_version"] == generation_identity(model)["prompt_version"]
    assert records[0]["usage"] == {"total_tokens": 42}
    assert all("sk-unit" not in path.read_text() for path in provider.directory.glob("*.json"))


def test_remote_generation_replays_without_key_and_legacy_identity_is_separate(provider, monkeypatch):
    local = LocalProviders(provider.directory)
    legacy_payload = {"model": QWEN, "synthetic": 1}
    monkeypatch.setattr(local, "_open", lambda _request: io.BytesIO(b'{"legacy":true}'))
    local.request("/api/v1/chat/completions", legacy_payload, "generation")
    monkeypatch.setattr(provider, "_open", lambda _request: io.BytesIO(json.dumps(_response()).encode()))
    assert provider.generate("backend", "synthetic", 1) == [TRAITS]
    provider.key_file.unlink()
    monkeypatch.setattr(provider, "_open", lambda _request: pytest.fail("replay attempted HTTP"))
    assert provider.generate("backend", "synthetic", 1) == [TRAITS]
    assert json.loads((provider.directory / "usage.json").read_text()) == {"generation": 2}
    remote = provider._identity("/v1/chat/completions", legacy_payload, "generation")
    legacy = local._identity("/api/v1/chat/completions", legacy_payload, "generation")
    assert digest(remote) != digest(legacy)
    assert set(generation_identity()) == {"endpoint", "model", "prompt_version"}


def test_legacy_local_generation_cache_keeps_original_payload_and_hash(tmp_path, monkeypatch):
    provider = LocalProviders(tmp_path)
    captured = []

    def respond(request):
        captured.append(request)
        return io.BytesIO(json.dumps({"choices": [{"message": {"content": json.dumps([TRAITS])}}]}).encode())

    monkeypatch.setattr(provider, "_open", respond)
    assert provider.generate("backend", "synthetic", 1) == [TRAITS]
    payload = json.loads(captured[0].data)
    assert payload["model"] == QWEN
    assert payload["max_tokens"] == 1800
    assert payload["enable_thinking"] is False
    assert captured[0].full_url == "http://127.0.0.1:12444/api/v1/chat/completions"
    expected_hash = digest({"path": "/api/v1/chat/completions", "payload": payload,
                            "model": QWEN, "prompt_version": "rerank-synthetic-v1"})
    assert (tmp_path / f"{expected_hash}.json").exists()
    monkeypatch.setattr(provider, "_open", lambda _request: pytest.fail("local replay attempted HTTP"))
    assert provider.generate("backend", "synthetic", 1) == [TRAITS]


@pytest.mark.parametrize("field,value", [("salary_min", 49999), ("duration_months", 121),
                                       ("grade", "expert"), ("description", "x" * 181),
                                       ("production_support", 1), ("status", "verified")])
def test_remote_generated_traits_are_validated(provider, monkeypatch, field, value):
    traits = deepcopy(TRAITS)
    traits[field] = value
    monkeypatch.setattr(provider, "request", lambda *_args: _response([traits]))
    with pytest.raises(ValueError):
        provider.generate("backend", "synthetic", 1)


@pytest.mark.parametrize("count", [0, 6])
def test_generation_invalid_batch_never_attempts_http(provider, monkeypatch, count):
    monkeypatch.setattr(provider, "_open", lambda _request: pytest.fail("invalid count attempted HTTP"))
    with pytest.raises(ValueError):
        provider.generate("backend", "synthetic", count)
    assert not (provider.directory / "usage.json").exists()


def test_length_finish_reason_rejects_even_complete_json(provider, monkeypatch):
    monkeypatch.setattr(provider, "request", lambda *_args: _response(finish_reason="length"))
    with pytest.raises(ValueError, match="truncated"):
        provider.generate("backend", "synthetic", 1)


def test_remote_generation_count_and_level_types_remain_strict(provider, monkeypatch):
    monkeypatch.setattr(provider, "request", lambda *_args: _response([TRAITS, TRAITS]))
    with pytest.raises(ValueError, match="profile count"):
        provider.generate("backend", "synthetic", 1)
    traits = deepcopy(TRAITS)
    traits["levels"]["python"] = True
    monkeypatch.setattr(provider, "request", lambda *_args: _response([traits]))
    with pytest.raises(ValueError, match="level"):
        provider.generate("backend", "synthetic", 1)


def test_remote_threads_parallelize_with_a_shared_five_slot_ceiling(provider, monkeypatch):
    active = maximum = 0
    guard = threading.Lock()
    saturated = threading.Event()

    def respond(_request):
        nonlocal active, maximum
        with guard:
            active += 1
            maximum = max(maximum, active)
            if active == 5:
                saturated.set()
        saturated.wait(timeout=.5)
        time.sleep(.025)
        with guard:
            active -= 1
        return io.BytesIO(b'{"synthetic":true}')

    other = SystemOneProviders(provider.directory, key_file=provider.key_file, generator_model=GEMMA_GENERATION_MODEL)
    monkeypatch.setattr(provider, "_open", respond)
    monkeypatch.setattr(other, "_open", respond)
    with ThreadPoolExecutor(max_workers=12) as executor:
        results = list(executor.map(lambda index: (provider if index % 2 else other).request(
            "/v1/chat/completions", {"synthetic": index}, "generation"), range(12)))
    assert maximum == 5
    assert results == [{"synthetic": True}] * 12
    assert json.loads((provider.directory / "usage.json").read_text()) == {"generation": 12}


def test_identical_concurrent_requests_share_one_http_and_budget(provider, monkeypatch):
    calls = []

    def respond(request):
        calls.append(request)
        time.sleep(.04)
        return io.BytesIO(b'{"synthetic":true}')

    monkeypatch.setattr(provider, "_open", respond)
    with ThreadPoolExecutor(max_workers=8) as executor:
        assert list(executor.map(lambda _index: provider.request(
            "/v1/chat/completions", {"synthetic": 1}, "generation"), range(8))) == [{"synthetic": True}] * 8
    assert len(calls) == 1
    assert json.loads((provider.directory / "usage.json").read_text()) == {"generation": 1}


def test_parallel_reservations_stop_at_cumulative_generation_budget(provider, monkeypatch):
    (provider.directory / "usage.json").write_text(json.dumps({"generation": 118, "pair": 11, "grade": 7}))
    calls = []

    def respond(request):
        calls.append(request)
        time.sleep(.02)
        return io.BytesIO(b'{"synthetic":true}')

    def attempt(index):
        try:
            provider.request("/v1/chat/completions", {"synthetic": index}, "generation")
            return True
        except BudgetExceeded:
            return False

    monkeypatch.setattr(provider, "_open", respond)
    with ThreadPoolExecutor(max_workers=8) as executor:
        assert sum(executor.map(attempt, range(8))) == 2
    assert len(calls) == 2
    assert json.loads((provider.directory / "usage.json").read_text()) == {"generation": 120, "pair": 11, "grade": 7}
    restarted = SystemOneProviders(provider.directory, key_file=provider.key_file)
    with pytest.raises(BudgetExceeded):
        restarted.request("/v1/chat/completions", {"synthetic": "restart"}, "generation")


def test_remote_processes_share_slots_and_atomic_ledger(provider):
    context = multiprocessing.get_context("fork")
    active, maximum = context.Value("i", 0), context.Value("i", 0)
    guard = context.Lock()
    start = context.Event()
    errors = context.Queue()

    def run(index):
        own = SystemOneProviders(provider.directory, key_file=provider.key_file,
                                 generator_model=GENERATION_MODELS[index % 2])

        def respond(_request):
            with guard:
                active.value += 1
                maximum.value = max(maximum.value, active.value)
            time.sleep(.15)
            with guard:
                active.value -= 1
            return io.BytesIO(b'{"synthetic":true}')

        own._open = respond
        start.wait(timeout=2)
        try:
            own.request("/v1/chat/completions", {"synthetic": index}, "generation")
        except (RuntimeError, ValueError, OSError) as exc:
            errors.put(type(exc).__name__)

    processes = [context.Process(target=run, args=(index,)) for index in range(9)]
    try:
        for process in processes:
            process.start()
        start.set()
        for process in processes:
            process.join(timeout=4)
            assert process.exitcode == 0
        assert errors.empty()
        assert 2 <= maximum.value <= 5
        assert json.loads((provider.directory / "usage.json").read_text()) == {"generation": 9}
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(timeout=2)
        errors.close()


def test_local_heavy_inference_remains_serial(tmp_path, monkeypatch):
    local = LocalProviders(tmp_path)
    active = maximum = 0
    guard = threading.Lock()

    def respond(_request):
        nonlocal active, maximum
        with guard:
            active += 1
            maximum = max(maximum, active)
        time.sleep(.025)
        with guard:
            active -= 1
        return io.BytesIO(b'{"synthetic":true}')

    monkeypatch.setattr(local, "_open", respond)
    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(lambda index: local.request("/v1/choice", {"synthetic": index}, "pair"), range(4)))
    assert maximum == 1


@pytest.mark.parametrize("value", ["token: sk-unit-authorized", "key = sk-unit-authorized",
                                  "api_key: sk-unit-authorized", "sk-unit-authorized"])
def test_sverk_section_supports_explicit_adjacent_key_labels(provider, value):
    provider.key_file.write_text("Qwen\nsk-unit-other\n\nSverk token\n" + value)
    request = provider._request("/v1/chat/completions", {}, "generation")
    assert request.get_header("Authorization") == "Bearer sk-unit-authorized"


def test_ambiguous_labelled_sverk_keys_are_refused(provider):
    provider.key_file.write_text("Sverk token\ntoken: sk-unit-first\napi_key: sk-unit-second")
    with pytest.raises(ValueError, match="unambiguous"):
        provider._request("/v1/chat/completions", {}, "generation")


@pytest.mark.parametrize("model", GENERATION_MODELS)
def test_remote_generation_errors_are_sanitized_and_spend_one_attempt(provider, monkeypatch, model):
    provider = SystemOneProviders(provider.directory, key_file=provider.key_file, generator_model=model)
    def fail(_request):
        raise TimeoutError("Authorization: Bearer sk-unit-test-credential")

    monkeypatch.setattr(provider, "_open", fail)
    with pytest.raises(RuntimeError, match="TimeoutError") as caught:
        provider.generate("backend", "synthetic", 1)
    assert "sk-unit" not in str(caught.value)
    assert json.loads((provider.directory / "usage.json").read_text()) == {"generation": 1}
    assert len(list(provider.directory.glob("*.failure-1.json"))) == 1
    assert all("sk-unit" not in path.read_text() for path in provider.directory.glob("*.json"))


@pytest.mark.parametrize("model", GENERATION_MODELS)
def test_remote_generation_redirect_never_forwards_credentials(provider, monkeypatch, model):
    provider = SystemOneProviders(provider.directory, key_file=provider.key_file, generator_model=model)
    import matching_engine.rerank_providers as module
    captured = []

    class RedirectResponse(HTTPSHandler):
        def https_open(self, request):
            captured.append(request)
            assert request.full_url == GENERATION_URL
            headers = Message()
            headers["Location"] = "https://unapproved.example/v1/chat/completions"
            response = addinfourl(io.BytesIO(), headers, request.full_url, 302)
            response.msg = "Found"
            return response

    monkeypatch.setattr(module, "build_opener", lambda handler: build_opener(RedirectResponse(), handler))
    with pytest.raises(RuntimeError, match="HTTPError"):
        provider.generate("backend", "synthetic", 1)
    assert len(captured) == 1


@pytest.mark.parametrize("model", ["gemma4", "unapproved-model", QWEN, " qwen3.8-27b", "gemma4-vlm/other"])
def test_generator_whitelist_rejects_other_aliases_without_cache_or_http(tmp_path, monkeypatch, model):
    monkeypatch.setattr(SystemOneProviders, "_open", lambda *_args: pytest.fail("invalid model attempted HTTP"))
    monkeypatch.setattr(SystemOneProviders, "_api_key", lambda *_args: pytest.fail("invalid model read credentials"))
    with pytest.raises(ValueError, match="unknown synthetic generator model"):
        generation_identity(model)
    target = tmp_path / "invalid-model-cache"
    with pytest.raises(ValueError, match="unknown synthetic generator model"):
        SystemOneProviders(target, generator_model=model)
    assert not target.exists()


def test_qwen_identity_remains_compatible_and_gemma_has_a_distinct_prompt_version(provider):
    assert generation_identity() == {"endpoint": GENERATION_URL, "model": "qwen3.8-27b",
                                     "prompt_version": "rerank-sverk-generation-v2"}
    assert generation_identity(GEMMA_GENERATION_MODEL) == {
        "endpoint": GENERATION_URL, "model": "gemma4-vlm", "prompt_version": "rerank-sverk-generation-gemma4-v1"}
    gemma = SystemOneProviders(provider.directory, key_file=provider.key_file, generator_model=GEMMA_GENERATION_MODEL)
    assert gemma._identity("/v1/systemone", {}, "pair") == provider._identity("/v1/systemone", {}, "pair")
    assert gemma._identity("/v1/systemone", {}, "grade") == provider._identity("/v1/systemone", {}, "grade")


def test_generator_cache_isolation_replays_each_model_without_spending_shared_budget(provider, monkeypatch):
    gemma = SystemOneProviders(provider.directory, key_file=provider.key_file, generator_model=GEMMA_GENERATION_MODEL)
    calls = []
    gemma_traits = {**TRAITS, "salary_min": 90000}

    def respond(request):
        model = json.loads(request.data)["model"]
        calls.append(model)
        profiles = [gemma_traits] if model == GEMMA_GENERATION_MODEL else [TRAITS]
        return io.BytesIO(json.dumps(_response(profiles, model=model)).encode())

    monkeypatch.setattr(provider, "_open", respond)
    monkeypatch.setattr(gemma, "_open", respond)
    assert provider.generate("backend", "same scenario", 1) == [TRAITS]
    assert gemma.generate("backend", "same scenario", 1) == [gemma_traits]
    assert calls == [GENERATION_MODEL, GEMMA_GENERATION_MODEL]
    records = [json.loads(path.read_text()) for path in provider.directory.glob("*.json") if path.name != "usage.json"]
    assert len(records) == 2
    assert {record["request"]["model"] for record in records} == set(GENERATION_MODELS)
    assert len({record["request"]["prompt_version"] for record in records}) == 2
    assert len({record["request_hash"] for record in records}) == 2
    provider.key_file.unlink()
    for generator in (provider, gemma):
        monkeypatch.setattr(generator, "_open", lambda _request: pytest.fail("model replay attempted HTTP"))
        monkeypatch.setattr(generator, "_api_key", lambda: pytest.fail("model replay read credentials"))
    assert provider.generate("backend", "same scenario", 1) == [TRAITS]
    assert gemma.generate("backend", "same scenario", 1) == [gemma_traits]
    assert json.loads((provider.directory / "usage.json").read_text()) == {"generation": 2}


def test_qwen_and_gemma_share_the_remaining_generation_attempts(provider, monkeypatch):
    gemma = SystemOneProviders(provider.directory, key_file=provider.key_file, generator_model=GEMMA_GENERATION_MODEL)
    (provider.directory / "usage.json").write_text(json.dumps({"generation": 118, "pair": 11, "grade": 7}))
    calls = []

    def respond(request):
        model = json.loads(request.data)["model"]
        calls.append(model)
        time.sleep(.02)
        return io.BytesIO(json.dumps(_response(model=model)).encode())

    def attempt(index):
        generator = provider if index % 2 == 0 else gemma
        try:
            generator.generate("backend", f"synthetic scenario {index}", 1)
            return True
        except BudgetExceeded:
            return False

    monkeypatch.setattr(provider, "_open", respond)
    monkeypatch.setattr(gemma, "_open", respond)
    with ThreadPoolExecutor(max_workers=8) as executor:
        assert sum(executor.map(attempt, range(8))) == 2
    assert len(calls) == 2
    assert json.loads((provider.directory / "usage.json").read_text()) == {"generation": 120, "pair": 11, "grade": 7}
    for model in GENERATION_MODELS:
        restarted = SystemOneProviders(provider.directory, key_file=provider.key_file, generator_model=model)
        with pytest.raises(BudgetExceeded):
            restarted.generate("backend", "synthetic restart", 1)
