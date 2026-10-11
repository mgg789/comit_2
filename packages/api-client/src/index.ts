import type { TestingPreflight, TestingSpecialization, TestingStartRequest, TestingSession, TestingNextTask, TestingAnswerRequest, TestingAnswerReceipt, TestingSupervision, TestingMediaRequest, TestingResult } from "@comit/shared";
/**
 * Minimal typed HTTP client for Comit services.
 * All apps talk to api-core through this wrapper so auth headers and error
 * handling stay in one place.
 */
import type { CandidateTestingHistory, EmployerProfileView, EmployerProfileMutation, SystemChatPage, SystemChatReadRequest, SystemChatReadStatus, ChatUnreadStatus, CandidateRecommendationsResponse, PublicShowcaseQuery, PublicShowcasePage, PublicShowcaseVacancy, PublicShowcaseCandidate, AccountRole, AccountView, AuthenticatedSession, EmailCodeReceipt, EmailCodeVerification,
  AgentsOrganizationChat, AgentsOrganizationMessagePage, AgentsOrganizationMessageRequest,
  AgentsOrganizationMessagesClearRequest,
  AgentsOrganizationTurn, AgentsOrganizationTask, AgentsOrganizationTaskRequest, AgentsOrganizationBudget,
  AgentsOrganizationContext, AgentsOrganizationMemory, AgentsOrganizationMemoryPatch,
  AgentsDocumentUpload, AgentsDocumentView, AgentsDocumentText, AgentsDocumentDownload,
  AgentsPendingAnswer, AgentsPendingAnswerRequest, AgentsTaskAvailabilityRequest,
  EmployerStatisticsQuery, EmployerStatistics,
  CandidateProfileView, CandidateProfileMutation, CandidatePipelineStatus, CombineRequest, CombineStatus, RerankStatus, AgentsMatchingStatus, VacancyPipelineStatus, FeedRefillStatus, SelectionPage, VacancyReady, FeedRequest, FeedPage, FeedDecisionRequest, FeedDecision, FeedImpressionRequest, FeedImpression, FeedListPage, SavedCard, VisitedCard, PreferenceStatus, PreferenceCommand, HypothesisStatus,
  InviteRequest, RejectSavedRequest, RecruitmentView, RecruitmentPage, RecruitmentAction, ContactsView, RecruitmentEventPage,
  ChatPage, ChatMessage, ChatReadRequest, ChatReadStatus, ChatEvent, RecruitmentStreamEvent, EventStreamOptions, SendChatMessage, ChatFile, UploadChatFile, DownloadChatFile,
  AgentsJobView, AgentsCapabilityView, AgentsDraftView, AgentsDraftPage, AgentsDraftCreate, AgentsDraftPatch, AgentsCreateRequest, SearchRequest, SearchPage, PublicCandidateDetail, ShowcaseLikeRequest, ShowcaseLikeReceipt } from "@comit/shared";

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
  /** Inspect eligibility without creating an attempt. */
  testingPreflight(specialization: TestingSpecialization, mode?: "retake" | "upgrade"): Promise<TestingPreflight>;
  /** Create the first attempt only after explicit consent and Start. */
  startTestingSession(body: TestingStartRequest): Promise<TestingSession>;
  /** Repeated reads return the same unanswered question. */
  nextTestingTask(sessionId: string): Promise<TestingNextTask>;
  /** Submit one choice or a bounded typed answer. */
  answerTestingTask(sessionId: string, body: TestingAnswerRequest): Promise<TestingAnswerReceipt>;
  /** Read actual grade publication status and cooldown. */
  testingResult(sessionId: string): Promise<TestingResult>;
  /** Finish after recording uploads and final supervision have succeeded. */
  finishTestingSession(sessionId: string): Promise<TestingResult>;
  /** Persist locally computed supervision counters. */
  testingSupervision(sessionId: string, body: TestingSupervision): Promise<{ session_id: string; badge: string }>;
  /** Upload an idempotent consent-authorized recording chunk. */
  uploadTestingMedia(sessionId: string, body: TestingMediaRequest): Promise<{ stored: string; retention_days: number }>;
  /** Stop an attempt without publishing incomplete evidence. */
  abandonTestingSession(sessionId: string): Promise<TestingSession>;
  /** Read account-owned company settings, including initial revision zero. */
  getEmployerProfile(): Promise<EmployerProfileView>;
  /** Save company settings with revision CAS. */
  updateEmployerProfile(body: EmployerProfileMutation): Promise<EmployerProfileView>;
  /** Page the persistent read-only Comit system chat. */
  getSystemChat(role: AccountRole, afterSequence?: number, limit?: number): Promise<SystemChatPage>;
  /** Mark only delivered system notification sequences read. */
  markSystemChatRead(role: AccountRole, body: SystemChatReadRequest): Promise<SystemChatReadStatus>;
  /** Count unread items across every authorized hiring chat and the system inbox. */
  getChatUnread(role: AccountRole): Promise<ChatUnreadStatus>;
  /** Read the latest persisted candidate recommendation without starting provider work. */
  getRecommendationsLatest(): Promise<CandidateRecommendationsResponse>;
  get<T>(path: string): Promise<T>;
  post<T>(path: string, body?: unknown): Promise<T>;
  /** Resolve the sole global employer chat from the authenticated account. */
  getOrganizationChat(): Promise<AgentsOrganizationChat>;
  /** Page durable global messages after a server sequence, including background reports. */
  getOrganizationMessages(afterSequence?: number, limit?: number): Promise<AgentsOrganizationMessagePage>;
  /** Clear idle history using owner-confirmed context CAS; memory, vault and billing stay intact. */
  clearOrganizationMessages(body: AgentsOrganizationMessagesClearRequest): Promise<AgentsOrganizationContext>;
  /** Queue a model turn with a stable retry key; acceptance does not claim completion. */
  sendOrganizationMessage(body: AgentsOrganizationMessageRequest): Promise<AgentsOrganizationTurn>;
  /** Poll a queued turn under the employer session. */
  getOrganizationTurn(turnId: string): Promise<AgentsOrganizationTurn>;
  /** List explicit candidate-chat delegations for this employer. */
  getOrganizationTasks(): Promise<AgentsOrganizationTask[]>;
  /** Authorize a bounded background task through the employer session. */
  createOrganizationTask(body: AgentsOrganizationTaskRequest): Promise<AgentsOrganizationTask>;
  /** Revoke a candidate-chat delegation; subsequent sends must recheck the stopped state. */
  stopOrganizationTask(taskId: string): Promise<AgentsOrganizationTask>;
  /** Resume paused negotiation using employer-confirmed intervals. */
  setOrganizationTaskAvailability(taskId: string, body: AgentsTaskAvailabilityRequest): Promise<AgentsOrganizationTask>;
  /** Read unanswered durable questions from background conversations. */
  getOrganizationPendingAnswers(): Promise<AgentsPendingAnswer[]>;
  /** Record a question answer through the owner session. */
  answerOrganizationQuestion(answerId: string, body: AgentsPendingAnswerRequest): Promise<AgentsPendingAnswer>;
  /** Heartbeat actual platform presence; provider offline is a separate state. */
  setOrganizationPresence(present: boolean): Promise<AgentsOrganizationChat>;
  /** Read rolling seven-day actual spend and unresolved reservations in micro-USD. */
  getOrganizationBudget(): Promise<AgentsOrganizationBudget>;
  /** Read compact current task state without replacing durable history. */
  getOrganizationContext(): Promise<AgentsOrganizationContext>;
  /** Read general employer memory kept separately from current task state. */
  getOrganizationMemory(): Promise<AgentsOrganizationMemory>;
  /** Update memory with the revision read by the caller. */
  patchOrganizationMemory(body: AgentsOrganizationMemoryPatch): Promise<AgentsOrganizationMemory>;
  /** Store one private UTF-8 TXT/MD or unencrypted PDF attachment. */
  uploadOrganizationDocument(body: AgentsDocumentUpload): Promise<AgentsDocumentView>;
  /** Read a bounded page of extracted private document text. */
  readOrganizationDocument(documentId: string, offset?: number, limit?: number): Promise<AgentsDocumentText>;
  /** Download original bytes only through the owner-authenticated gateway. */
  downloadOrganizationDocument(documentId: string): Promise<AgentsDocumentDownload>;
  /** Read own aggregate charts over an inclusive UTC period; optional vacancy_id narrows the dashboard. */
  getEmployerStatistics(query?: EmployerStatisticsQuery): Promise<EmployerStatistics>;
  /** Request a role-bound code; explicit development delivery may return dev_code. */
  requestLoginCode(role: AccountRole, email: string): Promise<EmailCodeReceipt>;
  /** Verify a code, including account consent only for first registration. */
  verifyLoginCode(role: AccountRole, body: EmailCodeVerification): Promise<AuthenticatedSession>;
  /** Resolve the current account from the same-origin cookie or configured Bearer token. */
  getAccount(role: AccountRole): Promise<AccountView>;
  /** Revoke this session, or all account sessions when all is true. */
  logout(role: AccountRole, all?: boolean): Promise<{ ok: true }>;
  /** Read the private editable source profile, including proof and delivery states. */
  getCandidateProfile(): Promise<CandidateProfileView>;
  /** Read the last 30 Moscow calendar days of own confirmed test results. */
  getCandidateTestingHistory(): Promise<CandidateTestingHistory>;
  /** Read actual matching readiness for the authenticated candidate. */
  getCandidatePipeline(): Promise<CandidatePipelineStatus>;
  /** CAS update; replace discards omitted editable fields, while PATCH preserves them. */
  saveCandidateProfile(body: CandidateProfileMutation, replace?: boolean): Promise<CandidateProfileView>;
  /** Retry the current failed matching handoff without changing profile revision. */
  retryCandidateProjection(): Promise<CandidateProfileView>;
  /** Read readiness only for a draft owned by the employer account. */
  getVacancyReady(draftId: string): Promise<VacancyReady>;
  /** Browse own editorial drafts; only created/accepted drafts with getVacancyReady success can receive a like. */
  getVacancyDrafts(limit?: number, offset?: number): Promise<AgentsDraftView[]>;
  /** Read optional AI availability and the explicit manual replacement. */
  getAgentCapabilities(): Promise<AgentsCapabilityView>;
  /** Cursor-page authoritative employer vacancies; offset remains available for existing clients. */
  listVacancies(limit?: number, cursor?: string, offset?: number): Promise<AgentsDraftPage>;
  /** Create an editable source draft without approving or publishing it. */
  createVacancy(body: AgentsDraftCreate): Promise<AgentsDraftView>;
  /** Queue cached requirement suggestions for the exact owned draft revision. */
  enrichVacancy(draftId: string, expectedRevision: number): Promise<AgentsJobView>;
  /** Read the state of an owner-scoped durable agent job. */
  getAgentJob(jobId: string): Promise<AgentsJobView>;
  /** Retry an owner-scoped failed job using the server retry policy. */
  retryAgentJob(jobId: string): Promise<AgentsJobView>;
  /** Read a managed vacancy, including archived audit status. */
  getVacancy(draftId: string): Promise<AgentsDraftView>;
  /** Stage a revision-checked edit; publishing requires a new approved summary. */
  patchVacancy(draftId: string, body: AgentsDraftPatch): Promise<AgentsDraftView>;
  /** Validate the exact editorial revision into a summary. */
  approveVacancy(draftId: string, expectedRevision: number): Promise<AgentsDraftView>;
  /** Confirm the exact approved summary and queue its matching snapshot. */
  publishVacancy(draftId: string, body: AgentsCreateRequest): Promise<AgentsDraftView>;
  /** Close and durably propagate a higher closed source revision. */
  closeVacancy(draftId: string, expectedRevision: number): Promise<AgentsDraftView>;
  /** Withdraw publication while preserving the historical shortlist and read-only chats. */
  withdrawVacancy(draftId: string, expectedRevision: number): Promise<AgentsDraftView>;
  /** Permanently delete a draft or closed vacancy and its scoped hiring/chat records. */
  archiveVacancy(draftId: string, expectedRevision: number): Promise<AgentsDraftView>;
  /** Discover public candidates without requiring any vacancy. */
  searchCandidates(body?: SearchRequest): Promise<SearchPage>;
  /** Browse anonymous published vacancies without requiring a session. */
  listPublicVacancies(query?: PublicShowcaseQuery): Promise<PublicShowcasePage<PublicShowcaseVacancy>>;
  /** Browse consented anonymous candidates without detail or contact access. */
  listPublicCandidates(query?: PublicShowcaseQuery): Promise<PublicShowcasePage<PublicShowcaseCandidate>>;
  /** Read the current anonymous public profile and authorized public PDF URL. */
  getPublicCandidate(candidateId: string): Promise<PublicCandidateDetail>;
  /** Save a displayed revision into an owned open vacancy using normal shortlist semantics. */
  likeShowcaseCandidate(draftId: string, body: ShowcaseLikeRequest): Promise<ShowcaseLikeReceipt>;
  /** Queue main top-k selection for an owned published vacancy draft. */
  requestSelection(draftId: string, body: CombineRequest): Promise<CombineStatus>;
  /** Read publication preparation and the actual background selection state. */
  getVacancyPipeline(draftId: string): Promise<AgentsMatchingStatus>;
  retryVacancyPreparation(draftId: string): Promise<VacancyPipelineStatus>;
  getSelectionStatus(draftId: string): Promise<CombineStatus>;
  retrySelection(draftId: string): Promise<CombineStatus>;
  getRerankStatus(draftId: string): Promise<RerankStatus>;
  retryRerank(draftId: string): Promise<RerankStatus>;
  /** Force one deduplicated retry of the current session's tail. */
  refillFeed(draftId: string, sessionId: string): Promise<FeedRefillStatus | null>;
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
  getSaved(draftId: string, limit?: number, offset?: number, cursor?: string): Promise<FeedListPage<SavedCard>>;
  /** Read retained visited history, including nonblocking updated entries. */
  getVisited(draftId: string, limit?: number, offset?: number, cursor?: string): Promise<FeedListPage<VisitedCard>>;
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
  getEmployerRecruitment(draftId: string, limit?: number, offset?: number, cursor?: string): Promise<RecruitmentPage>;
  /** Read only processes addressed to the authenticated candidate. */
  getCandidateRecruitment(limit?: number, offset?: number, cursor?: string): Promise<RecruitmentPage>;
  /** Cursor-list all processes belonging to the account role, without a selected vacancy. */
  getRecruitmentList(role: AccountRole, limit?: number, cursor?: string, offset?: number): Promise<RecruitmentPage>;
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
  /** Acknowledge only messages rendered while this conversation is visible. */
  acknowledgeChatRead(role: AccountRole, processId: string, body: ChatReadRequest): Promise<ChatReadStatus>;
  /** Consume one bounded chat SSE connection; persist event cursors and reconnect with the returned cursor. */
  streamChatEvents(role: AccountRole, processId: string, onEvent: (event: ChatEvent) => void | Promise<void>, options?: EventStreamOptions): Promise<number>;
  /** Consume contact-free recruitment notifications using the same account session and durable cursor. */
  streamRecruitmentEvents(role: AccountRole, onEvent: (event: RecruitmentStreamEvent) => void | Promise<void>, options?: EventStreamOptions): Promise<number>;
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
  async function stream<T extends { sequence: number; type: string }>(path: string, onEvent: (event: T) => void | Promise<void>, options: EventStreamOptions = {}): Promise<number> {
    let cursor = options.afterSequence ?? 0;
    if (!Number.isSafeInteger(cursor) || cursor < 0) throw new RangeError("invalid_stream_cursor");
    const headers: Record<string, string> = { accept: "text/event-stream" };
    const token = getAuthToken(); if (token) headers.authorization = `Bearer ${token}`;
    const response = await fetch(`${baseUrl}${path}?after_sequence=${cursor}`, { headers, credentials: "same-origin", ...(options.signal ? { signal: options.signal } : {}) });
    if (!response.ok) throw new ApiError(response.status, path, await response.json().catch(() => null));
    if (!response.headers.get("content-type")?.startsWith("text/event-stream") || !response.body) throw new ApiError(503, path, { detail: "invalid_event_stream" });
    const reader = response.body.getReader(), decoder = new TextDecoder();
    let pending = "";
    const frame = async (value: string): Promise<boolean> => {
      let eventName = "message", data = "";
      for (const line of value.split("\n")) {
        if (line.startsWith("event:")) eventName = line.slice(6).trim();
        if (line.startsWith("data:")) data += `${line.slice(5).trimStart()}\n`;
      }
      if (!data) return false;
      const parsed: unknown = JSON.parse(data);
      if (!parsed || typeof parsed !== "object") throw new ApiError(503, path, { detail: "invalid_event_stream" });
      const record = parsed as Record<string, unknown>;
      if (eventName === "error") throw new ApiError(typeof record.status === "number" ? record.status : 503, path, { detail: record.code, after_sequence: cursor });
      if (eventName === "reconnect") return true;
      const allowed = path.endsWith("/chat/events") ? ["message.created", "cursor"] : ["recruitment.changed", "cursor"];
      if (!allowed.includes(eventName) || record.type !== eventName
        || typeof record.sequence !== "number" || !Number.isSafeInteger(record.sequence) || record.sequence < cursor) throw new ApiError(503, path, { detail: "invalid_event_stream" });
      if (record.sequence > cursor) {
        if (eventName !== "cursor") await onEvent(parsed as T);
        else if (path.includes("/chat/events")) await onEvent(parsed as T);
        cursor = record.sequence;
      }
      return false;
    };
    try {
      while (true) {
        const part = await reader.read();
        if (part.done) break;
        pending = (pending + decoder.decode(part.value, { stream: true })).replace(/\r\n/g, "\n");
        let boundary: number;
        while ((boundary = pending.indexOf("\n\n")) >= 0) {
          const value = pending.slice(0, boundary); pending = pending.slice(boundary + 2);
          if (value.length > 256 * 1024) throw new ApiError(503, path, { detail: "invalid_event_stream" });
          if (await frame(value)) return cursor;
        }
        if (pending.length > 256 * 1024) throw new ApiError(503, path, { detail: "invalid_event_stream" });
      }
      return cursor;
    } finally { await reader.cancel().catch(() => undefined); }
  }
  async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
    const headers: Record<string, string> = body === undefined ? {} : { "content-type": "application/json" };
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
    getEmployerProfile: () => request("GET", "/employer/profile"),
    updateEmployerProfile: body => request("PATCH", "/employer/profile", body),
    getSystemChat: (role, afterSequence = 0, limit = 50) => request("GET", `/${role}/system-chat?after_sequence=${afterSequence}&limit=${limit}`),
    markSystemChatRead: (role, body) => request("POST", `/${role}/system-chat/read`, body),
    getChatUnread: role => request("GET", `/${role}/chat-unread`),
    getRecommendationsLatest: () => request("GET", "/candidate/recommendations/latest"),
    getOrganizationChat: () => request("GET", "/employer/organization-agent/chat"),
    getOrganizationMessages: (afterSequence, limit = 50) => request("GET", `/employer/organization-agent/messages?limit=${limit}${afterSequence === undefined ? "" : `&after_sequence=${afterSequence}`}`),
    clearOrganizationMessages: body => request("DELETE", "/employer/organization-agent/messages", body),
    sendOrganizationMessage: body => request("POST", "/employer/organization-agent/messages", body),
    getOrganizationTurn: turnId => request("GET", `/employer/organization-agent/turns/${encodeURIComponent(turnId)}`),
    getOrganizationTasks: () => request("GET", "/employer/organization-agent/tasks"),
    createOrganizationTask: body => request("POST", "/employer/organization-agent/tasks", body),
    stopOrganizationTask: taskId => request("DELETE", `/employer/organization-agent/tasks/${encodeURIComponent(taskId)}`),
    setOrganizationTaskAvailability: (taskId, body) => request("POST", `/employer/organization-agent/tasks/${encodeURIComponent(taskId)}/availability`, body),
    getOrganizationPendingAnswers: () => request("GET", "/employer/organization-agent/pending-answers"),
    answerOrganizationQuestion: (answerId, body) => request("POST", `/employer/organization-agent/pending-answers/${encodeURIComponent(answerId)}`, body),
    setOrganizationPresence: present => request("POST", "/employer/organization-agent/presence", { present }),
    getOrganizationBudget: () => request("GET", "/employer/organization-agent/budget"),
    getOrganizationContext: () => request("GET", "/employer/organization-agent/context"),
    getOrganizationMemory: () => request("GET", "/employer/organization-agent/memory"),
    patchOrganizationMemory: body => request("PATCH", "/employer/organization-agent/memory", body),
    uploadOrganizationDocument: body => request("POST", "/employer/organization-agent/documents", body),
    readOrganizationDocument: (documentId, offset = 0, limit = 8000) => request("GET", `/employer/organization-agent/documents/${encodeURIComponent(documentId)}?offset=${offset}&limit=${limit}`),
    downloadOrganizationDocument: documentId => request("GET", `/employer/organization-agent/documents/${encodeURIComponent(documentId)}/download`),
    getEmployerStatistics: (query = {}) => {
      const params = new URLSearchParams();
      for (const [key, value] of Object.entries(query)) if (value !== undefined) params.set(key, value);
      return request("GET", `/employer/statistics${params.size ? `?${params}` : ""}`);
    },
    get: (path) => request("GET", path),
    post: (path, body) => request("POST", path, body),
    requestLoginCode: (role, email) => request("POST", `/auth/${role}/code`, { email }),
    verifyLoginCode: (role, body) => request("POST", `/auth/${role}/verify`, body),
    getAccount: role => request("GET", `/auth/${role}/me`),
    logout: (role, all = false) => request("POST", `/auth/${role}/${all ? "logout-all" : "logout"}`),
    getCandidateProfile: () => request("GET", "/candidate/profile"),
    testingPreflight: (specialization, mode = "retake") => request("GET", `/candidate/testing/preflight?specialization=${encodeURIComponent(specialization)}&mode=${mode}`),
    startTestingSession: body => request("POST", "/candidate/testing/sessions", body),
    nextTestingTask: id => request("GET", `/candidate/testing/sessions/${encodeURIComponent(id)}/next`),
    answerTestingTask: (id, body) => request("POST", `/candidate/testing/sessions/${encodeURIComponent(id)}/answer`, body),
    finishTestingSession: id => request("POST", `/candidate/testing/sessions/${encodeURIComponent(id)}/finish`, {}),
    testingResult: id => request("GET", `/candidate/testing/sessions/${encodeURIComponent(id)}/result`),
    testingSupervision: (id, body) => request("POST", `/candidate/testing/sessions/${encodeURIComponent(id)}/supervision`, body),
    uploadTestingMedia: (id, body) => request("POST", `/candidate/testing/sessions/${encodeURIComponent(id)}/media`, body),
    abandonTestingSession: id => request("POST", `/candidate/testing/sessions/${encodeURIComponent(id)}/abandon`, {}),
    getCandidateTestingHistory: () => request("GET", "/candidate/testing-history"),
    getCandidatePipeline: () => request("GET", "/candidate/pipeline"),
    saveCandidateProfile: (body, replace = false) => request(replace ? "PUT" : "PATCH", "/candidate/profile", body),
    retryCandidateProjection: () => request("POST", "/candidate/profile/projection/retry", {}),
    getVacancyReady: draftId => request("GET", `/employer/vacancy-drafts/${encodeURIComponent(draftId)}/ready`),
    getVacancyDrafts: (limit = 50, offset = 0) => request("GET", `/employer/vacancy-drafts?limit=${limit}&offset=${offset}`),
    getAgentCapabilities: () => request("GET", "/employer/agents/capabilities"),
    listVacancies: (limit = 50, cursor, offset = 0) => request("GET", `/employer/vacancies?limit=${limit}&offset=${offset}${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ""}`),
    createVacancy: body => request("POST", "/employer/vacancies", body),
    enrichVacancy: (id, revision) => request("POST", `/employer/vacancy-drafts/${encodeURIComponent(id)}/enrich`, { expected_revision: revision }),
    getAgentJob: id => request("GET", `/employer/agents/jobs/${encodeURIComponent(id)}`),
    retryAgentJob: id => request("POST", `/employer/agents/jobs/${encodeURIComponent(id)}/retry`, {}),
    getVacancy: draftId => request("GET", `/employer/vacancies/${encodeURIComponent(draftId)}`),
    patchVacancy: (draftId, body) => request("PATCH", `/employer/vacancies/${encodeURIComponent(draftId)}`, body),
    approveVacancy: (draftId, expectedRevision) => request("POST", `/employer/vacancies/${encodeURIComponent(draftId)}/approve`, { expected_revision: expectedRevision }),
    publishVacancy: (draftId, body) => request("POST", `/employer/vacancies/${encodeURIComponent(draftId)}/publish`, body),
    closeVacancy: (draftId, expectedRevision) => request("POST", `/employer/vacancies/${encodeURIComponent(draftId)}/close`, { expected_revision: expectedRevision }),
    withdrawVacancy: (draftId, expectedRevision) => request("POST", `/employer/vacancies/${encodeURIComponent(draftId)}/withdraw`, { expected_revision: expectedRevision }),
    archiveVacancy: (draftId, expectedRevision) => request("DELETE", `/employer/vacancies/${encodeURIComponent(draftId)}`, { expected_revision: expectedRevision }),
    searchCandidates: (body = {}) => request("POST", "/employer/candidates/search", body),
    listPublicVacancies: (query = {}) => {
      const params = new URLSearchParams();
      for (const [key, value] of Object.entries(query)) if (value !== undefined && value !== null) params.set(key, String(value));
      return request("GET", `/public/showcase/vacancies${params.size ? `?${params}` : ""}`);
    },
    listPublicCandidates: (query = {}) => {
      const params = new URLSearchParams();
      for (const [key, value] of Object.entries(query)) if (value !== undefined && value !== null) params.set(key, String(value));
      return request("GET", `/public/showcase/candidates${params.size ? `?${params}` : ""}`);
    },
    getPublicCandidate: candidateId => request("GET", `/employer/candidates/${encodeURIComponent(candidateId)}`),
    likeShowcaseCandidate: (draftId, body) => request("POST", `/employer/vacancy-drafts/${encodeURIComponent(draftId)}/showcase-likes`, body),
    requestSelection: (draftId, body) => request("POST", `/employer/vacancy-drafts/${encodeURIComponent(draftId)}/combine`, body),
    getVacancyPipeline: draftId => request("GET", `/employer/vacancy-drafts/${encodeURIComponent(draftId)}/pipeline`),
    retryVacancyPreparation: draftId => request("POST", `/employer/vacancy-drafts/${encodeURIComponent(draftId)}/retry`),
    getSelectionStatus: draftId => request("GET", `/employer/vacancy-drafts/${encodeURIComponent(draftId)}/combine`),
    retrySelection: draftId => request("POST", `/employer/vacancy-drafts/${encodeURIComponent(draftId)}/combine/retry`),
    getRerankStatus: draftId => request("GET", `/employer/vacancy-drafts/${encodeURIComponent(draftId)}/rerank`),
    retryRerank: draftId => request("POST", `/employer/vacancy-drafts/${encodeURIComponent(draftId)}/rerank/retry`),
    refillFeed: (draftId, sessionId) => request("POST", `/employer/vacancy-drafts/${encodeURIComponent(draftId)}/feed/${encodeURIComponent(sessionId)}/refill`, { force: true }),
    getSelection: (draftId, limit = 50, offset = 0) => request("GET", `/employer/vacancy-drafts/${encodeURIComponent(draftId)}/selections?limit=${limit}&offset=${offset}`),
    openFeed: (draftId, body = {}) => request("POST", `/employer/vacancy-drafts/${encodeURIComponent(draftId)}/feed`, body),
    getFeed: (draftId, sessionId, cursor, limit = 50) => request("GET", `/employer/vacancy-drafts/${encodeURIComponent(draftId)}/feed/${encodeURIComponent(sessionId)}?limit=${limit}${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ""}`),
    decideFeed: (draftId, body) => request("POST", `/employer/vacancy-drafts/${encodeURIComponent(draftId)}/feed/decisions`, body),
    acknowledgeFeedImpression: (draftId, body) => request("POST", `/employer/vacancy-drafts/${encodeURIComponent(draftId)}/feed/impressions`, body),
    getSaved: (draftId, limit = 50, offset = 0, cursor) => request("GET", `/employer/vacancy-drafts/${encodeURIComponent(draftId)}/saved?limit=${limit}&offset=${offset}${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ""}`),
    getVisited: (draftId, limit = 50, offset = 0, cursor) => request("GET", `/employer/vacancy-drafts/${encodeURIComponent(draftId)}/visited?limit=${limit}&offset=${offset}${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ""}`),
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
    getEmployerRecruitment: (draftId, limit = 50, offset = 0, cursor) => request("GET", `/employer/vacancy-drafts/${encodeURIComponent(draftId)}/recruitment?limit=${limit}&offset=${offset}${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ""}`),
    getCandidateRecruitment: (limit = 50, offset = 0, cursor) => request("GET", `/candidate/recruitment?limit=${limit}&offset=${offset}${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ""}`),
    getRecruitmentList: (role, limit = 50, cursor, offset = 0) => request("GET", `/${role}/recruitment?limit=${limit}&offset=${offset}${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ""}`),
    getRecruitment: (role, processId) => request("GET", `/${role}/recruitment/${encodeURIComponent(processId)}`),
    actOnRecruitment: (role, processId, body) => request("POST", `/${role}/recruitment/${encodeURIComponent(processId)}/actions`, body),
    getRecruitmentContacts: processId => request("GET", `/employer/recruitment/${encodeURIComponent(processId)}/contacts`),
    getRecruitmentEvents: (role, afterSequence = 0, limit = 50) => request("GET", `/${role}/recruitment-events?after_sequence=${afterSequence}&limit=${limit}`),
    acknowledgeChatRead: (role, processId, body) => request("POST", `/${role}/recruitment/${encodeURIComponent(processId)}/chat/read`, body),
    getChatMessages: (role, processId, afterSequence = 0, limit = 50) => request("GET", `/${role}/recruitment/${encodeURIComponent(processId)}/chat/messages?after_sequence=${afterSequence}&limit=${limit}`),
    streamChatEvents: (role, processId, onEvent, options) => stream(`/${role}/recruitment/${encodeURIComponent(processId)}/chat/events`, onEvent, options),
    streamRecruitmentEvents: (role, onEvent, options) => stream(`/${role}/recruitment-events/stream`, onEvent, options),
    sendChatMessage: (role, processId, body) => request("POST", `/${role}/recruitment/${encodeURIComponent(processId)}/chat/messages`, body),
    uploadChatFile: (role, processId, body) => request("POST", `/${role}/recruitment/${encodeURIComponent(processId)}/chat/files`, body),
    downloadChatFile: (role, processId, fileId) => request("GET", `/${role}/recruitment/${encodeURIComponent(processId)}/chat/files/${encodeURIComponent(fileId)}`),
  };
}
