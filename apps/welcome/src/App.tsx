import { ACTION_ROW_BUTTON, ActionRow, Backdrop, LogoTile, Wordmark } from "@comit/ui-kit";
import employersIcon from "./assets/employers.svg";
import seekersIcon from "./assets/seekers.svg";
import showcaseIcon from "./assets/showcase.svg";

/** Entry points of the shared start screen (Figma «MAIN», node 770:1788), in design order. */
const ENTRIES = [
  {
    title: "Вакансии и резюме",
    description: "Общая витрина вакансий и резюме",
    action: "К витрине COMIT",
    href: "/employer/candidates",
    icon: showcaseIcon,
  },
  {
    title: "Для соискателей",
    description: "Разместите резюме и найдите работу",
    action: "К поиску работы",
    href: "/candidate/",
    icon: seekersIcon,
  },
  {
    title: "Для работадателей",
    description: "Находите сотрудников на COMIT",
    action: "К панели управления",
    href: "/employer/",
    icon: employersIcon,
  },
] as const;

/**
 * Common start screen on «/» for every visitor: brand block and three entry cards.
 * Sizes are the 1920 artboard values in rem, so the fluid root scale keeps the proportions.
 */
export default function App() {
  return (
    <main className="relative flex min-h-dvh flex-col items-center justify-center px-4 py-10 lg:py-[6rem]">
      <Backdrop variant="auth" />
      <div className="flex flex-col items-center gap-6 sm:flex-row lg:gap-[4.375rem]">
        <LogoTile size={300} className="hidden sm:inline-flex" />
        <LogoTile size={140} className="sm:hidden" />
        <h1 className="text-text">
          <Wordmark height={144} className="hidden sm:inline-block" />
          <Wordmark height={64} className="sm:hidden" />
        </h1>
      </div>
      <nav aria-label="Разделы COMIT" className="mt-10 flex w-full max-w-[55.5rem] flex-col gap-4 lg:mt-[6.0625rem] lg:gap-[2.125rem]">
        {ENTRIES.map((entry) => (
          <a key={entry.title} href={entry.href} className="group block rounded-[2rem] transition-transform duration-150 hover:-translate-y-0.5">
            <ActionRow
              leading={<img src={entry.icon} alt="" className="size-12 shrink-0 lg:size-[4.25rem]" />}
              title={entry.title}
              subtitle={entry.description}
              action={<span className={ACTION_ROW_BUTTON}>{entry.action}</span>}
            />
          </a>
        ))}
      </nav>
    </main>
  );
}
