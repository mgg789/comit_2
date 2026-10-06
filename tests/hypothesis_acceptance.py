"""Real-Harrier, isolated PostgreSQL acceptance of live hypothesis pair assignments."""

import json
import os
import subprocess
from pathlib import Path
from uuid import uuid4

from matching_engine.combine_models import CombineRequest
from matching_engine.combine_store import CombineStore
from matching_engine.combine_worker import CombineWorker
from matching_engine.database import connect, migrate
from matching_engine.feed_models import FeedDecisionRequest, FeedRequest
from matching_engine.feed_store import FeedStore
from matching_engine.hypothesis_store import HypothesisStore
from matching_engine.learning_store import LearningStore
from matching_engine.models import CandidateProfile, CoordinateAddition
from matching_engine.rerank_activation import bootstrap_packaged
from matching_engine.rerank_hash import digest
from matching_engine.rerank_store import RerankStore
from matching_engine.rerank_worker import RerankWorker
from matching_engine.store import Store
from matching_engine.vacancy_models import VacancyInput
from matching_engine.vacancy_store import VacancyStore
from matching_engine.vacancy_worker import VacancyWorker
from matching_engine.worker import Worker
from psycopg.conninfo import conninfo_to_dict


def drain(runner, maximum: int = 100) -> None:
    """Run actual finite pipeline work without replacing encoders, features or scoring."""
    for _ in range(maximum):
        if not runner.process_once():
            return
    raise AssertionError("pipeline did not drain")


