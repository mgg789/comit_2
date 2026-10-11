import { useRef, useState } from "react";
import { ApiError } from "@comit/api-client";
import type { CandidateDeclaredNarrative, CandidateProfileView } from "@comit/shared";
import { Action } from "@comit/ui-kit/design";
import { api } from "./runtime";
import { githubEvidenceLabel, githubProjectEvidence, githubRepositoryUrl, githubVerificationRequest } from "./github-verification-model";

/** Candidate-owned project checks preserve stored evidence during provider outages. */
export function GithubVerification({ profile, onSaved, edit, reload }: { profile: CandidateProfileView; onSaved: (profile: CandidateProfileView) => void; edit: () => void; reload: () => void }) {
  const [busy, setBusy] = useState<string | null>(null);
  const checking = useRef(false);
  const [error, setError] = useState<string | null>(null);
  async function verify(project: CandidateDeclaredNarrative) {
    if (checking.current) return;
    checking.current = true; setBusy(project.id ?? null); setError(null);
    try {
      const saved = await api.post<CandidateProfileView>("/candidate/profile/verifications/repository", githubVerificationRequest(project, profile.revision));
      onSaved(saved);
    } catch (reason) {
      setError(reason instanceof ApiError && reason.status === 409 ? "Резюме изменилось. Загрузите актуальную версию и повторите проверку." : reason instanceof ApiError && reason.status === 422 ? "Не удалось подтвердить публичный репозиторий с кодом. Проверьте ссылку и доступность проекта." : "Сервис проверки недоступен. Сохранённые результаты остаются в резюме; попробуйте позже.");
    } finally { checking.current = false; setBusy(null); }
  }
  const projects = profile.fields.projects ?? [];
  return <article className="panel candidate-profile-row"><h3>GitHub</h3>
    <p className="muted">Проверяем наличие кода в публичном репозитории. Принадлежность аккаунта и ваш вклад эта проверка не подтверждает.</p>
    {!projects.length && <p>Добавьте проект, ссылку на GitHub и описание своего вклада в резюме.</p>}
    {projects.map((project, index) => {
      const url = githubRepositoryUrl(project.link), evidence = githubProjectEvidence(project, profile.evidence);
      const ready = Boolean(project.id && url && project.description?.trim());
      return <div key={project.id ?? index} className="candidate-narrative"><h4>{project.title}</h4>
        {url && <a href={url} target="_blank" rel="noopener noreferrer">Открыть репозиторий ↗</a>}
        <p>{githubEvidenceLabel(evidence)}</p>
        {evidence?.checked_at && <p className="muted">Проверено: {new Date(evidence.checked_at).toLocaleString("ru-RU")}</p>}
        {!ready && <p className="muted">Сохраните описание вклада и ссылку на публичный репозиторий вида https://github.com/owner/repository. Ссылка на аккаунт или отдельный файл не подходит.</p>}
        <Action disabled={!ready || busy !== null} onClick={() => void verify(project)}>{busy === project.id ? "Проверяем…" : evidence ? "Проверить повторно" : "Проверить репозиторий"}</Action>
      </div>;
    })}
    <Action disabled={busy !== null} onClick={edit}>Редактировать проекты</Action>
    {busy && <p role="status">Проверяем публичное Git-дерево и наличие непустых файлов…</p>}
    {error && <div><p role="alert" className="form-error">{error}</p><Action disabled={busy !== null} onClick={reload}>Загрузить актуальное резюме</Action></div>}
  </article>;
}
