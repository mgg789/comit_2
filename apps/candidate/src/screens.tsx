import { WeeklyChallenge } from "./weekly-challenge";
import { ApiError } from "@comit/api-client";
import { Dialog } from "@comit/ui-kit/app-shell";
import { clearPersistentState, usePersistentState } from "@comit/ui-kit/ui-state";
import { chatReturnRoute } from "@comit/ui-kit/chat-navigation";
import { RecruitmentConversation, RecruitmentChatList, type ConversationTransport } from "@comit/ui-kit/recruitment-conversation";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { GRADES, SPECIALIZATIONS, SKILL_LEVELS, SKILL_GROUP_LABELS, skillLabel, skillLevelLabel, type CandidateDeclaredNarrative, type CandidateDeclaredSkill, type CandidateProfileFields, type CandidateProfileMutation, type CandidateProfileView, type RecruitmentAction, type RecruitmentState, type RecruitmentView } from "@comit/shared";
import { Action, Checkbox, Field, Icon, InlineSearch, PageTitle, Reveal, Select, State, RecruitmentClock, Tags, VacancyCardArrow } from "@comit/ui-kit/design";
import { api, errorText, readInvitations, readProfile, salary, useResource } from "./runtime";

import { FspLink } from "./fsp-link";
import { AchievementDocuments, uploadAchievementFile, profileFieldsForSave } from "./achievement-documents";
import { ResumeImport } from "./resume-import";
import { mergeResumeSuggestions } from "./resume-import-model";
import { TestingHeatmap } from "./testing-heatmap";
import { TestingLaunch } from "./testing-launch";

type Navigate = (path: string) => void;
const directions = { backend: "Backend", frontend: "Frontend", fullstack: "Fullstack", mobile: "Мобильная разработка", devops: "DevOps", data: "Данные", ml: "Машинное обучение", qa: "Тестирование" };
const grades = { intern: "Стажёр", junior: "Junior", middle: "Middle", senior: "Senior" };
const workTypes = { onsite: "В офисе", remote: "Удалённо", hybrid: "Гибрид" };
const states: Record<RecruitmentState, string> = { sent: "Новое приглашение", viewed: "Приглашение просмотрено", accepted: "Приглашение принято", declined: "Вы отказались", rejected: "Работодатель отказал", offer_sent: "Оффер получен", hired: "Оффер принят", offer_declined: "Оффер отклонён", withdrawn: "Приглашение отозвано", expired: "Предложение отозвано по сроку" };
const narrativeGroups = ["projects", "experience", "achievements"] as const;
const groupLabels = { projects: "Проекты", experience: "Опыт работы", achievements: "Достижения" };
function date(value: string | null): string { if (!value) return ""; const parsed = new Date(value); return Number.isNaN(parsed.getTime()) ? "" : new Intl.DateTimeFormat("ru-RU", { day: "numeric", month: "long", year: "numeric" }).format(parsed); }
function initials(name: string): string { return name.trim().split(/\s+/).slice(0, 2).map(part => part[0]).join("").toUpperCase() || "?"; }
function safeLink(value: string | null | undefined): string | undefined { if (!value) return; try { const url = new URL(value); if (url.protocol === "https:" || url.protocol === "http:") return url.href; } catch { /* Invalid declarations remain readable text, never executable links. */ } }

function NarrativeSection({ title, items, edit, achievements = false }: { title: string; items: CandidateDeclaredNarrative[]; edit: () => void; achievements?: boolean }) {
  return <section className="candidate-profile-section"><header><h2>{title}</h2><Action variant="primary" onClick={edit}>Добавить</Action></header><div className="candidate-narratives">{items.map((item, index) => <article className="panel candidate-narrative" key={item.id || index}><h3>{item.title}</h3>{item.organization && <p className="muted">{item.organization}</p>}{item.description && <p>{item.description}</p>}{safeLink(item.link) && <a href={safeLink(item.link)} target="_blank" rel="noopener noreferrer">Ссылка на работу ↗</a>}{achievements && item.id && <AchievementDocuments claimId={item.id} editable />}</article>)}{!items.length && <p className="candidate-empty-copy">Пока ничего не добавлено</p>}</div></section>;
}

/** Candidate-owned resume with explicit declarations and a CAS editor; evidence is never submitted as input. */
export function ProfileScreen({ navigate }: { navigate: Navigate }) {
  const resource = useResource(readProfile);
  const catalog = useResource(() => api.getCandidateSkillCatalog(), [resource.data?.revision]);
  const [editing, setEditing] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  if (resource.loading || resource.error) return <State loading={resource.loading} error={resource.error} retry={resource.refresh} />;
  const profile = resource.data, fields = profile?.fields || {};
  if (editing || !profile) return <ProfileEditor profile={profile} onCancel={profile ? () => setEditing(false) : undefined} onReload={() => { setEditing(false); resource.refresh(); }} onSave={(value, warning) => { resource.setData(value); setEditing(false); setMessage(warning || "Резюме сохранено"); }} />;
  const tags = [fields.primary_direction ? directions[fields.primary_direction] : "", fields.city || "", ...(fields.work_types || []).map(value => workTypes[value]), fields.salary ? salary(fields.salary.from_rub, fields.salary.to_rub) : ""];
  return <Reveal className="candidate-profile-page">
    {message && <p role="status" className="candidate-success">{message}</p>}
    <section className="candidate-profile-hero"><h1>{fields.display_name || "Ваше резюме"}</h1><div className="candidate-profile-avatar" aria-label="Инициалы кандидата">{initials(fields.display_name || "")}</div><Tags className="candidate-chips" shape="pill" values={tags} />{fields.description && <p className="panel candidate-profile-bio">{fields.description}</p>}</section>
    <div className="candidate-profile-body"><WeeklyChallenge changed={resource.refresh} /><section className="candidate-profile-section"><header><h2>Навыки</h2><Action variant="primary" onClick={() => setEditing(true)}>Добавить</Action></header>
      <p className="muted">Выберите навык и укажите свой уровень. Проверка подтверждает уровень этого же навыка.</p>
      {(catalog.loading || catalog.error) && <State loading={catalog.loading} error={catalog.error} retry={catalog.refresh} />}
      {(["domains", "stacks", "soft_skills"] as const).map(group => fields[group]?.length ? <article className="panel candidate-profile-row" key={group}><h3>{SKILL_GROUP_LABELS[group]}</h3>{fields[group]!.map(skill => {
        const choice = catalog.data?.skills.find(item => item.group === group && item.key === skill.key);
        return <div className="candidate-skill-assessment" key={skill.key}><strong>{choice?.label ?? skillLabel(group, skill.key)}</strong><span>Указано: {skillLevelLabel(skill.level)}</span>
          <span className="muted">{choice?.confirmed_level != null ? `Подтверждено: ${skillLevelLabel(choice.confirmed_level)}` : "Указано вами"}</span>
          {choice ? <TestingLaunch skill={choice} /> : <Action variant="light" disabled>{catalog.data ? "Нужно заменить навык" : "Проверяем каталог…"}</Action>}
          {catalog.data && !choice && <small>Старый навык сохранён. Замените его из каталога или удалите в редакторе.</small>}
        </div>;
      })}</article> : null)}
      {!(fields.domains?.length || fields.stacks?.length || fields.soft_skills?.length) && <p>Добавьте свои навыки</p>}
    </section>
    <section className="candidate-profile-section candidate-external-achievements"><h2>Достижения — внешний контур + ФСП</h2><FspLink profile={profile} onSaved={value => { resource.setData(value); setMessage("Достижения демо-ФСП добавлены в резюме"); }} /><article className="panel candidate-profile-row"><h3>GitHub</h3><p className="muted">{fields.projects?.length ? `Указано проектов: ${fields.projects.length}. Проверенные репозитории здесь пока не доступны.` : "Добавьте проекты в резюме. Проверенные репозитории здесь пока не доступны."}</p></article></section>
    {narrativeGroups.filter(group => fields[group]?.length).map(group => <NarrativeSection key={group} achievements={group === "achievements"} title={groupLabels[group]} items={fields[group] || []} edit={() => setEditing(true)} />)}
    <ProfileVisibility profile={profile} edit={() => setEditing(true)} reload={resource.refresh} saved={value => { resource.setData(value); setMessage(value.consents.pool ? "Резюме отправлено в подбор" : "Резюме отозвано из подбора"); }} />
    <div className="candidate-profile-actions"><Action onClick={() => navigate("/offers")}>К предложениям</Action><Action variant="primary" onClick={() => setEditing(true)}>Редактировать резюме</Action></div></div>
  </Reveal>;
}

