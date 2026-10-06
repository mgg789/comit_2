"""Versioned cohort accounting, source boundaries and crash-safe repair receipts."""

import json
from copy import deepcopy

import pytest

import matching_engine.rerank_cohort as cohort
from matching_engine.rerank_hash import digest


@pytest.fixture
def directory(tmp_path):
    (tmp_path / "families").mkdir()
    (tmp_path / "provider-cache").mkdir()
    (tmp_path / "provider-cache/usage.json").write_text('{"generation":28,"pair":736,"grade":119}')
    (tmp_path / "semantic-cache.json").write_text('{}')
    for item in cohort.family_manifest()[:12]:
        record={"manifest":item,"traits":[{}]*10,"record_hash":digest(item)}
        (tmp_path / "families" / f'{item["family_id"]}.json').write_text(json.dumps(record))
    return tmp_path


def test_full_manifest_keeps_splits_and_original_budget_headroom(directory):
    manifest=cohort.cohort_manifest(directory)
    assert manifest["profiles"]==420 and len(manifest["families"])==60
    assert manifest["split_counts"]=={"train":32,"validation":8,"calibration":8,"golden":12}
    assert manifest["normal_attempts"]=={"generation":48,"pair":1460,"grade":430}
    assert manifest["attempt_limits"]=={"generation":65,"pair":2264,"grade":481}
    assert len({key for item in manifest["families"] for key in item["keys"]})==37
    assert all(row["reuse"] is None or row["reuse"]["indices"]==[0,1,2,3,4,5,9] for row in manifest["families"])


def test_budget_cannot_reset_or_expand_to_fit_new_manifest(directory):
    (directory / "provider-cache/usage.json").write_text('{"generation":28,"pair":736,"grade":171}')
    with pytest.raises(ValueError,match="unchanged cumulative"):
        cohort.cohort_manifest(directory)


def test_existing_family_cannot_move_to_another_split(directory):
    path=directory / "families/backend-01.json";record=json.loads(path.read_text())
    record["manifest"]["split"]="golden";path.write_text(json.dumps(record))
    with pytest.raises(ValueError,match="ancestry"):
        cohort.cohort_manifest(directory)


def test_pilot_covers_directions_tie_unknown_and_rejected_without_model_scores():
    cases=cohort.pilot_cases()
    assert len(cases)==10 and {case["case_id"] for case in cases[:8]}==set(cohort.DIRECTIONS)
    assert sum(case["expected"]=='A' for case in cases)==7
    assert sum(case["expected"]=='tie' for case in cases)==1
    assert sum(case["expected"]=='insufficient' for case in cases)==2
    assert {case["A"]["status"] for case in cases[:8]}=={'confirmed','partial','self_reported','pending','rejected'}
    assert all('score' not in candidate for case in cases for candidate in (case['A'],case['B']))


def test_facts_cannot_cross_families_even_with_new_description():
    a=cohort.pilot_cases()[0]['A'];b={**deepcopy(a),'description':'Другой текст'}
    records=[{'manifest':{'source_id':'one','split':'train'},'traits':[a]},
             {'manifest':{'source_id':'two','split':'golden'},'traits':[b]}]
    with pytest.raises(ValueError,match='source facts'):
        cohort._check_sources(records)


def test_description_templates_cannot_cross_splits():
    a=cohort.pilot_cases()[0]['A'];b={**deepcopy(a),'salary_min':90000}
    records=[{'manifest':{'source_id':'one','split':'train'},'traits':[a]},
             {'manifest':{'source_id':'two','split':'golden'},'traits':[b]}]
    with pytest.raises(ValueError,match='description templates'):
        cohort._check_sources(records)


class CachedProvider:
    def __init__(self,directory):
        self.directory=directory

    def _identity(self,path,payload,kind):
        return {'path':path,'payload':payload,'kind':kind}


def test_one_repair_has_distinct_identity_and_finished_replay_does_not_reserve(directory):
    manifest=cohort.cohort_manifest(directory);provider=CachedProvider(directory / 'provider-cache')
    payload=lambda attempt:{'attempt':attempt}
    calls=[]
    def execute(attempt):
        calls.append(attempt)
        key=digest(provider._identity('/v1/chat/completions',payload(attempt),'pair'))
        (provider.directory / f'{key}.json').write_text('{}')
        if attempt==0:raise ValueError('invalid parsed response')
        return {'label':'A'}
    assert cohort._job(directory,manifest,provider,'job','pair',payload,execute)=={'label':'A'}
    assert calls==[0,1]
    used=cohort._read(directory / cohort.VERSION / 'attempt-budget.json')
    assert used['pair']==2
    assert cohort._job(directory,manifest,provider,'job','pair',payload,execute)=={'label':'A'}
    assert cohort._read(directory / cohort.VERSION / 'attempt-budget.json')==used
    assert calls==[0,1,1]


def test_interrupted_missing_raw_receipt_never_resends_ambiguously(directory):
    manifest=cohort.cohort_manifest(directory);provider=CachedProvider(directory / 'provider-cache')
    class Interrupted(BaseException):pass
    def execute(_attempt):raise Interrupted()
    with pytest.raises(Interrupted):
        cohort._job(directory,manifest,provider,'job','pair',lambda attempt:{'attempt':attempt},execute)
    with pytest.raises(ValueError,match='ambiguous interrupted'):
        cohort._job(directory,manifest,provider,'job','pair',lambda attempt:{'attempt':attempt},lambda _:pytest.fail('resent'))


def test_failed_pilot_never_expands_or_runs_harrier(directory,monkeypatch):
    monkeypatch.setattr(cohort,'run_pilot',lambda *_:{'passed':False})
    monkeypatch.setattr(cohort,'generate_sources',lambda *_:pytest.fail('expanded after failed pilot'))
    assert cohort.run_cohort(directory)['state']=='pilot_failed'
