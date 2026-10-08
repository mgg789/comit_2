import { Backdrop, Button, StateMessage } from "@comit/ui-kit";
import { Navigate, Route, Routes } from "react-router";
import { LoginPage } from "./auth/LoginPage";
import { useSession } from "./auth/session";
import { AppShell } from "./layout/AppShell";
import { NewVacancyRoute, VacanciesPage } from "./vacancies/VacanciesPage";

/** Employer cabinet (LHH inverse mechanics): the session gate decides between sign-in and the cabinet routes. */
export default function App() {
  const { state } = useSession();

  if (state.status === "loading") {
    return (
      <main className="flex min-h-dvh items-center justify-center">
        <Backdrop />
        <StateMessage kind="loading" title="Проверяем вход" />
      </main>
    );
  }
  if (state.status === "error") {
    return (
      <main className="flex min-h-dvh items-center justify-center">
        <Backdrop />
        <StateMessage
          kind="error"
          title="Сервис входа недоступен"
          description="Не удалось проверить сессию. Проверьте соединение и повторите."
          action={
            <Button variant="secondary" onClick={state.retry}>
              Повторить
            </Button>
          }
        />
      </main>
    );
  }
  if (state.status === "anonymous") return <LoginPage />;

  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<Navigate to="/vacancies" replace />} />
        <Route path="vacancies" element={<VacanciesPage />}>
          <Route path="new" element={<NewVacancyRoute />} />
        </Route>
        <Route path="*" element={<Navigate to="/vacancies" replace />} />
      </Route>
    </Routes>
  );
}
