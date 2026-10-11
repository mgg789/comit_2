import type { CandidateProfileFields } from "./candidate-profile.js";

/** One owner-private document; processing consent authorizes ephemeral extraction only. */
export interface ResumeImportRequest {
  filename: string;
  content_base64: string;
  processing_consent: true;
}
/** Unverified suggestions, never saved or published until the candidate reviews and saves. */
export interface ResumeImportResult {
  fields: Pick<CandidateProfileFields, "display_name" | "phone" | "city" | "description" | "primary_direction" | "declared_grade" | "work_types" | "experience" | "projects" | "achievements">;
  extracted_text: string;
  method: "pdf_text" | "ocr" | "mixed";
  warnings: string[];
}
