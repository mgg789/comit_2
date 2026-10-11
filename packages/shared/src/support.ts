/** Support-console contracts: one operator, every account's Comit conversation. */
import type { AccountRole } from "./auth.js";
import type { SystemNotification } from "./notifications.js";

/** Login requires the exact operator email and its access key from server env. */
export interface SupportLoginRequest { email: string; access_key: string }
/** Console identity is intentionally minimal; no user data lives in the session. */
export interface SupportOperatorView { email: string }
/** Successful login result; the session itself travels as an HttpOnly cookie. */
export interface SupportSessionView { operator: SupportOperatorView; expires_at: string }
/** One account's Comit conversation as the operator sees it. */
export interface SupportConversation {
  account_id: string;
  email: string;
  role: AccountRole;
  last_sequence: number;
  last_text: string | null;
  last_created_at: string | null;
  unread_count: number;
}
/** Offset page over conversations ordered by the latest activity. */
export interface SupportConversationPage { items: SupportConversation[]; has_more: boolean }
/** Operator-side read receipt; monotonic per conversation like the account one. */
export interface SupportReadStatus { read_sequence: number; unread_count: number }
/** Ascending message page for the operator; never exposes the account's own receipts. */
export interface SupportMessagePage { items: SystemNotification[]; next_sequence: number; has_more: boolean }
/** Operator-side unread counter for one conversation. */
export interface SupportConversationUnread { unread_count: number }