/** Consent and current revision readiness are independent; delivery alone never means publication. */
function ProfileVisibility({ profile, edit, reload, saved }: { profile: CandidateProfileView; edit: () => void; reload: () => void; saved: (value: CandidateProfileView) => void }) {
  const pipeline = useResource(async () => {
    if (!profile.consents.pool || !profile.consents.processing || profile.projection.state !== "delivered" || profile.projection.source_revision !== profile.revision) return null;
    try { return await api.getCandidatePipeline(); } catch (reason) {
      if (reason instanceof ApiError && (reason.status === 409 || reason.status === 404)) return null;
      throw reason;
    }
  }, [profile.revision, profile.consents.pool, profile.consents.processing, profile.projection.state, profile.projection.source_revision, profile.projection.projected_revision]);
  const [retrying, setRetrying] = useState(false), [retryError, setRetryError] = useState<string | null>(null);
  const [retried, setRetried] = useState(false);
  const [publishing, setPublishing] = useState(false), [publicationError, setPublicationError] = useState<string | null>(null), [withdrawalOpen, setWithdrawalOpen] = useState(false);
  const labels: Record<string, string> = { display_name: "имя", email_verified: "подтверждённая почта", city: "город", salary: "зарплатные ожидания", primary_direction: "направление", processing_consent: "согласие на обработку", pool_consent: "согласие на показ работодателям", consent_timestamp: "сохранённое согласие", consent_version: "актуальное согласие", approved_grade_result: "общий уровень опыта для категории подбора" };
  const current = pipeline.data?.profile_revision === profile.projection.projected_revision ? pipeline.data : null;
  let text = "Резюме сохранено. Готовим его для подбора.";
  if (!profile.consents.pool) text = "Показ работодателям выключен. Опубликуйте резюме, чтобы участвовать в подборе.";
  else if (!profile.consents.processing) text = "Для подбора нужно согласие на обработку резюме.";
  else if (profile.projection.state === "failed" && !retried) text = "Не удалось передать резюме в подбор. Можно повторить отправку.";
  else if (current?.missing_requirements.length) text = `Для подбора добавьте: ${current.missing_requirements.map(key => labels[key] || "недостающие данные профиля").join(", ")}.`;
  else if (current?.state === "ready") text = "Резюме участвует в подборе работодателей.";
  else if (current?.state === "failed") text = "Не удалось подготовить резюме. Обновите статус или попробуйте позже.";
  async function retry() {
    if (retrying) return;
    setRetrying(true); setRetryError(null);
    try { await api.retryCandidateProjection(); setRetried(true); reload(); } catch (reason) { setRetryError(errorText(reason)); } finally { setRetrying(false); }
  }
  async function publish(pool: boolean) {
    if (publishing) return;
    if (pool) {
      const fields = profile.fields;
      const missing = [!profile.consents.processing && "согласие на обработку данных", !fields.display_name?.trim() && "имя", !fields.city?.trim() && "город", !fields.salary && "зарплатные ожидания", !fields.primary_direction && "направление", !fields.declared_grade && "категорию опыта"].filter(Boolean);
      if (missing.length) { setPublicationError(`Для публикации заполните в резюме: ${missing.join(", ")}.`); return; }
    }
    setPublishing(true); setPublicationError(null);
    try {
      const training = Boolean(pool && profile.consents.training);
      saved(await api.saveCandidateProfile({ expected_revision: profile.revision, fields: {}, consent: { processing: profile.consents.processing, pool, version: profile.consents.version || "profile-v1", training, ...(training ? { training_version: profile.consents.training_version || "training-v1" } : {}) } }));
      setWithdrawalOpen(false);
    } catch (reason) { setPublicationError(errorText(reason)); } finally { setPublishing(false); }
  }
  return <section className="candidate-profile-section"><h2>Видимость профиля</h2><div className="panel candidate-profile-row"><p role="status">{text}</p>{pipeline.error && <State error="Не удалось проверить готовность подбора." retry={pipeline.refresh} />}{retryError && <p role="alert">{retryError}</p>}<div className="candidate-publication-actions">{!profile.consents.pool || !profile.consents.processing || current?.missing_requirements.length ? <Action onClick={edit}>Настроить резюме</Action> : profile.projection.state === "failed" && !retried ? <Action disabled={retrying} onClick={() => void retry()}>Повторить отправку</Action> : <Action disabled={pipeline.loading} onClick={pipeline.refresh}>Обновить статус</Action>}<Action className={profile.consents.pool ? "action-danger" : ""} variant={profile.consents.pool ? "glass" : "primary"} disabled={publishing} onClick={() => { setPublicationError(null); if (profile.consents.pool) setWithdrawalOpen(true); else void publish(true); }}>{publishing ? "Сохраняем…" : profile.consents.pool ? "Отозвать резюме" : "Опубликовать резюме"}</Action></div>{publicationError && !withdrawalOpen && <><p className="form-error" role="alert">{publicationError}</p><Action onClick={edit}>Редактировать резюме</Action><Action onClick={reload}>Загрузить актуальное резюме</Action></>}</div>{withdrawalOpen && <Dialog title="Отозвать резюме из подбора?" close={() => { if (!publishing) setWithdrawalOpen(false); }}><p>Работодатели больше не увидят резюме в подборе. Предыдущая переписка останется доступной для чтения, пока включено согласие на обработку данных. Данные резюме останутся сохранены, и вы сможете опубликовать его снова.</p>{publicationError && <p className="form-error" role="alert">{publicationError}</p>}<div className="form-actions"><Action disabled={publishing} onClick={() => setWithdrawalOpen(false)}>Отмена</Action><Action className="action-danger" disabled={publishing} onClick={() => void publish(false)}>{publishing ? "Отзываем…" : "Отозвать резюме"}</Action></div>{publicationError && <Action disabled={publishing} onClick={reload}>Загрузить актуальное резюме</Action>}</Dialog>}</section>;
}

