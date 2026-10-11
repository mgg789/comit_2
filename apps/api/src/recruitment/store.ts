/** Transactional hiring lifecycle; API owns decisions, matching owns the historical saved set. */
import { createHash, createHmac, randomUUID, timingSafeEqual } from "node:crypto";
import type { Pool, PoolClient, QueryResultRow } from "pg";
import type {
  AccountRole, CandidateProfileFields, InvitationTerms, InviteRequest, InterviewView,
  OfferView, RecruitmentAction, ContactsView, RecruitmentEvent, RecruitmentEventPage, RejectSavedRequest,
  RecruitmentPage, RecruitmentState, RecruitmentView,
} from "@comit/shared";
import { addWorkingDays, HIRING_TIME_ZONE, rejectionReasonCategories } from "@comit/shared";
import type { RecruitmentDeadline, RejectionReasonCategory } from "@comit/shared";
import { applyRecruitmentHire } from "../profiles/store.js";
import type { SystemNotificationsStore } from "../notifications/system-store.js";
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
  created_at: Date; updated_at: Date; deadline: RecruitmentDeadline | null;
  deadline_suspended: boolean; vacancy_open?: boolean | null;
  employer_contacted_at: Date | null; reminder_sent_at: Date | null;
}
interface ProfileSourceRow extends QueryResultRow {
  revision: string; fields: CandidateProfileFields; processing_consent: boolean; pool_consent: boolean;
}
interface ProfileRow extends ProfileSourceRow {
  email: string; email_verified: boolean; role: AccountRole;
}
const terminal = new Set<RecruitmentState>(["declined", "rejected", "hired", "offer_declined", "withdrawn", "expired"]);
const exitActions = new Set<RecruitmentAction["action"]>(["reject", "decline", "withdraw", "view", "interview_cancel", "interview_decline", "offer_decline"]);
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

/** Explicit policy is required before enabling clocks; unresolved product choices are not inferred. */
export interface RecruitmentPolicy {
  initialWorkingDays: 3 | 4;
  reminderWorkingDays: 3 | null;
  hireOn: "offer_accept" | "offer_send";
}

/** Reject vague employer feedback before any mutation, preserving full prose for participants only. */
export function validateRejectionFeedback(reason: string, categories?: RejectionReasonCategory[]): void {
  if (typeof reason !== "string" || reason.trim().length < 80 || reason.trim().split(/\s+/u).length < 10) fail(422, "rejection_feedback_too_short");
  if (!categories?.length || categories.length > rejectionReasonCategories.length || new Set(categories).size !== categories.length
    || categories.some(category => !rejectionReasonCategories.includes(category))) fail(422, "rejection_categories_required");
}

/** Persist process-level CAS, actor-scoped receipts and immutable ordered events atomically. */
export class RecruitmentStore {
  public constructor(private readonly pool: Pool, private readonly chatConfigured = false,
    private readonly cursorSecret = process.env.AUTH_SECRET ?? process.env.JWT_SECRET ?? randomUUID(),
    private readonly vacancyWritable?: (owner: string, draftId: string, client: Pool | PoolClient) => Promise<boolean>,
    private readonly policy: RecruitmentPolicy = { initialWorkingDays: 4, reminderWorkingDays: 3, hireOn: "offer_accept" }, private readonly notifications?: SystemNotificationsStore) {}

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

