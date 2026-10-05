"""Operator-only bounded experiment commands; runtime never imports the learner."""

import argparse
import itertools
import json
import os
import random
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from matching_engine.combine_models import (
    MatchCandidate,
    ScoringPolicy,
    project_narrative_facts,
)
from matching_engine.combine_scoring import select_candidates
from matching_engine.embeddings import (
    EMBEDDING_SPACE_ID,
    HarrierEncoder,
    build_semantic_text,
)
from matching_engine.models import (
    GRADE_RANK,
    CandidateProfile,
    category_for,
    confirmed_strength,
)
from matching_engine.rerank_features import (
    FeatureVector,
    build_features,
    feature_schema,
)
from matching_engine.rerank_hash import digest
from matching_engine.rerank_model import validate_artifact
from matching_engine.rerank_protocol import DIRECTIONS, expected_families
from matching_engine.rerank_providers import SystemOneProviders, teacher_identity
from matching_engine.strict import build_strict_vector, initial_schema
from matching_engine.strict_evidence import build_strict_evidence
from matching_engine.vacancy_features import build_strict_features
from matching_engine.vacancy_models import VacancyInput, VacancyReady

PREFERENCE_SCENARIOS = (
    "Prefer precise technical requirement coverage over a higher grade alone.",
    "Prefer reliable evidence over stronger unverified declarations when otherwise comparable.",
    "Prefer relevant domain experience, accepting a somewhat weaker matching stack.",
    "Prefer depth on key requirements over breadth on unrelated skills.",
    "Prefer relevant projects and production support over duration alone.",
    "Prefer teamwork and economical salary expectations within an acceptable technical fit.",
    "Prefer balanced evidence, transferable skills and relevant interests; do not punish missing facts.",
    "Prefer strong fit and sustainable salary expectations, considering uncertainty and grade together.",
)


def family_manifest() -> list[dict]:
    """Freeze all eight directions and source-family splits before any model is fitted."""
    output = []
    for row in expected_families():
        index = int(row["family_id"][-2:]) - 1 + DIRECTIONS.index(row["direction"])
        output.append({**row, "preference": PREFERENCE_SCENARIOS[index % 8],
                       "class_only": index % 5 == 0, "bio_absent": index % 4 == 0})
    return output


def label_manifest(directory: Path) -> dict:
    """Seal complete request/response/failure provenance and actual usage, without estimates."""
    records = []
    for path in sorted((directory / "provider-cache").glob("*.json")):
        if path.name == "usage.json":
            continue
        record = _read(path)
        if record["request_hash"] != digest(record["request"]):
            raise ValueError("provider request integrity failed")
        if "response" in record and record["response_hash"] != digest(record["response"]):
            raise ValueError("provider response integrity failed")
        records.append({"cache_file": path.name, "record_hash": digest(record), **record})
    return {"records": records, "usage": _read(directory / "provider-cache/usage.json")}


def label_validation(families: list[dict], pilot: dict) -> dict:
    """Derive label validity from auditable swap outcomes, never a manually assigned flag."""
    audits = [value for family in families for value in family["swap_audits"]]
    consistency = sum(audits) / max(1, len(audits))
    report = {"pilot_hash": pilot["report_hash"], "teacher": teacher_identity(), "families": len(families),
              "swap_correct": sum(audits), "swap_total": len(audits), "swap_consistency": consistency,
              "minimum_per_family": min((sum(f["swap_audits"]) for f in families), default=0),
              "insufficient_pairs": sum(p[2] == "insufficient" for f in families for p in f["pairs"]),
              "tie_pairs": sum(p[2] == "tie" for f in families for p in f["pairs"]),
              "unknown_grades": sum(g is None for f in families for g in f["grades"]),
              "threshold": .8}
    report["passed"] = (pilot["state"] == "passed" and pilot.get("swap_consistency", 0) >= .8
                        and pilot.get("teacher") == teacher_identity()
                        and all(f.get("teacher") == teacher_identity() for f in families)
                        and pilot.get("decisive_pairs", 0) >= 6 and len(families) == 60
                        and len(audits) == 180 and consistency >= .8
                        and report["minimum_per_family"] >= 2)
    return report


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def _read(path: Path) -> dict | list:
    return json.loads(path.read_text(encoding="utf-8"))


