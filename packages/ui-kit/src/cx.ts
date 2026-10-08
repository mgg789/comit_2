/** Join truthy class names; keeps conditional Tailwind classes readable without a dependency. */
export function cx(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
}