/** Restore only known editable field shapes; browser drafts never restore evidence or consent. */
function validProfileDraft(value: unknown): value is CandidateProfileFields {
  const record = (item: unknown): item is Record<string, unknown> => Boolean(item) && typeof item === "object" && !Array.isArray(item);
  const strings = (item: unknown): item is string[] => Array.isArray(item) && item.length <= 2000 && item.every(part => typeof part === "string");
  const finite = (item: unknown): item is number => typeof item === "number" && Number.isFinite(item);
  const nullableString = (item: unknown) => item === null || typeof item === "string";
  const skill = (item: unknown) => record(item) && Object.keys(item).every(key => ["key", "level"].includes(key)) && typeof item.key === "string" && finite(item.level) && item.level >= 0 && item.level <= 7;
  const facts = (item: unknown) => item === null || record(item) && Object.entries(item).every(([key, part]) => key === "domains" || key === "stacks" ? strings(part) : key === "duration_months" ? part === null || finite(part) && part >= 0 : key === "production_support" ? part === null || typeof part === "boolean" : key === "role" && (part === null || ["developer", "lead", "architect", "analyst", "designer", "researcher", "qa", "devops", "other"].some(role => role === part)));
  const narrative = (item: unknown) => record(item) && typeof item.title === "string" && Object.entries(item).every(([key, part]) => key === "facts" ? facts(part) : ["link", "organization"].includes(key) ? nullableString(part) : ["id", "title", "description"].includes(key) && typeof part === "string");
  if (!record(value)) return false;
  return Object.entries(value).every(([key, part]) => {
    if (["display_name", "city", "description"].includes(key)) return typeof part === "string";
    if (["phone", "fsp_id", "fsp_claim_id"].includes(key)) return nullableString(part);
    if (["relocation_cities", "preferences"].includes(key)) return strings(part);
    if (key === "work_types") return part === null || Array.isArray(part) && part.every(item => ["onsite", "remote", "hybrid"].some(type => type === item));
    if (key === "relocation_ready") return part === null || typeof part === "boolean";
    if (key === "salary") return part === null || record(part) && Object.keys(part).every(field => ["from_rub", "to_rub"].includes(field)) && finite(part.from_rub) && (part.to_rub === null || finite(part.to_rub) && part.to_rub >= part.from_rub) && part.from_rub >= 0;
    if (key === "primary_direction") return part === null || SPECIALIZATIONS.some(direction => direction === part);
    if (key === "declared_grade") return part === null || GRADES.some(grade => grade === part);
    if (["domains", "stacks", "soft_skills"].includes(key)) return Array.isArray(part) && part.every(skill);
    if (["projects", "experience", "achievements"].includes(key)) return Array.isArray(part) && part.every(narrative);
    return false;
  });
}

