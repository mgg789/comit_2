"""Employer lifecycle endpoint and PostgreSQL replay-fence regressions."""
import os
from uuid import uuid4

import psycopg
import pytest
from agents_service.database import connect, migrate
from agents_service.main import create_app
from agents_service.store import Store
from fastapi.testclient import TestClient


def test_employer_deletion_route_auth_and_owner():
    """Private cleanup derives its owner from the authenticated internal header."""
    owner = uuid4()
    class Repository:
        def delete_employer(self, owner_id):
            assert owner_id == owner
            return {"owner_id": str(owner_id), "deleted": True}
    client = TestClient(create_app(store=Repository(), token="synthetic-secret"))
    path = f"/internal/employers/{owner}"
    assert client.delete(path).status_code == 401
    assert client.delete(path, headers={"x-internal-token": "synthetic-secret", "x-owner-id": str(uuid4())}).status_code == 403
    response = client.delete(path, headers={"x-internal-token": "synthetic-secret", "x-owner-id": str(owner)})
    assert response.status_code == 200
    assert response.json() == {"owner_id": str(owner), "deleted": True}


@pytest.mark.integration
def test_employer_cleanup_purges_jobs_chats_and_blocks_recreation():
    """A queued or leased job and delayed owner writes cannot survive deletion."""
    database_url = os.getenv("AGENTS_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("AGENTS_TEST_DATABASE_URL is not configured")
    migrate(database_url)
    owner, other, draft, vacancy, job = (uuid4() for _ in range(5))
    with connect(database_url) as connection:
        connection.execute("INSERT INTO agents_service.drafts(draft_id,vacancy_id,owner_id) VALUES (%s,%s,%s)", (draft,vacancy,owner))
        connection.execute("INSERT INTO agents_service.organization_chats(owner_id,chat_id) VALUES (%s,%s),(%s,%s)", (owner,uuid4(),other,uuid4()))
        connection.execute("INSERT INTO agents_service.jobs(job_id,kind,owner_id,draft_id,body_hash,payload,state,lease_token) VALUES (%s,'publication',%s,%s,'synthetic','{}','processing',%s)", (job,owner,draft,uuid4()))
    reservation = uuid4()
    with connect(database_url) as connection:
        connection.execute("INSERT INTO agents_service.organization_usage_reservations(reservation_id,owner_id,upper_bound_micro_usd,status) VALUES (%s,%s,1,'captured')", (reservation,owner))
        connection.execute("""INSERT INTO agents_service.organization_usage_ledger(ledger_id,reservation_id,owner_id,input_tokens,output_tokens,input_micro_usd,output_micro_usd,total_micro_usd,rate_config)
            VALUES (%s,%s,%s,1,1,1,1,2,'{}')""", (uuid4(),reservation,owner))
        connection.execute("INSERT INTO agents_service.organization_provider_calls(owner_id,request_hash,status) VALUES (%s,'synthetic','in_flight')", (owner,))
        connection.execute("INSERT INTO agents_service.organization_tool_receipts(owner_id,idempotency_key,request_hash,result) VALUES (%s,%s,%s,'{}')", (owner,str(uuid4()),'a'*64))
    store = Store(database_url)
    assert store.delete_employer(owner) == {"owner_id": str(owner), "deleted": True}
    assert store.delete_employer(owner)["deleted"]
    with connect(database_url) as connection:
        for table in ("drafts", "jobs", "organization_chats"):
            assert connection.execute(f"SELECT 1 FROM agents_service.{table} WHERE owner_id=%s", (owner,)).fetchone() is None
        assert connection.execute("SELECT 1 FROM agents_service.organization_chats WHERE owner_id=%s", (other,)).fetchone()
        assert connection.execute("SELECT 1 FROM agents_service.vacancy_tombstones WHERE vacancy_id=%s", (vacancy,)).fetchone()
    for statement, params in [
        ("INSERT INTO agents_service.organization_chats(owner_id,chat_id) VALUES (%s,%s)", (owner,uuid4())),
        ("INSERT INTO agents_service.drafts(draft_id,vacancy_id,owner_id) VALUES (%s,%s,%s)", (uuid4(),uuid4(),owner)),
    ]:
        with pytest.raises(psycopg.errors.CheckViolation, match="employer_deleted"), connect(database_url) as connection:
            connection.execute(statement, params)


@pytest.mark.integration
def test_employer_cleanup_removes_vacancy_agent_descendants_only_for_owner():
    """Real vacancy-agent sessions, turns and all child journals are owner-isolated."""
    database_url = os.getenv("AGENTS_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("AGENTS_TEST_DATABASE_URL is not configured")
    migrate(database_url)
    owner, other = uuid4(), uuid4()
    graphs = []
    with connect(database_url) as connection:
        for account in (owner, other):
            draft, vacancy, session, turn, message, interaction = (uuid4() for _ in range(6))
            graphs.append((draft, session, turn, message, interaction))
            connection.execute("INSERT INTO agents_service.drafts(draft_id,vacancy_id,owner_id) VALUES (%s,%s,%s)", (draft,vacancy,account))
            connection.execute("""INSERT INTO agents_service.agent_sessions(session_id,owner_id,draft_id,agent_key,agent_version,knowledge_version,knowledge_hash,definition_hash,draft_revision)
                VALUES (%s,%s,%s,'vacancy','1','1','synthetic','synthetic',1)""", (session,account,draft))
            connection.execute("""INSERT INTO agents_service.agent_turns(turn_id,session_id,owner_id,draft_id,sequence,user_message,expected_revision,draft_revision,idempotency_key)
                VALUES (%s,%s,%s,%s,1,'synthetic',1,1,'synthetic')""", (turn,session,account,draft))
            connection.execute("INSERT INTO agents_service.agent_messages(message_id,session_id,turn_id,role,content,sequence) VALUES (%s,%s,%s,'user','synthetic',1)", (message,session,turn))
            connection.execute("INSERT INTO agents_service.agent_interactions(interaction_id,session_id,turn_id,kind,prompt) VALUES (%s,%s,%s,'buttons','synthetic')", (interaction,session,turn))
            connection.execute("INSERT INTO agents_service.agent_tool_journal(turn_id,call_id,tool_name,status,draft_revision) VALUES (%s,'synthetic','synthetic','started',1)", (turn,))
    store = Store(database_url)
    assert store.delete_employer(owner)["deleted"] is True
    assert store.delete_employer(owner)["deleted"] is True
    with connect(database_url) as connection:
        for expected, graph in enumerate(graphs):
            draft, session, turn, message, interaction = graph
            for table, column, identity in [
                ("drafts", "draft_id", draft), ("agent_sessions", "session_id", session),
                ("agent_turns", "turn_id", turn), ("agent_messages", "message_id", message),
                ("agent_interactions", "interaction_id", interaction), ("agent_tool_journal", "turn_id", turn),
            ]:
                count = connection.execute(f"SELECT count(*) AS n FROM agents_service.{table} WHERE {column}=%s", (identity,)).fetchone()["n"]
                assert count == expected, table
