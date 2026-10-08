/** Keep public assets under the configured welcome mount in production builds. */
const asset = (name: string) => `${import.meta.env.BASE_URL}design/${name}`;

/** Resolve real application mounts; development uses the sibling Vite servers. */
function applicationUrl(override: string | undefined, mount: string, port: number): string {
  if (override?.trim()) return override.trim();
  if (!import.meta.env.DEV) return mount;
  const sibling = new URL(window.location.origin);
  sibling.port = String(port);
  sibling.pathname = mount;
  return sibling.toString();
}

const candidateUrl = applicationUrl(import.meta.env.VITE_CANDIDATE_URL, "/candidate/", 5173);
const employerUrl = applicationUrl(import.meta.env.VITE_EMPLOYER_URL, "/employer/", 5174);
const entries = [
  { title: "Вакансии и резюме", description: "Общая витрина вакансий и резюме", action: "К витрине COMIT", icon: "showcase-icon.svg", href: `${employerUrl.split("#")[0]}#/showcase` },
  { title: "Для соискателей", description: "Разместите резюме и найдите работу", action: "К поиску работы", icon: "jobseeker-icon.svg", href: candidateUrl },
  { title: "Для работодателей", description: "Находите сотрудников на COMIT", action: "К панели управления", icon: "employer-icon.svg", href: employerUrl },
];

/** Public entrance to the existing candidate and employer applications. */
export default function App() {
  return (
    <main className="employer-app welcome-page">
      <div className="welcome-background" aria-hidden="true">
        <span className="welcome-sweep welcome-sweep-right"><img src={asset("background-sweep-right.png")} alt="" /></span>
        <span className="welcome-sweep welcome-sweep-left"><img src={asset("background-sweep-left.png")} alt="" /></span>
        <span className="welcome-glow welcome-glow-one"><img src={asset("landing-glow-1.svg")} alt="" /></span>
        <span className="welcome-glow welcome-glow-two"><img src={asset("landing-glow-2.svg")} alt="" /></span>
      </div>
      <div className="welcome-content">
        <h1 className="welcome-logo" aria-label="COMIT">
          <span className="welcome-logo-tile" aria-hidden="true"><img src={asset("comit-mark.svg")} alt="" /></span>
          <span className="welcome-wordmark" aria-hidden="true">comit</span>
        </h1>
        <nav className="welcome-entries" aria-label="Выберите раздел COMIT">
          {entries.map((entry) => (
            <article className="welcome-card" key={entry.title}>
              <img className="welcome-card-line" src={asset("card-arrow-line.svg")} alt="" />
              <img className="welcome-card-icon" src={asset(entry.icon)} alt="" />
              <div className="welcome-card-copy"><h2>{entry.title}</h2><p>{entry.description}</p></div>
              <a className="welcome-card-action" href={entry.href}>
                <span>{entry.action}</span>
              </a>
            </article>
          ))}
        </nav>
      </div>
    </main>
  );
}
