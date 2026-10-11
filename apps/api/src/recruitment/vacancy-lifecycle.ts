/** Durable owner-scoped vacancy transitions across HTTP service boundaries. */
import type { Pool, PoolClient, QueryResultRow } from "pg";
import type { AgentsDraftView } from "@comit/shared";
import { transaction } from "../auth/database.js";
import { RecruitmentError } from "./store.js";

type Action = "withdraw" | "close" | "delete";
interface Command extends QueryResultRow {
  draft_id: string; owner_id: string; expected_revision: string; action: Action;
  phase: "queued" | "source_applied" | "completed" | "failed"; result: AgentsDraftView;
}
/** Serialize vacancy mutations and participant writes with the same API-owned lock. */
export async function lockVacancy(client: PoolClient, draftId: string): Promise<void> {
  await client.query("SELECT pg_advisory_xact_lock(hashtextextended($1,0))", [`vacancy-lifecycle:${draftId}`]);
}

/** Remote authority and durable retries; unavailable dependencies always fail closed. */
export class VacancyLifecycle {
  public constructor(private readonly pool: Pool, private readonly transport: typeof fetch = fetch) {}

  /** Prevent editorial publish/edit from overtaking a partially acknowledged lifecycle command. */
  public async withEditorial<T>(draftId: string, operation: () => Promise<T>): Promise<T> {
    return transaction(this.pool, async client => {
      await lockVacancy(client, draftId);
      const pending = await client.query("SELECT 1 FROM vacancy_lifecycle_commands WHERE draft_id=$1 AND phase IN ('queued','source_applied')", [draftId]);
      if (pending.rowCount) throw new RecruitmentError(409, "vacancy_lifecycle_pending");
      return operation();
    });
  }

  /** Gate fresh feed generation before a remote transition has acknowledged its status. */
  public async pending(draftId: string): Promise<boolean> {
    return !!(await this.pool.query("SELECT 1 FROM vacancy_lifecycle_commands WHERE draft_id=$1 AND phase IN ('queued','source_applied')", [draftId])).rowCount;
  }

  private async call(service: "agents" | "matching" | "chat", path: string, owner: string, method = "GET", body?: object): Promise<Response> {
    const names = { agents: ["AGENTS_SERVICE_URL", "AGENTS_SERVICE_TOKEN", "http://agents-service:8104"],
      matching: ["MATCHING_ENGINE_URL", "CANDIDATE_PIPELINE_TOKEN", "http://matching-engine:8101"],
      chat: ["CHAT_SERVICE_URL", "CHAT_SERVICE_TOKEN", ""] } as const;
    const [urlKey, tokenKey, fallback] = names[service];
    const token = process.env[tokenKey], url = process.env[urlKey] ?? fallback;
    if (!token || !url) throw new RecruitmentError(503, `${service}_unavailable`);
    return this.transport(new URL(path, url), { method, headers: { "x-owner-id": owner, "x-internal-token": token,
      ...(body ? { "content-type": "application/json" } : {}) }, ...(body ? { body: JSON.stringify(body) } : {}),
      signal: AbortSignal.timeout(10000), redirect: "error" });
  }

  /** Check source ownership and publication on every new write, including candidate and agent actions. */
  public async writable(owner: string, draftId: string, client: Pool | PoolClient): Promise<boolean> {
    const lifecycle = (await client.query<{ pending: boolean | null; inactive_at: Date | null }>(`SELECT
      bool_or(phase IN ('queued','source_applied')) AS pending,
      max(updated_at) FILTER (WHERE phase='completed') AS inactive_at
      FROM vacancy_lifecycle_commands WHERE draft_id=$1`, [draftId])).rows[0];
    if (lifecycle?.pending) return false;
    const response = await this.call("agents", `/internal/vacancy-drafts/${draftId}`, owner);
    if (response.status === 404) return false;
    if (!response.ok) throw new RecruitmentError(503, "vacancy_authority_unavailable");
    const draft = await response.json() as AgentsDraftView;
    return draft.owner_id === owner && draft.draft_id === draftId && !draft.archived && draft.business_status === "open"
      && (draft.publication?.status === "accepted" || !!draft.published_at &&
        (!lifecycle?.inactive_at || Date.parse(draft.published_at) > lifecycle.inactive_at.getTime()));
  }

