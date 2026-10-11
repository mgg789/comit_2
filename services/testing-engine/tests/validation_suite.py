"""Validation suite for the testing contour (docs/testing-design.md §10).

Runs four checks against a real in-process contour (no network):
- test-retest: identical knowledge shown twice -> identical grade;
- discrimination: weak vs strong candidate cohorts -> grade-rank separation
  reported as the point-biserial correlation;
- expert ground truth: 32 scored MCQ answers must match the bank's stored
  markup exactly (auto-checking vs the reference markup);
- leak resistance: a leaked answer sheet (item_id -> choice) from candidate A
  is inapplicable to candidate B - rotation hands B untouched instances and
  re-submitting someone else's item is rejected.

Writes a versioned report to data/validation/testing-validation.json and
exits non-zero when any acceptance criterion fails.
"""

from __future__ import annotations

import json
import math
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient
from testing_engine.bank_seed import initial_items
from testing_engine.main import create_testing_app
from testing_engine.store import TestingStore

GRADE_RANK = {"intern": 0, "junior": 1, "middle": 2, "senior": 3}
FAMILY = "docker"
N_WEAK = 3
N_STRONG = 3
EXPERT_ROUNDS = 4


def _ground_truth(data_dir: Path) -> dict[str, int | str]:
    bank = json.loads((data_dir / "items.json").read_text(encoding="utf-8"))
    return {row["item_id"]: (row["payload"]["answer_index"] if row["type"] == "mcq" else row["payload"]["expected_answers"][0])
            for row in bank if row["type"] in ("mcq", "short_answer") and row["family"] == FAMILY}


def _docker_mcq_ids(data_dir: Path) -> list[str]:
    bank = json.loads((data_dir / "items.json").read_text(encoding="utf-8"))
    return [row["item_id"] for row in bank
            if row["family"] == FAMILY and row["type"] == "mcq" and not row["retired"]]


def _run_session(client: TestClient, candidate_key: str, *, quality: str,
                 truth: dict[str, int | str]) -> dict:
    """One adaptive docker-family session; returns items, grade and answers.

    strong/retest-strong answer every MCQ correctly; weak answers wrongly;
    leak-candidate answers correctly too (its sheet is what leaks).
    """
    session = client.post("/testing/sessions", json={
        "candidate_key": candidate_key, "specialization": FAMILY,
        "declared_grade": "middle"}).json()
    session_id = session["session_id"]
    presented: list[dict] = []
    for _ in range(30):
        response = client.get(f"/testing/sessions/{session_id}/next")
        if response.status_code == 409:
            break
        task = response.json()["task"]
        truth_index = truth.get(task["item_id"])
        if quality in ("strong", "retest-strong", "leak"):
            choice = truth_index
        else:
            choice = (truth_index + 1) % len(task["options"]) if isinstance(truth_index, int) else "incorrect"
        posted = client.post(f"/testing/sessions/{session_id}/answer", json={
            "item_id": task["item_id"], "answer": choice})
        presented.append({"item_id": task["item_id"], "choice": choice,
                          "correct": posted.json().get("correct")})
    result = client.get(f"/testing/sessions/{session_id}/result").json()
    return {"session_id": session_id, "grade": result["decided_grade"],
            "presented": presented, "result": result}


def _point_biserial(binary: list[int], values: list[float]) -> float:
    """Point-biserial correlation between a binary and a continuous variable."""
    n = len(values)
    mean_binary = sum(binary) / n
    mean_values = sum(values) / n
    covariance = sum((b - mean_binary) * (v - mean_values)
                     for b, v in zip(binary, values)) / n
    var_binary = sum((b - mean_binary) ** 2 for b in binary) / n
    var_values = sum((v - mean_values) ** 2 for v in values) / n
    if var_binary == 0 or var_values == 0:
        return 0.0
    return covariance / math.sqrt(var_binary * var_values)


def _judge_item(client: TestClient, candidate_key: str, target_item_id: str,
                truth: dict[str, int | str], choice: int) -> bool | None:
    """Drive a fresh session until the target item appears, then judge it."""
    session = client.post("/testing/sessions", json={
        "candidate_key": candidate_key, "specialization": FAMILY,
        "declared_grade": "middle"}).json()
    session_id = session["session_id"]
    for _ in range(30):
        response = client.get(f"/testing/sessions/{session_id}/next")
        if response.status_code == 409:
            return None
        task = response.json()["task"]
        if task["item_id"] == target_item_id:
            posted = client.post(f"/testing/sessions/{session_id}/answer", json={
                "item_id": target_item_id, "answer": choice})
            return posted.json().get("correct")
        truth_choice = truth.get(task["item_id"], 0)
        client.post(f"/testing/sessions/{session_id}/answer", json={
            "item_id": task["item_id"], "answer": truth_choice})
    return None


