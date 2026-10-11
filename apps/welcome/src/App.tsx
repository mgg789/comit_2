import { asset, candidateUrl, employerUrl, WelcomeBackground } from "./entry";
import { useEffect, useState } from "react";
import { PersistentStateScope } from "@comit/ui-kit/ui-state";
import { PublicShowcase } from "./showcase";
const entries = [
  { title: "Вакансии и резюме", description: "Общая витрина вакансий и резюме", action: "К витрине COMIT", icon: "showcase-icon.svg", href: "#/showcase/vacancies" },
  { title: "Для соискателей", description: "Разместите резюме и найдите работу", action: "К поиску работы", icon: "jobseeker-icon.svg", href: candidateUrl },
  { title: "Для работодателей", description: "Находите сотрудников на COMIT", action: "К панели управления", icon: "employer-icon.svg", href: employerUrl },
];

/** Public entrance to the existing candidate and employer applications. */
function Landing() {
  return (
    <main className="employer-app welcome-page">
      <WelcomeBackground />
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
        <a className="welcome-help-link" href="/help/">Как пользоваться Comit</a>
      </div>
    </main>
  );
}

/** Hash navigation keeps the anonymous showcase separate from role-authenticated application mounts. */
export default function App() {
  const [route, setRoute] = useState(window.location.hash);
  useEffect(() => { const update = () => setRoute(window.location.hash); window.addEventListener("hashchange", update); update(); return () => window.removeEventListener("hashchange", update); }, []);
  return route.startsWith("#/showcase") ? <PersistentStateScope scope="public-showcase"><PublicShowcase kind={route === "#/showcase/candidates" ? "candidates" : "vacancies"} /></PersistentStateScope> : <Landing />;
}
