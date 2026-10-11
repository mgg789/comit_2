import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const source = (relativePath: string) =>
  readFileSync(new URL(relativePath, import.meta.url), "utf8");

test("published vacancies close after persistence without waiting for matching readiness", () => {
  const vacancies = source("../src/vacancies.tsx");
  const publish = vacancies.match(
    /const publish = async \(\) => \{[\s\S]*?\n  \};\n  const requirementPanels/,
  )?.[0];

  assert.ok(publish, "publish handler must remain discoverable");
  assert.match(
    publish,
    /const result = await api\.publishVacancy[\s\S]*?onSaved\(result\);[\s\S]*?clearDraft\(formKey\);[\s\S]*?onClose\(\);/,
  );
  assert.doesNotMatch(publish, /getVacancyReady|setPublished|setInterval/);
});

test("vacancy and discovery copy stays concise", () => {
  const vacancies = source("../src/vacancies.tsx");
  const discovery = source("../src/discovery.tsx");

  assert.match(vacancies, /className="vacancy-description-hint"[\s\S]*?\{description\.label\}/);
  assert.doesNotMatch(vacancies, /description\.length\} символов/);
  assert.match(discovery, /placeholder="Имя, роль, стек"/);
  assert.doesNotMatch(discovery, /placeholder="Поиск: Имя, роль, стек"/);
});

test("the first vacancy step is wider while review and swipe controls keep their contracts", () => {
  const vacancies = source("../src/vacancies.css");
  const vacancyView = source("../src/vacancies.tsx");
  const sharedLayout = source("../src/index.css");

  assert.match(vacancies, /\.modal\.vacancy-editor:not\(\.is-review\)\s*\{\s*width: 640rem;/);
  assert.match(sharedLayout, /\.modal\.vacancy-editor\.is-review\s*\{\s*width: 726rem;/);
  assert.match(vacancyView, /className=\{`candidate-swipe glass-classic deck-tone-/);
  for (const tone of ["0", "1", "2"]) {
    assert.match(vacancies, new RegExp(`candidate-swipe\\.glass-classic\\.deck-tone-${tone} \\{\\n  background:`));
  }
  assert.doesNotMatch(vacancies, /candidate-controls[\s\S]*comit-glass-frost/);
});

test("expense captions expose the complete staged tariffs", () => {
  const discovery = source("../src/discovery.tsx");

  assert.match(discovery, /publication_kopeks\)} за первую публикацию/);
  assert.match(discovery, /invitation_kopeks\)} за принятое приглашение/);
  assert.ok(discovery.indexOf('className="panel statistics-expenses"') < discovery.indexOf('className="panel statistics-vacancies"'));
  assert.match(discovery, /deadline_penalty_kopeks\)} за просрочку или решение поддержки/);
});

test("company settings keep explicit space between the input and save action", () => {
  const settings = source("../src/settings.tsx");
  const styles = source("../src/settings.css");

  assert.match(settings, /<form className="settings-company-form" onSubmit=\{saveCompany\}>/);
  assert.match(styles, /\.settings-company-form \{ display: grid; gap: 16rem; \}/);
});
