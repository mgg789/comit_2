"""Create a versioned, uniformly relabeled cohort inside the original cumulative budgets."""

import argparse
import fcntl
import itertools
import json
import re
from collections import Counter
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path

from matching_engine.rerank_cohort_features import (
    build_family_features,
    candidate_profile,
    legacy_traits,
)
from matching_engine.rerank_cohort_provider import (
    CohortProviders,
    _parse,
    validate_candidate,
)
from matching_engine.rerank_diagnostics import REVERSE, _read, _seal
from matching_engine.rerank_experiment import _parallel, family_manifest
from matching_engine.rerank_hash import digest
from matching_engine.rerank_protocol import DIRECTIONS
from matching_engine.rerank_providers import (
    GEMMA_GENERATION_MODEL,
    LIMITS,
    _atomic_write,
)
from matching_engine.rerank_sample_probe import _cycles, _ordering_ceiling
from matching_engine.strict import INITIAL_GROUPS

VERSION = "rerank-grounded-cohort-v5"
PRIOR_VERSION = "rerank-grounded-cohort-v4"
CORE_KEYS = {
    "backend": ("python", "node", "go", "sql", "rest_api", "architecture_thinking", "teamwork"),
    "frontend": ("react", "vue", "html_css_jquery", "tailwind", "ux_userflow", "rest_api", "teamwork"),
    "fullstack": ("node", "python", "react", "sql", "rest_api", "architecture_thinking", "teamwork"),
    "mobile": ("react", "html_css_jquery", "sqlite", "rest_api", "ux_userflow", "time_management", "teamwork"),
    "devops": ("docker", "devops", "cybersecurity", "architecture_thinking", "sql", "python", "self_organization"),
    "data": ("python", "postgres", "sql", "data_analytics_stats", "math", "doc_databases", "teamwork"),
    "qa": ("algorithms", "rest_api", "sql", "python", "ux_userflow", "self_organization", "time_management"),
    "ml": ("python", "classical_ml", "neural_nets", "nlp_and_arbitrary_data", "math", "data_analytics_stats", "teamwork"),
}
RUBRIC = (
    "Independently assess suitability, never formal test grade or pair victories. "
    "3: strong fit, all stated technical levels met with relevant supporting facts; "
    "2: usable fit, at least half stated levels met and remaining gaps modest; "
    "1: significant gaps but some relevant declared ability; 0: explicit technical mismatch. "
    "For class-only, use formal grade and available structured relevant facts: middle/senior with "
    "supporting facts is strong, junior usable, intern limited; do not invent technical requirements. "
    "Unconfirmed declarations remain evidence of declared levels, with their status reported separately. "
    "Rejected confirmation is never confirmed evidence. Missing facts are not zero, false or low skill. "
    "If an essential exclusive measurement is unavailable, return null."
)


def _policy(index: int) -> dict:
    choices = (
        ["Prefer higher formal test grade within this class.", "Then prefer stronger explicit evidence of relevant facts."],
        ["Prefer greater coverage of the vacancy's explicitly stated technical levels.", "Then prefer greater depth on those requirements."],
        ["Prefer declared fit to the stated domains.", "Then prefer relevant project domain evidence and production support."],
        ["Prefer declared fit to the stated stacks.", "Then prefer stronger confirmation of those matching skills."],
        ["Prefer relevant projects with known production support.", "Then prefer depth on stated requirements."],
        ["Prefer balanced coverage of all stated requirements.", "Then prefer higher formal grade."],
        ["Prefer technical coverage and matching declared teamwork.", "Then prefer lower salary_min for otherwise comparable fit."],
        ["Prefer depth on the key stated requirements.", "Then prefer known relevant experience and stronger confirmation."],
    )
    return {"id": f"global-baseline-prior-{index % 8}", "ordered_priorities": choices[index % 8],
            "instructions": "Apply priorities in order. Use only explicitly provided typed facts. "
                "Unrelated breadth cannot substitute for requested skills. If a comparison signal is "
                "unavailable, do not encode it as zero or false. Do not infer facts from biography. "
                "Grade and level are separate from confirmation. These scenarios sample global baseline "
                "preferences; this model does not implement personalized employer-policy conditioning.",
            "required_measurements": [], "exclusive_criterion": False, "suitability_rubric": RUBRIC}


