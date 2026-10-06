"""Cache-only, immutable score-blind review packets and explicit disagreement reports.

Preparation never reads dataset labels or features. Comparison neither changes
teacher labels nor grants activation; claimed human authorship is unverified.
"""

import argparse
import fcntl
import itertools
import json
import re
from collections import Counter
from copy import deepcopy
from pathlib import Path

from matching_engine.rerank_cohort_provider import LABELS, validate_candidate
from matching_engine.rerank_hash import digest
from matching_engine.rerank_protocol import DIRECTIONS, expected_families
from matching_engine.rerank_providers import _atomic_write

REVIEW_VERSION = "score-blind-cohort-review-v1"
DEFAULT_COHORT = "rerank-grounded-cohort-v5"
REVIEWERS = ("assistant", "user")
_VACANCY_FIELDS = {"class", "domens", "stacks", "soft_skills", "bio", "salary"}
_POLICY_FIELDS = {"id", "ordered_priorities", "instructions", "required_measurements",
                  "exclusive_criterion", "suitability_rubric"}


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON review fields")
        value[key] = item
    return value


def _read(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
    if type(value) is not dict:
        raise ValueError("review artifact must be a JSON object")
    return value


def _verified(path: Path, hash_field: str) -> dict:
    value = _read(path)
    if (type(value.get(hash_field)) is not str
            or value[hash_field] != digest({key: item for key, item in value.items() if key != hash_field})):
        raise ValueError("review artifact hash mismatch")
    return value


def _target(directory: Path, cohort_id: str) -> Path:
    if (type(cohort_id) is not str
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,119}", cohort_id) is None):
        raise ValueError("invalid cohort directory ID")
    return directory / cohort_id


def _sources(directory: Path, cohort_id: str) -> tuple[dict, dict[str, dict]]:
    target = _target(directory, cohort_id)
    manifest = _verified(target / "manifest.json", "manifest_hash")
    expected = {item["family_id"]: item for item in expected_families()}
    rows = manifest.get("families")
    if (manifest.get("version") != cohort_id or type(rows) is not list or len(rows) != 60
            or any(type(row) is not dict for row in rows)):
        raise ValueError("review requires the complete frozen 60-family manifest")
    family_ids = [row.get("family_id") for row in rows]
    if any(type(item) is not str for item in family_ids) or set(family_ids) != set(expected):
        raise ValueError("review manifest family allocation mismatch")
    missing = [identifier for identifier in expected if not (target / "sources" / f"{identifier}.json").is_file()]
    if missing:
        raise ValueError(f"all 60 accepted sources required; missing {len(missing)}")
    records = {}
    for row in rows:
        identifier = row["family_id"]
        allocation = expected[identifier]
        if any(row.get(key) != allocation[key] for key in ("direction", "split", "source_id")):
            raise ValueError("review manifest ancestry or split mismatch")
        if (type(row.get("vacancy")) is not dict or set(row["vacancy"]) != _VACANCY_FIELDS
                or type(row.get("policy")) is not dict or not {"id", "ordered_priorities"} <= set(row["policy"])
                or not set(row["policy"]) <= _POLICY_FIELDS):
            raise ValueError("review vacancy or policy contains unexpected fields")
        record = _verified(target / "sources" / f"{identifier}.json", "record_hash")
        if record.get("manifest") != row or type(record.get("traits")) is not list or len(record["traits"]) != 7:
            raise ValueError("accepted source differs from frozen family")
        for trait in record["traits"]:
            validate_candidate(trait)
        records[identifier] = record
    return manifest, records


def _packets(manifest: dict, records: dict[str, dict], cohort_id: str) -> dict[str, dict]:
    ranked = sorted(records, key=lambda identifier: digest({"cohort": cohort_id, "family": identifier,
                                                           "selection": REVIEW_VERSION}))
    selected = []
    for direction in DIRECTIONS:
        selected.append(next(identifier for identifier in ranked
                             if records[identifier]["manifest"]["direction"] == direction))
    for split in ("train", "validation", "calibration", "golden"):
        if not any(records[identifier]["manifest"]["split"] == split for identifier in selected):
            selected.append(next(identifier for identifier in ranked
                                 if records[identifier]["manifest"]["split"] == split))
    selected.extend(identifier for identifier in ranked if identifier not in selected)
    selected = selected[:20]
    source_hashes = {identifier: records[identifier]["record_hash"] for identifier in sorted(records)}
    used: set[tuple[str, int, int]] = set()
    result = {}
    for reviewer in REVIEWERS:
        cases = []
        for identifier in selected:
            indices = sorted(itertools.combinations(range(7), 2), key=lambda pair: digest({
                "version": REVIEW_VERSION, "cohort": cohort_id, "family": identifier,
                "reviewer": reviewer, "indices": pair}))
            a, b = next(pair for pair in indices if (identifier, *pair) not in used)
            used.add((identifier, a, b))
            record, row = records[identifier], records[identifier]["manifest"]
            pair_id = digest({"cohort": cohort_id, "source": record["record_hash"],
                              "family": identifier, "a_index": a, "b_index": b})
            cases.append({"pair_id": pair_id, "family_id": identifier, "a_index": a, "b_index": b,
                          "direction": row["direction"], "split": row["split"],
                          "vacancy": deepcopy(row["vacancy"]), "policy": deepcopy(row["policy"]),
                          "A": deepcopy(record["traits"][a]), "B": deepcopy(record["traits"][b])})
        cases.sort(key=lambda case: digest({"reviewer": reviewer, "pair_id": case["pair_id"]}))
        packet = {"version": REVIEW_VERSION, "cohort_id": cohort_id, "reviewer_kind": reviewer,
                  "independent_human_evaluation": False, "manifest_hash": manifest["manifest_hash"],
                  "source_hashes": source_hashes, "cases": cases}
        packet["packet_hash"] = digest(packet)
        result[reviewer] = packet
    return result


def _sealed_json(path: Path, value: dict) -> None:
    if path.exists():
        if _read(path) != value:
            raise ValueError("immutable review artifact already contains different data")
        return
    _atomic_write(path, value)


def _markdown(packet: dict) -> str:
    def encoded(value: object) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    lines = ["# Слепая проверка синтетических пар", "", f"Рецензент: `{packet['reviewer_kind']}`.",
             f"Хеш пакета: `{packet['packet_hash']}`.", "",
             "20 пар выбраны только по принятым источникам. Метки учителя, main scores, признаки и прогнозы скрыты.",
             "Уровни — заявления 0–7; статусы каждого навыка и факта независимы. Null означает неизвестное.",
             "Формальный грейд не является оценкой пригодности. Описание не устанавливает структурированные факты.",
             "Для каждой пары укажите A, B, tie или insufficient и краткую причину; ошибку фактов отметьте factual_issue.",
             "Проверка ассистента не является независимой человеческой оценкой.", ""]
    for index, case in enumerate(packet["cases"], 1):
        lines.extend([f"## Пара {index}", "", f"`{case['pair_id']}`", "",
                      (f"Семья `{case['family_id']}`, направление `{case['direction']}`, split `{case['split']}`; "
                       f"A — индекс {case['a_index']}, B — индекс {case['b_index']}."), "",
                      f"Вакансия: `{encoded(case['vacancy'])}`", "",
                      "Приоритеты работодателя:", ""])
        for priority in case["policy"]["ordered_priorities"]:
            lines.append(f"- {priority}")
        lines.extend(["", f"Полная policy: `{encoded(case['policy'])}`", ""])
        for side in ("A", "B"):
            candidate = case[side]
            skills = "; ".join(f"{key}: {candidate['levels'][key]}/{candidate['skill_statuses'][key]}"
                               for key in sorted(candidate["levels"]))
            lines.extend([f"### Кандидат {side}", "", (f"Грейд `{candidate['grade']}`, исходный статус "
                          f"`{candidate['status']}`, salary_min `{candidate['salary_min']}`."), "",
                          f"Навыки (уровень/статус): {skills}.", "",
                          f"Описание: {candidate['description']}", "",
                          "Полные исходные факты:", "", "```json", encoded(candidate), "```", ""])
    return "\n".join(lines)


def prepare_reviews(directory: Path, cohort_id: str = DEFAULT_COHORT) -> dict[str, dict]:
    """Freeze two disjoint twenty-pair packets after all 60 sources pass hash checks.

    Writes JSON and Russian Markdown below cohort/reviews, without reading a
    dataset, predictions, features, credentials or making provider calls.
    """
    manifest, records = _sources(directory, cohort_id)
    packets = _packets(manifest, records, cohort_id)
    target = _target(directory, cohort_id) / "reviews"
    target.mkdir(parents=True, exist_ok=True)
    with (target / ".lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        for reviewer, packet in packets.items():
            _sealed_json(target / f"{reviewer}-packet.json", packet)
            markdown_path = target / f"{reviewer}-packet.md"
            markdown = _markdown(packet)
            if markdown_path.exists() and markdown_path.read_text(encoding="utf-8") != markdown:
                raise ValueError("immutable review Markdown changed")
            if not markdown_path.exists():
                markdown_path.write_text(markdown, encoding="utf-8")
    return packets


def _manual(value: dict, packet: dict) -> dict[str, dict]:
    if (set(value) not in ({"packet_hash", "reviewer_kind", "labels"},
                          {"packet_hash", "reviewer_kind", "labels", "claimed_human_review"})
            or value["packet_hash"] != packet["packet_hash"] or value["reviewer_kind"] != packet["reviewer_kind"]):
        raise ValueError("manual review packet or reviewer mismatch")
    claimed = value.get("claimed_human_review", value["reviewer_kind"] == "user")
    if type(claimed) is not bool or (value["reviewer_kind"] == "assistant" and claimed):
        raise ValueError("assistant review cannot claim human authorship")
    labels = value["labels"]
    if type(labels) is not list or len(labels) != 20:
        raise ValueError("manual review requires exactly 20 labels")
    result = {}
    for row in labels:
        if (type(row) is not dict or set(row) not in ({"pair_id", "label", "reason"},
                {"pair_id", "label", "reason", "factual_issue"}) or type(row.get("pair_id")) is not str
                or type(row.get("label")) is not str or row["label"] not in LABELS
                or type(row.get("reason")) is not str or not row["reason"].strip()
                or not 1 <= len(row["reason"]) <= 1000
                or type(row.get("factual_issue", False)) is not bool or row["pair_id"] in result):
            raise ValueError("invalid, duplicate or unexpected manual review fields")
        result[row["pair_id"]] = row
    if set(result) != {case["pair_id"] for case in packet["cases"]}:
        raise ValueError("manual review does not contain the exact 20 packet pair IDs")
    return result


def _dataset(directory: Path, cohort_id: str, manifest: dict, records: dict[str, dict]) -> dict:
    dataset = _verified(_target(directory, cohort_id) / "dataset.json", "dataset_hash")
    families = dataset.get("families")
    if (dataset.get("version") != cohort_id or dataset.get("manifest_hash") != manifest["manifest_hash"]
            or type(families) is not list or len(families) != 60):
        raise ValueError("completed dataset does not match review cohort")
    seen = set()
    expected_pairs = set(itertools.combinations(range(7), 2))
    for family in families:
        if (type(family) is not dict or family.get("record_hash") != digest({
                key: value for key, value in family.items() if key != "record_hash"})):
            raise ValueError("completed dataset family hash mismatch")
        identifier = family.get("manifest", {}).get("family_id")
        if type(identifier) is not str or identifier not in records or identifier in seen:
            raise ValueError("completed dataset family allocation mismatch")
        seen.add(identifier)
        source = records[identifier]
        if (family.get("source_hash") != source["record_hash"] or family.get("traits") != source["traits"]
                or family["manifest"] != source["manifest"] or family.get("vacancy") != source["manifest"]["vacancy"]):
            raise ValueError("completed dataset raw facts differ from accepted source")
        pairs = family.get("pairs")
        if (type(pairs) is not list or len(pairs) != 21
                or any(type(pair) is not list or len(pair) != 3 or type(pair[0]) is not int
                       or type(pair[1]) is not int or type(pair[2]) is not str or pair[2] not in LABELS for pair in pairs)
                or {(pair[0], pair[1]) for pair in pairs} != expected_pairs):
            raise ValueError("completed dataset requires exactly 21 unordered pair labels per family")
    return dataset


def _category(manual: str, teacher: str) -> str:
    if manual == teacher:
        return "agreement"
    if manual in ("A", "B") and teacher in ("A", "B"):
        return "preference_reversal"
    if "insufficient" in (manual, teacher):
        return "tie_vs_insufficient" if "tie" in (manual, teacher) else "decisive_vs_insufficient"
    return "decisive_vs_tie"


def compare_review(directory: Path, review_file: Path, cohort_id: str = DEFAULT_COHORT) -> dict:
    """Compare an exact twenty-answer review with sealed dataset labels; never relabel.

    Every factual_issue remains unresolved and blocks activation. User authorship
    is only a declaration; neither this report nor agreement verifies independence.
    """
    manifest, records = _sources(directory, cohort_id)
    manual = _read(review_file)
    reviewer = manual.get("reviewer_kind")
    if type(reviewer) is not str or reviewer not in REVIEWERS:
        raise ValueError("unknown manual reviewer kind")
    target = _target(directory, cohort_id) / "reviews"
    packet = _verified(target / f"{reviewer}-packet.json", "packet_hash")
    if packet != _packets(manifest, records, cohort_id)[reviewer]:
        raise ValueError("review packet differs from deterministic score-blind source selection")
    labels = _manual(manual, packet)
    dataset = _dataset(directory, cohort_id, manifest, records)
    families = {family["manifest"]["family_id"]: family for family in dataset["families"]}
    comparisons = []
    for case in packet["cases"]:
        teacher = next(pair[2] for pair in families[case["family_id"]]["pairs"]
                       if pair[:2] == [case["a_index"], case["b_index"]])
        review = labels[case["pair_id"]]
        comparisons.append({"pair_id": case["pair_id"], "family_id": case["family_id"],
                            "review_label": review["label"], "teacher_label": teacher,
                            "category": _category(review["label"], teacher), "reason": review["reason"],
                            "unresolved_factual_issue": review.get("factual_issue", False)})
    categories = dict(Counter(row["category"] for row in comparisons))
    unresolved = sum(row["unresolved_factual_issue"] for row in comparisons)
    report = {"version": REVIEW_VERSION, "cohort_id": cohort_id, "reviewer_kind": reviewer,
              "packet_hash": packet["packet_hash"], "manifest_hash": manifest["manifest_hash"],
              "dataset_hash": dataset["dataset_hash"], "source_hashes": packet["source_hashes"],
              "review_hash": digest(manual), "reviewed_pairs": 20, "category_counts": categories,
              "agreements": categories.get("agreement", 0), "disagreements": 20 - categories.get("agreement", 0),
              "unresolved_factual_issues": unresolved, "factual_issues_block_activation": unresolved > 0,
              "claimed_human_review": manual.get("claimed_human_review", reviewer == "user"),
              "human_authorship_verified": False, "independent_human_evaluation": False,
              "activation_allowed": False, "labels_changed": False, "comparisons": comparisons}
    report["report_hash"] = digest(report)
    with (target / ".lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        _sealed_json(target / f"{reviewer}-comparison-{report['review_hash']}.json", report)
    return report


def main() -> None:
    """Prepare blind packets or compare a completed manual review; no API calls."""
    parser = argparse.ArgumentParser(description="Score-blind synthetic cohort reviews without HTTP")
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("prepare", "compare"):
        subparser = commands.add_parser(command)
        subparser.add_argument("--directory", type=Path, default=Path("tests/artifacts/reranker"))
        subparser.add_argument("--cohort-id", default=DEFAULT_COHORT)
        if command == "compare":
            subparser.add_argument("--review-file", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "prepare":
        packets = prepare_reviews(args.directory, args.cohort_id)
        print(json.dumps({"state": "packets_prepared", "packets": {
            reviewer: packet["packet_hash"] for reviewer, packet in packets.items()}}, ensure_ascii=False))
    else:
        report = compare_review(args.directory, args.review_file, args.cohort_id)
        print(json.dumps({key: report[key] for key in ("reviewer_kind", "reviewed_pairs", "agreements",
              "disagreements", "unresolved_factual_issues", "activation_allowed")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
