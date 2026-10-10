import type { RejectionReasonCategory } from "./recommendations.js";

/** State of one employer/candidate process, visible to both participants. */
export type RecruitmentState = "sent" | "viewed" | "accepted" | "declined" | "rejected" | "offer_sent" | "hired" | "offer_declined" | "withdrawn" | "expired";

/** Interview category; each process may contain both categories. */
export type InterviewKind = "technical" | "general";

/** Lifecycle of a proposed interview. */
export type InterviewState = "proposed" | "scheduled" | "declined" | "cancelled" | "completed";

/** Employer's recorded result of an actually completed interview. */
export type InterviewOutcome = "passed" | "failed" | "neutral";

/** Salary interval in whole RUB; the upper bound must not be below the lower bound. */
export interface RecruitmentSalary { from_rub: number; to_rub: number }

/** Immutable invitation terms captured from the vacancy at invitation time. */
export interface InvitationTerms {
  company_name: string;
  description: string;
  salary: RecruitmentSalary;
  contact_method: "platform";
  vacancy_title: string | null;
  city: string;
  daily: string;
  work_type: string;
}

/** Employer command; omitted salary inherits the published vacancy's mandatory interval. */
export interface InviteRequest {
  candidate_id: string;
  idempotency_key: string;
  /** Compatibility only; server derives the saved account company and rejects mismatches. */
  company_name?: string;
  description: string;
  salary?: RecruitmentSalary;
}

/** Employer-only shortlist rejection; it creates no candidate-visible invitation. */
export interface RejectSavedRequest { candidate_id: string; idempotency_key: string; reason: string; categories?: RejectionReasonCategory[] }

/** Auditable reason and actor recorded for a process-closing decision. */
export interface RecruitmentClosure { reason: string; actor: "candidate" | "employer" | "system"; at: string; categories?: RejectionReasonCategory[] }

/** Interview schedule uses an absolute timestamp plus its IANA display time zone. */
export interface InterviewView {
  id: string;
  kind: InterviewKind;
  state: InterviewState;
  starts_at: string;
  time_zone: string;
  duration_minutes: number;
  format: "remote" | "onsite";
  location: string;
  completed_at: string | null;
  outcome: InterviewOutcome | null;
  feedback: string | null;
}

/** Concrete offer with whole-RUB compensation and absolute expiration timestamp. */
export interface OfferView {
  id: string;
  state: "sent" | "accepted" | "declined" | "expired";
  salary_rub: number;
  description: string;
  expires_at: string;
  sent_at: string;
  resolved_at: string | null;
}

/** CAS and idempotency metadata required for every process mutation. */
export interface RecruitmentActionIdentity { expected_revision: number; idempotency_key: string }

/** Strict command union; each operation accepts only its own fields. */
export type RecruitmentAction = RecruitmentActionIdentity & (
  | { action: "view" | "accept" }
  | { action: "decline" | "withdraw"; reason: string }
  | { action: "reject"; reason: string; categories?: RejectionReasonCategory[] }
  | { action: "interview_propose"; kind: InterviewKind; starts_at: string; time_zone: string; duration_minutes: number; format: "remote" | "onsite"; location: string }
  | { action: "interview_accept"; interview_id: string }
  | { action: "interview_decline" | "interview_cancel"; interview_id: string; reason: string }
  | { action: "interview_complete"; interview_id: string; outcome: InterviewOutcome; feedback: string; categories?: RejectionReasonCategory[] }
  | { action: "offer_send"; salary_rub: number; description: string; expires_at: string }
  | { action: "offer_accept"; offer_id: string }
  | { action: "offer_decline"; offer_id: string; reason: string }
);

/** Server-owned hiring stage clock; all durations use 24-hour weekdays in Moscow. */
export interface RecruitmentDeadline {
  stage: "initial" | "conversation" | "after_interview" | "interview_wait";
  due_at: string | null;
  remind_at: string | null;
  paused_until: string | null;
  business_days: number;
  time_zone: "Europe/Moscow";
}

/** Contact-free participant-scoped process snapshot with separately authorized chat access. */
export interface RecruitmentView {
  id: string;
  owner_id: string;
  candidate_id: string;
  vacancy_id: string;
  draft_id: string;
  profile_revision: number;
  revision: number;
  state: RecruitmentState;
  /** Null only for an internal rejection made before any invitation. */
  terms: InvitationTerms | null;
  invited_at: string | null;
  closure: RecruitmentClosure | null;
  interviews: InterviewView[];
  offers: OfferView[];
  allowed_actions: RecruitmentAction["action"][];
  /** An invited, consented process has a configured chat backend; individual calls may still return 503. */
  chat_available: boolean;
  /** Writing requires a live publication; historical chat remains readable when false. */
  chat_writable: boolean;
  /** Incoming unread messages for this caller; absent/null means unread status unavailable. */
  chat_unread_count?: number | null;
  /** Null before acceptance, while an offer awaits a reply, or after closure. */
  deadline?: RecruitmentDeadline | null;
  created_at: string;
  updated_at: string;
}

/** Contacts exposed only through an authorized process-specific employer endpoint. */
export interface ContactsView {
  candidate_id: string;
  display_name: string;
  email: string;
  phone: string | null;
  profile_revision: number;
}

/** Bounded participant-scoped recruitment list. */
export interface RecruitmentPage { items: RecruitmentView[]; limit: number; offset: number; has_more: boolean; next_cursor?: string | null; }

/** Immutable participant-scoped state notification; excludes contacts and message bodies. */
export interface RecruitmentEvent {
  event_id: string;
  sequence: number;
  process_id: string;
  revision: number;
  type: string;
  state: RecruitmentState;
  created_at: string;
}

/** Durable event cursor; next_sequence is the last returned sequence or the requested cursor. */
export interface RecruitmentEventPage { items: RecruitmentEvent[]; next_sequence: number; has_more: boolean }