function ProfileEditor({ profile, onCancel, onSave, onReload }: { profile: CandidateProfileView | null; onCancel?: () => void; onReload: () => void; onSave: (profile: CandidateProfileView, warning?: string) => void }) {
  const draftKey = `profile-editor:${profile?.revision || 0}:`;
  const catalog = useResource(() => api.getCandidateSkillCatalog());
  const [fields, setFields] = usePersistentState<CandidateProfileFields>(`${draftKey}fields`, () => structuredClone(profile?.fields || {}), validProfileDraft);
  const [relocationText, setRelocationText] = usePersistentState(`${draftKey}relocation`, (profile?.fields.relocation_cities || []).join(", "));
  const [preferenceText, setPreferenceText] = usePersistentState(`${draftKey}preferences`, (profile?.fields.preferences || []).join("\n"));
  const [pendingFiles, setPendingFiles] = useState<Record<string, File>>({});
  const [processing, setProcessing] = useState(profile?.consents.processing || false);
  const [pool, setPool] = useState(profile?.consents.pool || false);
  const [withdrawalOpen, setWithdrawalOpen] = useState(false);
  const [busy, setBusy] = useState(false), [error, setError] = useState<string | null>(null);
  function update<K extends keyof CandidateProfileFields>(key: K, value: CandidateProfileFields[K]) { setFields(current => ({ ...current, [key]: value })); }
  function updateNarrative(group: typeof narrativeGroups[number], index: number, value: Partial<CandidateDeclaredNarrative>) { update(group, (fields[group] || []).map((item, position) => position === index ? { ...item, ...value } : item)); }
  function updateSkill(group: "domains" | "stacks" | "soft_skills", index: number, value: Partial<CandidateDeclaredSkill>) { update(group, (fields[group] || []).map((item, position) => position === index ? { ...item, ...value } : item)); }
  async function withdrawConsent() {
    if (busy || !profile) return;
    setBusy(true); setError(null);
    try {
      const saved = await api.saveCandidateProfile({ expected_revision: profile.revision, fields: {}, consent: { processing: false, pool: false, version: profile.consents.version || "profile-v1", training: false } });
      clearPersistentState(`candidate:${saved.candidate_id}`, draftKey); onSave(saved);
    } catch (reason) { setError(errorText(reason)); } finally { setBusy(false); }
  }
  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (busy) return;
    const missing = [!fields.display_name?.trim() && "имя", !fields.city?.trim() && "город", !fields.salary && "зарплатные ожидания", !fields.primary_direction && "направление", !fields.declared_grade && "категорию опыта"].filter(Boolean);
    if (missing.length) { setError(`Чтобы сохранить резюме и участвовать в подборе, заполните: ${missing.join(", ")}.`); return; }
    if (fields.salary && (!Number.isSafeInteger(fields.salary.from_rub) || fields.salary.from_rub < 0)) { setError("Укажите зарплату целым числом рублей."); return; }
    if (!event.currentTarget.reportValidity()) return;
    if (fields.salary && fields.salary.to_rub !== null && fields.salary.to_rub < fields.salary.from_rub) { setError("Верхняя граница зарплаты должна быть не меньше нижней."); return; }
    if (!processing || !pool) { setError("Чтобы сохранить резюме, согласитесь на обработку данных и включите показ работодателям."); return; }
    setBusy(true); setError(null);
    const editableFields = { ...profileFieldsForSave(fields, profile?.fields), ...(fields.salary ? { salary: { from_rub: fields.salary.from_rub, to_rub: null } } : {}) }; delete editableFields.fsp_id; delete editableFields.fsp_claim_id;
    const body: CandidateProfileMutation = { expected_revision: profile?.revision || 0, fields: { ...editableFields, relocation_cities: relocationText.split(",").map(value => value.trim()).filter(Boolean), preferences: preferenceText.split("\n").map(value => value.trim()).filter(Boolean) } };
    if (!profile || processing !== profile.consents.processing || pool !== profile.consents.pool) body.consent = { processing, pool, version: profile?.consents.version || "profile-v1", training: Boolean(profile?.consents.training && processing && pool), ...(profile?.consents.training && processing && pool ? { training_version: profile?.consents.training_version || "training-v1" } : {}) };
    try {
      const saved = await api.saveCandidateProfile(body);
      let failedFiles = 0;
      for (const [index, claim] of (saved.fields.achievements ?? []).entries()) {
        const editorId = fields.achievements?.[index]?.id;
        const file = editorId ? pendingFiles[editorId] : undefined;
        if (file && claim.id) { try { await uploadAchievementFile(file, claim.id); } catch { failedFiles++; } }
      }
      clearPersistentState(`candidate:${saved.candidate_id}`, draftKey);
      onSave(saved, failedFiles ? `Резюме сохранено, но не удалось загрузить документов: ${failedFiles}. Добавьте их ещё раз в разделе достижений.` : undefined);
    } catch (reason) { setError(errorText(reason)); } finally { setBusy(false); }
  }
  return <Reveal className="candidate-profile-editor"><PageTitle title={profile ? "Редактировать резюме" : "Создать резюме"} back={onCancel} /><form onSubmit={save} noValidate><fieldset disabled={busy}><div className="form-grid"><ResumeImport onApply={suggestions => setFields(current => mergeResumeSuggestions(current, suggestions))} /><div className="form-row"><Field label="Ваше имя"><input required maxLength={160} value={fields.display_name || ""} onChange={event => update("display_name", event.target.value)} /></Field><Field label="Город"><input maxLength={160} value={fields.city || ""} onChange={event => update("city", event.target.value)} /></Field></div>
    <div className="form-row"><Field label="Направление"><Select aria-label="Направление" value={fields.primary_direction || ""} onChange={event => { const value = event.target.value; update("primary_direction", SPECIALIZATIONS.find(item => item === value) || null); }}><option value="">Не выбрано</option>{SPECIALIZATIONS.map(value => <option value={value} key={value}>{directions[value]}</option>)}</Select></Field><Field label="Общая категория опыта" hint="Нужна для категории подбора. Уровень каждого навыка задаётся отдельно ниже."><Select aria-label="Указанный вами грейд" value={fields.declared_grade || ""} onChange={event => update("declared_grade", GRADES.find(item => item === event.target.value) || null)}><option value="">Не выбран</option>{GRADES.map(value => <option key={value} value={value}>{grades[value]}</option>)}</Select></Field></div>
    <Field label="О себе"><textarea maxLength={10000} value={fields.description || ""} onChange={event => update("description", event.target.value)} /></Field><div className="form-row"><Field label="Телефон" hint="Контакты открываются работодателю после принятия приглашения."><input type="tel" maxLength={64} value={fields.phone || ""} onChange={event => update("phone", event.target.value || null)} /></Field><Field label="ID участника ФСП" hint="Подключение доступно в разделе ФСП после сохранения резюме."><input readOnly value={profile?.fields.fsp_id || "Не указан"} /></Field></div>
    <Field label="Минимальная зарплата, ₽" hint="Укажите сумму, от которой готовы рассматривать предложения. Верхнего ограничения нет."><input type="text" inputMode="numeric" pattern="[0-9]+" maxLength={16} value={fields.salary?.from_rub ?? ""} onChange={event => { const value = event.target.value.replace(/\s/g, ""); if (!/^\d*$/.test(value)) return; update("salary", value === "" ? null : { from_rub: Number(value), to_rub: null }); }} /></Field>
    <div className="candidate-checks">{(["onsite", "remote", "hybrid"] as const).map(value => <label key={value}><Checkbox checked={fields.work_types?.includes(value) || false} onChange={event => update("work_types", event.target.checked ? [...(fields.work_types || []), value] : (fields.work_types || []).filter(item => item !== value))} />{workTypes[value]}</label>)}</div>
    <label className="candidate-checks candidate-relocation-check"><Checkbox checked={fields.relocation_ready || false} onChange={event => update("relocation_ready", event.target.checked)} />Готовность к переезду</label>
    <Field label="Города для переезда, через запятую"><input disabled={!fields.relocation_ready} value={relocationText} onChange={event => setRelocationText(event.target.value)} /></Field>
    <Field label="Предпочтения, каждое с новой строки"><textarea value={preferenceText} onChange={event => setPreferenceText(event.target.value)} /></Field>
    {(catalog.loading || catalog.error) && <State loading={catalog.loading} error={catalog.error} retry={catalog.refresh} />}
    {(["domains", "stacks", "soft_skills"] as const).map(group => {
      const choices = catalog.data?.skills.filter(item => item.group === group) ?? [];
      const unused = choices.filter(item => !(fields[group] ?? []).some(skill => skill.key === item.key));
      return <section className="candidate-editor-group" key={group}><h2>{SKILL_GROUP_LABELS[group]}</h2>{(fields[group] || []).map((skill, index) => {
        const known = choices.some(item => item.key === skill.key);
        return <div className="candidate-skill-edit" key={index}><Field label="Навык" hint={catalog.data && !known ? "Старый навык: замените из каталога или удалите. Дополнительный опыт можно описать в разделе «О себе»." : undefined}>
          <Select required aria-label={`Навык ${index + 1}: ${SKILL_GROUP_LABELS[group]}`} value={skill.key} disabled={!catalog.data} onChange={event => updateSkill(group, index, { key: event.target.value, level: skill.level || 1 })}>
            {!known && <option value={skill.key}>{skill.key || "Выберите навык"} — прежнее значение</option>}
            {choices.filter(item => item.key === skill.key || !(fields[group] ?? []).some(other => other.key === item.key)).map(item => <option key={item.key} value={item.key}>{item.label}</option>)}
          </Select></Field><Field label="Ваш уровень"><Select aria-label={`Уровень: ${skillLabel(group, skill.key)}`} value={skill.level} disabled={!known} onChange={event => updateSkill(group, index, { level: Number(event.target.value) })}>
            {skill.level === 0 && <option value={0}>Уровень не выбран</option>}{SKILL_LEVELS.map((label, level) => <option value={level + 1} key={label}>{label}</option>)}
          </Select></Field><Action type="button" onClick={() => update(group, (fields[group] || []).filter((_, position) => position !== index))} aria-label={`Удалить навык ${skillLabel(group, skill.key)}`}>Удалить</Action></div>;
      })}<Action type="button" disabled={!unused.length} onClick={() => { const first = unused[0]; if (first) update(group, [...(fields[group] || []), { key: first.key, level: 1 }]); }}>Добавить навык</Action></section>;
    })}
    {narrativeGroups.map(group => <section className="candidate-editor-group" key={group}><h2>{groupLabels[group]}</h2>{(fields[group] || []).map((item, index) => <div className="panel candidate-narrative-edit" key={item.id || index}><Field label="Название"><input required maxLength={300} value={item.title} onChange={event => updateNarrative(group, index, { title: event.target.value })} /></Field><Field label={group === "achievements" ? "Мероприятие" : "Организация"}><input maxLength={300} value={item.organization || ""} onChange={event => updateNarrative(group, index, { organization: event.target.value })} /></Field><Field label="Описание"><textarea maxLength={10000} value={item.description || ""} onChange={event => updateNarrative(group, index, { description: event.target.value })} /></Field><Field label="Ссылка"><input type="url" maxLength={2000} pattern="https?://.*" value={item.link || ""} onChange={event => updateNarrative(group, index, { link: event.target.value || null })} /></Field>{group === "achievements" && (item.id && profile?.fields.achievements?.some(claim => claim.id === item.id) ? <AchievementDocuments claimId={item.id} editable /> : <Field label="Фото или PDF достижения" hint="До 10 МБ. Файл загрузится после сохранения резюме."><input type="file" accept="application/pdf,image/png,image/jpeg,image/webp" onChange={event => { const file = event.target.files?.[0]; if (!item.id || !file) return; if (file.size === 0 || file.size > 10 * 1024 * 1024 || !["application/pdf", "image/png", "image/jpeg", "image/webp"].includes(file.type)) { setError("Выберите PDF или фотографию до 10 МБ."); event.target.value = ""; return; } setPendingFiles(current => ({ ...current, [item.id!]: file })); }} />{item.id && pendingFiles[item.id] && <small>{pendingFiles[item.id]?.name} — готов к загрузке</small>}</Field>)}<Action type="button" onClick={() => update(group, (fields[group] || []).filter((_, position) => position !== index))}>Удалить запись</Action></div>)}<Action type="button" onClick={() => update(group, [...(fields[group] || []), { id: crypto.randomUUID(), title: "", description: "" }])}>Добавить запись</Action></section>)}
    <section className="panel candidate-consent"><h2>Согласие и видимость</h2><p>Это отдельные настройки резюме. Согласие при регистрации их не заменяет.</p><p className="muted">Для сохранения резюме нужны обе отметки.</p><label><Checkbox checked={processing} onChange={event => setProcessing(event.target.checked)} />Согласен(а) на обработку данных резюме</label><label><Checkbox checked={pool} onChange={event => setPool(event.target.checked)} />Показывать резюме работодателям в подборе</label><a href="#/legal/processing-consent" target="_blank" rel="noopener noreferrer">Прочитать условия обработки данных</a>{profile && (profile.consents.processing || profile.consents.pool) && <Action type="button" disabled={busy} onClick={() => { setError(null); setWithdrawalOpen(true); }}>Отозвать согласия резюме</Action>}</section>
    {error && !withdrawalOpen && <div><p className="form-error" role="alert">{error}</p><Action type="button" onClick={onReload}>Загрузить актуальное резюме</Action></div>}<div className="form-actions">{onCancel && <Action type="button" onClick={onCancel}>Отмена</Action>}<Action variant="primary" type="submit" disabled={busy || !processing || !pool}>{busy ? "Сохраняем…" : "Сохранить резюме"}</Action></div></div></fieldset></form>{withdrawalOpen && <Dialog title="Отозвать согласия резюме?" close={() => { if (!busy) setWithdrawalOpen(false); }}><p>Резюме перестанет участвовать в подборе. Переписка по приглашениям станет недоступна. Согласие на использование данных для обучения тоже будет отозвано.</p><p>Изменения в редакторе не будут сохранены.</p>{error && <p className="form-error" role="alert">{error}</p>}<div className="form-actions"><Action type="button" disabled={busy} onClick={() => setWithdrawalOpen(false)}>Отмена</Action><Action type="button" disabled={busy} onClick={() => void withdrawConsent()}>{busy ? "Отзываем…" : "Отозвать согласия"}</Action></div>{error && <Action type="button" disabled={busy} onClick={onReload}>Загрузить актуальное резюме</Action>}</Dialog>}</Reveal>;
}

