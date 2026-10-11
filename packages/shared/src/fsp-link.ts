/** Availability never implies a connection to the real federation registry. */
export interface FspLinkStatus { demo_available: boolean; real_available: false }
/** Browser-visible short-lived authorization state; the provider code stays server-side. */
export interface FspDemoAuthorization { state: string; expires_at: string; authorize_url: string; callback_url: string; demo: true }
/** Explicit consent to import the state-bound synthetic participant, not arbitrary submitted facts. */
export interface FspDemoApproval { expected_revision: number; state: string; import_consent: true }
