/** Transactional hiring lifecycle; API owns decisions, matching owns the historical saved set. */
import { createHash, createHmac, randomUUID, timingSafeEqual } from "node:crypto";
import type { Pool, PoolClient, QueryResultRow } from "pg";
import type {
  AccountRole, CandidateProfileFields, InvitationTerms, InviteRequest, InterviewView,
  OfferView, RecruitmentAction, ContactsView, RecruitmentEvent, RecruitmentEventPage, RejectSavedRequest,
  RecruitmentPage, RecruitmentState, RecruitmentView,
} from "@comit/shared";
import { timestamp, transaction } from "../auth/database.js";
import { canonicalJson } from "../profiles/projection.js";
import { lockVacancy } from "./vacancy-lifecycle.js";

/** Sanitized domain error; routes never return PostgreSQL errors or private payloads. */
export class RecruitmentError extends Error {
  public constructor(public readonly status: number, public readonly code: string) { super(code); }
}

interface ProcessRow extends QueryResultRow {
  id: string; owner_id: string; candidate_id: string; vacancy_id: string; draft_id: string;
  profile_revision: string; revision: string; state: RecruitmentState; terms: InvitationTerms | null;
  invited_at: Date | null; closure: RecruitmentView["closure"];
  created_at: Date; updated_at: Date;
}
interface ProfileSourceRow extends QueryResultRow {
  revision: string; fields: CandidateProfileFields; processing_consent: boolean; pool_consent: boolean;
}
interface ProfileRow extends ProfileSourceRow {
  email: string; email_verified: boolean; role: AccountRole;
}
const terminal = new Set<RecruitmentState>(["declined", "rejected", "hired", "offer_declined", "withdrawn"]);
const accepted = new Set<RecruitmentState>(["accepted", "offer_sent", "hired"]);
const fail = (status: number, code: string): never => { throw new RecruitmentError(status, code); };

/** Hash the scoped command, not upstream mutable data, so receipts survive source changes. */
export function recruitmentFingerprint(scope: string, payload: InviteRequest | RecruitmentAction | RejectSavedRequest): string {
  return createHash("sha256").update(canonicalJson({ scope, payload })).digest("hex");
}

/** Trusted snapshot granted by the owned matching saved exposure and published vacancy. */
export interface InvitationGrant {
  draft_id: string; vacancy_id: string; candidate_id: string; profile_revision: number;
  terms: InvitationTerms;
}

/** Persist process-level CAS, actor-scoped receipts and immutable ordered events atomically. */
export class RecruitmentStore {
  public constructor(private readonly pool: Pool, private readonly chatConfigured = false,
    private readonly cursorSecret = process.env.AUTH_SECRET ?? process.env.JWT_SECRET ?? randomUUID(),
    private readonly vacancyWritable?: (owner: string, draftId: string, client: PoolClient) => Promise<boolean>) {}

  private cursorScope(role: AccountRole, actorId: string, draftId?: string): string {
    return createHash("sha256").update(canonicalJson({ role, actorId, draftId: draftId ?? null })).digest("hex");
  }

  private encodeCursor(scope: string, created: string, id: string): string {
    const payload = Buffer.from(JSON.stringify({ v: 1, scope, created, id })).toString("base64url");
    return `${payload}.${createHmac("sha256", this.cursorSecret).update(payload).digest("hex")}`;
  }

  private decodeCursor(cursor: string, scope: string): { created: string; id: string } {
    if (cursor.length > 1024 || !/^[A-Za-z0-9_-]+\.[a-f0-9]{64}$/.test(cursor)) return fail(409, "invalid_cursor");
    const [payload, signature] = cursor.split(".");
    const expected = createHmac("sha256", this.cursorSecret).update(payload!).digest();
    if (!timingSafeEqual(expected, Buffer.from(signature!, "hex"))) return fail(409, "invalid_cursor");
    try {
      const parsed: unknown = JSON.parse(Buffer.from(payload!, "base64url").toString("utf8"));
      if (!parsed || typeof parsed !== "object") return fail(409, "invalid_cursor");
      const value = parsed as Record<string, unknown>;
      if (value.v !== 1 || value.scope !== scope || typeof value.id !== "string" || !/^[a-f0-9-]{36}$/i.test(value.id)
        || typeof value.created !== "string" || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$/.test(value.created)) return fail(409, "invalid_cursor");
      return { created: value.created, id: value.id };
    } catch { return fail(409, "invalid_cursor"); }
  }