def _profile_output_bound(content: object) -> int:
    """Count completed outer profile objects plus one started partial object; unknown output costs seven."""
    if type(content) is not str:
        return 7
    match = re.match(r'\s*\{\s*"profiles"\s*:\s*\[',content)
    if match is None:
        return 7
    offset = match.end();count = 0;decoder = json.JSONDecoder()
    while True:
        while offset < len(content) and content[offset] in ' \t\r\n,':offset += 1
        if offset == len(content) or content[offset] == ']':return count
        if content[offset] != '{':return max(7,count)
        count += 1
        try:
            _value,offset = decoder.raw_decode(content,offset)
        except json.JSONDecodeError:
            return count


def _spent_profiles(directory: Path, attempts: int, historical: int) -> tuple[int,list[dict]]:
    """Bind precise completed/truncated output counts to raw hashes; missing HTTP replies cost seven."""
    receipts = []
    for path in (directory / 'provider-cache').glob('*.json'):
        if path.name == 'usage.json':continue
        raw = _read(path);request = raw.get('request',{})
        if request.get('kind') != 'generation' or not request.get('cohort_id','').startswith('rerank-grounded-cohort-') or 'response' not in raw:
            continue
        if raw['request_hash'] != digest(request) or raw['response_hash'] != digest(raw['response']):
            raise ValueError('generation output accounting receipt changed')
        response = raw['response'];choices = response.get('choices',[]) if type(response) is dict else []
        content = choices[0].get('message',{}).get('content') if len(choices)==1 else None
        receipts.append({'request_hash':raw['request_hash'],'response_hash':raw['response_hash'],
                         'profile_upper_bound':_profile_output_bound(content)})
    remaining = attempts-historical-len(receipts)
    if remaining < 0:raise ValueError('generation accounting exceeds cumulative attempts')
    return historical*5 + sum(r['profile_upper_bound'] for r in receipts) + remaining*7,sorted(receipts,key=lambda r:r['request_hash'])