def main() -> None:
    """Exercise declared synthetic facts/actions using actual Harrier, PDF and database code."""
    root = Path(__file__).resolve().parents[1]
    url = os.environ["HYPOTHESIS_ACCEPTANCE_DATABASE_URL"]
    config = conninfo_to_dict(url)
    assert config.get("host") in ("localhost", "127.0.0.1", "::1")
    assert config["dbname"].startswith("comit_hypothesis_")
    target = root / "tests/artifacts/hypotheses" / config["dbname"]
    target.mkdir(mode=0o700, parents=True, exist_ok=False)
    with connect(url) as connection:
        assert connection.execute("SELECT to_regclass('candidate_pipeline.candidates') AS name").fetchone()["name"] is None
    subprocess.run(["node", "apps/api/dist/auth/migrate.js"], cwd=root,
        env={**os.environ, "DATABASE_URL": url}, check=True, stdout=subprocess.DEVNULL)
    migrations = migrate(url)
    with connect(url) as connection:
        connection.execute("UPDATE learning_pipeline.settings SET source='synthetic'")
    store, vacancies = Store(url), VacancyStore(url)
    seed = json.loads((root / "data/seed/candidate-pipeline.json").read_text())[0]["profile"]
    ids = [uuid4() for _ in range(22)]
    for index,candidate in enumerate(ids):
        profile = json.loads(json.dumps(seed))
        profile.update(source_revision=1, primary_direction="backend", city="Москва", work_types=["remote"],
                       salary={"from_rub":100000,"to_rub":150000}, description="Synthetic Python API backend services.")
        profile["grade_result"]["grade"]="senior" if index<18 else "middle"
        profile["stacks"]=[{"key":"python","level":4 if index<18 else 2 if index%2==0 else 5,
            "status":"confirmed" if index<18 or index%2==0 else "self_reported"}]
        store.intake(candidate,CandidateProfile.model_validate(profile))
        with connect(url) as connection:
            connection.execute("INSERT INTO accounts(id,email,role,email_verified) VALUES(%s,%s,'candidate',true)",
                               (candidate,f'hypothesis-{index}@example.invalid'))
            connection.execute("""INSERT INTO candidate_profiles(candidate_id,revision,processing_consent,pool_consent,
                consent_version,consent_accepted_at) VALUES(%s,1,true,true,'synthetic-hypothesis-v1',now())""",(candidate,))
    drain(Worker(store))
    assert all(store.status(candidate).state=='ready' for candidate in ids)
    owner,vacancy=uuid4(),uuid4()
    vacancies.intake(vacancy,VacancyInput.model_validate({"source_revision":1,"owner_id":owner,"status":"open",
        "zp_min":100000,"zp_max":200000,"daily":"full_time","type":"remote","city":"Москва",
        "class":"backend","stacks":{"python":4},"bio":"Python API backend services"}))
    drain(VacancyWorker(vacancies))
    combines=CombineStore(url)
    combines.request(vacancy,CombineRequest(top_k=18))
    drain(CombineWorker(combines))
    reranks=RerankStore(url)
    bootstrap_packaged(reranks)
    drain(RerankWorker(reranks))
    feed,hypotheses=FeedStore(url),HypothesisStore(url)
    prefix=feed.open(owner,FeedRequest(limit=18),vacancy)
    assert prefix.has_more and prefix.next_cursor
    with connect(url) as connection:
        delivered_before=connection.execute("SELECT exposure_id,position,card FROM feed_pipeline.exposures WHERE served_at IS NOT NULL ORDER BY position").fetchall()
    first=feed.page(owner,vacancy,prefix.session_id,prefix.next_cursor,1)
    card=first.items[0]
    assert card.hypothesis_id=='H2' and card.exploration_added and card.kind=='reserve'
    assert card.order_probability==.5 and 0<card.selection_probability<=1
    assert feed.page(owner,vacancy,prefix.session_id,prefix.next_cursor,1).items[0].model_dump()==card.model_dump()
    second=feed.page(owner,vacancy,prefix.session_id,first.next_cursor,1).items[0]
    assert second.hypothesis_pair_id==card.hypothesis_pair_id and second.hypothesis_role!=card.hypothesis_role
    for item in (card,second):
        body=FeedDecisionRequest(exposure_id=item.exposure_id,action='like' if item.hypothesis_role=='test' else 'dislike',idempotency_key=uuid4())
        receipt=feed.decision(owner,vacancy,body)
        assert feed.decision(owner,vacancy,body).receipt_id==receipt.receipt_id
    state=hypotheses.status(owner,vacancy)
    result=next(x for x in state['hypotheses'] if x['hypothesis_id']=='H2')
    assert result['supporting']==1 and result['status']=='unclear'
    manifest=LearningStore(url).export(target/'dataset',b'synthetic-hypothesis-acceptance-32bytes',source='synthetic')
    assert manifest['explicit_decisions']==2 and manifest['rows']==20 and not manifest['propensity_available']
    with connect(url) as connection:
        assert connection.execute("SELECT selected_count FROM matching_pipeline.runs").fetchone()['selected_count']==18
        assert connection.execute("SELECT count(*) AS count FROM hypothesis_pipeline.pairs").fetchone()['count']==1
        assert connection.execute("SELECT exposure_id,position,card FROM feed_pipeline.exposures WHERE exposure_id=ANY(%s::uuid[]) ORDER BY position",
                                  ([row['exposure_id'] for row in delivered_before],)).fetchall()==delivered_before
        dimensions=connection.execute("SELECT vector_dims(semantic_vector) AS dims FROM candidate_pipeline.pool LIMIT 1").fetchone()['dims']
        connection.execute("UPDATE candidate_profiles SET pool_consent=false,revision=revision+1 WHERE candidate_id=%s",(card.candidate_id,))
    assert hypotheses.status(owner,vacancy)['pairs']==0
    with connect(url) as connection:
        assert connection.execute("SELECT parameters FROM hypothesis_pipeline.pairs").fetchone()['parameters'] is None
    reset=hypotheses.command(owner,vacancy,'reset',uuid4(),state['revision'])
    assert reset['pairs']==0
    store.extend_schema(CoordinateAddition(group='stack',key='hypothesis_acceptance_extension'))
    from matching_engine.store import ConflictError, NotFoundError
    try:
        feed.page(owner,vacancy,prefix.session_id,first.next_cursor,1)
    except (ConflictError,NotFoundError):
        stale_rejected=True
    else:
        raise AssertionError('stale schema feed accepted')
    report={"version":"hypothesis-acceptance-v1","state":"passed_synthetic_mechanics","profiles":22,
        "ordinary_deliveries":18,"research_deliveries":2,"real_harrier_dimensions":dimensions,"migrations":migrations,
        "family":"H2","conditional_pair_probability":card.selection_probability,"order_probability":card.order_probability,
        "actual_outside_top_k_pair":True,"main_selected_count_unchanged":True,"delivery_replay_equal":True,
        "prefix_immutable":True,"budget_pairs":1,"no_single_pair_confirmation":True,"learner_explicit_actions":2,
        "export_manifest_hash":manifest['manifest_hash'],"global_propensity":None,"source_only_withdrawal":True,
        "parameters_purged":True,"reset":True,"new_coordinate_stale_rejected":stale_rejected,"network_model_calls":0,
        "human_quality_evaluated":False,"production_deployed":False}
    report['report_hash']=digest(report)
    path=target/'report.json'
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    path.chmod(0o600)
    print(json.dumps(report,ensure_ascii=False))


if __name__=='__main__':
    main()