def generate(directory: Path) -> None:
    """Expand a sealed pilot; persist a stopped receipt on validation/provider failure."""
    try:
        _generate(directory)
    except Exception as exc:
        _write(directory / "generation-report.json", {"state": "stopped", "teacher": teacher_identity(),
            "error_type": type(exc).__name__,
            "completed_families": len(list((directory / "families").glob("*.json")))})
        raise
    _write(directory / "generation-report.json", {"state": "passed", "teacher": teacher_identity(),
        "completed_families": 60})


def _generate(directory: Path) -> None:
    """Cache every family under the frozen split; never overwrite another teacher's data."""
    pilot = _read(directory / "pilot-report.json")
    if pilot["state"] != "passed":
        raise ValueError("pilot stopped; generation and training remain disabled")
    if pilot["report_hash"] != digest({k: v for k, v in pilot.items() if k != "report_hash"}):
        raise ValueError("pilot integrity failed")
    if pilot.get("teacher") != teacher_identity():
        raise ValueError("pilot teacher differs from the active provider")
    manifest = family_manifest()
    manifest_path = directory / "split-manifest.json"
    if manifest_path.exists() and _read(manifest_path) != manifest:
        raise ValueError("sealed split manifest differs; do not overwrite held-out data")
    _write(manifest_path, manifest)
    providers = SystemOneProviders(directory / "provider-cache")
    schema = initial_schema()
    document, query = HarrierEncoder(), HarrierEncoder("vacancy_query")
    semantic_path = directory / "semantic-cache.json"
    semantics = _read(semantic_path) if semantic_path.exists() else {}

    def encode(encoder: HarrierEncoder, text: str) -> list[float]:
        key = digest({"model": encoder.model_version, "text": text})
        if key not in semantics:
            semantics[key] = encoder.encode(text)
            _write(semantic_path, semantics)
        return semantics[key]

    families = []
    seed_path = Path(__file__).resolve().parents[4] / "data/seed/candidate-pipeline.json"
    seed = _read(seed_path)[0]["profile"]
    for item in manifest:
        family_path = directory / "families" / f"{item['family_id']}.json"
        if family_path.exists():
            existing = _read(family_path)
            if existing["manifest"] != item:
                raise ValueError("cached family manifest changed")
            if existing.get("teacher") != teacher_identity():
                raise ValueError("cached family belongs to another teacher")
            families.append(existing)
            continue
        first = (_read(directory / "pilot-profiles.json") if item["family_id"] == "backend-01"
                 else providers.generate(item["direction"], item["preference"] + " Batch 1"))
        traits = first + providers.generate(item["direction"], item["preference"] + " Batch 2")
        targets = {"domens": {}, "stacks": {}, "soft_skills": {}} if item["class_only"] else {
            "domens": {"sql": 4}, "stacks": {"python": 5, "react": 3}, "soft_skills": {"teamwork": 3}}
        vacancy_source = VacancyInput.model_validate({"owner_id": str(uuid5(NAMESPACE_URL, item["family_id"])),
            "source_revision": 1, "status": "open", "zp_min": 50000, "zp_max": 200000,
            "daily": "full_time", "type": "remote", "city": "Москва", "class": item["direction"],
            "bio": None if item["bio_absent"] else "Разработка API и интерфейсов, SQL, взаимодействие в команде",
            **targets})
        vector, mask = build_strict_features(vacancy_source, schema)
        semantic = encode(query, vacancy_source.bio) if vacancy_source.bio else None
        target = VacancyReady(**vacancy_source.model_dump(by_alias=True), vacancy_id=uuid5(NAMESPACE_URL, item["family_id"]),
            revision=1, build_mode="class_only" if item["class_only"] else "mixed", strict_vector=vector, requirement_mask=mask,
            strict_schema_version=1, strict_schema_hash=schema.schema_hash, semantic_vector=semantic,
            semantic_model_version=query.model_version if semantic else None, semantic_space_id=EMBEDDING_SPACE_ID if semantic else None,
            semantic_available=semantic is not None, published_at=datetime.now(UTC))
        profiles = []
        for index, trait in enumerate(traits):
            # Controlled synthetic variants are explicit source facts, never inferred labels.
            status = "rejected" if index == 9 else trait["status"]
            trait["grade"] = ("intern", "junior", "middle", "senior")[(index + int(item["family_id"][-2:])) % 4]
            raw = json.loads(json.dumps(seed))
            raw.update(primary_direction=item["direction"], work_types=["remote"], city="Москва",
                       salary={"from_rub": trait["salary_min"], "to_rub": min(200000, trait["salary_min"] + 20000)},
                       description=trait["description"], experience=[], achievements=[], preferences=[], metadata={})
            raw["grade_result"]["grade"] = trait["grade"]
            for group, keys in (("domains", ("sql", "rest_api")), ("stacks", ("python", "react")), ("soft_skills", ("teamwork",))):
                raw[group] = [{"key": key, "level": trait["levels"][key], "status": status} for key in keys]
            raw["projects"] = [{"title": "Синтетический проект", "description": trait["description"],
                "status": trait["status"], "facts": {"domains": [{"key": "sql", "status": trait["status"]}],
                    "stacks": [{"key": "python", "status": trait["status"]}],
                    "duration_months": {"value": trait["duration_months"], "status": trait["status"]},
                    "production_support": {"value": trait["production_support"], "status": trait["status"]}}}]
            raw["projects"][0]["status"] = status
            if index % 4 == 0:
                raw["projects"][0].pop("facts")
            # The teacher must see the same controlled absence/status as feature inference.
            trait["status"] = status
            trait["structured_facts_available"] = index % 4 != 0
            if not trait["structured_facts_available"]:
                trait["duration_months"] = None
                trait["production_support"] = None
            profiles.append(CandidateProfile.model_validate(raw))
        ranking = sorted(range(10), key=lambda i: (-GRADE_RANK[profiles[i].grade_result.grade], -confirmed_strength(profiles[i]), i))
        candidates = []
        for index, profile in enumerate(profiles):
            rank = 1 + sum((GRADE_RANK[p.grade_result.grade], confirmed_strength(p)) >
                           (GRADE_RANK[profile.grade_result.grade], confirmed_strength(profile)) for p in profiles)
            candidates.append(MatchCandidate(candidate_id=uuid5(NAMESPACE_URL, f"{item['family_id']}/{index}"),
                profile_revision=1, display_name=f"Synthetic {index}", city=profile.city, salary=profile.salary,
                category=category_for(profile), rank=rank, class_size=10, rating_score=1-(rank-1)/9,
                confirmed_strength=confirmed_strength(profile), strict_vector=build_strict_vector(profile, schema),
                strict_evidence=build_strict_evidence(profile, schema), strict_schema_version=1,
                strict_schema_hash=schema.schema_hash, semantic_vector=encode(document, build_semantic_text(profile)),
                semantic_model_version=document.model_version, semantic_space_id=EMBEDDING_SPACE_ID,
                published_at=datetime.now(UTC), resume_url="/synthetic-public.pdf", work_types=["remote"],
                narrative_facts=project_narrative_facts({"projects": profile.projects, "experience": profile.experience,
                                                       "achievements": profile.achievements})))
        selected = select_candidates(target, candidates, ScoringPolicy(), 10)
        cards = {card.candidate_id: card for card in selected.items}
        features = [asdict(build_features(target, candidate, cards[candidate.candidate_id], schema)) for candidate in candidates]
        # Teacher receives raw declared traits and preferences, never main/model scores or feature vectors.
        teacher_target = {"class": item["direction"], **targets, "bio": vacancy_source.bio,
                          "salary": {"min": 50000, "max": 200000}}
        pairs = [(a, b, providers.pair(teacher_target, traits[a], traits[b], item["preference"]))
                 for a, b in itertools.combinations(range(10), 2)]
        audits = []
        for a, b, label in pairs[:3]:
            swapped = providers.pair(teacher_target, traits[b], traits[a], item["preference"])
            audits.append(swapped == {"A": "B", "B": "A", "tie": "tie", "insufficient": "insufficient"}[label])
        if sum(audits) < 2:
            raise ValueError(f"teacher consistency failed for {item['family_id']}; stop expansion")
        grades = [providers.grade(teacher_target, trait, item["preference"]) for trait in traits]
        record = {"manifest": item, "teacher": teacher_identity(), "traits": traits, "vacancy": teacher_target, "vectors": features,
                  "main_scores": [cards[c.candidate_id].explanation.score for c in candidates],
                  "grades": grades, "pairs": pairs, "swap_audits": audits,
                  "profile_order": ranking, "slice_tags": ["class_only" if item["class_only"] else "mixed",
                      "bio_absent" if item["bio_absent"] else "semantic_available", "mixed_evidence", "partial_facts"]}
        _write(family_path, record)
        families.append(record)
    provenance = label_manifest(directory)
    _write(directory / "label-manifest.json", provenance)
    validation = label_validation(families, pilot)
    _write(directory / "label-validation.json", validation)
    dataset = {"families": families, "teacher": teacher_identity(), "split_hash": digest(manifest), "label_cache_hash": digest(provenance),
               "pilot_hash": pilot["report_hash"], "label_validation": validation,
               "label_validation_hash": digest(validation)}
    dataset["dataset_hash"] = digest(dataset)
    _write(directory / "dataset.json", dataset)