/** Owner-scoped invitation list; all salary and lifecycle data come from immutable API terms. */
export function OffersScreen({ navigate, query, setQuery, focusSearch = false }: { navigate: Navigate; query: string; setQuery: (value: string) => void; focusSearch?: boolean }) {
  const searchInput = useRef<HTMLInputElement>(null);
  const resource = useResource(readInvitations);
  useEffect(() => {
    const focus = () => searchInput.current?.focus();
    if (focusSearch) focus();
    window.addEventListener("candidate:focus-offer-search", focus);
    return () => window.removeEventListener("candidate:focus-offer-search", focus);
  }, [focusSearch, resource.loading, resource.error]);
  const [filter, setFilter] = usePersistentState("offers:filter", "all", (value): value is string => typeof value === "string" && ["all", "new", "accepted"].includes(value));
  if (resource.loading || resource.error) return <State loading={resource.loading} error={resource.error} retry={resource.refresh} />;
  const items = (resource.data || []).filter(item => item.terms && (filter === "all" || (filter === "new" ? item.state === "sent" || item.state === "viewed" : ["accepted", "offer_sent", "hired"].includes(item.state))) && (!query || `${item.terms.company_name} ${item.terms.vacancy_title || ""}`.toLocaleLowerCase("ru-RU").includes(query.toLocaleLowerCase("ru-RU"))));
  return <Reveal><PageTitle title="Входящие предложения" /><div className="candidate-offer-filters"><InlineSearch inputRef={searchInput} label="Компания или название вакансии" placeholder="Поиск: компания, вакансия" value={query} onChange={setQuery} /><Select aria-label="Показать предложения" value={filter} onChange={event => setFilter(event.target.value)}><option value="all">Все предложения</option><option value="new">Новые приглашения</option><option value="accepted">Принятые приглашения</option></Select><span className="muted">{items.length} предложений</span></div>{items.length ? <div className="candidate-offer-grid">{items.map(item => <InvitationCard key={item.id} item={item} navigate={navigate} />)}</div> : <State empty={query ? "По этому запросу предложений нет" : "Здесь появятся предложения работодателей"} />}</Reveal>;
}
function InvitationCard({ item, navigate }: { item: RecruitmentView; navigate: Navigate }) {
  const terms = item.terms; if (!terms) return null;
  return <article className="candidate-offer-card"><div className="candidate-offer-card-head"><span className="candidate-offer-status">{states[item.state]}</span></div><button className="candidate-offer-open" onClick={() => navigate(`/offer/${item.id}`)} aria-label={`Открыть предложение ${terms.company_name}`}><h2>{terms.vacancy_title || "Предложение о работе"}</h2><p>{terms.company_name}</p></button><Tags className="candidate-chips" variant="blue" values={[terms.city, workTypes[terms.work_type as keyof typeof workTypes] ?? terms.work_type, terms.daily]} /><div className="candidate-offer-salary">{salary(terms.salary.from_rub, terms.salary.to_rub)}</div><footer><span><Icon name="clock" />{date(item.invited_at || item.created_at)}</span><button className="candidate-offer-arrow-button" onClick={() => navigate(`/offer/${item.id}`)} aria-label={`Подробнее о предложении ${terms.company_name}`}><VacancyCardArrow /></button></footer></article>;
}

