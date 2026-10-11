/** Metadata for a file attached to a durable Comit system message. */
export interface SystemChatFile {
  id: string;
  name: string;
  media_type: string;
  size: number;
  sha256: string;
}

/** Base64 upload accepted only for the authenticated account or support operator. */
export interface SystemChatFileUpload {
  idempotency_key: string;
  name: string;
  media_type: string;
  data_base64: string;
}

/** Opaque file bytes returned only after owner/support ACL checks. */
export interface SystemChatFileDownload extends SystemChatFile {
  data_base64: string;
}

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
  files: SystemChatFile[];
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
export interface SystemChatMessageRequest { idempotency_key: string; text?: string; files?: SystemChatFile[] }
/** Monotonic system inbox receipt and exact unread item count. */
export interface SystemChatReadStatus { read_sequence: number; unread_count: number }
/** Global badge includes every authorized hiring chat; null means private chat unavailable. */
export interface ChatUnreadStatus {
  system_unread_count: number;
  recruitment_unread_count: number | null;
  total_unread_count: number | null;
}
