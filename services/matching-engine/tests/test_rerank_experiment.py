"""Budget/cache and split safeguards without live provider calls."""

import io
import json
import runpy
from collections import Counter
from pathlib import Path

import pytest
from matching_engine.rerank_experiment import (
    family_manifest,
    generate,
    label_manifest,
    label_validation,
    review_package,
    train_dataset,
)
from matching_engine.rerank_providers import BudgetExceeded, LocalProviders, digest


def test_manifest_covers_all_directions_and_frozen_split_counts() -> None:
    manifest = family_manifest()
    assert len(manifest) == 60
    assert Counter(x["split"] for x in manifest) == {"train": 32, "validation": 8, "calibration": 8, "golden": 12}
    assert len({x["source_id"] for x in manifest}) == 60
    for direction in {x["direction"] for x in manifest}:
        counts = Counter(x["split"] for x in manifest if x["direction"] == direction)
        assert counts["train"] == 4 and counts["validation"] == counts["calibration"] == 1
        assert counts["golden"] in (1, 2)


def test_failed_pilot_never_calls_generator(tmp_path, monkeypatch) -> None:
    (tmp_path / "pilot-report.json").write_text(json.dumps({"state": "stopped"}))
    monkeypatch.setattr(LocalProviders, "generate", lambda *_args: pytest.fail("provider called after failed pilot"))
    with pytest.raises(ValueError, match="pilot stopped"):
        generate(tmp_path)


def test_cache_replays_exact_responses_and_reserves_budget(tmp_path, monkeypatch) -> None:
    import matching_engine.rerank_providers as module
    calls = []

    def call(request, timeout):
        calls.append(request)
        return io.BytesIO(b'{"choice":"A","usage":null}')

    monkeypatch.setattr(module, "urlopen", call)
    provider = LocalProviders(tmp_path)
    assert provider.request("/v1/choice", {"synthetic": 1}, "pair")["choice"] == "A"
    assert provider.request("/v1/choice", {"synthetic": 1}, "pair")["choice"] == "A"
    assert len(calls) == 1
    assert json.loads((tmp_path / "usage.json").read_text())["pair"] == 1
    (tmp_path / "usage.json").write_text(json.dumps({"pair": 3000}))
    with pytest.raises(BudgetExceeded):
        provider.request("/v1/choice", {"synthetic": 2}, "pair")
    assert len(calls) == 1


def test_failed_call_consumes_budget_without_false_success(tmp_path, monkeypatch) -> None:
    import matching_engine.rerank_providers as module

    def fail(*_args, **_kwargs):
        raise TimeoutError("local provider unavailable")

    monkeypatch.setattr(module, "urlopen", fail)
    with pytest.raises(TimeoutError):
        LocalProviders(tmp_path).request("/v1/choice", {}, "pair")
    assert json.loads((tmp_path / "usage.json").read_text())["pair"] == 1
    assert len(list(tmp_path.glob("*.failure-1.json"))) == 1


@pytest.mark.parametrize("url", ["https://example.com", "http://127.0.0.1:12444?token=test", "http://user:test@localhost"])
def test_provider_refuses_external_or_credential_urls(tmp_path, url) -> None:
    with pytest.raises(ValueError):
        LocalProviders(tmp_path, url)


def test_nonfinite_artifact_hash_refused() -> None:
    with pytest.raises(ValueError):
        digest({"weight": float("nan")})


def test_labels_cannot_be_declared_valid_without_swap_receipts():
    invalid = label_validation([], {"state": "passed", "report_hash": "test", "swap_consistency": 1., "decisive_pairs": 10})
    assert not invalid["passed"]


def test_dataset_cannot_spoof_split_hash_with_wrong_family_ids(tmp_path):
    dataset = {"families": [], "split_hash": digest(family_manifest())}
    dataset["dataset_hash"] = digest(dataset)
    (tmp_path / "dataset.json").write_text(json.dumps(dataset))
    with pytest.raises(ValueError, match="actual family assignments"):
        train_dataset(tmp_path)


def test_label_manifest_includes_complete_requests_and_usage(tmp_path, monkeypatch):
    import matching_engine.rerank_providers as module
    monkeypatch.setattr(module, "urlopen", lambda *_args, **_kwargs: io.BytesIO(b'{"choice":"B","usage":null}'))
    provider = LocalProviders(tmp_path / "provider-cache")
    provider.request("/v1/choice", {"synthetic": 1}, "pair")
    manifest = label_manifest(tmp_path)
    assert manifest["usage"]["pair"] == 1
    record = manifest["records"][0]
    assert record["request"]["path"] == "/v1/choice"
    assert record["request"]["model"] and record["request"]["prompt_version"]
    assert record["usage"] is None and record["usage_estimated"] is False
    assert digest(manifest) == digest(label_manifest(tmp_path))


def test_laya_fractional_score_uses_independent_distribution_or_unknown(tmp_path, monkeypatch) -> None:
    provider = LocalProviders(tmp_path)
    monkeypatch.setattr(provider, "request", lambda *_args: {"score": 2.2, "confidence": .7,
        "probabilities": {"0": 0., "1": .05, "2": .7, "3": .25}})
    assert provider.grade({}, {}, "synthetic") == 2
    monkeypatch.setattr(provider, "request", lambda *_args: {"score": 1.5, "confidence": 0.,
        "probabilities": {"0": .25, "1": .25, "2": .25, "3": .25}})
    assert provider.grade({}, {}, "synthetic") is None


def test_passed_pilot_does_not_consume_the_600_profile_grade_budget(tmp_path, monkeypatch):
    profiles = [{"levels": {"python": index}} for index in range(5)]
    monkeypatch.setattr(LocalProviders, "generate", lambda *_args: profiles)
    monkeypatch.setattr(LocalProviders, "pair", lambda _self, _v, a, b, _p: "A" if a["levels"]["python"] > b["levels"]["python"] else "B")
    monkeypatch.setattr(LocalProviders, "grade", lambda *_args: pytest.fail("pilot spent full-cohort grade budget"))
    script = Path(__file__).resolve().parents[3] / "tests/rerank_pilot.py"
    namespace = runpy.run_path(str(script))
    namespace["main"].__globals__["__file__"] = str(tmp_path / "rerank_pilot.py")
    namespace["main"]()
    report = json.loads((tmp_path / "artifacts/reranker/pilot-report.json").read_text())
    assert report["state"] == "passed" and report["grade_contract_runtime_verified"] is False


def test_review_packets_are_disjoint_and_blind(tmp_path) -> None:
    family = {"manifest": {"family_id": "gold", "split": "golden", "preference": "synthetic preference"},
              "traits": [{"grade": "junior"} for _ in range(10)], "vacancy": {"class": "backend"},
              "pairs": [(a, b, "A") for a in range(10) for b in range(a + 1, 10)]}
    (tmp_path / "dataset.json").write_text(json.dumps({"families": [family]}))
    review_package(tmp_path)
    user = json.loads((tmp_path / "review-user.json").read_text())
    assistant = json.loads((tmp_path / "review-assistant.json").read_text())
    assert len(user) == len(assistant) == 20
    assert not {x["pair_id"] for x in user} & {x["pair_id"] for x in assistant}
    assert all(set(x) == {"pair_id", "vacancy", "preferences", "A", "B"} for x in user + assistant)