/** Invitation details enforce server allowed_actions, revisions, and stable retry identities for every mutation. */
export function OfferScreen({ id, navigate }: { id: string; navigate: Navigate }) {
  const resource = useResource(() => api.getRecruitment("candidate", id), [id]);
  const [busy, setBusy] = useState(false), [error, setError] = useState<string | null>(null), [declining, setDeclining] = useState(false), [reason, setReason] = usePersistentState(`offer:${id}:reason`, "");
  const identities = useRef(new Map<string, string>());
  const viewed = useRef(new Set<string>());
  const [decliningInterview, setDecliningInterview] = useState<string | null>(null);
  const currentId = useRef(id); currentId.current = id;
  useEffect(() => { setBusy(false); setError(null); setDeclining(false); setDecliningInterview(null); }, [id]);
  async function act(action: RecruitmentAction["action"], extra?: { reason?: string; offer_id?: string; interview_id?: string }) {
    const item = resource.data; if (!item || item.id !== id || busy || !item.allowed_actions.includes(action)) return;
    const key = `${id}:${item.revision}:${action}:${extra?.offer_id || extra?.interview_id || ""}:${extra?.reason || ""}`;
    const identity = identities.current.get(key) || crypto.randomUUID(); identities.current.set(key, identity);
    const common = { expected_revision: item.revision, idempotency_key: identity };
    let body: RecruitmentAction;
    if (action === "accept" || action === "view") body = { ...common, action };
    else if (action === "decline") body = { ...common, action, reason: extra?.reason || "Кандидат отклонил приглашение" };
    else if (action === "offer_accept" && extra?.offer_id) body = { ...common, action, offer_id: extra.offer_id };
    else if (action === "offer_decline" && extra?.offer_id) body = { ...common, action, offer_id: extra.offer_id, reason: extra.reason || "Кандидат отклонил оффер" };
    else if (action === "interview_accept" && extra?.interview_id) body = { ...common, action, interview_id: extra.interview_id };
    else if (action === "interview_decline" && extra?.interview_id) body = { ...common, action, interview_id: extra.interview_id, reason: extra.reason || "Кандидату не подходит время собеседования" };
    else return;
    setBusy(true); setError(null);
    try { const result = await api.actOnRecruitment("candidate", id, body); if (currentId.current === id) { resource.setData(result); setDeclining(false); setDecliningInterview(null); setReason(""); } } catch (failure) { if (currentId.current === id) setError(errorText(failure)); } finally { if (currentId.current === id) setBusy(false); }
  }
  useEffect(() => {
    if (busy || resource.data?.id !== id || !resource.data.allowed_actions.includes("view") || viewed.current.has(id)) return;
    viewed.current.add(id); void act("view");
  }, [id, resource.data, busy]);
  if (resource.loading || resource.error) return <State loading={resource.loading} error={resource.error} retry={resource.refresh} />;
  const item = resource.data, terms = item?.terms;
  if (!item || !terms) return <State empty="Предложение не найдено" />;
  return <Reveal><PageTitle title={`${terms.company_name} · ${terms.vacancy_title || "Предложение о работе"}`} back={() => navigate("/offers")} /><div className="candidate-offer-detail"><section className="panel candidate-offer-terms"><span className="candidate-offer-status">{states[item.state]}</span><RecruitmentClock deadline={item.deadline} /><h2>{terms.company_name}</h2><div className="candidate-offer-salary">{salary(terms.salary.from_rub, terms.salary.to_rub)}</div><Tags className="candidate-chips" variant="blue" values={[terms.city, workTypes[terms.work_type as keyof typeof workTypes] ?? terms.work_type, terms.daily]} /><p>{terms.description}</p><small>Приглашение от {date(item.invited_at || item.created_at)}</small><div className="candidate-offer-actions">{item.allowed_actions.includes("accept") && <Action variant="primary" disabled={busy} onClick={() => void act("accept")}>Принять приглашение</Action>}{item.allowed_actions.includes("decline") && <Action disabled={busy} onClick={() => setDeclining(true)}>Отклонить приглашение</Action>}<Action disabled={busy} onClick={resource.refresh}>Обновить</Action></div>{declining && <form className="candidate-decline" onSubmit={event => { event.preventDefault(); void act("decline", { reason: reason.trim() }); }}><Field label="Причина отказа"><textarea required maxLength={2000} value={reason} onChange={event => setReason(event.target.value)} disabled={busy} /></Field><div className="candidate-offer-actions"><Action type="button" disabled={busy} onClick={() => setDeclining(false)}>Отмена</Action><Action type="submit" disabled={busy}>Подтвердить отказ</Action></div></form>}{error && <p role="alert" className="form-error">{error}</p>}</section>
    {(item.interviews.length > 0 || item.offers.length > 0) && <section className="panel candidate-offer-progress"><h2>Собеседования и офферы</h2>{item.interviews.map(interview => <article key={interview.id}><h3>{interview.kind === "technical" ? "Техническое собеседование" : "Собеседование"}</h3><p>{new Intl.DateTimeFormat("ru-RU", { dateStyle: "long", timeStyle: "short", timeZone: interview.time_zone }).format(new Date(interview.starts_at))} · {interview.time_zone}</p><p>{interview.location}</p>{interview.state === "proposed" && item.allowed_actions.includes("interview_accept") && <Action disabled={busy} onClick={() => void act("interview_accept", { interview_id: interview.id })}>Подтвердить время</Action>}{interview.state === "proposed" && item.allowed_actions.includes("interview_decline") && <Action disabled={busy} onClick={() => { setDecliningInterview(interview.id); }}>Отклонить время собеседования</Action>}{decliningInterview === interview.id && <form className="candidate-decline" onSubmit={event => { event.preventDefault(); void act("interview_decline", { interview_id: interview.id, reason: reason.trim() }); }}><Field label="Почему время не подходит?"><textarea required maxLength={2000} value={reason} onChange={event => setReason(event.target.value)} disabled={busy} /></Field><div className="candidate-offer-actions"><Action type="button" disabled={busy} onClick={() => setDecliningInterview(null)}>Отмена</Action><Action type="submit" disabled={busy}>Отправить ответ</Action></div></form>}</article>)}{item.offers.map(offer => <article key={offer.id}><h3>Предложение о работе</h3><p>{new Intl.NumberFormat("ru-RU").format(offer.salary_rub)} ₽</p><p>{offer.description}</p><small>Действует до {date(offer.expires_at)}</small>{offer.state === "sent" && item.allowed_actions.includes("offer_accept") && <Action variant="primary" disabled={busy} onClick={() => void act("offer_accept", { offer_id: offer.id })}>Принять оффер</Action>}{offer.state === "sent" && item.allowed_actions.includes("offer_decline") && <Action disabled={busy} onClick={() => void act("offer_decline", { offer_id: offer.id })}>Отклонить оффер</Action>}</article>)}</section>}
    {item.chat_available ? <div className="candidate-offer-chat-link"><Action onClick={() => navigate(`/chat/${item.id}`)}>Перейти в чат</Action></div> : <p className="muted">Переписка по этому приглашению пока недоступна.</p>}</div></Reveal>;
}