  /** Read an already committed receipt before remote prechecks; conflicting reuse is rejected. */
  public async receipt(actorId: string, key: string, fingerprint: string): Promise<RecruitmentView | null> {
    return this.receiptFor(this.pool, actorId, key, fingerprint);
  }

  private async receiptFor(client: Pool | PoolClient, actorId: string, key: string, fingerprint: string): Promise<RecruitmentView | null> {
    const row = (await client.query<{ fingerprint: string; result: RecruitmentView }>(
      "SELECT fingerprint,result FROM recruitment_receipts WHERE actor_id=$1 AND idempotency_key=$2", [actorId, key])).rows[0];
    if (!row) return null;
    if (row.fingerprint !== fingerprint) fail(409, "idempotency_conflict");
    return row.result;
  }

  private async lockKey(client: PoolClient, actorId: string, key: string): Promise<void> {
    await client.query("SELECT pg_advisory_xact_lock(hashtextextended($1,0))", [`recruitment:${actorId}:${key}`]);
  }

  private async profile(client: PoolClient, candidateId: string): Promise<ProfileRow> {
    // Profile mutation locks account before source profile; keep the same order.
    const account = (await client.query<{ email: string; email_verified: boolean; role: AccountRole }>(
      "SELECT email,email_verified,role FROM accounts WHERE id=$1 FOR SHARE", [candidateId])).rows[0];
    if (!account || account.role !== "candidate" || !account.email_verified) return fail(409, "candidate_account_unavailable");
    const profile = (await client.query<ProfileSourceRow>(
      "SELECT revision,fields,processing_consent,pool_consent FROM candidate_profiles WHERE candidate_id=$1 FOR SHARE", [candidateId])).rows[0];
    if (!profile || !profile.processing_consent) return fail(409, "candidate_processing_consent_required");
    return { ...profile, ...account };
  }

  private async process(client: PoolClient, role: AccountRole, actorId: string, id: string): Promise<ProcessRow> {
    const column = role === "employer" ? "owner_id" : "candidate_id";
    const source = (await client.query<{ draft_id: string }>(`SELECT draft_id FROM recruitment_processes WHERE id=$1 AND ${column}=$2`, [id, actorId])).rows[0];
    if (!source) return fail(404, "recruitment_not_found");
    await lockVacancy(client, source.draft_id);
    const row = (await client.query<ProcessRow>(`SELECT * FROM recruitment_processes WHERE id=$1 AND ${column}=$2
      AND ($3::boolean OR invited_at IS NOT NULL) FOR UPDATE`, [id, actorId, role === "employer"])).rows[0];
    if (!row) return fail(404, "recruitment_not_found");
    return row;
  }

  private async event(client: PoolClient, row: ProcessRow, type: string): Promise<void> {
    // Serializes event insertion until commit: a polling cursor cannot skip a lower uncommitted sequence.
    await client.query("SELECT pg_advisory_xact_lock(hashtextextended('recruitment-event-order',0))");
    await client.query("INSERT INTO recruitment_events(event_id,process_id,revision,type,state) VALUES($1,$2,$3,$4,$5)",
      [randomUUID(), row.id, row.revision, type, row.state]);
  }

  private async bump(client: PoolClient, row: ProcessRow, state: RecruitmentState, type: string): Promise<void> {
    const updated = (await client.query<ProcessRow>(
      "UPDATE recruitment_processes SET state=$2,revision=revision+1,updated_at=now() WHERE id=$1 RETURNING *", [row.id, state])).rows[0]!;
    Object.assign(row, updated);
    await this.event(client, row, type);
  }

  private async expire(client: PoolClient, row: ProcessRow): Promise<void> {
    if (row.state !== "offer_sent") return;
    const result = await client.query("UPDATE recruitment_offers SET state='expired',resolved_at=clock_timestamp() WHERE process_id=$1 AND state='sent' AND expires_at<=clock_timestamp() RETURNING id", [row.id]);
    if (result.rowCount) await this.bump(client, row, "accepted", "offer.expired");
  }

