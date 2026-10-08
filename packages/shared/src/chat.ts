/** Published attachment metadata; filenames and media types are encrypted with the message in QGramm. */
export interface ChatFile {
  id: string;
  name: string;
  media_type: string;
  size: number;
  sha256: string;
}

/** Participant-visible BASIC message; IDs refer to this recruitment process only. */
export interface ChatMessage {
  id: string;
  sequence: number;
  sender_id: string;
  created_at: string;
  text: string;
  files: ChatFile[];
}

/** Ascending durable history; persist next_sequence only after processing the returned page. */
export interface ChatPage { messages: ChatMessage[]; next_sequence: number }

/** Durable cursor event conceals unrelated QGramm log records while preserving continuation. */
export type ChatEvent = { sequence: number; type: "message.created"; message: ChatMessage } | { sequence: number; type: "cursor" };
/** Private bridge page validated before SSE delivery; clients receive each event separately. */
export interface ChatEventPage { events: ChatEvent[]; next_sequence: number; }

/** Contact-free recruitment notification transported by the same bounded SSE channel. */
export interface RecruitmentStreamEvent { sequence: number; type: "recruitment.changed"; event: import("./recruitment.js").RecruitmentEvent; }

/** Caller persists each processed cursor and reconnects after the server's bounded stream lifetime. */
export interface EventStreamOptions { afterSequence?: number; signal?: AbortSignal; }

/** UUID retry key binds the complete text and attachment metadata; at least one must be nonempty. */
export interface SendChatMessage { idempotency_key: string; text: string; files: ChatFile[] }

/** Bounded JSON upload, up to 8 MiB decoded; attach the returned file in a message to publish it. */
export interface UploadChatFile { idempotency_key: string; name: string; media_type: string; data_base64: string }

/** Download only a published attachment in an authorized process; browsers should save it as a file. */
export interface DownloadChatFile extends ChatFile { data_base64: string }
