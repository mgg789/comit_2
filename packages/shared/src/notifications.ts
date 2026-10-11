/** Navigation metadata attached to a system event, never a source of authorization. */
export interface SystemNotificationTarget {
  kind: "recruitment" | "vacancy" | "candidate_profile" | "settings";
  id?: string;
  path: string;
}
/** Durable account-only notification; text excludes private feedback and contact data. */
export interface SystemNotification {
  id: string;
  sequence: number;
  type: string;
  text: string;
  created_at: string;
  target: SystemNotificationTarget | null;
}
/** Page the permanent read-only Comit chat using an account-local sequence. */
export interface SystemChatPage {
  id: "comit-system";
  title: "Comit";
  items: SystemNotification[];
  next_sequence: number;
  read_sequence: number;
  unread_count: number;
  has_more: boolean;
}
/** Advance the receipt only through a sequence previously delivered to this account. */
export interface SystemChatReadRequest { read_sequence: number }
/** Send a message into the Comit conversation with a stable UUID retry key. */
export interface SystemChatMessageRequest { idempotency_key: string; text: string }
/** Monotonic system inbox receipt and exact unread item count. */
export interface SystemChatReadStatus { read_sequence: number; unread_count: number }
/** Global badge includes every authorized hiring chat; null means private chat unavailable. */
export interface ChatUnreadStatus {
  system_unread_count: number;
  recruitment_unread_count: number | null;
  total_unread_count: number | null;
}