  private async view(client: PoolClient, role: AccountRole, row: ProcessRow): Promise<RecruitmentView> {
    const meetings = (await client.query<InterviewView & QueryResultRow>(
      "SELECT id,kind,state,starts_at,time_zone,duration_minutes,format,location,completed_at,outcome,feedback FROM recruitment_interviews WHERE process_id=$1 ORDER BY created_at,id", [row.id])).rows;
    const offers = (await client.query<OfferView & QueryResultRow>(
      "SELECT id,state,salary_rub,description,expires_at,sent_at,resolved_at FROM recruitment_offers WHERE process_id=$1 ORDER BY sent_at,id", [row.id])).rows;
    const interviews = meetings.map(meeting => ({ ...meeting, starts_at: timestamp(meeting.starts_at),
      completed_at: meeting.completed_at === null ? null : timestamp(meeting.completed_at) }));
    const mappedOffers = offers.map(offer => ({ ...offer, salary_rub: Number(offer.salary_rub),
      expires_at: timestamp(offer.expires_at), sent_at: timestamp(offer.sent_at), resolved_at: offer.resolved_at === null ? null : timestamp(offer.resolved_at) }));
    let actions: RecruitmentAction["action"][] = [];
    if (!terminal.has(row.state)) {
      if (role === "employer") {
        actions = ["reject"];
        if (row.state === "accepted" || row.state === "offer_sent") {
          if (row.state === "accepted") actions.push("offer_send");
          if (["technical", "general"].some(kind => !interviews.some(item => item.kind === kind && ["proposed", "scheduled", "completed"].includes(item.state)))) actions.push("interview_propose");
          if (interviews.some(item => ["proposed", "scheduled"].includes(item.state))) actions.push("interview_cancel");
          if (interviews.some(item => item.state === "scheduled")) actions.push("interview_complete");
        }
      } else if (row.state === "sent" || row.state === "viewed") actions = ["accept", "decline", ...(row.state === "sent" ? ["view" as const] : [])];
      else {
        actions = ["withdraw"];
        if ((row.state === "accepted" || row.state === "offer_sent") && interviews.some(item => item.state === "proposed")) actions.push("interview_accept", "interview_decline");
        if (row.state === "offer_sent") actions.push("offer_accept", "offer_decline");
      }
    }
    // Consent withdrawal closes access to further progression, not the historical record.
    const consent = (await client.query<{ processing_consent: boolean }>("SELECT processing_consent FROM candidate_profiles WHERE candidate_id=$1", [row.candidate_id])).rows[0];
    if (!consent?.processing_consent) actions = actions.filter(action => ["reject", "decline", "withdraw", "view", "interview_cancel", "interview_decline", "offer_decline"].includes(action));
    const vacancyOpen = !this.vacancyWritable || await this.vacancyWritable(row.owner_id, row.draft_id, client);
    if (!vacancyOpen) actions = [];
    return { id: row.id, owner_id: row.owner_id, candidate_id: row.candidate_id, vacancy_id: row.vacancy_id, draft_id: row.draft_id,
      profile_revision: Number(row.profile_revision), revision: Number(row.revision), state: row.state, terms: row.terms,
      interviews, offers: mappedOffers, allowed_actions: actions,
      chat_available: this.chatConfigured && row.invited_at !== null && !!consent?.processing_consent,
      chat_writable: this.chatConfigured && row.invited_at !== null && !!consent?.processing_consent && !terminal.has(row.state) && vacancyOpen,
      invited_at: row.invited_at === null ? null : timestamp(row.invited_at), closure: row.closure,
      created_at: timestamp(row.created_at), updated_at: timestamp(row.updated_at) };
  }

