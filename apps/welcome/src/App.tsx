import { Button } from "@comit/ui-kit";

/**
 * Welcome face and vacancy showcase — lowest priority surface (context/70-mvp-scope.md).
 * Kept minimal on purpose until the core mechanics are demo-ready.
 */
export default function App() {
  return (
    <main className="min-h-screen bg-[var(--comit-bg)] text-[var(--comit-text)]">
      <div className="mx-auto flex max-w-3xl flex-col gap-8 px-6 py-20">
        <header className="flex flex-col gap-3">
          <h1 className="text-4xl font-bold">
            Comit — платформа подбора ИТ-специалистов с обратной механикой
          </h1>
          <p className="text-[var(--comit-text-muted)]">
            Кандидаты попадают в категории через обязательное тестирование, а работодатели сами
            находят нужную категорию и выходят на конкретного человека с предложением и уровнем
            заработной платы. Условия видны до начала общения.
          </p>
        </header>
        <section className="flex flex-wrap gap-4">
          <Button onClick={() => (window.location.href = "/candidate/")}>
            Я соискатель
          </Button>
          <Button variant="secondary" onClick={() => (window.location.href = "/employer/")}>
            Я работодатель
          </Button>
        </section>
        <section className="grid gap-4 sm:grid-cols-3">
          {[
            {
              title: "Категория по тесту",
              text: "Специализация и грейд подтверждаются обязательным тестированием, а не самоописанным резюме.",
            },
            {
              title: "Верифицированный профиль",
              text: "Очки рейтинга начисляются только за подтверждаемые факты, включая достижения ФСП.",
            },
            {
              title: "ЗП и обратная связь",
              text: "Зарплата обязательна в каждом предложении, а обратная связь — контракт, а не вежливость.",
            },
          ].map((card) => (
            <article key={card.title} className="flex flex-col gap-2 rounded-xl bg-white p-5 shadow-sm">
              <h2 className="font-semibold">{card.title}</h2>
              <p className="text-sm leading-6 text-[var(--comit-text-muted)]">{card.text}</p>
            </article>
          ))}
        </section>
      </div>
    </main>
  );
}
