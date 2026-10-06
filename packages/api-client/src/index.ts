/**
 * Minimal typed HTTP client for Comit services.
 * All apps talk to api-core through this wrapper so auth headers and error
 * handling stay in one place.
 */
import type { AccountRole, AccountView, AuthenticatedSession, EmailCodeReceipt, EmailCodeVerification,
  CandidateProfileView, CandidateProfileMutation, CombineRequest, CombineStatus, SelectionPage, VacancyReady, FeedRequest, FeedPage, FeedDecisionRequest, FeedDecision, FeedImpressionRequest, FeedImpression, FeedListPage, SavedCard, VisitedCard, PreferenceStatus, PreferenceCommand, HypothesisStatus,
  InviteRequest, RejectSavedRequest, RecruitmentView, RecruitmentPage, RecruitmentAction, ContactsView, RecruitmentEventPage,
  ChatPage, ChatMessage, SendChatMessage, ChatFile, UploadChatFile, DownloadChatFile } from "@comit/shared";

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly url: string,
    public readonly body: unknown,
  ) {
    super(`API ${status} on ${url}`);
    this.name = "ApiError";
  }
}

export interface ApiClientOptions {
  baseUrl?: string;
  getAuthToken?: () => string | null;
}

export interface ApiClient {
  get<T>(path: string): Promise<T>;
  post<T>(path: string, body?: unknown): Promise<T>;
  /** Queue a role-bound code; the receipt never contains the OTP. */
  requestLoginCode(role: AccountRole, email: string): Promise<EmailCodeReceipt>;
  /** Verify a code, including account consent only for first registration. */
  verifyLoginCode(role: AccountRole, body: EmailCodeVerification): Promise<AuthenticatedSession>;
  /** Resolve the current account from the same-origin cookie or configured Bearer token. */
  getAccount(role: AccountRole): Promise<AccountView>;
  /** Revoke this session, or all account sessions when all is true. */
  logout(role: AccountRole, all?: boolean): Promise<{ ok: true }>;
  /** Read the private editable source profile, including proof and delivery states. */
  getCandidateProfile(): Promise<CandidateProfileView>;
  /** CAS update; replace discards omitted editable fields, while PATCH preserves them. */
  saveCandidateProfile(body: CandidateProfileMutation, replace?: boolean): Promise<CandidateProfileView>;
  /** Retry the current failed matching handoff without changing profile revision. */
  retryCandidateProjection(): Promise<CandidateProfileView>;
  /** Read readiness only for a draft owned by the employer account. */
  getVacancyReady(draftId: string): Promise<VacancyReady>;
  /** Queue main top-k selection for an owned published vacancy draft. */
  requestSelection(draftId: string, body: CombineRequest): Promise<CombineStatus>;
  /** Return the current contact-free selection page and public authorised PDF links. */
  getSelection(draftId: string, limit?: number, offset?: number): Promise<SelectionPage>;
  /** Open an owned vacancy portion from current rerank. */
  openFeed(draftId: string, body?: FeedRequest): Promise<FeedPage>;
  /** Continue a fixed portion; cursors survive own likes/dislikes. */
  getFeed(draftId: string, sessionId: string, cursor?: string, limit?: number): Promise<FeedPage>;
  /** Record a reaction using a UUID idempotency key. */
  decideFeed(draftId: string, body: FeedDecisionRequest): Promise<FeedDecision>;
  /** Acknowledge the visible card once; retries must reuse the same UUID key. */
  acknowledgeFeedImpression(draftId: string, body: FeedImpressionRequest): Promise<FeedImpression>;
  /** Read the saved membership scoped to this draft's vacancy. */
  getSaved(draftId: string, limit?: number, offset?: number): Promise<FeedListPage<SavedCard>>;
  /** Read retained visited history, including nonblocking updated entries. */
  getVisited(draftId: string, limit?: number, offset?: number): Promise<FeedListPage<VisitedCard>>;
  /** Read the current vacancy-local preference control for an owned draft. */
  getPreferenceStatus(draftId: string): Promise<PreferenceStatus>;
  /** Queue a preference fit/retry using a revision-checked idempotent command. */
  retryPreferences(draftId: string, body: PreferenceCommand): Promise<PreferenceStatus>;
  /** Disable and clear live preference activation using a revision-checked command. */
  resetPreferences(draftId: string, body: PreferenceCommand): Promise<PreferenceStatus>;
  /** Roll back the active live preference model using a revision-checked command. */
  rollbackPreferences(draftId: string, body: PreferenceCommand): Promise<PreferenceStatus>;
  /** Read bounded comparison evidence for the employer's owned vacancy draft. */
  getHypothesisStatus(draftId: string): Promise<HypothesisStatus>;
  /** Pause exploration with a revision-checked idempotent control command. */
  pauseHypotheses(draftId: string, body: PreferenceCommand): Promise<HypothesisStatus>;
  /** Resume exploration with a revision-checked idempotent control command. */
  resumeHypotheses(draftId: string, body: PreferenceCommand): Promise<HypothesisStatus>;
  /** Invalidate prior hypothesis evidence and reset its budget using a CAS command. */
  resetHypotheses(draftId: string, body: PreferenceCommand): Promise<HypothesisStatus>;
  /** Invite an eligible candidate using immutable vacancy terms and a UUID retry key. */
  inviteCandidate(draftId: string, body: InviteRequest): Promise<RecruitmentView>;
  /** Close a saved shortlist entry before inviting; this decision is employer-only. */
  rejectSavedCandidate(draftId: string, body: RejectSavedRequest): Promise<RecruitmentView>;
  /** Read all process states belonging to an owned vacancy draft. */
  getEmployerRecruitment(draftId: string, limit?: number, offset?: number): Promise<RecruitmentPage>;
  /** Read only processes addressed to the authenticated candidate. */
  getCandidateRecruitment(limit?: number, offset?: number): Promise<RecruitmentPage>;
  /** Read a contact-free process snapshot as an authorized participant. */
  getRecruitment(role: AccountRole, processId: string): Promise<RecruitmentView>;
  /** Apply a role-authorized CAS mutation; exact retry keys are safe to replay. */
  actOnRecruitment(role: AccountRole, processId: string, body: RecruitmentAction): Promise<RecruitmentView>;
  /** Read contacts through a process-specific employer permission check. */
  getRecruitmentContacts(processId: string): Promise<ContactsView>;
  /** Poll durable participant events after the last acknowledged sequence. */
  getRecruitmentEvents(role: AccountRole, afterSequence?: number, limit?: number): Promise<RecruitmentEventPage>;
  /** Poll ascending messages after the last processed cursor in a participant's invitation. */
  getChatMessages(role: AccountRole, processId: string, afterSequence?: number, limit?: number): Promise<ChatPage>;
  /** Send text and already uploaded files using a stable UUID retry key. */
  sendChatMessage(role: AccountRole, processId: string, body: SendChatMessage): Promise<ChatMessage>;
  /** Upload up to 8 MiB; publication occurs when the returned metadata is attached to a message. */
  uploadChatFile(role: AccountRole, processId: string, body: UploadChatFile): Promise<ChatFile>;
  /** Download an attachment published in this participant-owned invitation. */
  downloadChatFile(role: AccountRole, processId: string, fileId: string): Promise<DownloadChatFile>;
}