  /** Create exactly one hiring process per saved owner/vacancy/candidate, preserving the granted terms. */
  public async invite(ownerId: string, request: InviteRequest, grant: InvitationGrant): Promise<RecruitmentView> {
    const fingerprint = recruitmentFingerprint(`invite:${grant.draft_id}`, request);
    return transaction(this.pool, async client => {
      await lockVacancy(client, grant.draft_id);
      if (this.vacancyWritable && !await this.vacancyWritable(ownerId, grant.draft_id, client)) return fail(409, "vacancy_not_published");
      await this.lockKey(client, ownerId, request.idempotency_key);
      const old = await this.receiptFor(client, ownerId, request.idempotency_key, fingerprint);
      if (old) return old;
      const owner = (await client.query<{ role: AccountRole }>("SELECT role FROM accounts WHERE id=$1 FOR SHARE", [ownerId])).rows[0];
      if (owner?.role !== "employer") return fail(403, "employer_role_required");
      if (grant.candidate_id !== request.candidate_id) return fail(409, "invitation_grant_mismatch");
      const source = await this.profile(client, request.candidate_id);
      if (!source.pool_consent) return fail(409, "candidate_pool_consent_required");
      const delivery = (await client.query<{ source_revision: string; projected_revision: string | null; state: string }>(
        "SELECT source_revision,projected_revision,state FROM candidate_profile_outbox WHERE candidate_id=$1 ORDER BY source_revision DESC LIMIT 1", [request.candidate_id])).rows[0];
      if (!delivery || delivery.state !== "delivered" || Number(delivery.source_revision) !== Number(source.revision)
        || Number(delivery.projected_revision) !== grant.profile_revision) return fail(409, "candidate_source_obsolete");
      const existing = (await client.query("SELECT id FROM recruitment_processes WHERE owner_id=$1 AND vacancy_id=$2 AND candidate_id=$3", [ownerId, grant.vacancy_id, request.candidate_id])).rows[0];
      if (existing) return fail(409, "recruitment_already_exists");
      // Pair lock also serializes different idempotency keys racing to create the same process.
      await client.query("SELECT pg_advisory_xact_lock(hashtextextended($1,0))", [`recruitment-pair:${ownerId}:${grant.vacancy_id}:${request.candidate_id}`]);
      const row = (await client.query<ProcessRow>(`INSERT INTO recruitment_processes
        (id,owner_id,candidate_id,vacancy_id,draft_id,profile_revision,state,terms,invited_at) VALUES($1,$2,$3,$4,$5,$6,'sent',$7,now()) ON CONFLICT(owner_id,vacancy_id,candidate_id) DO NOTHING RETURNING *`,
        [randomUUID(), ownerId, request.candidate_id, grant.vacancy_id, grant.draft_id, grant.profile_revision, grant.terms])).rows[0];
      if (!row) return fail(409, "recruitment_already_exists");
      await this.event(client, row, "invitation.sent");
      const result = await this.view(client, "employer", row);
      await this.saveReceipt(client, ownerId, request.idempotency_key, fingerprint, result);
      return result;
    });
  }

  private async saveReceipt(client: PoolClient, actorId: string, key: string, fingerprint: string, view: RecruitmentView): Promise<void> {
    await client.query("INSERT INTO recruitment_receipts(actor_id,idempotency_key,fingerprint,result) VALUES($1,$2,$3,$4)", [actorId, key, fingerprint, view]);
  }

  /** Reject a saved candidate before invitation without exposing an internal selection to the candidate. */
  public async rejectSaved(ownerId: string, draftId: string, vacancyId: string, profileRevision: number, request: RejectSavedRequest): Promise<RecruitmentView> {
    const fingerprint = recruitmentFingerprint(`reject-saved:${draftId}`, request);
    return transaction(this.pool, async client => {
      await lockVacancy(client, draftId);
      if (this.vacancyWritable && !await this.vacancyWritable(ownerId, draftId, client)) return fail(409, "vacancy_not_published");
      await this.lockKey(client, ownerId, request.idempotency_key);
      const prior = await this.receiptFor(client, ownerId, request.idempotency_key, fingerprint);
      if (prior) return prior;
      const rows = await client.query<{ id: string; role: AccountRole }>("SELECT id,role FROM accounts WHERE id=ANY($1::uuid[]) FOR SHARE", [[ownerId, request.candidate_id]]);
      if (!rows.rows.some(row => row.id === ownerId && row.role === "employer") || !rows.rows.some(row => row.id === request.candidate_id && row.role === "candidate")) return fail(409, "recruitment_accounts_unavailable");
      await client.query("SELECT pg_advisory_xact_lock(hashtextextended($1,0))", [`recruitment-pair:${ownerId}:${vacancyId}:${request.candidate_id}`]);
      const row = (await client.query<ProcessRow>(`INSERT INTO recruitment_processes
        (id,owner_id,candidate_id,vacancy_id,draft_id,profile_revision,state,terms,closure)
        VALUES($1,$2,$3,$4,$5,$6,'rejected',NULL,jsonb_build_object('reason',$7::text,'actor','employer','at',now()))
        ON CONFLICT(owner_id,vacancy_id,candidate_id) DO NOTHING RETURNING *`,
        [randomUUID(), ownerId, request.candidate_id, vacancyId, draftId, profileRevision, request.reason])).rows[0];
      if (!row) return fail(409, "recruitment_already_exists");
      await this.event(client, row, "shortlist.rejected");
      const result = await this.view(client, "employer", row);
      await this.saveReceipt(client, ownerId, request.idempotency_key, fingerprint, result);
      return result;
    });
  }