  private async profile(client: PoolClient, candidateId: string, exclusive = false): Promise<ProfileRow> {
    // Profile mutation locks account before source profile; keep the same order.
    const account = (await client.query<{ email: string; email_verified: boolean; role: AccountRole }>(
      `SELECT email,email_verified,role FROM accounts WHERE id=$1 ${exclusive ? "FOR UPDATE" : "FOR SHARE"}`, [candidateId])).rows[0];
    if (!account || account.role !== "candidate" || !account.email_verified) return fail(409, "candidate_account_unavailable");
    const profile = (await client.query<ProfileSourceRow>(
      `SELECT revision,fields,processing_consent,pool_consent FROM candidate_profiles WHERE candidate_id=$1 ${exclusive ? "FOR UPDATE" : "FOR SHARE"}`, [candidateId])).rows[0];
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
    if (this.notifications) {
      const label: Record<string, string> = {
        "invitation.sent": "Работодатель отправил приглашение. Откройте предложение, чтобы ответить.",
        view: "Кандидат просмотрел приглашение.", accept: "Кандидат принял приглашение. Начался срок для следующего действия работодателя.",
        decline: "Кандидат отклонил приглашение.", withdraw: "Кандидат отозвал участие в предложении.",
        reject: "Работодатель отклонил кандидата. Подробная обратная связь доступна в предложении.",
        "shortlist.rejected": "Вы отклонили сохранённого кандидата с обратной связью.",
        "interview.propose": "Работодатель предложил собеседование. Подтвердите время в предложении.",
        "interview.accept": "Собеседование согласовано. Срок ожидания приостановлен до окончания встречи.",
        "interview.decline": "Кандидат отклонил предложенное время собеседования.",
        "interview.cancel": "Собеседование отменено. Возобновлён срок для следующего действия.",
        "interview.complete": "Работодатель записал результат собеседования.",
        "offer.send": "Работодатель отправил оффер. Откройте предложение, чтобы принять решение.",
        "offer.accept": "Кандидат принял оффер.", "offer.decline": "Кандидат отклонил оффер.",
        "offer.expired": "Срок ответа на оффер истёк.",
        "deadline.reminder": "Остался один рабочий день для следующего действия работодателя.",
        "deadline.expired": "Предложение автоматически отозвано: работодатель не выполнил действие в срок.",
        "deadline.suspended": "Срок найма приостановлен: вакансия или доступ к профилю сейчас недоступны.",
        "deadline.resumed": "Срок найма возобновлён. Семь рабочих дней для следующего действия работодателя.",
        "deadline.after_interview": "Время собеседования закончилось. У работодателя три рабочих дня для оффера или отказа.",
        "chat.first_employer_message": "Работодатель начал диалог. Семь рабочих дней для назначения собеседования или решения.",
      };
      const text = label[type];
      if (text) {
        await this.notifications.append(client, row.owner_id, `recruitment:${row.id}:${row.revision}`, type, text,
          { kind: "recruitment", id: row.id, path: `/chat/${row.id}` });
        if (row.invited_at) await this.notifications.append(client, row.candidate_id, `recruitment:${row.id}:${row.revision}`, type, text,
          { kind: "recruitment", id: row.id, path: `/offer/${row.id}` });
      }
    }
  }

  private async bump(client: PoolClient, row: ProcessRow, state: RecruitmentState, type: string): Promise<void> {
    const updated = (await client.query<ProcessRow>(
      "UPDATE recruitment_processes SET state=$2,revision=revision+1,updated_at=now() WHERE id=$1 RETURNING *", [row.id, state])).rows[0]!;
    Object.assign(row, updated);
    await this.event(client, row, type);
  }

  private async clock(client: PoolClient): Promise<Date> {
    return (await client.query<{ now: Date }>("SELECT clock_timestamp() AS now")).rows[0]!.now;
  }

  private deadline(stage: RecruitmentDeadline["stage"], anchor: Date, days: number): RecruitmentDeadline {
    return { stage, due_at: addWorkingDays(anchor, days).toISOString(), business_days: days,
      remind_at: stage === "initial" && this.policy?.reminderWorkingDays !== null && this.policy?.reminderWorkingDays !== undefined
        && this.policy.reminderWorkingDays < days ? addWorkingDays(anchor, this.policy.reminderWorkingDays).toISOString() : null,
      paused_until: null, time_zone: HIRING_TIME_ZONE };
  }

  private async setDeadline(client: PoolClient, row: ProcessRow, deadline: RecruitmentDeadline | null): Promise<void> {
    await client.query("UPDATE recruitment_processes SET deadline=$2,reminder_sent_at=NULL WHERE id=$1", [row.id, deadline]);
    row.deadline = deadline; row.reminder_sent_at = null;
  }

