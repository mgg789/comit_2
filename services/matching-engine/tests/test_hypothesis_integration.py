"""PostgreSQL paired exploration with actual feature builders and immutable feed receipts."""

from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from matching_engine.combine_models import CombineRequest
from matching_engine.combine_store import CombineStore
from matching_engine.database import connect
from matching_engine.feed_models import (
    FeedDecisionRequest,
    FeedRequest,
    ShowcaseLikeRequest,
)
from matching_engine.feed_store import FeedStore
from matching_engine.hypothesis_store import HypothesisStore
from matching_engine.learning_store import LearningStore
from matching_engine.main import create_app
from matching_engine.preference_store import PreferenceStore
from matching_engine.rerank_activation import bootstrap_packaged
from matching_engine.rerank_hash import digest
from matching_engine.rerank_store import RerankStore
from matching_engine.rerank_worker import RerankWorker
from matching_engine.store import ConflictError
from matching_engine.vacancy_store import VacancyStore
from test_combine_integration import finish
from test_contracts import eligible_profile
from test_pipeline_integration import (
    store as store,  # noqa: PLC0414 - disposable PostgreSQL fixture.
)
from test_pipeline_integration import worker
from test_vacancy_contracts import eligible_vacancy
from test_vacancy_integration import run

pytestmark = pytest.mark.integration


def exploration_feed(store, *, top_k=18, sparse=False, count=22, ordinary_limit=18):
    """Publish 18 strong ordinary and four contrasting middle profiles with declared synthetic source."""
    with connect(store.database_url) as connection:
        connection.execute("UPDATE learning_pipeline.settings SET source='synthetic'")
    owner_id, vacancy_id = uuid4(), uuid4()
    for index in range(count):
        profile = eligible_profile(work_types=["remote"])
        if index < 18:
            profile.grade_result.grade = "senior"
            profile.stacks[0].level = 4
            profile.stacks[0].status = "confirmed"
        else:
            profile.stacks[0].level = 2 if index % 2 == 0 else 5
            profile.stacks[0].status = "confirmed" if index % 2 == 0 else "self_reported"
        store.intake(UUID(int=index+1), profile)
        assert worker(store).process_once()
    vacancies = VacancyStore(store.database_url)
    vacancies.intake(vacancy_id, eligible_vacancy(owner_id=owner_id, **({} if sparse else {"stacks": {"python": 4}})))
    assert run(vacancies).process_once()
    combines = CombineStore(store.database_url)
    combines.request(vacancy_id, CombineRequest(top_k=top_k))
    finish(combines)
    reranks = RerankStore(store.database_url)
    bootstrap_packaged(reranks)
    assert RerankWorker(reranks).process_once()
    feed = FeedStore(store.database_url)
    page = feed.open(owner_id, FeedRequest(limit=ordinary_limit), vacancy_id)
    assert len(page.items)==ordinary_limit and all(item.hypothesis_pair_id is None for item in page.items)
    return feed, owner_id, vacancy_id, page