  /** Read one participant-owned aggregate; expired offers transition under the same aggregate lock. */
  public async get(role: AccountRole, actorId: string, id: string): Promise<RecruitmentView> {
    return transaction(this.pool, async client => {
      const row = await this.process(client, role, actorId, id);
      await this.expire(client, row);
      return this.view(client, role, row);
    });
  }

  /** Page only the authenticated participant's processes; another owner's draft returns an empty page. */
  public async list(role: AccountRole, actorId: string, limit = 50, offset = 0, draftId?: string, cursor?: string): Promise<RecruitmentPage> {
    if (!Number.isSafeInteger(limit) || limit < 1 || limit > 100 || !Number.isSafeInteger(offset) || offset < 0) return fail(422, "invalid_pagination");
    const column = role === "employer" ? "owner_id" : "candidate_id";
    if (cursor && offset) return fail(409, "pagination_modes_conflict");
    const scope = this.cursorScope(role, actorId, draftId);
    const boundary = cursor ? this.decodeCursor(cursor, scope) : null;
    // Keep PostgreSQL's microseconds intact: a JavaScript Date loses candidates sharing a millisecond.
    const rows = (await this.pool.query<{ id: string; cursor_created: string }>(`SELECT id,
      to_char(created_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"') AS cursor_created
      FROM recruitment_processes WHERE ${column}=$1
      AND ($2::uuid IS NULL OR draft_id=$2) AND ($5::boolean OR invited_at IS NOT NULL)
      AND ($6::timestamptz IS NULL OR (created_at,id) < ($6::timestamptz,$7::uuid))
      ORDER BY created_at DESC,id DESC LIMIT $3 OFFSET $4`, [actorId, draftId ?? null, limit + 1, offset, role === "employer", boundary?.created ?? null, boundary?.id ?? null])).rows;
    const items: RecruitmentView[] = [];
    for (const row of rows.slice(0, limit)) items.push(await this.get(role, actorId, row.id));
    const last = rows[Math.min(rows.length, limit) - 1];
    return { items, limit, offset, has_more: rows.length > limit,
      next_cursor: rows.length > limit && last ? this.encodeCursor(scope, last.cursor_created, last.id) : null };
  }

  /** Read live private contacts only after candidate acceptance and with current processing consent. */
  public async contacts(ownerId: string, id: string): Promise<ContactsView> {
    return transaction(this.pool, async client => {
      const row = await this.process(client, "employer", ownerId, id);
      await this.expire(client, row);
      if (!accepted.has(row.state)) return fail(403, "invitation_acceptance_required");
      const source = await this.profile(client, row.candidate_id);
      return { candidate_id: row.candidate_id, display_name: source.fields.display_name ?? "", email: source.email,
        phone: source.fields.phone ?? null, profile_revision: Number(source.revision) };
    });
  }