def train_dataset(directory: Path) -> dict:
    """Train from immutable cached data only; never calls a generator, teacher or Harrier."""
    from matching_engine.rerank_training import TrainingFamily, train
    dataset = _read(directory / "dataset.json")
    original_hash = dataset.pop("dataset_hash")
    if digest(dataset) != original_hash or dataset["split_hash"] != digest(family_manifest()):
        raise ValueError("dataset or split integrity failed")
    if [f["manifest"] for f in dataset["families"]] != family_manifest():
        raise ValueError("actual family assignments differ from the sealed manifest")
    if dataset.get("teacher") != teacher_identity() or any(
            f.get("teacher") != teacher_identity() for f in dataset["families"]):
        raise ValueError("dataset contains incompatible teacher labels")
    if digest(dataset["label_validation"]) != dataset["label_validation_hash"]:
        raise ValueError("label validation receipt integrity failed")
    if digest(_read(directory / "label-manifest.json")) != dataset["label_cache_hash"]:
        raise ValueError("label provenance receipt integrity failed")
    code_hash = digest({name: (Path(__file__).parent / name).read_text(encoding="utf-8")
                        for name in ("rerank_training.py", "rerank_features.py", "rerank_model.py")})
    state_path = directory / "training-state.json"
    state = _read(state_path) if state_path.exists() else {"attempts": 0, "dataset_hash": original_hash, "code_hash": code_hash}
    if state["dataset_hash"] != original_hash or state["code_hash"] != code_hash:
        raise ValueError("sealed training context changed; previously exposed golden cannot be retuned")
    if (directory / "artifact.json").exists():
        return validate_artifact(_read(directory / "artifact.json"))
    if state["attempts"] >= 2:
        raise ValueError("training attempt budget exhausted")
    state["attempts"] += 1
    _write(state_path, state)
    families = [TrainingFamily(family_id=item["manifest"]["family_id"], direction=item["manifest"]["direction"],
        split=item["manifest"]["split"], source_id=item["manifest"]["source_id"],
        vectors=[FeatureVector(tuple(v["names"]), tuple(v["values"]), tuple(v["available"]), v["schema_hash"]) for v in item["vectors"]],
        main_scores=item["main_scores"], grades=item["grades"], pairs=[tuple(p) for p in item["pairs"]],
        slice_tags=tuple(item["slice_tags"])) for item in dataset["families"]]
    artifact = train(families, feature_schema(initial_schema()), {"dataset_hash": original_hash,
        "split_hash": dataset["split_hash"], "label_cache_hash": dataset["label_cache_hash"],
        "label_validation": dataset["label_validation"], "label_validation_hash": dataset["label_validation_hash"],
        "model_version": "r0-pairwise-v1"})
    _write(directory / "artifact.json", artifact)
    return artifact


