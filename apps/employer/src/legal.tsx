import { LegalText } from "@comit/ui-kit/design";

const documents = import.meta.glob("../../../docs/legal/*.md", {
  query: "?raw",
  import: "default",
  eager: true,
}) as Record<string, string>;

/** Public legal documents rendered from the repository's canonical text, without requiring a session. */
export function LegalDocument({ name }: { name: string }) {
  const text = documents[`../../../docs/legal/${name}.md`];
  return (
    <main className="legal-document panel">
      <a href="#/vacancies">← Вернуться к приложению</a>
      {!text ? (
        <h1>Документ не найден</h1>
      ) : (
        <LegalText text={text} />
      )}
    </main>
  );
}
