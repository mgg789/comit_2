/** Candidate-owned adaptive testing contracts; timestamps use UTC ISO8601. */
export type TestingSpecialization = "algorithms" | "sql" | "rest_api" | "python" | "react" | "docker" | "teamwork";
export type TestingGrade = "intern" | "junior" | "middle" | "senior";
export interface TestingPreflight {
  supported_question_types: Array<"mcq" | "short_answer">; active_session_status: "active" | "finished" | null;
  specialization: TestingSpecialization; supported_specializations: TestingSpecialization[];
  eligible: boolean; next_attempt_at: string | null; upgrade_at: string | null;
  active_supervision: TestingSupervision | null;
  active_session_consents: (Omit<TestingStartRequest["consents"], "processing"> & { processing?: boolean }) | null; next_sequence: number;
  active_session_id: string | null; recording_enabled: boolean; active_recording_enabled: boolean; max_questions: number;
}
export interface TestingStartRequest {
  specialization: TestingSpecialization; declared_grade?: TestingGrade; mode?: "retake" | "upgrade";
  consents: { version: "testing-media-v1"; processing: true; screen_recording: boolean; video_analysis: boolean };
}
export interface TestingSession { session_id: string; status: "active" | "finished" | "abandoned"; tasks_given?: number }
export interface TestingTask {
  item_id: string; family: string; level: number; type: "mcq" | "short_answer" | "code" | "extended";
  prompt: string; options?: string[]; function_name?: string;
}
export interface TestingNextTask { session_id: string; answered: number; decided_grade: TestingGrade | null; task: TestingTask }
export interface TestingAnswerRequest { item_id: string; answer: string | number }
export interface TestingAnswerReceipt extends TestingSession { answered: number; correct: boolean | null; judge_status: string; decided_grade: TestingGrade | null }
export interface TestingSupervision {
  total_seconds: number; in_frame_seconds: number; focus_lost_seconds: number;
  multi_face_events: number; camera: "granted" | "denied" | "unavailable";
}
export interface TestingMediaRequest { kind: "screen" | "webcam"; data_base64: string; sequence: number; mime_type?: string }
export interface TestingResult extends TestingSession {
  capture_finalized: boolean; decided_grade: TestingGrade | null; answered: number; correct: number; next_attempt_at: string;
  grade_publication: { published: boolean; status: string } | null; supervision: string | null;
}
