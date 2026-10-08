import { Button } from "@comit/ui-kit";

/** Skeleton shell of the candidate app; real screens land in S1–S2 (context/70-mvp-scope.md). */
export default function App() {
  return (
    <main className="min-h-screen bg-[var(--comit-bg)] text-[var(--comit-text)]">
      <div className="mx-auto flex max-w-2xl flex-col gap-6 px-6 py-16">
        <header className="flex flex-col gap-2">
          <p className="text-sm font-semibold uppercase tracking-widest text-[var(--comit-text-muted)]">
            Comit
          </p>
          <h1 className="text-3xl font-bold">Кабинет кандидата</h1>
          <p className="text-[var(--comit-text-muted)]">
            Профиль, опрос специализации, грейд-тест и приглашения работодателей. Каркас —
            наполнение в спринтах S1–S2.
          </p>
        </header>
        <section className="flex flex-col gap-3 rounded-xl bg-surface p-6 shadow-sm">
          <h2 className="text-lg font-semibold">Что здесь будет</h2>
          <ul className="list-disc pl-5 text-sm leading-6">
            <li>Профиль и верификация данных (очки рейтинга за подтверждённое)</li>
            <li>Опрос по отрасли и специализации, выбор заявленного грейда</li>
            <li>Грейд-тест: категория присваивается только через тест</li>
            <li>Приглашения с обязательной ЗП «от–до» и статусами</li>
            <li>Привязка профиля к ID участника ФСП (или честное отсутствие)</li>
          </ul>
        </section>
        <footer>
          <Button variant="secondary" onClick={() => (window.location.href = "/")}>
            На витрину
          </Button>
        </footer>
      </div>
    </main>
  );
}