const candidateConversationTransport: ConversationTransport = {
  markRead: (id, throughSequence) => api.acknowledgeChatRead("candidate", id, { through_sequence: throughSequence }),
  getMessages: (id, after, limit) => api.getChatMessages("candidate", id, after, limit),
  sendMessage: (id, body) => api.sendChatMessage("candidate", id, body),
  uploadFile: (id, body) => api.uploadChatFile("candidate", id, body),
  downloadFile: (id, fileId) => api.downloadChatFile("candidate", id, fileId),
};

/** The chat directory reads invitation metadata only; private messages load after choosing a thread. */
export function ChatsScreen({ navigate, systemUnread }: { navigate: Navigate; systemUnread?: number }) {
  const resource = useResource(readInvitations);
  const [refreshError, setRefreshError] = useState<string | null>(null);
  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try { const items = await readInvitations(); if (active) { resource.setData(items); setRefreshError(null); } }
      catch (reason) { if (active) setRefreshError(errorText(reason)); }
      finally { if (active) timer = setTimeout(poll, 5000); }
    };
    timer = setTimeout(poll, 5000);
    return () => { active = false; clearTimeout(timer); };
  }, []);
  const items = (resource.data || []).flatMap(item => item.chat_available && item.terms ? [{ id: item.id, title: item.terms.company_name, subtitle: item.terms.vacancy_title || "Предложение о работе", state: states[item.state], unread: item.chat_unread_count }] : []);
  return <Reveal><PageTitle title="Чаты" />{refreshError && <p className="form-error" role="alert">{refreshError}</p>}<RecruitmentChatList items={[{ id: "comit-system", title: "Comit", subtitle: "Уведомления платформы", state: "Системный чат", unread: systemUnread, system: true }, ...items]} loading={resource.loading} error={resource.error} retry={resource.refresh} emptyText="Здесь появятся чаты с работодателями" onOpen={id => navigate(`/chat/${id}`)} /></Reveal>;
}

/** A standalone authorized conversation keeps the caller in its URL and refreshes server permissions. */
export function ChatScreen({ id, route, navigate }: { id: string; route: string; navigate: Navigate }) {
  const resource = useResource(() => api.getRecruitment("candidate", id), [id]);
  const [pollError, setPollError] = useState<string | null>(null);
  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    setPollError(null);
    const poll = async () => {
      try {
        const process = await api.getRecruitment("candidate", id);
        if (active) { resource.setData(process); setPollError(null); }
      } catch (reason) {
        if (active) { setPollError(errorText(reason)); resource.setData(current => current ? { ...current, chat_writable: false } : current); }
      } finally { if (active) timer = setTimeout(poll, 3000); }
    };
    timer = setTimeout(poll, 3000);
    return () => { active = false; clearTimeout(timer); };
  }, [id]);
  const item = resource.data;
  return <section className="recruitment-screen"><div className="chat-back"><Action className="circle" aria-label="Вернуться" onClick={() => navigate(chatReturnRoute(route, "/chat"))}><Icon name="back" /></Action><h1>Чат</h1></div>{resource.loading || resource.error ? <div className="recruitment-thread"><State loading={resource.loading} error={resource.error} retry={resource.refresh} /></div> : item ? <div className="recruitment-thread"><header className="participant-header"><span className="anonymous-avatar" aria-hidden="true"><Icon name="candidates" /></span><div className="participant-name"><strong>{item.terms?.company_name || "Работодатель"}</strong><small>{states[item.state]}</small></div></header>{pollError && <p className="form-error" role="alert">{pollError}</p>}<RecruitmentConversation key={item.id} process={item} accountId={item.candidate_id} transport={candidateConversationTransport} errorText={errorText} emptyText="Напишите работодателю, чтобы начать разговор." messageLabel="Сообщение работодателю" /></div> : <div className="recruitment-thread"><State empty="Чат не найден" /></div>}</section>;
}

/** Radar shows declared domain levels only; empty axes do not imply assessed skills. */
function DomainRadar({ domains }: { domains: CandidateDeclaredSkill[] }) {
  const axes = Math.max(6, domains.length);
  const point = (index: number, radius: number) => {
    const angle = index * Math.PI * 2 / axes - Math.PI / 2;
    return [150 + Math.cos(angle) * radius, 150 + Math.sin(angle) * radius] as const;
  };
  const ring = (radius: number) => Array.from({ length: axes }, (_, index) => point(index, radius).join(",")).join(" ");
  return <svg className="candidate-domain-radar" viewBox="0 0 300 300" role="img" aria-label="Указанные вами уровни областей знаний от Intern до Senior+">
    {[1, 2, 3, 4, 5].map(level => <polygon key={level} className="candidate-domain-grid" points={ring(level * 20)} />)}
    {Array.from({ length: axes }, (_, index) => { const [x, y] = point(index, 100); return <line key={index} className="candidate-domain-axis" x1="150" y1="150" x2={x} y2={y} />; })}
    <polygon className="candidate-domain-value" points={Array.from({ length: axes }, (_, index) => point(index, 100 * (domains[index]?.level || 0) / 7).join(",")).join(" ")} />
    {domains.map((domain, index) => { const [x, y] = point(index, 100 * domain.level / 7), [labelX, labelY] = point(index, 123); return <g key={domain.key} role="img" aria-label={`${skillLabel("domains", domain.key)}: ${skillLevelLabel(domain.level)}, указано вами`}><circle className="candidate-domain-dot" cx={x} cy={y} r="3" /><text className="candidate-domain-label" x={labelX} y={labelY} textAnchor={labelX < 140 ? "end" : labelX > 160 ? "start" : "middle"} dominantBaseline="middle">{skillLabel("domains", domain.key)}</text></g>; })}
  </svg>;
}