  /** Hold participant/process and consent locks through bounded chat I/O so closure or withdrawal cannot race delivery. */
  public async withChatGrant<T>(role: AccountRole, actorId: string, id: string, write: boolean,
    operation: (grant: { process_id: string; owner_id: string; candidate_id: string; actor_id: string }) => Promise<T>): Promise<T> {
    return transaction(this.pool, async client => {
      const row = await this.process(client, role, actorId, id);
      if (!row.invited_at) return fail(404, "chat_not_found");
      await this.profile(client, row.candidate_id);
      if (write && terminal.has(row.state)) return fail(409, "chat_read_only");
      if (write && this.vacancyWritable && !await this.vacancyWritable(row.owner_id, row.draft_id, client)) return fail(409, "chat_read_only");
      return operation({ process_id: row.id, owner_id: row.owner_id, candidate_id: row.candidate_id, actor_id: actorId });
    });
  }

  /** Publish ordered participant-scoped metadata for polling and future consumers without private contacts. */
  public async events(role: AccountRole, actorId: string, after = 0, limit = 50): Promise<RecruitmentEventPage> {
    if (!Number.isSafeInteger(after) || after < 0 || !Number.isSafeInteger(limit) || limit < 1 || limit > 100) return fail(422, "invalid_pagination");
    const column = role === "employer" ? "owner_id" : "candidate_id";
    const rows = (await this.pool.query<RecruitmentEvent & QueryResultRow>(`SELECT e.* FROM recruitment_events e
      JOIN recruitment_processes p ON p.id=e.process_id WHERE p.${column}=$1 AND e.sequence>$2
      AND ($4::boolean OR p.invited_at IS NOT NULL) ORDER BY e.sequence LIMIT $3`, [actorId, after, limit + 1, role === "employer"])).rows;
    const items = rows.slice(0, limit).map(row => ({ event_id: row.event_id, sequence: Number(row.sequence), process_id: row.process_id,
      revision: Number(row.revision), type: row.type, state: row.state, created_at: timestamp(row.created_at) }));
    return { items, next_sequence: items.at(-1)?.sequence ?? after, has_more: rows.length > limit };
  }

