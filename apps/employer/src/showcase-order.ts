import { GRADES, type Grade } from "@comit/shared";

/** Client-side orderings of the showcase; "relevance" keeps the server's search order. */
export type ShowcaseSort = "relevance" | "salary_asc" | "salary_desc" | "grade_desc";

/** Sort choices in menu order, labelled as the trigger shows them. */
export const SHOWCASE_SORTS: ReadonlyArray<{ value: ShowcaseSort; label: string }> = [
  { value: "relevance", label: "по релевантности" },
  { value: "salary_asc", label: "сначала дешевле" },
  { value: "salary_desc", label: "сначала дороже" },
  { value: "grade_desc", label: "сначала старше грейд" },
];

/** Type guard for values restored from browser storage. */
export function isShowcaseSort(value: unknown): value is ShowcaseSort {
  return SHOWCASE_SORTS.some((item) => item.value === value);
}

/**
 * Short candidate code typed in search, e.g. "0A40" or "Кандидат 0a40" (bare codes need a digit).
 * Public names are anonymous (`Кандидат XXXX` = first hex digits of the id), so such a
 * query can only be answered by an id prefix, never by the server's text search.
 * @returns lower-case hex prefix (4–8 chars) or null for an ordinary query.
 */
export function candidateCode(query: string): string | null {
  const match = /^(кандидат\s*)?([0-9a-f]{4,8})$/i.exec(query.trim());
  const code = match?.[2];
  // Without the "Кандидат" prefix a digit is required, so words like "cafe" stay text queries.
  if (!code || (!match[1] && !/\d/.test(code))) return null;
  return code.toLowerCase();
}

type Sortable = {
  candidate_id: string;
  salary: { from_rub: number; to_rub: number | null };
  category: { grade: Grade };
};

/**
 * Order loaded cards without mutating the input. The sort is stable, so ties keep the
 * server relevance order. Salary ascending compares the lower bound, descending the upper one.
 */
export function orderCandidates<T extends Sortable>(items: readonly T[], sort: ShowcaseSort): T[] {
  const rows = items.map((item, index) => ({ item, index }));
  const compare = (a: T, b: T): number => {
    if (sort === "salary_asc") return a.salary.from_rub - b.salary.from_rub || (a.salary.to_rub ?? a.salary.from_rub) - (b.salary.to_rub ?? b.salary.from_rub);
    if (sort === "salary_desc") return (b.salary.to_rub ?? b.salary.from_rub) - (a.salary.to_rub ?? a.salary.from_rub) || b.salary.from_rub - a.salary.from_rub;
    if (sort === "grade_desc") return GRADES.indexOf(b.category.grade) - GRADES.indexOf(a.category.grade);
    return 0;
  };
  return rows.sort((a, b) => compare(a.item, b.item) || a.index - b.index).map((row) => row.item);
}