def review_package(directory: Path) -> None:
    """Publish disjoint twenty-pair blind packets without labels or computed scores."""
    dataset = _read(directory / "dataset.json")
    items = []
    for family in dataset["families"]:
        if family["manifest"]["split"] == "golden":
            for a, b, _label in family["pairs"]:
                items.append({"pair_id": f"{family['manifest']['family_id']}/{a}/{b}",
                    "vacancy": family["vacancy"], "preferences": family["manifest"]["preference"],
                    "A": family["traits"][a], "B": family["traits"][b]})
    random.Random(42).shuffle(items)
    if len(items) < 40:
        raise ValueError("insufficient golden pairs for disjoint review")
    _write(directory / "review-user.json", items[:20])
    _write(directory / "review-assistant.json", items[20:40])


def seal_reviews(directory: Path, user_path: Path, assistant_path: Path) -> dict:
    """Attach actual operator-supplied review receipts; never synthesize human answers."""
    receipts = {}
    issues = []
    for person, path in (("user", user_path), ("assistant", assistant_path)):
        packet = _read(directory / f"review-{person}.json")
        expected = {pair["pair_id"] for pair in packet}
        answers = _read(path)
        if not isinstance(answers, list) or len(answers) != 20:
            raise ValueError("each review must contain exactly twenty real answers")
        if {answer.get("pair_id") for answer in answers} != expected or len(expected) != 20:
            raise ValueError("review answers do not match the sealed blind packet")
        for answer in answers:
            if set(answer) != {"pair_id", "choice", "factual_issue"} or answer["choice"] not in ("A", "B", "tie", "insufficient"):
                raise ValueError("invalid review receipt")
            if answer["factual_issue"] is not None and not isinstance(answer["factual_issue"], str):
                raise ValueError("factual issue must be a string or null")
            if answer["factual_issue"]:
                issues.append(f"{person}/{answer['pair_id']}: {answer['factual_issue']}")
        receipts[person] = {"hash": digest(answers), "packet_hash": digest(packet), "pair_ids": sorted(expected), "answers": answers}
    if set(receipts["user"]["pair_ids"]) & set(receipts["assistant"]["pair_ids"]):
        raise ValueError("human and assistant reviews overlap")
    artifact = _read(directory / "artifact.json")
    dataset = _read(directory / "dataset.json")
    if artifact["dataset_hash"] != dataset["dataset_hash"]:
        raise ValueError("review dataset does not belong to the trained artifact")
    artifact.pop("content_hash", None)
    artifact["reviews"] = {"user_pairs": 20, "assistant_pairs": 20, "disjoint_blind": True,
                           "factual_issues": issues, "receipts": receipts, "dataset_hash": artifact["dataset_hash"]}
    result = validate_artifact(artifact)
    _write(directory / "artifact-reviewed.json", result)
    return result