def test_full_pool_pair_probability_replay_and_actions(store, tmp_path):
    """Explore outside top-k after18 deliveries, then ingest real explicit reactions without labels for no-action."""
    feed, owner, vacancy, prefix = exploration_feed(store)
    # Main pool has ended; a forward request at the delivered cursor still checks eligible exploration.
    from matching_engine.feed_store import _encode_cursor
    cursor = _encode_cursor(18)
    page = feed.page(owner,vacancy,prefix.session_id,cursor,1)
    assert len(page.items)==1
    first = page.items[0]
    assert first.hypothesis_id=="H2" and first.kind=="reserve" and first.exploration_added
    assert first.hypothesis_pair_id and first.order_probability==.5 and 0<first.selection_probability<=1
    replay = feed.page(owner,vacancy,prefix.session_id,cursor,1)
    assert replay.items[0].model_dump()==first.model_dump()
    second = feed.page(owner,vacancy,prefix.session_id,page.next_cursor,1).items[0]
    assert second.hypothesis_pair_id==first.hypothesis_pair_id and second.hypothesis_role!=first.hypothesis_role
    with connect(store.database_url) as connection:
        assert connection.execute("SELECT count(*) AS count FROM hypothesis_pipeline.pairs").fetchone()["count"]==1
        pair=connection.execute("SELECT * FROM hypothesis_pipeline.pairs").fetchone()
        support=pair["parameters"]["support"]
        assert sum(row["probability"] for row in support)==pytest.approx(1.)
        selected=pair["parameters"]["selected_index"]
        assert support[selected]["comparison_hash"]==pair["comparison_hash"]
        assert pair["pair_probability"]==pytest.approx(support[selected]["probability"])
        assert sum(row["probability"] for row in support[:selected])<=pair["draw"]<sum(row["probability"] for row in support[:selected+1])
        assert 'preferred_delta' not in first.hypothesis_parameters  # Numeric participant facts remain only in purgeable registry.
        assert connection.execute("SELECT count(*) AS count FROM learning_pipeline.samples WHERE sample_scope='exploration'").fetchone()["count"]==2
        main=connection.execute("SELECT selected_count FROM matching_pipeline.runs").fetchone()
        assert main["selected_count"]==18
    status=HypothesisStore(store.database_url).status(owner,vacancy)
    assert next(x for x in status["hypotheses"] if x["hypothesis_id"]=="H2")["decisive"]==0
    for item in (first,second):
        request=FeedDecisionRequest(exposure_id=item.exposure_id,action="like" if item.hypothesis_role=="test" else "dislike",idempotency_key=uuid4())
        receipt=feed.decision(owner,vacancy,request)
        assert feed.decision(owner,vacancy,request).receipt_id==receipt.receipt_id
    status=HypothesisStore(store.database_url).status(owner,vacancy)
    result=next(x for x in status["hypotheses"] if x["hypothesis_id"]=="H2")
    assert result["supporting"]==1 and result["status"]=="unclear"
    # The existing local learner sees both actually delivered actions, including outside-top-k snapshots.
    with connect(store.database_url) as connection:
        control=connection.execute("SELECT * FROM preference_pipeline.controls").fetchone()
        assert len(PreferenceStore._observations(connection,control))==2
    manifest=LearningStore(store.database_url).export(tmp_path/'export',b'synthetic-test-only-key-32bytes---',source='synthetic')
    assert manifest["rows"]==20 and manifest["explicit_decisions"]==2 and not manifest["propensity_available"]
    import json
    observations=[json.loads(line) for line in (tmp_path/'export/observations.jsonl').read_text().splitlines()]
    personal=[exposure for row in observations for exposure in row["exposures"] if exposure["hypothesis_pair_id"]]
    assert len(personal)==2 and all(x["propensity"] is None and x["order_probability"]==.5 for x in personal)
    assert all(x["hypothesis_pair_id"]!=str(first.hypothesis_pair_id) for x in personal)


def test_budget_no_second_pair_without_ordinary_credits(store):
    feed,owner,vacancy,prefix=exploration_feed(store,top_k=22)
    page=feed.page(owner,vacancy,prefix.session_id,prefix.next_cursor,2)
    assert all(item.hypothesis_pair_id for item in page.items)
    feed.page(owner,vacancy,prefix.session_id,page.next_cursor,2)
    with connect(store.database_url) as connection:
        assert connection.execute("SELECT count(*) AS count FROM hypothesis_pipeline.pairs").fetchone()["count"]==1


def test_missing_class_only_is_not_testable(store):
    feed,owner,vacancy,prefix=exploration_feed(store,sparse=True)
    from matching_engine.feed_store import _encode_cursor
    assert feed.page(owner,vacancy,prefix.session_id,_encode_cursor(18),2).items==[]
    status=HypothesisStore(store.database_url).status(owner,vacancy)
    assert status["pairs"]==0 and all(x["status"]=="not_testable" for x in status["hypotheses"])
    assert status["search_report"]["feasible_pairs"]==0


def test_showcase_only_does_not_create_hypothesis_or_preference_context(store):
    """A manual session without ML runs remains outside hypothesis control and learner identity."""
    from test_showcase_likes_integration import manual_pool, request
    feed, _, owner, vacancy, candidates = manual_pool(store)
    feed.showcase_like(owner, vacancy, request(candidates[0]))
    status = HypothesisStore(store.database_url).status(owner, vacancy)
    assert status["context_hash"] is None and status["pairs"] == 0
    with connect(store.database_url) as connection:
        assert connection.execute("SELECT count(*) AS n FROM preference_pipeline.controls").fetchone()["n"] == 0
        assert connection.execute("SELECT count(*) AS n FROM hypothesis_pipeline.controls").fetchone()["n"] == 0


