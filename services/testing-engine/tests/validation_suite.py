"""Reproducible synthetic validation; not an empirical psychometric study.

84 ability profiles across seven families, independent retest response draws,
independent numeric answer calculation, and real API leak/replay checks.
No metric is presented as population calibration or an expert panel result.
"""
from __future__ import annotations

import json
import math
import random
import re
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient
from testing_engine.main import create_testing_app
from testing_engine.store import TestingStore
from testing_engine.variants import FAMILIES, instantiate

GRADE_RANK = {"intern": 0, "junior": 1, "middle": 2, "senior": 3}


def correlation(x: list[float], y: list[float]) -> float:
    """Pearson/point-biserial correlation, zero for a constant series."""
    mx, my = sum(x) / len(x), sum(y) / len(y)
    xx, yy = sum((v-mx)**2 for v in x), sum((v-my)**2 for v in y)
    return sum((a-mx)*(b-my) for a,b in zip(x,y, strict=True))/math.sqrt(xx*yy) if xx*yy else 0.0


def _answer(store: TestingStore, task: dict) -> int | str:
    """Synthetic knowledge oracle uses private keys with public option remap."""
    bank = json.loads(store.items_path.read_text())
    payload = next(row["payload"] for row in bank if row["item_id"] == task["item_id"])
    if task["type"] == "mcq":
        return task["options"].index(payload["options"][payload["answer_index"]])
    return payload["expected_answers"][0]


def session(client: TestClient, store: TestingStore, owner: str, family: str,
            ability: float, draw_seed: int) -> dict:
    """Run one profile; ability is externally specified before any grading."""
    rng = random.Random(draw_seed)
    response = client.post("/testing/sessions", json={"candidate_key": owner,
                          "specialization": family, "declared_grade": "middle"})
    assert response.status_code == 200
    sid = response.json()["session_id"]
    items=[]
    for _ in range(30):
        response = client.get(f"/testing/sessions/{sid}/next")
        if response.status_code == 409:
            break
        assert response.status_code == 200
        task=response.json()["task"]
        chance=.25 + .75/(1+math.exp(-(ability+1-task["level"])*1.7))
        answer=_answer(store,task)
        if rng.random() > chance:
            answer=(answer+1)%len(task["options"]) if isinstance(answer,int) else "incorrect"
        submitted=client.post(f"/testing/sessions/{sid}/answer",json={"item_id":task["item_id"],"answer":answer})
        assert submitted.status_code == 200
        items.append(task["item_id"])
    result=client.get(f"/testing/sessions/{sid}/result").json()
    return {"grade":result["decided_grade"],"items":items,"answered":result["answered"]}


def run(data_dir: Path) -> dict:
    """Compute actual metrics and a separate technical acceptance gate."""
    client=TestClient(create_testing_app(data_dir=data_dir,allow_unauthenticated=True))
    store=TestingStore(data_dir)
    profiles=[]
    for f,family in enumerate(FAMILIES):
        for k in range(12):
            ability=k*3/11
            profile_dir = data_dir / f"profile-{f}-{k}"
            profile_client = TestClient(create_testing_app(profile_dir, allow_unauthenticated=True))
            profile_store = TestingStore(profile_dir)
            first=session(profile_client,profile_store,f"profile-{f}-{k}-a",family,ability,1000+f*100+k)
            second=session(profile_client,profile_store,f"profile-{f}-{k}-b",family,ability,9000+f*100+k)
            profiles.append({"family":family,"ability":round(ability,4),"first":first,"retest":second})
    first=[float(GRADE_RANK[row["first"]["grade"]]) for row in profiles]
    retest=[float(GRADE_RANK[row["retest"]["grade"]]) for row in profiles]
    classes=[float(row["ability"]>=1.5) for row in profiles]
    # Independent numeric calculation: answer key is never used by this oracle.
    checks=[]
    for family in FAMILIES:
        for seed in range(12):
            item=instantiate(family,2,seed=seed)
            numbers=[int(v) for v in re.findall(r"\d+",item["prompt"])]
            if family in ("algorithms","rest_api"):
                expected=numbers[-1] if family=="rest_api" else numbers[0]
            elif family=="python":
                start,stop,step=numbers
                expected=len(range(start,stop,step))
            elif family=="react":
                expected=numbers[0]+numbers[1]
            else:
                expected=numbers[0]*numbers[1]
            checks.append(item["expected_answers"]==[str(expected)])
    # Burn every seed item without a fabricated LLM refill. Runtime must generate
    # fresh parameterized stock rather than recycle a leaked instance.
    leaked={row["item_id"] for row in json.loads(store.items_path.read_text())}
    from testing_engine.store import _atomic_write, _today
    bank = json.loads(store.items_path.read_text())
    for row in bank:
        row["last_served_date"] = _today()
    _atomic_write(store.items_path, bank)
    follower=session(client,store,"leak-follower","docker",1.5,54321)
    overlap=len(leaked.intersection(follower["items"]))
    report={"protocol":"synthetic-multifamily-v2","sample_size":len(profiles),
            "families":list(FAMILIES),"profiles":profiles,
            "test_retest":{"paired_profiles":len(profiles),"independent_response_draws":True,
                           "exact_grade_agreement":sum(a==b for a,b in zip(first,retest,strict=True))/len(first),
                           "rank_correlation":round(correlation(first,retest),4)},
            "discrimination":{"point_biserial":round(correlation(classes,first),4),
                              "class_sizes":[classes.count(0.),classes.count(1.)],
                              "ability_rank_correlation":round(correlation([row["ability"] for row in profiles],first),4),
                              "observed_grade_counts":{grade:first.count(float(rank)) for grade,rank in GRADE_RANK.items()}},
            "independent_numeric_ground_truth":{"checked":len(checks),"agreement":sum(checks)/len(checks)},
            "leak_resistance":{"burned_instances":len(leaked),"follower_questions":len(follower["items"]),"overlap":overlap},
            "limitations":["Synthetic response model is not human validation.",
                           "Blueprint difficulty has not been calibrated by independent experts.",
                           "Semantic memorization of a whole skill is not detectable by item rotation."]}
    report["passed"]=all(checks) and overlap==0 and len(follower["items"])>0
    return report


def main() -> None:
    """Write a versioned, reproducible report with no invented empirical claim."""
    target=Path(__file__).resolve().parents[3]/"data/validation/testing-validation.json"
    with tempfile.TemporaryDirectory() as directory:
        report=run(Path(directory))
    target.write_text(json.dumps(report,ensure_ascii=False,indent=1)+"\n",encoding="utf-8")
    print(json.dumps({k:v for k,v in report.items() if k!="profiles"},ensure_ascii=False,indent=1))
    if not report["passed"]:
        raise SystemExit(1)


if __name__=="__main__":
    main()