/** Create a same-origin API client; optional Bearer credentials override session cookies server-side. */
export function createApiClient({
  baseUrl = "/api",
  getAuthToken = () => null,
}: ApiClientOptions = {}): ApiClient {
  async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
    const headers: Record<string, string> = { "content-type": "application/json" };
    const token = getAuthToken();
    if (token) headers.authorization = `Bearer ${token}`;

    const response = await fetch(`${baseUrl}${path}`, {
      method,
      headers,
      credentials: "same-origin",
      body: body === undefined ? undefined : JSON.stringify(body),
    });

    const payload: unknown = await response.json().catch(() => null);
    if (!response.ok) throw new ApiError(response.status, path, payload);
    return payload as T;
  }

  return {
    get: (path) => request("GET", path),
    post: (path, body) => request("POST", path, body),
    requestLoginCode: (role, email) => request("POST", `/auth/${role}/code`, { email }),
    verifyLoginCode: (role, body) => request("POST", `/auth/${role}/verify`, body),
    getAccount: role => request("GET", `/auth/${role}/me`),
    logout: (role, all = false) => request("POST", `/auth/${role}/${all ? "logout-all" : "logout"}`),
    getCandidateProfile: () => request("GET", "/candidate/profile"),
    saveCandidateProfile: (body, replace = false) => request(replace ? "PUT" : "PATCH", "/candidate/profile", body),
    retryCandidateProjection: () => request("POST", "/candidate/profile/projection/retry", {}),
    getVacancyReady: draftId => request("GET", `/employer/vacancy-drafts/${encodeURIComponent(draftId)}/ready`),
    requestSelection: (draftId, body) => request("POST", `/employer/vacancy-drafts/${encodeURIComponent(draftId)}/combine`, body),
    getSelection: (draftId, limit = 50, offset = 0) => request("GET", `/employer/vacancy-drafts/${encodeURIComponent(draftId)}/selections?limit=${limit}&offset=${offset}`),
    openFeed: (draftId, body = {}) => request("POST", `/employer/vacancy-drafts/${encodeURIComponent(draftId)}/feed`, body),
    getFeed: (draftId, sessionId, cursor, limit = 50) => request("GET", `/employer/vacancy-drafts/${encodeURIComponent(draftId)}/feed/${encodeURIComponent(sessionId)}?limit=${limit}${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ""}`),
    decideFeed: (draftId, body) => request("POST", `/employer/vacancy-drafts/${encodeURIComponent(draftId)}/feed/decisions`, body),
    acknowledgeFeedImpression: (draftId, body) => request("POST", `/employer/vacancy-drafts/${encodeURIComponent(draftId)}/feed/impressions`, body),
    getSaved: (draftId, limit = 50, offset = 0) => request("GET", `/employer/vacancy-drafts/${encodeURIComponent(draftId)}/saved?limit=${limit}&offset=${offset}`),
    getVisited: (draftId, limit = 50, offset = 0) => request("GET", `/employer/vacancy-drafts/${encodeURIComponent(draftId)}/visited?limit=${limit}&offset=${offset}`),
    getPreferenceStatus: draftId => request("GET", `/employer/vacancy-drafts/${encodeURIComponent(draftId)}/preferences`),
    retryPreferences: (draftId, body) => request("POST", `/employer/vacancy-drafts/${encodeURIComponent(draftId)}/preferences/retry`, body),
    resetPreferences: (draftId, body) => request("POST", `/employer/vacancy-drafts/${encodeURIComponent(draftId)}/preferences/reset`, body),
    rollbackPreferences: (draftId, body) => request("POST", `/employer/vacancy-drafts/${encodeURIComponent(draftId)}/preferences/rollback`, body),
    getHypothesisStatus: draftId => request("GET", `/employer/vacancy-drafts/${encodeURIComponent(draftId)}/hypotheses`),
    pauseHypotheses: (draftId, body) => request("POST", `/employer/vacancy-drafts/${encodeURIComponent(draftId)}/hypotheses/pause`, body),
    resumeHypotheses: (draftId, body) => request("POST", `/employer/vacancy-drafts/${encodeURIComponent(draftId)}/hypotheses/resume`, body),
    resetHypotheses: (draftId, body) => request("POST", `/employer/vacancy-drafts/${encodeURIComponent(draftId)}/hypotheses/reset`, body),
    inviteCandidate: (draftId, body) => request("POST", `/employer/vacancy-drafts/${encodeURIComponent(draftId)}/invitations`, body),
    rejectSavedCandidate: (draftId, body) => request("POST", `/employer/vacancy-drafts/${encodeURIComponent(draftId)}/recruitment/reject`, body),
    getEmployerRecruitment: (draftId, limit = 50, offset = 0) => request("GET", `/employer/vacancy-drafts/${encodeURIComponent(draftId)}/recruitment?limit=${limit}&offset=${offset}`),
    getCandidateRecruitment: (limit = 50, offset = 0) => request("GET", `/candidate/recruitment?limit=${limit}&offset=${offset}`),
    getRecruitment: (role, processId) => request("GET", `/${role}/recruitment/${encodeURIComponent(processId)}`),
    actOnRecruitment: (role, processId, body) => request("POST", `/${role}/recruitment/${encodeURIComponent(processId)}/actions`, body),
    getRecruitmentContacts: processId => request("GET", `/employer/recruitment/${encodeURIComponent(processId)}/contacts`),
    getRecruitmentEvents: (role, afterSequence = 0, limit = 50) => request("GET", `/${role}/recruitment-events?after_sequence=${afterSequence}&limit=${limit}`),
    getChatMessages: (role, processId, afterSequence = 0, limit = 50) => request("GET", `/${role}/recruitment/${encodeURIComponent(processId)}/chat/messages?after_sequence=${afterSequence}&limit=${limit}`),
    sendChatMessage: (role, processId, body) => request("POST", `/${role}/recruitment/${encodeURIComponent(processId)}/chat/messages`, body),
    uploadChatFile: (role, processId, body) => request("POST", `/${role}/recruitment/${encodeURIComponent(processId)}/chat/files`, body),
    downloadChatFile: (role, processId, fileId) => request("GET", `/${role}/recruitment/${encodeURIComponent(processId)}/chat/files/${encodeURIComponent(fileId)}`),
  };
}
