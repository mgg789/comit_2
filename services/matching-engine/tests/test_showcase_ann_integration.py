"""Synthetic PostgreSQL benchmark proves indexed retrieval and exact-filter recall."""
import json
from uuid import UUID

import pytest
from matching_engine.database import connect
from matching_engine.embeddings import MODEL_VERSION
from matching_engine.showcase_store import ShowcaseStore
from test_contracts import eligible_profile
from test_pipeline_integration import worker

pytestmark = pytest.mark.integration
pytest_plugins = ["test_pipeline_integration"]


def test_hnsw_plan_and_recall_against_exact_neighbors(store):
    """Exercise the actual indexed expression on a pool beyond the retrieval cap."""
    store.intake(UUID(int=1), eligible_profile())
    assert worker(store).process_once()
    query = json.dumps([1.0] + [0.0] * 639)
    with connect(store.database_url) as connection:
        connection.execute("""INSERT INTO candidate_pipeline.candidates
            SELECT (jsonb_populate_record(NULL::candidate_pipeline.candidates,
                to_jsonb(c)||jsonb_build_object('candidate_id',md5('ann-'||i)::uuid))).*
            FROM candidate_pipeline.candidates c CROSS JOIN generate_series(1,15000) i WHERE c.candidate_id=%s""", (UUID(int=1),))
        connection.execute("""INSERT INTO candidate_pipeline.pool
            SELECT (jsonb_populate_record(NULL::candidate_pipeline.pool,
                to_jsonb(p)||jsonb_build_object('candidate_id',md5('ann-'||i)::uuid,
                'semantic_model_version',%s::text,'semantic_vector',
                '['||cos(i*.0004)::text||','||sin(i*.0004)::text||','||repeat('0,',637)||'0]'))).*
            FROM candidate_pipeline.pool p CROSS JOIN generate_series(1,15000) i WHERE p.candidate_id=%s""", (MODEL_VERSION, UUID(int=1)))
        connection.execute("ANALYZE candidate_pipeline.pool")
        connection.execute("SET LOCAL hnsw.ef_search=400")
        ids = [row["candidate_id"] for row in connection.execute("SELECT candidate_id FROM candidate_pipeline.pool").fetchall()]
        # Production scopes this same preference to its bounded ANN statement.
        connection.execute("SET LOCAL enable_seqscan=off")
        plan = connection.execute("EXPLAIN (FORMAT JSON,ANALYZE) SELECT candidate_id FROM candidate_pipeline.pool ORDER BY semantic_vector <=> %s::vector LIMIT 400", (query,)).fetchone()["QUERY PLAN"]
        assert "candidate_pool_semantic_hnsw" in json.dumps(plan), plan
        approximate = connection.execute("SELECT candidate_id FROM candidate_pipeline.pool ORDER BY semantic_vector <=> %s::vector LIMIT 100", (query,)).fetchall()
        connection.execute("SET LOCAL enable_seqscan=on")
        exact = connection.execute("SELECT candidate_id FROM candidate_pipeline.pool WHERE candidate_id=ANY(%s) ORDER BY (semantic_vector <=> %s::vector)+0,candidate_id LIMIT 100", (ids, query)).fetchall()
        recall = len({r["candidate_id"] for r in approximate} & {r["candidate_id"] for r in exact}) / 100
        assert recall >= .95
        print(json.dumps({"fixture_rows": len(ids), "recall_at_100": recall,
                          "ann_400_execution_ms": plan[0]["Execution Time"]}))
        neighbors = ShowcaseStore._neighbors(connection, "specialization='backend'", [], json.loads(query))
        assert len(neighbors) == 200
