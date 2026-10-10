/** One email owns one account with one of the two fixed roles. */
export type AccountRole = "candidate" | "employer";

/** Consent for storing an account; candidate pool publication requires separate consent. */
export interface AccountConsent {
  processing: true;
  version: string;
  accepted_at: string;
}

/** Private authenticated account view; never included in public candidate cards. */
export interface AccountView {
  id: string;
  email: string;
  role: AccountRole;
  email_verified: true;
  created_at: string;
  consent: AccountConsent;
}

/** Queued delivery receipt, rather than a claim that the recipient received mail. */
export interface EmailCodeReceipt {
  challenge_id: string;
  expires_at: string;
  resend_after: string;
  /** Random challenge code returned only by explicitly enabled development delivery. */
  dev_code?: string;
}

/** Registration and login share verification; consent is required for the first account. */
export interface EmailCodeVerification {
  email: string;
  challenge_id: string;
  code: string;
  consent?: { processing: true; version: string };
}

/** JWT is also available to API clients; browsers use the HttpOnly role cookie. */
export interface AuthenticatedSession {
  account: AccountView;
  access_token: string;
  token_type: "Bearer";
  expires_at: string;
}