  /** Apply a participant action once, enforcing role/state/CAS, limits and server-time deadlines. */
  public async act(role: AccountRole, actorId: string, id: string, command: RecruitmentAction): Promise<RecruitmentView> {
    const fingerprint = recruitmentFingerprint(`action:${role}:${id}`, command);
    const committed = await this.receipt(actorId, command.idempotency_key, fingerprint);
    if (committed) return committed;
    // Commit deadline transitions independently: a rejected late command must not roll them back.
    // The mutation transaction still locks/rechecks everything, including expiry and the receipt.
    await this.get(role, actorId, id);
    return transaction(this.pool, async client => {
      await this.lockKey(client, actorId, command.idempotency_key);
      const previous = await this.receiptFor(client, actorId, command.idempotency_key, fingerprint);
      if (previous) return previous;
      const row = await this.process(client, role, actorId, id);
      await this.expire(client, row);
      if (Number(row.revision) !== command.expected_revision) return fail(409, "recruitment_revision_conflict");
      const view = await this.view(client, role, row);
      if (!view.allowed_actions.includes(command.action)) return fail(409, "recruitment_action_unavailable");
      if (!["view", "decline", "withdraw", "reject", "interview_cancel", "interview_decline", "offer_decline"].includes(command.action)) await this.profile(client, row.candidate_id);
      let state = row.state;
      switch (command.action) {
        case "view": state = "viewed"; break;
        case "accept": state = "accepted"; break;
        case "decline": state = "declined"; break;
        case "withdraw": state = "withdrawn"; break;
        case "reject": state = "rejected"; break;
        case "interview_propose": {
          try { new Intl.DateTimeFormat("en", { timeZone: command.time_zone }); } catch { return fail(422, "invalid_time_zone"); }
          if (command.format === "remote") {
            try { const url = new URL(command.location); if (!["https:", "http:"].includes(url.protocol) || url.username || url.password) return fail(422, "invalid_meeting_url"); }
            catch { return fail(422, "invalid_meeting_url"); }
          }
          const free = !view.interviews.some(item => item.kind === command.kind && ["proposed", "scheduled", "completed"].includes(item.state));
          if (!free) return fail(409, "interview_type_limit");
          const future = (await client.query<{ valid: boolean }>("SELECT $1::timestamptz>clock_timestamp() AS valid", [command.starts_at])).rows[0]!.valid;
          if (!future) return fail(422, "interview_must_be_future");
          await client.query(`INSERT INTO recruitment_interviews(id,process_id,kind,state,starts_at,time_zone,duration_minutes,format,location)
            VALUES($1,$2,$3,'proposed',$4,$5,$6,$7,$8)`, [randomUUID(), id, command.kind, command.starts_at, command.time_zone, command.duration_minutes, command.format, command.location]);
          break;
        }
        case "interview_accept": case "interview_decline": case "interview_cancel": case "interview_complete": {
          const meeting = view.interviews.find(item => item.id === command.interview_id);
          if (!meeting) return fail(404, "interview_not_found");
          const validStates = command.action === "interview_cancel" ? ["proposed", "scheduled"]
            : command.action === "interview_complete" ? ["scheduled"] : ["proposed"];
          if (!validStates.includes(meeting.state)) return fail(409, "interview_state_conflict");
          if (command.action === "interview_accept") {
            const future = (await client.query<{ valid: boolean }>("SELECT $1::timestamptz>clock_timestamp() AS valid", [meeting.starts_at])).rows[0]!.valid;
            if (!future) return fail(409, "interview_proposal_elapsed");
            await client.query("UPDATE recruitment_interviews SET state='scheduled' WHERE id=$1", [meeting.id]);
          } else if (command.action === "interview_complete") {
            const begun = (await client.query<{ valid: boolean }>("SELECT $1::timestamptz<=clock_timestamp() AS valid", [meeting.starts_at])).rows[0]!.valid;
            if (!begun) return fail(409, "interview_not_started");
            await client.query("UPDATE recruitment_interviews SET state='completed',completed_at=now(),outcome=$2,feedback=$3 WHERE id=$1", [meeting.id, command.outcome, command.feedback]);
          } else await client.query("UPDATE recruitment_interviews SET state=$2,feedback=$3 WHERE id=$1", [meeting.id, command.action === "interview_decline" ? "declined" : "cancelled", command.reason]);
          break;
        }
        case "offer_send": {
          const future = (await client.query<{ valid: boolean }>("SELECT $1::timestamptz>clock_timestamp() AS valid", [command.expires_at])).rows[0]!.valid;
          if (!future) return fail(422, "offer_must_expire_in_future");
          await client.query("INSERT INTO recruitment_offers(id,process_id,state,salary_rub,description,expires_at) VALUES($1,$2,'sent',$3,$4,$5)", [randomUUID(), id, command.salary_rub, command.description, command.expires_at]);
          state = "offer_sent";
          break;
        }
        case "offer_accept": case "offer_decline": {
          const offer = view.offers.find(item => item.id === command.offer_id && item.state === "sent");
          if (!offer) return fail(409, "active_offer_not_found");
          const result = await client.query("UPDATE recruitment_offers SET state=$2,resolved_at=clock_timestamp() WHERE id=$1 AND expires_at>clock_timestamp() RETURNING id", [offer.id, command.action === "offer_accept" ? "accepted" : "declined"]);
          if (!result.rowCount) return fail(409, "offer_expired");
          state = command.action === "offer_accept" ? "hired" : "offer_declined";
          break;
        }
      }
      if (terminal.has(state)) {
        if ("reason" in command) {
          await client.query("UPDATE recruitment_processes SET closure=jsonb_build_object('reason',$2::text,'actor',$3::text,'at',now()) WHERE id=$1", [id, command.reason, role]);
        }
        await client.query("UPDATE recruitment_interviews SET state='cancelled',feedback=COALESCE(feedback,'process_closed') WHERE process_id=$1 AND state IN ('proposed','scheduled')", [id]);
        // Rejected/withdrawn processes terminate any outstanding offer without accepting it.
        await client.query("UPDATE recruitment_offers SET state='declined',resolved_at=now() WHERE process_id=$1 AND state='sent'", [id]);
      }
      await this.bump(client, row, state, command.action.replaceAll("_", "."));
      const result = await this.view(client, role, row);
      await this.saveReceipt(client, actorId, command.idempotency_key, fingerprint, result);
      return result;
    });
  }
}