def main() -> None:
    """Run explicit offline stages or operator registration; training never activates a model."""
    parser = argparse.ArgumentParser(description="Bounded Comit R0 experiment")
    parser.add_argument("command", choices=("manifest", "generate", "train", "review-package", "seal-reviews", "register", "promote"))
    parser.add_argument("--directory", type=Path, default=Path("tests/artifacts/reranker"))
    parser.add_argument("--artifact", type=Path)
    parser.add_argument("--hash")
    parser.add_argument("--user-review", type=Path)
    parser.add_argument("--assistant-review", type=Path)
    args = parser.parse_args()
    if args.command == "manifest":
        _write(args.directory / "split-manifest.json", family_manifest())
    elif args.command == "generate":
        generate(args.directory)
    elif args.command == "train":
        train_dataset(args.directory)
    elif args.command == "review-package":
        review_package(args.directory)
    elif args.command == "seal-reviews":
        if args.user_review is None or args.assistant_review is None:
            parser.error("both review receipt paths are required")
        seal_reviews(args.directory, args.user_review, args.assistant_review)
    else:
        from matching_engine.rerank_store import RerankStore
        store = RerankStore(os.environ["DATABASE_URL"])
        if args.command == "register":
            if args.artifact is None:
                parser.error("--artifact required")
            print(store.register(validate_artifact(_read(args.artifact))))
        else:
            if not args.hash:
                parser.error("--hash required")
            store.promote(args.hash)


if __name__ == "__main__":
    main()
