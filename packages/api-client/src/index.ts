/**
 * Minimal typed HTTP client for Comit services.
 * All apps talk to api-core through this wrapper so auth headers and error
 * handling stay in one place.
 */
import type { AccountRole, AccountView, AuthenticatedSession, EmailCodeReceipt, EmailCodeVerification,
  CandidateProfileView, CandidateProfileMutation, CombineRequest, CombineStatus, SelectionPage, VacancyReady } from "@comit/shared";

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
  };
}