  /** Persist a checked CAS command before any remote mutation, then resume its exact outcome on retries. */
  public async execute(owner: string, draftId: string, revision: number, action: Action): Promise<AgentsDraftView> {
    const outstanding = (await this.pool.query<Command>("SELECT * FROM vacancy_lifecycle_commands WHERE owner_id=$1 AND draft_id=$2 AND action=$3 AND phase IN ('queued','source_applied')", [owner, draftId, action])).rows[0];
    if (outstanding) return this.run(draftId, Number(outstanding.expected_revision), action, owner);
    await transaction(this.pool, async client => {
      await lockVacancy(client, draftId);
      const prior = (await client.query<Command>("SELECT * FROM vacancy_lifecycle_commands WHERE draft_id=$1 AND expected_revision=$2 AND action=$3 AND owner_id=$4", [draftId, revision, action, owner])).rows[0];
      if (prior && prior.phase !== "failed") return;
      const pending = await client.query("SELECT 1 FROM vacancy_lifecycle_commands WHERE draft_id=$1 AND phase IN ('queued','source_applied')", [draftId]);
      if (pending.rowCount) throw new RecruitmentError(409, "vacancy_lifecycle_pending");
      const response = await this.call("agents", `/internal/vacancy-drafts/${draftId}`, owner);
      if (!response.ok) throw new RecruitmentError(response.status === 404 ? 404 : 503, "owned_vacancy_unavailable");
      const draft = await response.json() as AgentsDraftView;
      if (draft.owner_id !== owner || draft.draft_id !== draftId) throw new RecruitmentError(404, "owned_vacancy_unavailable");
      if (draft.revision !== revision) throw new RecruitmentError(409, "draft_revision_conflict");
      if (draft.archived || action === "withdraw" && draft.business_status !== "open"
        || action === "close" && draft.business_status === "closed"
        || action === "delete" && !["draft", "closed"].includes(draft.business_status ?? "draft")) throw new RecruitmentError(409, "vacancy_action_unavailable");
      // Deletion retries need identifiers and a revision, never a retained copy of removed content.
      const snapshot: AgentsDraftView = action === "delete" ? {
        draft_id: draft.draft_id, vacancy_id: draft.vacancy_id, owner_id: draft.owner_id,
        revision: draft.revision, state: "editing", fields: {}, business_status: draft.business_status,
      } : draft;
      if (action === "delete") {
        await client.query("DELETE FROM vacancy_lifecycle_commands WHERE draft_id=$1 AND phase IN ('completed','failed')", [draftId]);
      }
      await client.query(`INSERT INTO vacancy_lifecycle_commands(draft_id,owner_id,expected_revision,action,result)
        VALUES($1,$2,$3,$4,$5) ON CONFLICT(draft_id,expected_revision,action) DO UPDATE SET phase='queued',result=excluded.result,updated_at=now()`,
        [draftId, owner, revision, action, snapshot]);
    });
    return this.run(draftId, revision, action, owner);
  }

