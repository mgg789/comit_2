/** A short weekly practice task; it never grants or changes a professional grade. */
export interface WeeklyQuestion { id: string; prompt: string; choices: string[] }
/** UTC Monday boundaries keep rewards non-stacking and make expiration reproducible. */
export interface WeeklyChallengeView {
  week: string; expires_at: string; status: "available" | "started" | "completed";
  questions: WeeklyQuestion[]; correct: number | null; passed: boolean | null;
  completed_at: string | null;
}
/** All answers are submitted once per account/week; retries return the saved result. */
export interface WeeklyChallengeAnswer { week: string; answers: Record<string, string> }
