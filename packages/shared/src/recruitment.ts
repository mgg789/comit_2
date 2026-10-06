/** State of one employer/candidate process, visible to both participants. */
export type RecruitmentState = "sent" | "viewed" | "accepted" | "declined" | "rejected" | "offer_sent" | "hired" | "offer_declined" | "withdrawn";

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
  company_name: string;
  description: string;
  salary?: RecruitmentSalary;
}

/** Employer-only shortlist rejection; it creates no candidate-visible invitation. */
export interface RejectSavedRequest { candidate_id: string; idempotency_key: string; reason: string }

/** Auditable reason and actor recorded for a process-closing decision. */
export interface RecruitmentClosure { reason: string; actor: "candidate" | "employer"; at: string }

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
  | { action: "decline" | "withdraw" | "reject"; reason: string }
  | { action: "interview_propose"; kind: InterviewKind; starts_at: string; time_zone: string; duration_minutes: number; format: "remote" | "onsite"; location: string }
  | { action: "interview_accept"; interview_id: string }
  | { action: "interview_decline" | "interview_cancel"; interview_id: string; reason: string }
  | { action: "interview_complete"; interview_id: string; outcome: InterviewOutcome; feedback: string }
  | { action: "offer_send"; salary_rub: number; description: string; expires_at: string }
  | { action: "offer_accept"; offer_id: string }
  | { action: "offer_decline"; offer_id: string; reason: string }
);

/** Contact-free process snapshot; chat remains unavailable in this lifecycle increment. */
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
  chat_available: false;
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
export interface RecruitmentPage { items: RecruitmentView[]; limit: number; offset: number; has_more: boolean }

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