def test_showcase_session_does_not_reset_ranked_context_or_credit_research(store):
    """Newer manual sessions cannot replace the ranked context or contribute ordinary credits."""
    feed, owner, vacancy, prefix = exploration_feed(store, ordinary_limit=17)
    hypotheses = HypothesisStore(store.database_url)
    before = hypotheses.status(owner, vacancy)
    with connect(store.database_url) as connection:
        preference = connection.execute("SELECT * FROM preference_pipeline.controls").fetchone()
        jobs = connection.execute("SELECT count(*) AS n FROM preference_pipeline.jobs").fetchone()["n"]
    feed.showcase_like(owner, vacancy, ShowcaseLikeRequest(candidate_id=UUID(int=19),
        profile_revision=1, idempotency_key=uuid4()))
    after = hypotheses.status(owner, vacancy)
    assert after["context_hash"] == before["context_hash"] and after["pairs"] == 0
    with connect(store.database_url) as connection:
        assert connection.execute("SELECT * FROM preference_pipeline.controls").fetchone() == preference
        assert connection.execute("SELECT count(*) AS n FROM preference_pipeline.jobs").fetchone()["n"] == jobs
        manual = connection.execute("SELECT * FROM feed_pipeline.sessions WHERE source='showcase'").fetchone()
    assert feed.page(owner, vacancy, manual["session_id"], None, 1).items == []
    ordinary = feed.page(owner, vacancy, prefix.session_id, prefix.next_cursor, 1)
    assert ordinary.items[0].candidate_id == UUID(int=18) and ordinary.items[0].hypothesis_pair_id is None
    # Only the eighteenth ranked delivery unlocks the first research pair.
    with connect(store.database_url) as connection:
        assert connection.execute("SELECT count(*) AS n FROM hypothesis_pipeline.pairs").fetchone()["n"] == 1


def test_pending_pair_is_pinned_across_preference_refresh_and_pause(store):
    feed,owner,vacancy,prefix=exploration_feed(store,top_k=22)
    page=feed.page(owner,vacancy,prefix.session_id,prefix.next_cursor,1)
    first=page.items[0]
    assert first.hypothesis_pair_id
    with connect(store.database_url) as connection:
        connection.execute("UPDATE preference_pipeline.controls SET revision=revision+1")
    second=feed.page(owner,vacancy,prefix.session_id,page.next_cursor,1).items[0]
    assert second.hypothesis_pair_id==first.hypothesis_pair_id
    hypotheses=HypothesisStore(store.database_url)
    before=hypotheses.status(owner,vacancy)
    key=uuid4()
    pause=hypotheses.command(owner,vacancy,"pause",key,before["revision"])
    assert not pause["enabled"] and pause["pairs"]==1  # Delivered evidence retained.
    assert hypotheses.command(owner,vacancy,"pause",key,before["revision"])["revision"]==pause["revision"]
    with pytest.raises(ConflictError):
        hypotheses.command(owner,vacancy,"resume",key,pause["revision"])
    with pytest.raises(ConflictError):
        hypotheses.command(owner,vacancy,"reset",uuid4(),before["revision"])
    reset=hypotheses.command(owner,vacancy,"reset",uuid4(),pause["revision"])
    assert reset["enabled"] and reset["pairs"]==0
    assert feed.exposure(owner,vacancy,first.exposure_id).hypothesis_pair_id==first.hypothesis_pair_id


def test_matching_consent_invalidates_pair_and_censors_second(store):
    feed,owner,vacancy,prefix=exploration_feed(store)
    from matching_engine.feed_store import _encode_cursor
    page=feed.page(owner,vacancy,prefix.session_id,_encode_cursor(18),1)
    with connect(store.database_url) as connection:
        cid=connection.execute("SELECT candidate_id FROM feed_pipeline.exposures WHERE served_at IS NULL").fetchone()["candidate_id"]
        # Matching withdrawal path purges parameters and suppresses the added card immediately.
        connection.execute("UPDATE candidate_pipeline.candidates SET profile=jsonb_set(profile,'{consents,pool}','false') WHERE candidate_id=%s",(cid,))
        LearningStore.withdraw(connection,cid)
    assert feed.page(owner,vacancy,prefix.session_id,page.next_cursor,1).items==[]
    assert HypothesisStore(store.database_url).status(owner,vacancy)["pairs"]==0
    with connect(store.database_url) as connection:
        assert connection.execute("SELECT parameters FROM hypothesis_pipeline.pairs").fetchone()["parameters"] is None


def test_owner_http_commands_and_expired_session_status(store):
    _feed,owner,vacancy,_prefix=exploration_feed(store)
    api=TestClient(create_app(store=store,internal_token='synthetic-hypothesis-test-token'))
    headers={"X-Internal-Token":"synthetic-hypothesis-test-token","X-Owner-ID":str(owner)}
    path=f'/internal/vacancies/{vacancy}/hypotheses'
    assert api.get(path).status_code==401
    assert api.get(path,headers={**headers,"X-Owner-ID":str(uuid4())}).status_code==404
    with connect(store.database_url) as connection:
        connection.execute("UPDATE feed_pipeline.sessions SET created_at=now()-interval '2 days',expires_at=now()-interval '1 day'")
    response=api.get(path,headers=headers)
    assert response.status_code==200 and len(response.json()["hypotheses"])==10
    assert api.post(path+'/reset',headers=headers,json={"expected_revision":response.json()["revision"],"idempotency_key":str(uuid4())}).status_code==200
    assert api.post(path+'/arbitrary',headers=headers,json={"expected_revision":0,"idempotency_key":str(uuid4())}).status_code==422


