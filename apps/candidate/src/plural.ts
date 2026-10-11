const rules = new Intl.PluralRules("ru-RU");

/**
 * Noun form that agrees with a number: `pluralWord(3, ["предложение", "предложения", "предложений"])`.
 * @param forms one / few / many forms, as Intl.PluralRules("ru") categories them.
 */
export function pluralWord(count: number, forms: readonly [one: string, few: string, many: string]): string {
  const category = rules.select(count);
  return category === "one" ? forms[0] : category === "few" ? forms[1] : forms[2];
}

/** Russian count with the right noun form: `countLabel(1, …)` → "1 предложение". */
export function countLabel(count: number, forms: readonly [one: string, few: string, many: string]): string {
  return `${count} ${pluralWord(count, forms)}`;
}