  private async hiringExpiry(client: PoolClient, row: ProcessRow): Promise<void> {
    if (!this.policy || row.state !== "accepted") return;
    // Unknown authority on a degraded read must not suspend, expire or otherwise mutate clocks.
    if (row.vacancy_open === null) return;
    row.vacancy_open ??= !this.vacancyWritable || await this.vacancyWritable(row.owner_id, row.draft_id, client);
    const consent = (await client.query<{ processing_consent: boolean }>("SELECT processing_consent FROM candidate_profiles WHERE candidate_id=$1", [row.candidate_id])).rows[0];
    if (!row.vacancy_open || !consent?.processing_consent) {
      if (!row.deadline_suspended) {
        await client.query("UPDATE recruitment_processes SET deadline_suspended=true,deadline=NULL,reminder_sent_at=NULL WHERE id=$1", [row.id]);
        row.deadline_suspended = true; row.deadline = null;
        await this.bump(client, row, row.state, "deadline.suspended");
      }
      return;
    }
    if (row.deadline_suspended) {
      await client.query("UPDATE recruitment_processes SET deadline_suspended=false WHERE id=$1", [row.id]);
      row.deadline_suspended = false;
      await this.setDeadline(client, row, this.deadline("conversation", await this.clock(client), 7));
      await this.bump(client, row, row.state, "deadline.resumed");
    }
    const now = await this.clock(client);
    if (!row.deadline) {
      // Legacy rows receive a full grace period at first policy reconciliation, never updated_at.
      const meeting = (await client.query<{ starts_at: Date; duration_minutes: number; state: string }>(`SELECT starts_at,duration_minutes,state FROM recruitment_interviews
        WHERE process_id=$1 AND state IN ('proposed','scheduled','completed') ORDER BY (state='proposed'),starts_at DESC LIMIT 1`, [row.id])).rows[0];
      if (meeting && meeting.state !== "proposed") {
        const end = new Date(meeting.starts_at.getTime() + meeting.duration_minutes * 60_000);
        await this.setDeadline(client, row, end > now
          ? { stage: "interview_wait", due_at: null, remind_at: null, paused_until: end.toISOString(), business_days: 0, time_zone: HIRING_TIME_ZONE }
          : this.deadline("after_interview", now, 3));
      } else {
        const contacted = row.employer_contacted_at || meeting || (await client.query(`SELECT 1 FROM recruitment_events
          WHERE process_id=$1 AND type IN ('chat.first_employer_message','interview.propose') LIMIT 1`, [row.id])).rowCount;
        await this.setDeadline(client, row, this.deadline(contacted ? "conversation" : "initial", now, contacted ? 7 : this.policy.initialWorkingDays));
      }
    }
    let deadline = row.deadline!;
    if (deadline.stage === "interview_wait" && deadline.paused_until && Date.parse(deadline.paused_until) <= now.getTime()) {
      await this.setDeadline(client, row, this.deadline("after_interview", new Date(deadline.paused_until), 3));
      await this.bump(client, row, row.state, "deadline.after_interview");
      deadline = row.deadline!;
    }
    if (deadline.due_at && Date.parse(deadline.due_at) <= now.getTime()) {
      await client.query("UPDATE recruitment_processes SET closure=jsonb_build_object('reason','employer_action_deadline_elapsed','actor','system','at',$2::timestamptz),deadline=NULL WHERE id=$1", [row.id, now]);
      await client.query("UPDATE recruitment_interviews SET state='cancelled',feedback='process_deadline_elapsed' WHERE process_id=$1 AND state IN ('proposed','scheduled')", [row.id]);
      await this.bump(client, row, "expired", "deadline.expired");
    } else if (deadline.remind_at && !row.reminder_sent_at && Date.parse(deadline.remind_at) <= now.getTime()) {
      await client.query("UPDATE recruitment_processes SET reminder_sent_at=$2 WHERE id=$1", [row.id, now]);
      row.reminder_sent_at = now;
      await this.bump(client, row, row.state, "deadline.reminder");
    }
  }