def test_source_only_withdrawal_before_projection(store):
    """Source consent cancels the pending comparison even while matching still shows both consents."""
    feed,owner,vacancy,prefix=exploration_feed(store)
    page=feed.page(owner,vacancy,prefix.session_id,prefix.next_cursor,1)
    with connect(store.database_url) as connection:
        assert connection.execute("SELECT to_regclass('public.candidate_profiles') AS name").fetchone()["name"] is None
        connection.execute("CREATE TABLE public.candidate_profiles(candidate_id uuid PRIMARY KEY,processing_consent boolean,pool_consent boolean)")
        connection.execute("INSERT INTO public.candidate_profiles SELECT candidate_id,true,true FROM candidate_pipeline.candidates")
        candidate=connection.execute("SELECT candidate_id FROM feed_pipeline.exposures WHERE served_at IS NULL").fetchone()["candidate_id"]
        connection.execute("UPDATE public.candidate_profiles SET pool_consent=false WHERE candidate_id=%s",(candidate,))
    try:
        assert feed.page(owner,vacancy,prefix.session_id,page.next_cursor,1).items==[]
        assert HypothesisStore(store.database_url).status(owner,vacancy)["pairs"]==0
        with connect(store.database_url) as connection:
            assert connection.execute("SELECT invalidation_reason FROM hypothesis_pipeline.pairs").fetchone()["invalidation_reason"]=="consent_withdrawn"
    finally:
        with connect(store.database_url) as connection:
            connection.execute("DROP TABLE public.candidate_profiles")


def test_fixed_batch_persists_and_withdrawal_does_not_replace_evidence(store):
    """Explicit storage fixtures test frozen batches/purge independently from actual pair-selection tests."""
    feed,owner,vacancy,prefix=exploration_feed(store,top_k=62,count=62)
    feed.page(owner,vacancy,prefix.session_id,prefix.next_cursor,100)
    hypotheses=HypothesisStore(store.database_url)
    with connect(store.database_url) as connection:
        control=connection.execute("SELECT * FROM hypothesis_pipeline.controls").fetchone()
        sources=connection.execute("SELECT * FROM feed_pipeline.exposures ORDER BY position").fetchall()
        # Twenty explicit durable pair fixtures; grouping independence is covered
        # by allocator tests. This case checks registry sealing/purge only.
        for index in range(21):
            test,other=sources[2*index],sources[2*index+1]
            connection.execute("""INSERT INTO hypothesis_pipeline.pairs(pair_id,vacancy_id,owner_id,context_hash,epoch,session_id,
                hypothesis_id,test_candidate,control_candidate,test_exposure,control_exposure,comparison_hash,policy_hash,parameters,
                pair_probability,draw,test_first) VALUES(%s,%s,%s,%s,%s,%s,'H10',%s,%s,%s,%s,%s,%s,'{}',1,0,true)""",
                (uuid4(),vacancy,owner,control["context_hash"],control["epoch"],prefix.session_id,
                 test["candidate_id"],other["candidate_id"],test["exposure_id"],other["exposure_id"],str(index),control["context_hash"]))
    # Alternate actual actions; each adjacent fixture is decisive in one direction.
    for index,item in enumerate(sources):
        feed.decision(owner,vacancy,FeedDecisionRequest(exposure_id=item["exposure_id"],action='like' if index%2==0 else 'dislike',idempotency_key=uuid4()))
    # Sealing happens on the twentieth completed reaction pair, before any status read.
    with connect(store.database_url) as connection:
        frozen=connection.execute("SELECT * FROM hypothesis_pipeline.batches WHERE hypothesis_id='H10'").fetchone()
        assert frozen and len(frozen["pair_ids"])==20 and frozen["report"]["decisive"]==20
    result=next(x for x in hypotheses.status(owner,vacancy)["hypotheses"] if x["hypothesis_id"]=='H10')
    assert result["batch_status"]=='frozen' and result["decisive"]==20
    assert result["observed_decisive"]==21 and result["post_batch_decisive"]==1
    with connect(store.database_url) as connection:
        batch=connection.execute("SELECT * FROM hypothesis_pipeline.batches WHERE hypothesis_id='H10'").fetchone()
        assert digest(batch["report"])==batch["report_hash"]
        LearningStore.withdraw(connection,sources[0]["candidate_id"])
        batch=connection.execute("SELECT * FROM hypothesis_pipeline.batches WHERE hypothesis_id='H10'").fetchone()
        assert batch["report"] is None and batch["invalidated_at"]
    after=next(x for x in hypotheses.status(owner,vacancy)["hypotheses"] if x["hypothesis_id"]=='H10')
    assert after["status"]=='unclear' and after["batch_status"]=='invalidated' and after["decisive"]==0
