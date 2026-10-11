"""Configured generator and rubric fallback must use the same Timeweb wire."""

import json

import pytest
from testing_engine.generator import DeepSeekGenerator, GenerationUnavailable
from testing_engine.llm_config import completion_config
from testing_engine.rubric import RubricJudge


def test_timeweb_generator_uses_configured_endpoint(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "timeweb")
    monkeypatch.setenv("TIMEWEB_API_KEY", "synthetic")
    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def read(self, _limit):
            return json.dumps({"choices": [{"message": {"content": '{"prompt":"test"}'}}]}).encode()
    def open_request(request, **kwargs):
        body = json.loads(request.data)
        assert request.full_url == "https://api.timeweb.ai/v1/chat/completions"
        assert body["model"] == "timeweb/gpt-oss-120b"
        assert body["reasoning_effort"] == "low"
        assert "thinking" not in body
        return Response()
    monkeypatch.setattr("urllib.request.urlopen", open_request)
    assert DeepSeekGenerator().generate_item("python", 2, "mcq")["family"] == "python"


def test_timeweb_rubric_fallback(monkeypatch):
    monkeypatch.delenv("JEV_API_KEY", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "timeweb")
    monkeypatch.setenv("TIMEWEB_API_KEY", "synthetic")
    judge = RubricJudge()
    def request(payload, key):
        assert payload["reasoning_effort"] == "low"
        assert payload["model"] == "timeweb/gpt-oss-120b"
        return {"choices": [{"message": {"content": '{"criteria":[{"met":true}]}'}}]}
    monkeypatch.setattr(judge, "_request", request)
    assert judge.judge({"prompt": "task", "rubric": ["point"]}, "answer") == (True, "rubric:timeweb")


def test_provider_failures_remain_bounded(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "timeweb")
    monkeypatch.setenv("TIMEWEB_API_KEY", "synthetic")
    def fail(*args, **kwargs):
        raise OSError("private upstream detail")
    monkeypatch.setattr("urllib.request.urlopen", fail)
    with pytest.raises(GenerationUnavailable, match="^llm_generation_unavailable$"):
        DeepSeekGenerator().generate_item("python", 2, "mcq")


@pytest.mark.parametrize(("key", "value"), [("LLM_PROVIDER", "other"), ("TIMEWEB_REASONING_EFFORT", "max"), ("TIMEWEB_BASE_URL", "http://api.timeweb.ai/v1")])
def test_invalid_configuration_fails_closed(monkeypatch, key, value):
    monkeypatch.setenv("LLM_PROVIDER", "timeweb")
    monkeypatch.setenv(key, value)
    with pytest.raises(ValueError):
        completion_config()


@pytest.mark.parametrize("confidence", [1, 0.4, None])
def test_jev_mirror_rubric_wire_and_uncertainty(monkeypatch, confidence):
    monkeypatch.setenv("JEV_PROVIDER", "openrouter-mirror")
    monkeypatch.setenv("JEV_API_KEY", "synthetic")
    judge = RubricJudge()
    assert judge.url == "https://openrouter.droidje.com/api/alpha/decisions"
    assert judge.model == "typesafe/jev-1.13"
    def request(payload, key):
        assert payload["state"]["text"]
        assert payload["questions"]["0"]["type"] == "choice"
        assert payload["questions"]["0"]["criteria"] == {"not_met": "not_met", "met": "met"}
        return {"model": "typesafe/jev-1.13-20260917", "answers": {"0": {"type": "choice", "choice": "met", "probabilities": {"met": 1, "not_met": 0}, "confidence": confidence}}}
    monkeypatch.setattr(judge, "_request", request)
    verdict = judge.judge({"prompt": "task", "rubric": ["point"]}, "answer")
    if confidence == 1:
        assert verdict == (True, "rubric:jev-mirror")
    else:
        assert verdict[0] is None


def test_jev_mirror_rubric_uses_proxy_auth(monkeypatch):
    monkeypatch.setenv("JEV_PROVIDER", "openrouter-mirror")
    monkeypatch.setenv("JEV_API_KEY", "synthetic")
    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def read(self, _limit):
            return b'{}'
    def open_request(request, **kwargs):
        assert request.get_header("X-proxy-token") == "synthetic"
        assert request.get_header("Authorization") is None
        return Response()
    monkeypatch.setattr("urllib.request.urlopen", open_request)
    assert RubricJudge()._request({}, "key") == {}


@pytest.mark.parametrize("model", ["typesafe/jev-1.13", "typesafe/jev-1.13-20260917", "wrong/model", "typesafe/jev-1.13-other", "typesafe/jev-1.13-202609170", None])
def test_mirror_rubric_rejects_wrong_model_without_cache(monkeypatch, tmp_path, model):
    monkeypatch.setenv("JEV_PROVIDER", "openrouter-mirror")
    monkeypatch.setenv("JEV_API_KEY", "synthetic")
    judge = RubricJudge(cache_dir=tmp_path)
    response = {"model": model, "answers": {"0": {"type": "choice", "choice": "met", "confidence": 1,
        "probabilities": {"met": 1, "not_met": 0}}}}
    monkeypatch.setattr(judge, "_request", lambda *_: response)
    verdict = judge.judge({"prompt": "task", "rubric": ["point"]}, "answer")
    if model in {"typesafe/jev-1.13", "typesafe/jev-1.13-20260917"}:
        assert verdict == (True, "rubric:jev-mirror")
        assert len(list(tmp_path.glob("*.json"))) == 1
    else:
        assert verdict == (None, "text_judge_unavailable")
        assert list(tmp_path.iterdir()) == []