  private async run(draftId: string, revision: number, action: Action, owner: string): Promise<AgentsDraftView> {
    return transaction(this.pool, async client => {
      await lockVacancy(client, draftId);
      const command = (await client.query<Command>("SELECT * FROM vacancy_lifecycle_commands WHERE draft_id=$1 AND expected_revision=$2 AND action=$3 AND owner_id=$4 FOR UPDATE", [draftId, revision, action, owner])).rows[0];
      if (!command || command.phase === "failed") throw new RecruitmentError(409, "vacancy_action_unavailable");
      if (command.phase === "completed") return command.result;
      let result = command.result;
      if (command.phase === "queued") {
        const probe = await this.call("agents", action === "delete" ? `/internal/vacancies/${draftId}/deletion`
          : `/internal/vacancies/${draftId}/lifecycle/${revision}/${action}`, owner);
        let applied = false;
        if (probe.ok) {
          if (action === "delete") {
            const receipt = await probe.json() as { source_revision: number };
            applied = receipt.source_revision === revision + 1;
            if (applied) result = { ...result, revision: revision + 1, archived: true };
          } else {
            result = await probe.json() as AgentsDraftView;
            applied = result.owner_id === owner && result.draft_id === draftId && result.revision === revision + 1;
          }
        } else if (probe.status !== 404) throw new RecruitmentError(503, "vacancy_lifecycle_pending");
        if (!applied) {
          const response = await this.call("agents", `/internal/vacancies/${draftId}${action === "delete" ? "" : `/${action}`}`, owner,
            action === "delete" ? "DELETE" : "POST", { expected_revision: revision });
          if (!response.ok) throw new RecruitmentError([404, 409, 422].includes(response.status) ? 409 : 503, [404, 409, 422].includes(response.status) ? "vacancy_lifecycle_conflict" : "vacancy_lifecycle_pending");
          result = await response.json() as AgentsDraftView;
        }
        // The source mutation remains authoritative even if this transaction subsequently rolls back.
        await client.query("UPDATE vacancy_lifecycle_commands SET phase='source_applied',result=$4,updated_at=now() WHERE draft_id=$1 AND expected_revision=$2 AND action=$3", [draftId, revision, action, result]);
      }
      const matching = await this.call("matching", `/internal/vacancies/${result.vacancy_id}${action === "delete" ? "" : "/deactivate"}`, owner,
        action === "delete" ? "DELETE" : "POST", { owner_id: owner, source_revision: result.revision,
          ...(action === "delete" ? {} : { status: action === "withdraw" ? "draft" : "closed" }) });
      if (!matching.ok) throw new RecruitmentError(503, "vacancy_lifecycle_pending");
      const processes = (await client.query<{ id: string; candidate_id: string }>("SELECT id,candidate_id FROM recruitment_processes WHERE owner_id=$1 AND draft_id=$2 AND invited_at IS NOT NULL ORDER BY id FOR UPDATE", [owner, draftId])).rows;
      if (action === "delete") {
        for (const process of processes) {
          const response = await this.call("chat", "/internal/chat/purge", owner, "POST", { process_id: process.id, owner_id: owner, candidate_id: process.candidate_id, actor_id: owner });
          if (!response.ok) throw new RecruitmentError(503, "vacancy_lifecycle_pending");
        }
        await client.query("DELETE FROM recruitment_receipts WHERE result->>'draft_id'=$1 AND result->>'owner_id'=$2", [draftId, owner]);
        await client.query("DELETE FROM recruitment_processes WHERE owner_id=$1 AND draft_id=$2", [owner, draftId]);
      } else {
        const cancelled = await client.query<{ process_id: string }>(`UPDATE recruitment_interviews SET state='cancelled',feedback='vacancy_inactive'
          WHERE process_id IN (SELECT id FROM recruitment_processes WHERE owner_id=$1 AND draft_id=$2)
          AND state IN ('proposed','scheduled') RETURNING process_id`, [owner, draftId]);
        for (const id of new Set(cancelled.rows.map(row => row.process_id))) {
          await client.query(`WITH updated AS (UPDATE recruitment_processes SET revision=revision+1,updated_at=now() WHERE id=$1 RETURNING *)
            INSERT INTO recruitment_events(event_id,process_id,revision,type,state) SELECT gen_random_uuid(),id,revision,'vacancy.inactive',state FROM updated`, [id]);
        }
      }
      if (action === "delete") {
        result = { draft_id: result.draft_id, vacancy_id: result.vacancy_id, owner_id: result.owner_id, revision: result.revision, state: "editing", fields: {}, archived: true, business_status: result.business_status };
        await client.query("DELETE FROM vacancy_lifecycle_commands WHERE draft_id=$1 AND NOT (expected_revision=$2 AND action=$3)", [draftId, revision, action]);
      }
      await client.query("UPDATE vacancy_lifecycle_commands SET phase='completed',result=$4,updated_at=now() WHERE draft_id=$1 AND expected_revision=$2 AND action=$3", [draftId, revision, action, result]);
      return result;
    }).catch(async (error: unknown) => {
      if (error instanceof RecruitmentError && error.code === "vacancy_lifecycle_conflict") {
        await this.pool.query("UPDATE vacancy_lifecycle_commands SET phase='failed',updated_at=now() WHERE draft_id=$1 AND expected_revision=$2 AND action=$3 AND phase='queued'", [draftId, revision, action]);
      }
      throw error;
    });
  }

  /** Resume a bounded pending batch; remote outages retain the durable command and block unsafe writes. */
  public async resume(): Promise<void> {
    const commands = (await this.pool.query<Command>("SELECT * FROM vacancy_lifecycle_commands WHERE phase IN ('queued','source_applied') ORDER BY created_at LIMIT 10")).rows;
    for (const command of commands) {
      try { await this.run(command.draft_id, Number(command.expected_revision), command.action, command.owner_id); } catch { /* Retry on the next bounded tick. */ }
    }
  }
}
