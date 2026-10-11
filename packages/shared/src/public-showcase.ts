/** Minimal anonymous catalogue cards; detailed profiles remain authenticated. */
import type { Category, Grade, Specialization } from "./index.js";
import type { VacancyWorkType } from "./vacancy-pipeline.js";
/** Server-applied public filters; continuation is invalidated when query or source changes. */
export interface PublicShowcaseQuery { q?: string; limit?: number; cursor?: string | null; specialization?: Specialization; grade?: Grade; stack?: string; sort?: "newest" | "oldest"; }
/** Anonymous classification and public skills without evidence, links or private contacts. */
export interface PublicShowcaseCandidate { candidate_id: string; display_name: string; city: string; salary: { from_rub: number; to_rub: number | null }; category: Category; stacks: string[]; published_at: string; }
/** Published vacancy display fields; absence of a declared company or category is explicit. */
export interface PublicShowcaseVacancy { vacancy_id: string; title: string; company_name: string | null; city: string; salary: { from_rub: number; to_rub: number }; work_types: VacancyWorkType[]; schedule: string; category: { specialization: Specialization | null; grade: Grade | null }; stacks: string[]; published_at: string; }
/** Bounded, snapshot-bound page; unavailable services return an error instead of a fabricated empty pool. */
export interface PublicShowcasePage<T> { items: T[]; limit: number; has_more: boolean; next_cursor: string | null; }
