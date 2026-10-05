import { Button } from "@comit/ui-kit";

/** Skeleton shell of the employer app; LHH-style inverse mechanics lands in S3–S4 (context/70-mvp-scope.md). */
export default function App() {
  return (
    <main className="min-h-screen bg-[var(--comit-bg)] text-[var(--comit-text)]">
      <div className="mx-auto flex max-w-2xl flex-col gap-6 px-6 py-16">
        <header className="flex flex-col gap-2">
          <p className="text-sm font-semibold uppercase tracking-widest text-[var(--comit-text-muted)]">
            Comit
          </p>
          <h1 className="text-3xl font-bold">Кабинет работодателя</h1>
          <p className="text-[var(--comit-text-muted)]">
            Категории кандидатов, подборка с объяснением попадания, тиндер-разбор и приглашения.
            Каркас — наполнение в спринтах S3–S4.
          </p>
        </header>
        <section className="flex flex-col gap-3 rounded-xl bg-white p-6 shadow-sm">
          <h2 className="text-lg font-semibold">Что здесь будет</h2>
          <ul className="list-disc pl-5 text-sm leading-6">
            <li>Профиль компании и описание потребности (стек, специализация, грейд)</li>
            <li>Подборка кандидатов с обоснованием попадания — не чёрный ящик</li>
            <li>Поиск по банку кандидатов с фильтрами (специализация, грейд, стек, ФСП)</li>
            <li>Тиндер-разбор: лайк/скип обучает вектор вакансии</li>
            <li>Приглашения с обязательной ЗП «от–до»; контакты — только после принятия</li>
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
