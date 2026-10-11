import { useState } from "react";
import { ApiError } from "@comit/api-client";
import type { CandidateProfileView, FspLinkStatus, FspDemoAuthorization } from "@comit/shared";
import { Dialog } from "@comit/ui-kit/app-shell";
import { Action, Checkbox, Field, Select } from "@comit/ui-kit/design";
import { api, useResource } from "./runtime";

const readStatus = () => api.get<FspLinkStatus>("/candidate/profile/fsp/status");

/** Explicit synthetic registry linking: candidate-bound state, consent and no browser-visible provider code. */
export function FspLink({ profile, onSaved }: { profile: CandidateProfileView; onSaved: (profile: CandidateProfileView) => void }) {
  const status = useResource(readStatus);
  const [open, setOpen] = useState(false), [busy, setBusy] = useState(false), [consent, setConsent] = useState(false);
  const [fixture, setFixture] = useState(profile.fields.fsp_id || "fsp-001"), [authorization, setAuthorization] = useState<FspDemoAuthorization | null>(null);
  const [error, setError] = useState<string | null>(null);
  const linked = Boolean(profile.fields.fsp_id);
  const demoEvidence = Object.values(profile.evidence).some(item => item.verified_value && typeof item.verified_value === "object" && !Array.isArray(item.verified_value) && item.verified_value.demo_registry === true);
  async function start() {
    setBusy(true); setError(null);
    try { setAuthorization(await api.post<FspDemoAuthorization>("/candidate/profile/fsp/demo/authorize", { expected_revision: profile.revision, demo_fixture_id: fixture })); }
    catch { setError("Не удалось открыть демо-реестр. Обновите страницу и попробуйте ещё раз."); }
    finally { setBusy(false); }
  }
  async function approve() {
    if (!authorization || !consent || busy) return;
    setBusy(true); setError(null);
    try {
      const saved = await api.post<CandidateProfileView>("/candidate/profile/fsp/demo/approve", { expected_revision: profile.revision, state: authorization.state, import_consent: true });
      onSaved(saved); setOpen(false); setAuthorization(null); setConsent(false);
    } catch (reason) {
      setAuthorization(null); setConsent(false);
      setError(reason instanceof ApiError && reason.status === 409 ? "Резюме изменилось или время подтверждения истекло. Обновите страницу и начните подключение заново." : "Не удалось импортировать достижения. Можно начать подключение заново.");
    } finally { setBusy(false); }
  }
  function close() { if (!busy) { setOpen(false); setAuthorization(null); setConsent(false); setError(null); } }
  return <article className="panel candidate-profile-row"><h3>ФСП</h3>
    <p>{linked ? `ID участника: ${profile.fields.fsp_id}` : "Подключите учётную запись, чтобы добавить участие в соревнованиях и достижения."}</p>
    {demoEvidence && <p className="muted">Данные из демонстрационного реестра ФСП. Это учебная учётная запись.</p>}
    {!linked && <p className="muted">Отсутствие истории ФСП не мешает пользоваться платформой.</p>}
    {status.loading ? <p role="status">Проверяем доступность подключения…</p> : status.error ? <Action onClick={status.refresh}>Повторить проверку подключения</Action> : status.data?.demo_available ? <Action onClick={() => setOpen(true)}>{linked ? "Обновить данные демо-ФСП" : "Подключить демо-ФСП"}</Action> : <p className="muted">Подключение реального реестра ФСП пока недоступно.</p>}
    {open && <Dialog title="Подключение демо-ФСП" close={close}><p>Это демонстрационная система. Выберите учебную учётную запись; пароль от реальной ФСП не нужен.</p>
      {!authorization ? <><Field label="Учётная запись демо-реестра"><Select value={fixture} disabled={busy || linked} onChange={event => setFixture(event.target.value)}><option value="fsp-001">Иван Иванов — два достижения</option><option value="fsp-002">Мария Петрова — одно достижение</option></Select></Field><Action disabled={busy} onClick={() => void start()}>{busy ? "Подключаем…" : "Продолжить"}</Action></> : <><p>Разрешить импорт достижений выбранного демо-аккаунта в ваше резюме? Они будут явно отмечены как данные демо-реестра.</p><label className="candidate-checks"><Checkbox checked={consent} disabled={busy} onChange={event => setConsent(event.target.checked)} />Разрешаю привязать демо-аккаунт и импортировать его достижения</label><Action variant="primary" disabled={!consent || busy} onClick={() => void approve()}>{busy ? "Импортируем…" : "Разрешить и подключить"}</Action></>}
      {error && <p className="form-error" role="alert">{error}</p>}
    </Dialog>}
  </article>;
}