  /** Sweep bounded active processes even when both browsers are closed; normal aggregate locks serialize races. */
  public async processDeadlines(limit = 100): Promise<number> {
    if (!this.policy) return 0;
    const rows = (await this.pool.query<{ id: string; owner_id: string }>(`SELECT id,owner_id FROM recruitment_processes
      WHERE (state='offer_sent' AND EXISTS(SELECT 1 FROM recruitment_offers o WHERE o.process_id=recruitment_processes.id AND o.state='sent' AND o.expires_at<=now())) OR (state='accepted' AND NOT deadline_suspended AND (deadline IS NULL OR
        (deadline->>'due_at')::timestamptz<=now() OR (deadline->>'paused_until')::timestamptz<=now()
        OR (reminder_sent_at IS NULL AND (deadline->>'remind_at')::timestamptz<=now())))
      ORDER BY updated_at,id LIMIT $1`, [limit])).rows;
    for (const row of rows) await this.get("employer", row.owner_id, row.id);
    return rows.length;
  }

  private async expire(client: PoolClient, row: ProcessRow): Promise<void> {
    await this.hiringExpiry(client, row);
    if (row.state !== "offer_sent") return;
    const result = await client.query("UPDATE recruitment_offers SET state='expired',resolved_at=clock_timestamp() WHERE process_id=$1 AND state='sent' AND expires_at<=clock_timestamp() RETURNING id", [row.id]);
    if (result.rowCount) {
      if (this.policy) await this.setDeadline(client, row, this.deadline("conversation", await this.clock(client), 7));
      await this.bump(client, row, "accepted", "offer.expired");
    }
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
    const vacancyOpen = row.vacancy_open !== undefined ? row.vacancy_open === true
      : (!this.vacancyWritable || await this.vacancyWritable(row.owner_id, row.draft_id, client));
    if (!vacancyOpen) actions = actions.filter(action => exitActions.has(action));
    return { id: row.id, owner_id: row.owner_id, candidate_id: row.candidate_id, vacancy_id: row.vacancy_id, draft_id: row.draft_id,
      profile_revision: Number(row.profile_revision), revision: Number(row.revision), state: row.state, terms: row.terms,
      interviews, offers: mappedOffers, allowed_actions: actions,
      chat_available: this.chatConfigured && row.invited_at !== null && !!consent?.processing_consent,
      chat_writable: this.chatConfigured && row.invited_at !== null && !!consent?.processing_consent && !terminal.has(row.state) && vacancyOpen,
      invited_at: row.invited_at === null ? null : timestamp(row.invited_at), closure: row.closure,
      deadline: vacancyOpen && !!consent?.processing_consent ? row.deadline : null, created_at: timestamp(row.created_at), updated_at: timestamp(row.updated_at) };
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
    validateRejectionFeedback(request.reason, request.categories);
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
        VALUES($1,$2,$3,$4,$5,$6,'rejected',NULL,jsonb_build_object('reason',$7::text,'actor','employer','at',now(),'categories',$8::jsonb))
        ON CONFLICT(owner_id,vacancy_id,candidate_id) DO NOTHING RETURNING *`,
        [randomUUID(), ownerId, request.candidate_id, vacancyId, draftId, profileRevision, request.reason, JSON.stringify(request.categories ?? [])])).rows[0];
      if (!row) return fail(409, "recruitment_already_exists");
      await this.event(client, row, "shortlist.rejected");
      const result = await this.view(client, "employer", row);
      await this.saveReceipt(client, ownerId, request.idempotency_key, fingerprint, result);
      return result;
    });
  }

  /** Fetch each unique source once outside transactions; one failed batch opens the read-only circuit. */
  private async readAuthorities(rows: Array<{ owner_id: string; draft_id: string }>): Promise<Map<string, boolean | null>> {
    const unique = [...new Map(rows.map(row => [row.draft_id, row])).values()];
    const result = new Map<string, boolean | null>();
    let failed = false;
    const until = Date.now() + 10_000;
    for (let index = 0; index < unique.length; index += 4) {
      await Promise.all(unique.slice(index, index + 4).map(async row => {
        if (failed || Date.now() >= until) { result.set(row.draft_id, null); return; }
        try { result.set(row.draft_id, !this.vacancyWritable || await this.vacancyWritable(row.owner_id, row.draft_id, this.pool)); }
        catch { failed = true; result.set(row.draft_id, null); }
      }));
    }
    return result;
  }

  private async readAggregate(role: AccountRole, actorId: string, id: string, authority: boolean | null, checkedAt: Date): Promise<RecruitmentView> {
    return transaction(this.pool, async client => {
      const row = await this.process(client, role, actorId, id);
      // A local close/withdraw after the remote probe wins under the same lifecycle lock.
      const changed = (await client.query(`SELECT 1 FROM vacancy_lifecycle_commands WHERE draft_id=$1
        AND (phase IN ('queued','source_applied') OR (phase='completed' AND updated_at >= $2)) LIMIT 1`, [row.draft_id, checkedAt])).rowCount;
      row.vacancy_open = changed ? false : authority;
      await this.expire(client, row);
      return this.view(client, role, row);
    });
  }

  /** Read a participant-owned aggregate without holding a database connection during source I/O. */
  public async get(role: AccountRole, actorId: string, id: string): Promise<RecruitmentView> {
    const column = role === "employer" ? "owner_id" : "candidate_id";
    const source = (await this.pool.query<{ owner_id: string; draft_id: string; checked_at: Date }>(`SELECT owner_id,draft_id,clock_timestamp() AS checked_at
      FROM recruitment_processes WHERE id=$1 AND ${column}=$2 AND ($3::boolean OR invited_at IS NOT NULL)`, [id, actorId, role === "employer"])).rows[0];
    if (!source) return fail(404, "recruitment_not_found");
    const authorities = await this.readAuthorities([source]);
    return this.readAggregate(role, actorId, id, authorities.get(source.draft_id) ?? null, source.checked_at);
  }

  /** Page only the authenticated participant's processes; another owner's draft returns an empty page. */
  public async list(role: AccountRole, actorId: string, limit = 50, offset = 0, draftId?: string, cursor?: string): Promise<RecruitmentPage> {
    if (!Number.isSafeInteger(limit) || limit < 1 || limit > 100 || !Number.isSafeInteger(offset) || offset < 0) return fail(422, "invalid_pagination");
    const column = role === "employer" ? "owner_id" : "candidate_id";
    if (cursor && offset) return fail(409, "pagination_modes_conflict");
    const scope = this.cursorScope(role, actorId, draftId);
    const boundary = cursor ? this.decodeCursor(cursor, scope) : null;
    // Keep PostgreSQL's microseconds intact: a JavaScript Date loses candidates sharing a millisecond.
    const rows = (await this.pool.query<{ id: string; owner_id: string; draft_id: string; checked_at: Date; cursor_created: string }>(`SELECT id,owner_id,draft_id,clock_timestamp() AS checked_at,
      to_char(created_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"') AS cursor_created
      FROM recruitment_processes WHERE ${column}=$1
      AND ($2::uuid IS NULL OR draft_id=$2) AND ($5::boolean OR invited_at IS NOT NULL)
      AND ($6::timestamptz IS NULL OR (created_at,id) < ($6::timestamptz,$7::uuid))
      ORDER BY created_at DESC,id DESC LIMIT $3 OFFSET $4`, [actorId, draftId ?? null, limit + 1, offset, role === "employer", boundary?.created ?? null, boundary?.id ?? null])).rows;
    const items: RecruitmentView[] = [];
    const authorities = await this.readAuthorities(rows.slice(0, limit));
    for (const row of rows.slice(0, limit)) items.push(await this.readAggregate(role, actorId, row.id, authorities.get(row.draft_id) ?? null, row.checked_at));
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
    operation: (grant: { process_id: string; owner_id: string; candidate_id: string; actor_id: string }) => Promise<T>, messageActivity = false): Promise<T> {
    if (write) await this.get(role, actorId, id);
    return transaction(this.pool, async client => {
      const row = await this.process(client, role, actorId, id);
      await this.expire(client, row);
      if (!row.invited_at) return fail(404, "chat_not_found");
      await this.profile(client, row.candidate_id);
      if (write && terminal.has(row.state)) return fail(409, "chat_read_only");
      if (write && this.vacancyWritable && !await this.vacancyWritable(row.owner_id, row.draft_id, client)) return fail(409, "chat_read_only");
      const result = await operation({ process_id: row.id, owner_id: row.owner_id, candidate_id: row.candidate_id, actor_id: actorId });
      if (this.policy && write && messageActivity && role === "employer" && row.state === "accepted" && !row.employer_contacted_at) {
        const now = await this.clock(client);
        await client.query("UPDATE recruitment_processes SET employer_contacted_at=$2 WHERE id=$1", [row.id, now]);
        if (row.deadline?.stage !== "interview_wait" && row.deadline?.stage !== "after_interview") {
          await this.setDeadline(client, row, this.deadline("conversation", now, 7));
          await this.bump(client, row, row.state, "chat.first_employer_message");
        }
      }
      return result;
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

  private async applyHire(client: PoolClient, row: ProcessRow): Promise<boolean> {
    if (!row.terms) return fail(409, "invitation_terms_required");
    return applyRecruitmentHire(client, row.id, row.candidate_id, row.terms);
  }

  /** Apply a participant action once, enforcing role/state/CAS, limits and server-time deadlines. */
  public async act(role: AccountRole, actorId: string, id: string, command: RecruitmentAction): Promise<RecruitmentView> {
    if (command.action === "reject") validateRejectionFeedback(command.reason, command.categories);
    if (command.action === "interview_complete" && command.outcome === "failed") validateRejectionFeedback(command.feedback, command.categories);
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
      if (exitActions.has(command.action)) {
        try { row.vacancy_open = !this.vacancyWritable || await this.vacancyWritable(row.owner_id, row.draft_id, client); }
        catch { row.vacancy_open = null; }
      }
      await this.expire(client, row);
      if (Number(row.revision) !== command.expected_revision) return fail(409, "recruitment_revision_conflict");
      const view = await this.view(client, role, row);
      if (!view.allowed_actions.includes(command.action)) return fail(409, "recruitment_action_unavailable");
      if (!["view", "decline", "withdraw", "reject", "interview_cancel", "interview_decline", "offer_decline"].includes(command.action)) await this.profile(client, row.candidate_id, command.action === "offer_accept" || command.action === "offer_send");
      let state = row.state;
      let hireChanged = false;
      switch (command.action) {
        case "view": state = "viewed"; break;
        case "accept": state = "accepted";
          if (this.policy) await this.setDeadline(client, row, this.deadline("initial", await this.clock(client), this.policy.initialWorkingDays));
          break;
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
          if (this.policy && row.state === "accepted" && row.deadline?.stage !== "interview_wait") await this.setDeadline(client, row, this.deadline("conversation", await this.clock(client), 7));
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
            if (this.policy && row.state === "accepted") {
              const latest = (await client.query<{ ends_at: Date }>("SELECT max(starts_at+duration_minutes*interval '1 minute') AS ends_at FROM recruitment_interviews WHERE process_id=$1 AND state='scheduled'", [id])).rows[0]!;
              const end = latest.ends_at;
              await this.setDeadline(client, row, { stage: "interview_wait", due_at: null, remind_at: null,
                paused_until: end.toISOString(), business_days: 0, time_zone: HIRING_TIME_ZONE });
            }
          } else if (command.action === "interview_complete") {
            const begun = (await client.query<{ valid: boolean }>("SELECT $1::timestamptz<=clock_timestamp() AS valid", [meeting.starts_at])).rows[0]!.valid;
            if (!begun) return fail(409, "interview_not_started");
            await client.query("UPDATE recruitment_interviews SET state='completed',completed_at=now(),outcome=$2,feedback=$3,reason_categories=$4 WHERE id=$1", [meeting.id, command.outcome, command.feedback, command.categories ?? []]);
          } else {
            await client.query("UPDATE recruitment_interviews SET state=$2,feedback=$3 WHERE id=$1", [meeting.id, command.action === "interview_decline" ? "declined" : "cancelled", command.reason]);
            if (this.policy && row.state === "accepted") {
              const pending = (await client.query<{ ends_at: Date | null }>("SELECT max(starts_at+duration_minutes*interval '1 minute') AS ends_at FROM recruitment_interviews WHERE process_id=$1 AND state='scheduled'", [id])).rows[0]!;
              await this.setDeadline(client, row, pending.ends_at ? { stage: "interview_wait", due_at: null, remind_at: null,
                paused_until: pending.ends_at.toISOString(), business_days: 0, time_zone: HIRING_TIME_ZONE }
                : this.deadline("conversation", await this.clock(client), 7));
            }
          }
          break;
        }
        case "offer_send": {
          const future = (await client.query<{ valid: boolean }>("SELECT $1::timestamptz>clock_timestamp() AS valid", [command.expires_at])).rows[0]!.valid;
          if (!future) return fail(422, "offer_must_expire_in_future");
          await client.query("INSERT INTO recruitment_offers(id,process_id,state,salary_rub,description,expires_at) VALUES($1,$2,'sent',$3,$4,$5)", [randomUUID(), id, command.salary_rub, command.description, command.expires_at]);
          state = "offer_sent";
          await this.setDeadline(client, row, null);
          if (this.policy?.hireOn === "offer_send") hireChanged = await this.applyHire(client, row);
          break;
        }
        case "offer_accept": case "offer_decline": {
          const offer = view.offers.find(item => item.id === command.offer_id && item.state === "sent");
          if (!offer) return fail(409, "active_offer_not_found");
          const result = await client.query("UPDATE recruitment_offers SET state=$2,resolved_at=clock_timestamp() WHERE id=$1 AND expires_at>clock_timestamp() RETURNING id", [offer.id, command.action === "offer_accept" ? "accepted" : "declined"]);
          if (!result.rowCount) return fail(409, "offer_expired");
          state = command.action === "offer_accept" ? "hired" : "offer_declined";
          if (command.action === "offer_accept" && this.policy?.hireOn === "offer_accept") hireChanged = await this.applyHire(client, row);
          break;
        }
      }
      if (terminal.has(state)) {
        await this.setDeadline(client, row, null);
        if ("reason" in command) {
          await client.query("UPDATE recruitment_processes SET closure=jsonb_build_object('reason',$2::text,'actor',$3::text,'at',now(),'categories',$4::jsonb) WHERE id=$1", [id, command.reason, role, JSON.stringify('categories' in command ? command.categories ?? [] : [])]);
        }
        await client.query("UPDATE recruitment_interviews SET state='cancelled',feedback=COALESCE(feedback,'process_closed') WHERE process_id=$1 AND state IN ('proposed','scheduled')", [id]);
        // Rejected/withdrawn processes terminate any outstanding offer without accepting it.
        await client.query("UPDATE recruitment_offers SET state='declined',resolved_at=now() WHERE process_id=$1 AND state='sent'", [id]);
      }
      await this.bump(client, row, state, command.action.replaceAll("_", "."));
      if (hireChanged && this.notifications && row.terms) await this.notifications.append(client, row.candidate_id, `hired:${row.id}`, "candidate.hired",
        `Вы приняты на работу в ${row.terms.company_name}. Мы добавили этот пункт в опыт работы и отозвали резюме из пула.`,
        { kind: "candidate_profile", path: "/resume" });
      const result = await this.view(client, role, row);
      await this.saveReceipt(client, actorId, command.idempotency_key, fingerprint, result);
      return result;
    });
  }
}