def cohort_manifest(directory: Path) -> dict:
    """Freeze ancestry, seven-profile quotas, direction coverage and costs before new HTTP."""
    old = {p.stem: _read(p) for p in (directory / "families").glob("*.json")}
    keys = [key for group in INITIAL_GROUPS.values() for key in group]
    rows = []
    for offset, source in enumerate(family_manifest()):
        item = {**source, "cohort_id": VERSION}
        index = int(item["family_id"][-2:]) - 1
        selected = list(CORE_KEYS[item["direction"]])
        selected.extend(key for key in (keys[(offset * 2) % len(keys)], keys[(offset * 2 + 1) % len(keys)]) if key not in selected)
        branch = ("class_only", "mixed", "domain_only", "stack_only")[index % 4]
        groups = {"domens": {}, "stacks": {}, "soft_skills": {}}
        for group, known in INITIAL_GROUPS.items():
            field = {"domain": "domens", "stack": "stacks", "soft_skill": "soft_skills"}[group]
            if branch == "class_only" or branch == "domain_only" and group != "domain" or branch == "stack_only" and group != "stack":
                continue
            groups[field] = {key: 4 for key in CORE_KEYS[item["direction"]] if key in known}
        item.update(keys=selected, branch=branch, class_only=branch == "class_only", bio_absent=index % 3 == 0,
                    profile_count=7, policy=_policy(index + DIRECTIONS.index(item["direction"])))
        item["vacancy"] = {"class": item["direction"], **groups,
                           "bio": None if item["bio_absent"] else f"Направление {item['direction']}: {', '.join(CORE_KEYS[item['direction']])}",
                           "salary": {"min": 50000, "max": 200000}}
        if item["family_id"] in old:
            record = old[item["family_id"]]
            if any(record["manifest"][k] != source[k] for k in ("family_id", "source_id", "direction", "split")):
                raise ValueError("historical source ancestry changed")
            item["reuse"] = {"record_hash": record["record_hash"], "indices": [*range(6), len(record["traits"]) - 1]}
        else:
            item["reuse"] = None
        prior_path = directory / PRIOR_VERSION / "sources" / f'{item["family_id"]}.json'
        if not prior_path.exists():
            prior_path = directory / "rerank-grounded-cohort-v2" / "sources" / f'{item["family_id"]}.json'
        item["prior_source"] = None
        if prior_path.exists() and item["reuse"] is None:
            prior = _read(prior_path)
            comparable = {k:v for k,v in prior["manifest"].items() if k not in ("cohort_id", "prior_source")}
            if comparable != {k:v for k,v in item.items() if k not in ("cohort_id", "prior_source")}:
                raise ValueError("prior source ancestry or requirements changed")
            item["prior_source"] = {"path":str(prior_path.relative_to(directory)),"record_hash":prior["record_hash"]}
        rows.append(item)
    before = _read(directory / "provider-cache/usage.json")
    first_path = directory / "rerank-grounded-cohort-v1" / "manifest.json"
    historical_generation = _read(first_path)["ledger_before"]["generation"] if first_path.exists() else before["generation"]
    spent_profile_bound, generation_receipts = _spent_profiles(directory,before["generation"],historical_generation)
    limits = {"generation": min(LIMITS["generation"] - before["generation"], (600 - spent_profile_bound) // 7),
              "pair": LIMITS["pair"] - before["pair"], "grade": LIMITS["grade"] - before["grade"]}
    normal = {"generation": sum(r["reuse"] is None and r["prior_source"] is None for r in rows), "pair": 60 * (21 + 3) + 20, "grade": 420 + 10}
    if any(normal[k] > limits[k] for k in limits):
        raise ValueError("new cohort does not fit the unchanged cumulative budgets")
    manifest = {"version": VERSION, "families": rows, "ledger_before": before, "attempt_limits": limits,
                "normal_attempts": normal, "global_limits": LIMITS, "split_counts": dict(Counter(r["split"] for r in rows)),
                "profiles": 420, "unique_historical_profiles_preserved": True,
                "generation_profile_bound": "Historical attempts x5; sealed raw outer-profile counts incl partial; missing responses x7; future attempts x7 <=600",
                "pair_audits": "21 unordered pairs plus exactly three reverse orientations per family",
                "deduplication": "Exact facts across independent families; normalized descriptions across splits. Same-split template reuse reported separately",
                "model_scope": "Global baseline prior, not employer-policy-conditioned ranking",
                "activation_allowed": False, "generator": GEMMA_GENERATION_MODEL,
                "generation_spent_profile_bound":spent_profile_bound,
                "generation_profile_receipts":generation_receipts,
                "seed_hash": digest((Path(__file__).resolve().parents[4] / "data/seed/candidate-pipeline.json").read_bytes().hex()),
                "protected_source_hashes": {str(path.relative_to(directory)):digest(path.read_bytes().hex()) for path in
                    [*(directory / "families").glob("*.json"),
                     *(p for p in (directory / "provider-cache").glob("*.json") if p.name != "usage.json"),
                     directory / "semantic-cache.json",
                     *(directory / PRIOR_VERSION).rglob("*.json"),
                     *(directory / "rerank-grounded-cohort-v3").rglob("*.json"),
                     *(directory / "rerank-grounded-cohort-v2").rglob("*.json")]},
                "code_hashes": {name: digest((Path(__file__).parent / name).read_bytes().hex()) for name in
                                ("rerank_cohort.py", "rerank_cohort_provider.py", "rerank_cohort_features.py",
                                 "rerank_features.py", "combine_scoring.py", "models.py", "embeddings.py",
                                 "strict.py", "strict_evidence.py", "vacancy_features.py")}}
    manifest["manifest_hash"] = digest(manifest)
    _seal(directory / VERSION / "manifest.json", manifest)
    return manifest


def _reserve(directory: Path, manifest: dict, kind: str) -> None:
    target = directory / VERSION
    with (target / ".budget.lock").open("a+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        path = target / "attempt-budget.json"
        used = _read(path) if path.exists() else {k: 0 for k in LIMITS}
        if used[kind] >= manifest["attempt_limits"][kind]:
            raise ValueError("cohort attempt allowance exhausted")
        used[kind] += 1
        _atomic_write(path, used)


def _job(directory: Path, manifest: dict, provider: CohortProviders, job_id: str, kind: str, payload, execute,
         *, start_attempt: int = 0) -> dict | list:
    """Persist each attempt; replay exact responses, retry once with a new nonce, never repeat ambiguous calls."""
    folder = directory / VERSION / "jobs"
    folder.mkdir(parents=True, exist_ok=True)
    if start_attempt not in (0, 1):
        raise ValueError("only one repair attempt is allowed")
    for attempt in range(start_attempt, 2):
        request = payload(attempt)
        identity = provider._identity("/v1/chat/completions", request, kind)
        key = digest(identity)
        path = folder / f"{job_id}-{attempt}.json"
        cache = provider.directory / f"{key}.json"
        if path.exists():
            saved = _read(path)
            if saved["request_hash"] != key:
                raise ValueError("cohort job request identity changed")
            if saved["state"] == "failed":
                continue
            if not cache.exists():
                raise ValueError("ambiguous interrupted cohort call requires receipt review")
            if saved["state"] == "passed":
                answer = execute(attempt)
                if saved["answer_hash"] != digest(answer):
                    raise ValueError("cohort answer changed during replay")
                return answer
        else:
            _reserve(directory, manifest, kind)
            _seal(path, {"request_hash": key, "state": "started", "kind": kind})
        try:
            answer = execute(attempt)
        except (ValueError, TypeError, KeyError, RuntimeError) as error:
            failures = list(provider.directory.glob(f"{key}.failure-*.json"))
            codes = [int(_read(p).get("http_status", 0)) for p in failures]
            _atomic_write(path, {"request_hash": key, "state": "failed", "kind": kind,
                                 "error_type": type(error).__name__, "http_statuses": codes})
            if any(code in (401, 403, 404, 400, 422) for code in codes):
                raise ValueError("fatal cohort provider HTTP failure") from None
            continue
        _atomic_write(path, {"request_hash": key, "state": "passed", "kind": kind,
                             "answer": answer, "answer_hash": digest(answer)})
        return answer
    raise ValueError("cohort response invalid after one repair attempt")


def pilot_cases() -> list[dict]:
    """Preregister ten raw-fact cases across eight directions, including tie and required-signal abstention."""
    cases = []
    for i, direction in enumerate(DIRECTIONS):
        key = CORE_KEYS[direction][0]
        def candidate(level, status, key=key):
            return {"grade": "junior", "status": status, "levels": {key: level}, "skill_statuses": {key: status},
                    "salary_min": 80000, "description": "Синтетический пример без дополнительных фактов",
                    "structured_facts_available": False, "duration_months": None, "duration_status": None,
                    "production_support": None, "production_status": None, "role": None, "role_status": None,
                    "project_domains": [], "project_stacks": []}
        a = candidate(6, ("confirmed", "partial", "self_reported", "pending", "rejected")[i % 5])
        b = candidate(6 if i == 7 else 2, a["status"])
        policy = {"id": f"pilot-{direction}", "ordered_priorities": [f"The sole preference is higher declared levels.{key}. Confirmation, grade, salary and narrative cannot replace this measurement."],
                  "required_measurements": [f"levels.{key}"], "exclusive_criterion": True,
                  "suitability_rubric": f"Grade3 when known declared levels.{key} >=4; grade1 when known level below4; null only if this measurement is missing. No other facts are required."}
        cases.append({"case_id": direction, "vacancy": {"class": direction}, "A": a, "B": b,
                      "policy": policy, "expected": "tie" if i == 7 else "A", "expected_grade": 3})
    for name, measurement in (("missing_uptime", "uptime_percentage"), ("unknown_duration", "duration_months")):
        item = deepcopy(cases[0]);item["case_id"] = name
        item["policy"] = {"id": name, "ordered_priorities": [f"The only comparison is higher {measurement}. Without measurements for BOTH candidates abstain; no substitutes are allowed."],
                          "required_measurements": [measurement], "exclusive_criterion": True,
                          "suitability_rubric": f"Suitability cannot be assessed without {measurement}; return null if missing. No other fields can substitute."}
        item["expected"] = "insufficient";item["expected_grade"] = None
        if name == "unknown_duration":
            item["B"].update(structured_facts_available=True, duration_months=60, duration_status="confirmed")
        cases.append(item)
    return cases


def run_pilot(directory: Path, manifest: dict) -> dict:
    """Check twenty directed pairs and ten independent grades before expanding; no model predictions."""
    provider = CohortProviders(directory / "provider-cache", cohort_id=VERSION)
    cases = pilot_cases()
    target = directory / VERSION
    packet = {"version": VERSION, "cases": cases, "reviewer_kind": "assistant", "independent_human_evaluation": False}
    packet["packet_hash"] = digest(packet)
    _seal(target / "pilot-packet.json", packet)
    if (target / "pilot-report.json").exists():
        report = _read(target / "pilot-report.json")
        if report["packet_hash"] != packet["packet_hash"] or report["report_hash"] != digest({k:v for k,v in report.items() if k != "report_hash"}):
            raise ValueError("cohort pilot report changed")
        return report
    def pair(job):
        c, reverse = job
        a,b = (c["B"],c["A"]) if reverse else (c["A"],c["B"])
        result = _job(directory, manifest, provider, f'pilot-{c["case_id"]}-{int(reverse)}', "pair",
                      lambda attempt: provider.pair_payload(c["vacancy"],a,b,c["policy"],attempt=attempt),
                      lambda attempt: provider.judge(c["vacancy"],a,b,c["policy"],attempt=attempt))
        return {"case_id": c["case_id"], "reverse": reverse, "label": result["label"],
                "agrees": result["label"] == (REVERSE[c["expected"]] if reverse else c["expected"])}
    pairs = _parallel(pair, [(c,r) for c in cases for r in (False,True)])
    def grade(c):
        result = _job(directory,manifest,provider,f'pilot-grade-{c["case_id"]}',"grade",
                      lambda attempt: provider.suitability_payload(c["vacancy"],c["A"],c["policy"],attempt=attempt),
                      lambda attempt: provider.suitability(c["vacancy"],c["A"],c["policy"],attempt=attempt))
        return {"case_id": c["case_id"], "grade": result["grade"], "agrees": result["grade"] == c["expected_grade"]}
    grades = _parallel(grade,cases)
    report = {"packet_hash": packet["packet_hash"], "pairs": pairs, "grades": grades,
              "passed": all(r["agrees"] for r in pairs+grades), "http_scope": "synthetic only",
              "training_started": False, "activation_allowed": False}
    report["report_hash"] = digest(report);_seal(target / "pilot-report.json",report)
    return report


def _check_sources(records: list[dict]) -> dict:
    owners = {};descriptions = {};duplicates = []
    for record in records:
        for trait in record["traits"]:
            keys = (digest({k:v for k,v in trait.items() if k != "description"}), digest(trait))
            for key in keys:
                previous = owners.setdefault(key,record["manifest"]["source_id"])
                if previous != record["manifest"]["source_id"]:
                    raise ValueError("source facts shared across independent families")
            text = " ".join(trait["description"].lower().split())
            previous = descriptions.setdefault(text,record["manifest"]["split"])
            if previous != record["manifest"]["split"]:
                duplicates.append(text)
    if duplicates:
        raise ValueError("identical description templates cross splits")
    return {"exact_fact_leakage": False, "exact_description_leakage": False,
            "near_duplicate_policy": "Exact fact and normalized description blocking; approximate similarity reported separately, not silently relabeled"}


def generate_sources(directory: Path, manifest: dict) -> list[dict]:
    """Persist seven raw profiles per frozen family; keep old sources and labels untouched."""
    provider = CohortProviders(directory / "provider-cache", cohort_id=VERSION,
                               generator_model=manifest["generator"])
    target = directory / VERSION / "sources";target.mkdir(parents=True,exist_ok=True)
    def generate(item):
        path = target / f'{item["family_id"]}.json'
        saved = _read(path) if path.exists() else None
        if saved:
            record = saved
            if record["record_hash"] != digest({k:v for k,v in record.items() if k != "record_hash"}) or record["manifest"] != item:
                raise ValueError("cohort source receipt changed")
        if item["reuse"]:
            old = _read(directory / "families" / f'{item["family_id"]}.json')
            if old["record_hash"] != item["reuse"]["record_hash"]:
                raise ValueError("historical source changed")
            traits = legacy_traits(directory,old,item["reuse"]["indices"])
        elif item["prior_source"]:
            link = item["prior_source"];seen = set();traits = None
            while True:
                prior_path = directory / link["path"]
                if link["path"] in seen or prior_path.resolve().parent.parent.parent != directory.resolve() or prior_path.parent.name != 'sources':
                    raise ValueError("invalid prior source lineage")
                seen.add(link['path']);prior = _read(prior_path)
                if prior["record_hash"] != link["record_hash"] or prior["record_hash"] != digest({k:v for k,v in prior.items() if k != "record_hash"}):
                    raise ValueError("prior accepted source changed")
                if {k:v for k,v in prior['manifest'].items() if k not in ('cohort_id','prior_source')} != {k:v for k,v in item.items() if k not in ('cohort_id','prior_source')}:
                    raise ValueError("prior source lineage changed ancestry")
                if traits is not None and prior['traits'] != traits:
                    raise ValueError("prior source lineage changed facts")
                traits = prior['traits'];parent = prior.get('source_receipts')
                if type(parent) is not dict or 'path' not in parent:break
                link = parent
            receipts = [_read(path) for path in (prior_path.parent.parent / "jobs").glob(f'generate-{item["family_id"]}-*.json')]
            matching = [receipt for receipt in receipts if receipt["state"] == "passed" and receipt["answer"] == prior["traits"] and receipt["answer_hash"] == digest(prior["traits"])]
            if len(matching) != 1:
                raise ValueError("prior source lacks exact accepted generation receipt")
            raw = _read(provider.directory / f'{matching[0]["request_hash"]}.json')
            if raw["request_hash"] != matching[0]["request_hash"] or raw["response_hash"] != digest(raw["response"]) or digest(raw["request"]) != raw["request_hash"]:
                raise ValueError("prior raw generation receipt changed")
            if _parse(raw["response"],raw["request"]["model"]) != {"profiles":prior["traits"]}:
                raise ValueError("prior parsed source differs from raw generation")
            traits = prior["traits"]
        else:
            if saved and not any((directory / VERSION / "jobs" / f'generate-{item["family_id"]}-{i}.json').exists() for i in (0,1)):
                raise ValueError("generated source has no pinned job receipt")
            traits = _job(directory,manifest,provider,f'generate-{item["family_id"]}',"generation",
                          lambda attempt: provider.generation_payload(item["direction"],item["family_id"],item["keys"],attempt=attempt),
                          lambda attempt: provider.generate_family(item["direction"],item["family_id"],item["keys"],attempt=attempt),
                          start_attempt=0 if not saved else saved.get("generation_start_attempt",0))
        if len(traits) != 7:
            raise ValueError("cohort family must contain seven accepted source profiles")
        for trait in traits:validate_candidate(trait)
        if saved:
            if traits != saved["traits"]:
                raise ValueError("source traits differ from pinned generation receipt")
            return saved
        record = {"manifest": item, "traits": traits, "source_receipts": item["reuse"] or item["prior_source"] or f'generate-{item["family_id"]}'}
        record["record_hash"] = digest(record);_seal(path,record)
        print(json.dumps({"phase":"sources","family":item["family_id"],"profiles":7}),flush=True)
        return record
    first_new = next((item for item in manifest["families"] if item["reuse"] is None and item["prior_source"] is None),None)
    if first_new is not None:
        generate(first_new)  # Validate one new-generator batch before allowing five concurrent jobs.
    records = _parallel(generate,manifest["families"])
    owners = {};texts = {};repair = set()
    for record in records:
        for trait in record["traits"]:
            family = record["manifest"]["family_id"]
            fact = digest({k:v for k,v in trait.items() if k != "description"})
            text = " ".join(trait["description"].lower().split())
            previous = owners.setdefault(fact,record)
            other = texts.setdefault(text,record)
            for collision in (previous,other if other["manifest"]["split"] != record["manifest"]["split"] else record):
                if collision["manifest"]["family_id"] != family:
                    selected = record if record["manifest"]["reuse"] is None and record["manifest"]["prior_source"] is None else collision
                    if selected["manifest"]["reuse"] is not None or selected["manifest"]["prior_source"] is not None:
                        raise ValueError("historical reused sources collide; do not rewrite original facts")
                    repair.add(selected["manifest"]["family_id"])
    for index,record in enumerate(records):
        item = record["manifest"]
        if item["family_id"] not in repair:
            continue
        traits = _job(directory,manifest,provider,f'generate-{item["family_id"]}',"generation",
                      lambda attempt,item=item: provider.generation_payload(item["direction"],item["family_id"],item["keys"],attempt=attempt),
                      lambda attempt,item=item: provider.generate_family(item["direction"],item["family_id"],item["keys"],attempt=attempt),start_attempt=1)
        if traits == record["traits"]:
            raise ValueError("duplicate source repair already consumed; cannot request another attempt")
        _seal(directory / VERSION / "source-history" / f'{record["record_hash"]}.json',record)
        updated = {**record,"traits":traits,"generation_start_attempt":1,"supersedes":record["record_hash"],"repair_reason":"cross-family duplicate quarantine"}
        updated.pop("record_hash");updated["record_hash"] = digest(updated)
        _atomic_write(target / f'{item["family_id"]}.json',updated);records[index]=updated
    isolation = _check_sources(records)
    _seal(directory / VERSION / "source-isolation.json",isolation)
    return records


def label_family(directory: Path, manifest: dict, source: dict, class_profiles: list,
                 class_source_lineage: dict[str,str], semantic_seed: dict) -> dict:
    """Relabel all raw pairs and independent suitability, keeping reverse audits out of training rows."""
    item = source["manifest"];traits = source["traits"]
    pool_hash = digest(sorted(digest(profile.model_dump(mode="json")) for profile in class_profiles))
    provider = CohortProviders(directory / "provider-cache",cohort_id=VERSION)
    target = directory / VERSION / "families" / f'{item["family_id"]}.json'
    if target.exists():
        saved = _read(target)
        if saved["source_hash"] != source["record_hash"] or saved["class_pool_hash"] != pool_hash or saved["record_hash"] != digest({k:v for k,v in saved.items() if k != "record_hash"}):
            raise ValueError("labeled cohort family changed")
        return saved
    feature_path = directory / VERSION / "features" / f'{item["family_id"]}.json'
    if feature_path.exists():
        feature_record = _read(feature_path)
        if feature_record["source_hash"] != source["record_hash"] or feature_record["class_pool_hash"] != pool_hash or feature_record["hash"] != digest({k:v for k,v in feature_record.items() if k != "hash"}):
            raise ValueError("cohort feature receipt changed")
    else:
        vectors, scores, order, provenance = build_family_features(item,item["vacancy"],traits,directory / VERSION / "semantic-cache.json",VERSION,
                                                                  class_profiles=class_profiles,class_source_lineage=class_source_lineage,
                                                                  semantic_seed=semantic_seed)
        feature_record = {"source_hash":source["record_hash"],"class_pool_hash":pool_hash,"vectors":[asdict(v) for v in vectors],"main_scores":scores,"profile_order":order,"provenance":provenance}
        feature_record["hash"] = digest(feature_record);_seal(feature_path,feature_record)
    indices = list(itertools.combinations(range(7),2))
    def pair(pair):
        a,b = pair
        return _job(directory,manifest,provider,f'{item["family_id"]}-pair-{a}-{b}',"pair",
                    lambda attempt: provider.pair_payload(item["vacancy"],traits[a],traits[b],item["policy"],attempt=attempt),
                    lambda attempt: provider.judge(item["vacancy"],traits[a],traits[b],item["policy"],attempt=attempt))
    answers = _parallel(pair,indices)
    audit_pairs = [indices[i] for i in (0,7,14)]
    reverse = _parallel(pair,[(b,a) for a,b in audit_pairs])
    def grade(i):
        return _job(directory,manifest,provider,f'{item["family_id"]}-grade-{i}',"grade",
                    lambda attempt: provider.suitability_payload(item["vacancy"],traits[i],item["policy"],attempt=attempt),
                    lambda attempt: provider.suitability(item["vacancy"],traits[i],item["policy"],attempt=attempt))
    suitability = _parallel(grade,list(range(7)))
    pairs = [[a,b,answer["label"]] for (a,b),answer in zip(indices,answers,strict=True)]
    audits = [answer["label"] == REVERSE[answers[indices.index(pair)]["label"]] for pair,answer in zip(audit_pairs,reverse,strict=True)]
    record = {"manifest":item,"teacher":provider.teacher_identity(),"source_hash":source["record_hash"],"traits":traits,
              "vacancy":item["vacancy"],**feature_record,"grades":[answer["grade"] for answer in suitability],"pairs":pairs,
              "pair_answers":answers,"suitability_answers":suitability,"swap_answers":reverse,"swap_audits":audits,
              "slice_tags":[item["branch"],"bio_absent" if item["bio_absent"] else "semantic_available","mixed_evidence","partial_facts"],
              "cyclic_components":_cycles(7,pairs),"scalar_order_ceiling":_ordering_ceiling(7,pairs),
              "quarantined":sum(audits)<2}
    record["record_hash"] = digest(record);_seal(target,record)
    print(json.dumps({"phase":"labels","family":item["family_id"],"swaps":sum(audits),"decisive":sum(p[2] in ('A','B') for p in pairs),"quarantined":record["quarantined"]}),flush=True)
    return record


def run_cohort(directory: Path, *, pilot_only: bool = False) -> dict:
    """Run or resume bounded expansion; publish quality evidence, never train or activate a model."""
    target = directory / VERSION;target.mkdir(parents=True,exist_ok=True)
    with (target / ".run.lock").open("a+") as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        manifest_path = target / "manifest.json"
        manifest = _read(manifest_path) if manifest_path.exists() else cohort_manifest(directory)
        if manifest["manifest_hash"] != digest({k:v for k,v in manifest.items() if k != "manifest_hash"}):
            raise ValueError("cohort manifest hash changed")
        for name,value in manifest["code_hashes"].items():
            if digest((Path(__file__).parent / name).read_bytes().hex()) != value:
                raise ValueError("frozen cohort code changed")
        if digest((Path(__file__).resolve().parents[4] / "data/seed/candidate-pipeline.json").read_bytes().hex()) != manifest["seed_hash"]:
            raise ValueError("frozen candidate seed changed")
        for name,value in manifest["protected_source_hashes"].items():
            if digest((directory / name).read_bytes().hex()) != value:
                raise ValueError("historical source bytes changed")
        pilot = run_pilot(directory,manifest)
        if pilot_only or not pilot["passed"]:
            return {"state":"pilot_passed" if pilot["passed"] else "pilot_failed","pilot":pilot}
        sources = generate_sources(directory,manifest)
        semantic_path = target / "semantic-cache.json"
        if not semantic_path.exists():
            _seal(semantic_path,_read(directory / "semantic-cache.json"))
        pools = {direction:[candidate_profile(trait,direction) for source in sources
                            if source["manifest"]["direction"]==direction for trait in source["traits"]]
                 for direction in DIRECTIONS}
        lineages = {direction:{f'{source["manifest"]["family_id"]}/{i}':digest(candidate_profile(trait,direction).model_dump(mode="json"))
                               for source in sources if source["manifest"]["direction"]==direction for i,trait in enumerate(source["traits"])}
                    for direction in DIRECTIONS}
        semantic_seed = _read(directory / "semantic-cache.json")
        families = [label_family(directory,manifest,source,pools[source["manifest"]["direction"]],
                                 lineages[source["manifest"]["direction"]],semantic_seed) for source in sources]
        decisive = {r["manifest"]["family_id"]:sum(p[2] in ('A','B') for p in r["pairs"]) for r in families}
        swaps = sum(sum(r["swap_audits"]) for r in families)
        quality = {"minimum_golden_decisive_pairs":min(decisive[r["manifest"]["family_id"]] for r in families if r["manifest"]["split"]=='golden'),
                   "swap_correct":swaps,"swap_total":180,"quarantined_families":[r["manifest"]["family_id"] for r in families if r["quarantined"]]}
        quality["passed"] = swaps >=162 and not quality["quarantined_families"] and quality["minimum_golden_decisive_pairs"]>=6
        dataset = {"version":VERSION,"manifest_hash":manifest["manifest_hash"],"families":families,"teacher":families[0]["teacher"],"quality":quality,"pilot_hash":pilot["report_hash"],"training_started":False,"activation_allowed":False}
        dataset["dataset_hash"] = digest(dataset);_seal(target / "dataset.json",dataset)
        report = {"state":"labeled_quality_passed_needs_review" if quality["passed"] else "labeled_quality_failed_needs_repair",
                  "families":60,"profiles":420,"split_counts":manifest["split_counts"],"directions":dict(Counter(r["manifest"]["direction"] for r in families)),
                  "evidence_statuses":dict(Counter(t["status"] for r in families for t in r["traits"])),
                  "declared_level_keys":sorted({k for r in families for t in r["traits"] for k in t["levels"]}),
                  "pair_labels":dict(Counter(p[2] for r in families for p in r["pairs"])),"quality":quality,
                  "cycles":sum(bool(r["cyclic_components"]) for r in families),"minimum_unavoidable_disagreements":sum(r["scalar_order_ceiling"]["minimum_unavoidable_disagreements"] for r in families),
                  "ledger_before":manifest["ledger_before"],"ledger_after":_read(directory / "provider-cache/usage.json"),"attempts":_read(target / "attempt-budget.json"),
                  "dataset_hash":dataset["dataset_hash"],"golden_model_predictions":0,"training_started":False,"activation_allowed":False,
                  "review_requirement":"Separate score-blind assistant20 and user20 before activation; no independent human assessment claimed"}
        report["historical_source_bytes_preserved"] = all(digest((directory / name).read_bytes().hex()) == value
                                                       for name,value in manifest["protected_source_hashes"].items())
        if not report["historical_source_bytes_preserved"]:
            raise ValueError("historical source bytes changed during expansion")
        descriptions = Counter((r["manifest"]["split"]," ".join(t["description"].lower().split())) for r in sources for t in r["traits"])
        report["same_split_description_template_groups"] = sum(count>1 for count in descriptions.values())
        report["report_hash"] = digest(report);_seal(target / "report.json",report)
        return report


def main() -> None:
    """Start/resume the new namespace; credentials stay lazy and never appear in output."""
    parser=argparse.ArgumentParser(description="Uniform grounded cohort relabeling and expansion")
    parser.add_argument("--directory",type=Path,default=Path("tests/artifacts/reranker"))
    parser.add_argument("--pilot-only",action="store_true")
    args=parser.parse_args()
    print(json.dumps(run_cohort(args.directory,pilot_only=args.pilot_only),ensure_ascii=False))


if __name__ == "__main__":
    main()