/** Owner dashboard with shared grid rows; only declared per-skill levels are charted. */
export function StatisticsScreen({ navigate }: { navigate: Navigate }) {
  const testingHistory = useResource(() => api.getCandidateTestingHistory());
  const invitations = useResource(readInvitations), profile = useResource(readProfile), recommendations = useResource(() => api.getRecommendationsLatest());
  const items = (invitations.data || []).filter(item => item.terms);
  const cutoff = Date.now() - 30 * 24 * 60 * 60 * 1000;
  const recent = items.filter(item => Date.parse(item.invited_at || item.created_at) >= cutoff);
  const domains = profile.data?.fields.domains || [];
  const skills = (["stacks", "soft_skills"] as const).flatMap(group => (profile.data?.fields[group] || []).map(skill => ({ ...skill, group })));
  return <Reveal><PageTitle title="Твой профиль в цифрах" /><div className="candidate-statistics">
    <section className="panel candidate-stat-recommendations"><h2 className="candidate-recommendations-title">Рекомендации от платформы<span className="candidate-recommendations-info"><button type="button" aria-label="Как формируются рекомендации" aria-describedby="candidate-recommendations-explanation"><Icon name="info" /></button><span className="tooltip" id="candidate-recommendations-explanation" role="tooltip">Рекомендации основаны на агрегированной обратной связи работодателей по вашей кандидатуре и системной аналитике платформы.</span></span></h2>{recommendations.loading || recommendations.error ? <State loading={recommendations.loading} error={recommendations.error} retry={recommendations.refresh} /> : recommendations.data?.item ? <>{recommendations.data.item.text.split(/\n{2,}/).map((paragraph, index) => <p key={index}>{paragraph}</p>)}{recommendations.data.item.state !== "ready" && <p className="muted">{recommendations.data.item.state === "insufficient_data" ? "Для персональных рекомендаций пока недостаточно обезличенных данных." : "AI сейчас недоступен. Показаны рекомендации по известным причинам."}</p>}<small className="muted">Обновлено {date(recommendations.data.item.generated_at)}</small></> : profile.data && !(profile.data.consents.training && profile.data.consents.training_version === "training-v2") ? <><p className="muted">Для персональных рекомендаций нужно отдельное разрешение на использование обезличенных данных. Его можно включить в настройках.</p><Action onClick={() => navigate("/settings")}>Настроить рекомендации</Action></> : <p className="muted">Когда появится достаточно обратной связи и данных подбора, здесь будут ваши еженедельные рекомендации.</p>}</section>
    <section className="panel candidate-stat-counts"><div><strong>—</strong><span>показов в подборе</span><small>Данные пока недоступны</small></div><div><strong>{invitations.loading || invitations.error ? "—" : recent.length}</strong><span>приглашений за 30 дней</span></div><div><strong>—</strong><span>рейтинг профиля</span><small>Данные пока недоступны</small></div></section>
    <section className="panel candidate-stat-history"><h2>История оценок и тестов</h2>{testingHistory.loading || testingHistory.error ? <State loading={testingHistory.loading} error={testingHistory.error} retry={testingHistory.refresh} /> : testingHistory.data ? <TestingHeatmap history={testingHistory.data} /> : <p className="muted">История тестирования пока недоступна.</p>}</section>
    <section className="panel candidate-stat-market"><h2>Рынок З/П</h2><p className="muted">Рыночные зарплаты по категориям пока недоступны.</p><div className="candidate-market-salary"><div><p>Рыночная вилка</p><Tags className="candidate-chips" shape="pill" values={["Данные недоступны"]} /></div><div><p>Ваши ожидания</p><Tags className="candidate-chips" shape="pill" values={[profile.data?.fields.salary ? salary(profile.data.fields.salary.from_rub, profile.data.fields.salary.to_rub) : "Не указаны"]} /></div></div></section>
    <section className="panel candidate-stat-offers"><h2>Приглашения работодателей</h2>{invitations.loading || invitations.error ? <State loading={invitations.loading} error={invitations.error} retry={invitations.refresh} /> : <><div className="candidate-stat-offer-count"><strong>{items.filter(item => ["accepted", "offer_sent", "hired"].includes(item.state)).length}</strong><span>принятых приглашений</span></div><div className="candidate-stat-offer-count"><strong>{items.filter(item => item.state === "sent" || item.state === "viewed").length}</strong><span>ожидают ответа</span></div><Action onClick={() => navigate("/offers")}>Посмотреть предложения</Action></>}</section>
    <section className="panel candidate-stat-domains" aria-label="Диаграмма доменов">{profile.loading || profile.error ? <State loading={profile.loading} error={profile.error} retry={profile.refresh} /> : domains.length ? <DomainRadar domains={domains} /> : <div className="candidate-chart-empty"><p>Домены пока не указаны</p><Action onClick={() => navigate("/resume")}>Добавить в резюме</Action></div>}</section>
    <section className="panel candidate-stat-skills" aria-label="Диаграмма навыков">{profile.loading || profile.error ? <State loading={profile.loading} error={profile.error} retry={profile.refresh} /> : !skills.length ? <div className="candidate-chart-empty"><p>Навыки пока не указаны</p><Action onClick={() => navigate("/resume")}>Добавить в резюме</Action></div> : <svg className="candidate-skill-map" viewBox={`0 0 500 ${Math.max(1, skills.length) * 42}`} role="img" aria-label={skills.length ? "Указанные вами уровни навыков от Intern до Senior+" : "Навыки пока не указаны"}>{skills.map((skill, index) => <g key={`${skill.group}:${skill.key}`} role="img" aria-label={`${skillLabel(skill.group, skill.key)}: ${skillLevelLabel(skill.level)}, указано вами`}><rect className="candidate-skill-track" x="0" y={index * 42} width="500" height="24" rx="12" /><rect className="candidate-skill-value" x="0" y={index * 42} width={500 * skill.level / 7} height="24" rx="12" /></g>)}</svg>}</section>
  </div></Reveal>;
}

/** Explicit recommendations/training opt-in is separate from mandatory profile processing and publication. */
export function RecommendationConsentSettings() {
  const resource = useResource(readProfile);
  const [choice, setChoice] = useState<boolean | null>(null), [busy, setBusy] = useState(false), [error, setError] = useState<string | null>(null), [saved, setSaved] = useState(false);
  const profile = resource.data;
  const enabled = Boolean(profile?.consents.training && profile?.consents.training_version === "training-v2");
  const selected = choice ?? enabled;
  async function save() {
    if (!profile || busy) return;
    if (selected && (!profile.consents.processing || !profile.consents.pool)) { setError("Для рекомендаций нужно действующее согласие на обработку и опубликованное резюме."); return; }
    setBusy(true); setError(null); setSaved(false);
    try { resource.setData(await api.saveCandidateProfile({ expected_revision: profile.revision, fields: {}, consent: { processing: profile.consents.processing, pool: profile.consents.pool, version: profile.consents.version || "profile-v1", training: selected, ...(selected ? { training_version: "training-v2" } : {}) } })); setChoice(null); setSaved(true); }
    catch (reason) { setError(errorText(reason)); }
    finally { setBusy(false); }
  }
  return <section className="candidate-recommendation-consent"><h2>Персональные рекомендации</h2>{resource.loading || resource.error ? <State loading={resource.loading} error={resource.error} retry={resource.refresh} /> : profile ? <><label className="candidate-consent-check"><Checkbox checked={selected} disabled={busy || !profile.consents.processing || !profile.consents.pool} onChange={event => { setChoice(event.target.checked); setSaved(false); }} />Разрешаю использование обезличенных данных для обучения и рекомендаций</label><p className="muted">Это добровольный выбор. Он не нужен для сохранения резюме и откликов работодателей.</p>{(!profile.consents.processing || !profile.consents.pool) && <p className="muted">Сначала опубликуйте резюме и подтвердите согласие на его обработку.</p>}<a href="#/legal/training-consent">Условия использования данных</a><Action disabled={busy || choice === null || selected === enabled} onClick={() => void save()}>{busy ? "Сохраняем…" : "Сохранить выбор"}</Action>{saved && <p role="status">Выбор сохранён.</p>}{error && <p role="alert" className="form-error">{error}</p>}</> : <p className="muted">Сначала создайте резюме, чтобы включить рекомендации.</p>}</section>;
}