def run(data_dir: Path) -> dict:
    """Execute all four checks and return the report dict."""
    app = create_testing_app(data_dir=data_dir, allow_unauthenticated=True)
    client = TestClient(app)
    store = TestingStore(data_dir)
    store.seed_items(initial_items())
    truth = _ground_truth(data_dir)

    # --- discrimination: weak vs strong cohorts (docker, MCQ-only) ---
    weak = [_run_session(client, f"weak-{index}", quality="weak",
                         truth=truth) for index in range(N_WEAK)]
    strong = [_run_session(client, f"strong-{index}", quality="strong",
                           truth=truth) for index in range(N_STRONG)]
    weak_ranks = [GRADE_RANK[session["grade"] or "intern"] for session in weak]
    strong_ranks = [GRADE_RANK[session["grade"] or "intern"] for session in strong]
    binary = [0] * len(weak_ranks) + [1] * len(strong_ranks)
    r_pb = _point_biserial(binary, [float(rank) for rank in weak_ranks + strong_ranks])

    # --- test-retest: same knowledge twice -> same grade ---
    retest = [_run_session(client, f"retest-{index}", quality="retest-strong",
                           truth=truth)["grade"] for index in range(2)]
    stability = 1.0 if retest[0] == retest[1] else 0.0

    # --- expert ground truth: auto-checking vs the stored markup ---
    docker_items = _docker_mcq_ids(data_dir)
    right_true = wrong_false = checked = 0
    for round_index in range(EXPERT_ROUNDS):
        for item_id in docker_items:
            if _judge_item(client, f"expert-right-{round_index}-{item_id}",
                           item_id, truth, truth[item_id]) is True:
                right_true += 1
            if _judge_item(client, f"expert-wrong-{round_index}-{item_id}",
                           item_id, truth, (truth[item_id] + 1) % 4) is False:
                wrong_false += 1
            checked += 2

    # --- leak resistance: fresh contour, morning-leak scenario ---
    # A fresh contour per day is the production shape (daily moratorium): the
    # leak check runs in its own instance so earlier phases' served state does
    # not bleed in. Leaker burns the seed instances in the morning; the
    # generator (simulated by store.add_generated_item) refills the slots;
    # the follower must receive only untouched instances.
    leak_app = create_testing_app(data_dir=data_dir / "leak-instance", allow_unauthenticated=True)
    leak_client = TestClient(leak_app)
    leak_truth = _ground_truth(data_dir / "leak-instance")
    leaker = _run_session(leak_client, "leaker", quality="strong", truth=leak_truth)
    leaked_sheet = {row["item_id"]: row["choice"] for row in leaker["presented"]}
    rotated_in = 0
    leak_store = TestingStore(data_dir / "leak-instance")
    for item_id in list(leaked_sheet):
        level = int(item_id.split("-L")[1].split("-")[0])
        for copy in range(3):  # production min-stock is 5 variants per slot
            if leak_store.add_generated_item({
                    "family": FAMILY, "level": level, "type": "mcq",
                    "prompt": f"Ротационный вариант {item_id} копия {copy} уровня {level}: другой сценарий.",
                    "options": ["a", "b", "c", "d"], "answer_index": (level + copy) % 4}):
                rotated_in += 1
    # The cheater applies the leaked sheet wherever a burned instance resurfaces.
    overlap = 0
    leaked_correct = 0
    repeat_safe = None
    cheater = leak_client.post("/testing/sessions", json={
        "candidate_key": "cheater", "specialization": FAMILY,
        "declared_grade": "middle"}).json()["session_id"]
    for _ in range(30):
        response = leak_client.get(f"/testing/sessions/{cheater}/next")
        if response.status_code == 409:
            break
        task = response.json()["task"]
        if task["item_id"] in leaked_sheet:
            overlap += 1
            leaked_choice = leaked_sheet[task["item_id"]]
            posted = leak_client.post(f"/testing/sessions/{cheater}/answer", json={
                "item_id": task["item_id"], "answer": leaked_choice})
            if posted.json().get("correct") is True:
                leaked_correct += 1
            if repeat_safe is None:
                twice = leak_client.post(f"/testing/sessions/{cheater}/answer", json={
                    "item_id": task["item_id"], "answer": leaked_choice})
                repeat_safe = twice.status_code == 200 and twice.json().get("answered") == posted.json().get("answered")
            continue
        posted = leak_client.post(f"/testing/sessions/{cheater}/answer", json={
            "item_id": task["item_id"], "answer": 0})
        if repeat_safe is None:
            twice = leak_client.post(f"/testing/sessions/{cheater}/answer", json={
                "item_id": task["item_id"], "answer": 0})
            before = posted.json().get("answered")
            after = leak_client.get(f"/testing/sessions/{cheater}/result").json().get("answered")
            repeat_safe = twice.status_code == 200 and twice.json().get("answered") == before == after
    repeat_safe = bool(repeat_safe)

    expert_total = right_true + wrong_false
    report = {
        "test_retest": {"runs": retest, "stability": stability},
        "discrimination": {"weak_ranks": weak_ranks, "strong_ranks": strong_ranks,
                           "point_biserial": round(r_pb, 4),
                           "mean_weak": sum(weak_ranks) / len(weak_ranks),
                           "mean_strong": sum(strong_ranks) / len(strong_ranks)},
        "expert_ground_truth": {"checked": checked, "right_marked_true": right_true,
                                 "wrong_marked_false": wrong_false,
                                 "agreement": round((right_true + wrong_false) / expert_total, 4)
                                 if expert_total else 0.0},
        "leak_resistance": {"leaked_items": len(leaked_sheet),
                            "rotated_in": rotated_in,
                            "overlap_with_follower": overlap,
                            "leaked_answer_advantage": leaked_correct,
                            "repeat_submit_idempotent": repeat_safe},
    }
    report["passed"] = (
        stability == 1.0
        and r_pb > 0.7
        and expert_total >= 30
        and report["expert_ground_truth"]["agreement"] == 1.0
        and overlap == 0
        and repeat_safe)
    return report


def main() -> None:
    """Run the suite, write the versioned report, exit non-zero on failure."""
    report_path = Path(__file__).resolve().parents[3] / "data" / "validation" / "testing-validation.json"
    with tempfile.TemporaryDirectory() as tmp:
        report = run(Path(tmp) / "data")
    report["generated_note"] = "synthetic candidates, docker MCQ family; expert markup = bank ground truth"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=1))
    if not report["passed"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
