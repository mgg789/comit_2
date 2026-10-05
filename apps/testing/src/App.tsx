import { Button } from "@comit/ui-kit";

/** Skeleton shell of the testing app; blueprint mechanics lands in S2 (context/60-testing-engine.md). */
export default function App() {
  return (
    <main className="min-h-screen bg-[var(--comit-bg)] text-[var(--comit-text)]">
      <div className="mx-auto flex max-w-2xl flex-col gap-6 px-6 py-16">
        <header className="flex flex-col gap-2">
          <p className="text-sm font-semibold uppercase tracking-widest text-[var(--comit-text-muted)]">
            Comit
          </p>
          <h1 className="text-3xl font-bold">Сервис тестирования</h1>
          <p className="text-[var(--comit-text-muted)]">
            Параметризованные задания, устойчивые к утечке: у каждого кандидата свой вариант с
            одинаковой сложностью. Каркас — наполнение в спринте S2.
          </p>
        </header>
        <section className="flex flex-col gap-3 rounded-xl bg-white p-6 shadow-sm">
          <h2 className="text-lg font-semibold">Что здесь будет</h2>
          <ul className="list-disc pl-5 text-sm leading-6">
            <li>Грейд-тест на заявленный уровень с адаптивным движением вверх/вниз</li>
            <li>Код-задачи с автопроверкой, квизы, задания «предложи подход»</li>
            <li>Cooldown смены грейда (раз в 3 месяца) и история попыток</li>
            <li>Регулярные короткие задачи от работодателей</li>
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
