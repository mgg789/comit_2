import { ApiError } from "@comit/api-client";
import { RecruitmentConversation, type ConversationTransport } from "@comit/ui-kit/recruitment-conversation";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { GRADES, SPECIALIZATIONS, type CandidateDeclaredNarrative, type CandidateDeclaredSkill, type CandidateProfileFields, type CandidateProfileMutation, type CandidateProfileView, type RecruitmentAction, type RecruitmentState, type RecruitmentView } from "@comit/shared";
import { Action, Field, Icon, PageTitle, Reveal, Select, State, Tags } from "@comit/ui-kit/design";
import { api, errorText, readInvitations, readProfile, salary, useResource } from "./runtime";

type Navigate = (path: string) => void;
const directions = { backend: "Backend", frontend: "Frontend", fullstack: "Fullstack", mobile: "Мобильная разработка", devops: "DevOps", data: "Данные", ml: "Машинное обучение", qa: "Тестирование" };
const grades = { intern: "Стажёр", junior: "Junior", middle: "Middle", senior: "Senior" };
const workTypes = { onsite: "В офисе", remote: "Удалённо", hybrid: "Гибрид" };
const states: Record<RecruitmentState, string> = { sent: "Новое приглашение", viewed: "Приглашение просмотрено", accepted: "Приглашение принято", declined: "Вы отказались", rejected: "Работодатель отказал", offer_sent: "Оффер получен", hired: "Оффер принят", offer_declined: "Оффер отклонён", withdrawn: "Приглашение отозвано" };
const narrativeGroups = ["projects", "experience", "achievements"] as const;
const groupLabels = { projects: "Проекты", experience: "Опыт работы", achievements: "Достижения" };
function date(value: string | null): string { if (!value) return ""; const parsed = new Date(value); return Number.isNaN(parsed.getTime()) ? "" : new Intl.DateTimeFormat("ru-RU", { day: "numeric", month: "long", year: "numeric" }).format(parsed); }
function initials(name: string): string { return name.trim().split(/\s+/).slice(0, 2).map(part => part[0]).join("").toUpperCase() || "?"; }
function safeLink(value: string | null | undefined): string | undefined { if (!value) return; try { const url = new URL(value); if (url.protocol === "https:" || url.protocol === "http:") return url.href; } catch { /* Invalid declarations remain readable text, never executable links. */ } }

function NarrativeSection({ title, items, edit }: { title: string; items: CandidateDeclaredNarrative[]; edit: () => void }) {
  return <section className="candidate-profile-section"><header><h2>{title}</h2><Action variant="primary" onClick={edit}>Добавить</Action></header><div className="candidate-narratives">{items.map((item, index) => <article className="panel candidate-narrative" key={item.id || index}><h3>{item.title}</h3>{item.organization && <p className="muted">{item.organization}</p>}{item.description && <p>{item.description}</p>}{safeLink(item.link) && <a href={safeLink(item.link)} target="_blank" rel="noopener noreferrer">Ссылка на работу ↗</a>}</article>)}{!items.length && <p className="candidate-empty-copy">Пока ничего не добавлено</p>}</div></section>;
}

/** Candidate-owned resume with explicit declarations and a CAS editor; evidence is never submitted as input. */
export function ProfileScreen({ navigate }: { navigate: Navigate }) {
  const resource = useResource(readProfile);
  const [editing, setEditing] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  if (resource.loading || resource.error) return <State loading={resource.loading} error={resource.error} retry={resource.refresh} />;
  const profile = resource.data, fields = profile?.fields || {};
  if (editing || !profile) return <ProfileEditor profile={profile} onCancel={profile ? () => setEditing(false) : undefined} onReload={() => { setEditing(false); resource.refresh(); }} onSave={value => { resource.setData(value); setEditing(false); setMessage("Резюме сохранено"); }} />;
  const tags = [fields.primary_direction ? directions[fields.primary_direction] : "", fields.city || "", ...(fields.work_types || []).map(value => workTypes[value]), fields.salary ? salary(fields.salary.from_rub, fields.salary.to_rub) : ""];
  return <Reveal className="candidate-profile-page">
    {message && <p role="status" className="candidate-success">{message}</p>}
    <section className="candidate-profile-hero"><h1>{fields.display_name || "Ваше резюме"}</h1><div className="candidate-profile-avatar" aria-label="Инициалы кандидата">{initials(fields.display_name || "")}</div><Tags className="candidate-chips" shape="pill" values={tags} />{fields.description && <p className="candidate-profile-bio">{fields.description}</p>}</section>
    <div className="candidate-profile-body"><section className="panel candidate-weekly-task"><div><h3>Мини-задание</h3><p className="muted">Еженедельные задания пока недоступны.</p></div><Action variant="light" disabled>Пройти задание</Action></section><section className="candidate-profile-section"><header><h2>Навыки</h2><Action variant="primary" onClick={() => setEditing(true)}>Добавить</Action></header><div className="panel candidate-skills"><Tags className="candidate-chips" shape="pill" variant="muted" values={[...(fields.domains || []), ...(fields.stacks || []), ...(fields.soft_skills || [])].map(skill => skill.key)} /><small>Указаны вами. Проверка навыков хранится отдельно.</small>{!(fields.domains?.length || fields.stacks?.length || fields.soft_skills?.length) && <p>Добавьте свои навыки</p>}</div></section>
    <section className="candidate-profile-section candidate-external-achievements"><h2>Достижения — внешний контур + ФСП</h2><article className="panel candidate-profile-row"><h3>ФСП</h3><p className="muted">{fields.fsp_id ? `Указан ID участника: ${fields.fsp_id}. Подтверждённые записи здесь пока не доступны.` : "Подключение учётной записи ФСП здесь пока недоступно."}</p></article><article className="panel candidate-profile-row"><h3>GitHub</h3><p className="muted">{fields.projects?.length ? `Указано проектов: ${fields.projects.length}. Проверенные репозитории здесь пока не доступны.` : "Добавьте проекты в резюме. Проверенные репозитории здесь пока не доступны."}</p></article></section>
    <section className="candidate-profile-section candidate-grade-section"><h2>Уровни навыков</h2><p className="muted">У каждого навыка своя оценка от 0 до 7. Указанный вами уровень и результат проверки показаны отдельно.</p>{(["domains", "stacks", "soft_skills"] as const).map(group => <article className="panel candidate-profile-row" key={group}><h3>{{ domains: "Домены", stacks: "Стеки", soft_skills: "Личные навыки" }[group]}</h3>{fields[group]?.length ? fields[group].map(skill => <div className="candidate-skill-assessment" key={skill.key}><strong>{skill.key}</strong><span>{skill.level} / 7</span><span className="muted">{{ confirmed: "Подтверждено", partial: "Частично подтверждено", self_reported: "Указано вами", pending: "Проверяется", rejected: "Не подтверждено" }[profile.evidence[`${group}:${skill.key}`]?.status || "self_reported"]}</span></div>) : <p className="muted">Пока не указаны</p>}</article>)}</section>
    {narrativeGroups.filter(group => fields[group]?.length).map(group => <NarrativeSection key={group} title={groupLabels[group]} items={fields[group] || []} edit={() => setEditing(true)} />)}
    <ProfileVisibility profile={profile} edit={() => setEditing(true)} reload={resource.refresh} />
    <div className="candidate-profile-actions"><Action onClick={() => navigate("/offers")}>К предложениям</Action><Action variant="primary" onClick={() => setEditing(true)}>Редактировать резюме</Action></div></div>
  </Reveal>;
}

/** Consent and current revision readiness are independent; delivery alone never means publication. */
function ProfileVisibility({ profile, edit, reload }: { profile: CandidateProfileView; edit: () => void; reload: () => void }) {
  const pipeline = useResource(async () => {
    if (!profile.consents.pool || !profile.consents.processing || profile.projection.state !== "delivered" || profile.projection.projected_revision !== profile.revision) return null;
    try { return await api.getCandidatePipeline(); } catch (reason) {
      if (reason instanceof ApiError && (reason.status === 409 || reason.status === 404)) return null;
      throw reason;
    }
  }, [profile.revision, profile.consents.pool, profile.consents.processing, profile.projection.state, profile.projection.projected_revision]);
  const [retrying, setRetrying] = useState(false), [retryError, setRetryError] = useState<string | null>(null);
  const [retried, setRetried] = useState(false);
  const labels: Record<string, string> = { display_name: "имя", email_verified: "подтверждённая почта", city: "город", salary: "зарплатные ожидания", primary_direction: "направление", processing_consent: "согласие на обработку", pool_consent: "согласие на показ работодателям", consent_timestamp: "сохранённое согласие", consent_version: "актуальное согласие", approved_grade_result: "общий уровень опыта для категории подбора" };
  const current = pipeline.data?.profile_revision === profile.revision ? pipeline.data : null;
  let text = "Резюме сохранено. Готовим его для подбора.";
  if (!profile.consents.pool) text = "Показ работодателям выключен. Чтобы участвовать в подборе, включите его при редактировании резюме.";
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
  return <section className="candidate-profile-section"><h2>Видимость профиля</h2><div className="panel candidate-profile-row"><p role="status">{text}</p>{pipeline.error && <State error="Не удалось проверить готовность подбора." retry={pipeline.refresh} />}{retryError && <p role="alert">{retryError}</p>}{!profile.consents.pool || !profile.consents.processing || current?.missing_requirements.length ? <Action onClick={edit}>Настроить резюме</Action> : profile.projection.state === "failed" && !retried ? <Action disabled={retrying} onClick={() => void retry()}>Повторить отправку</Action> : <Action disabled={pipeline.loading} onClick={pipeline.refresh}>Обновить статус</Action>}</div></section>;
}

function ProfileEditor({ profile, onCancel, onSave, onReload }: { profile: CandidateProfileView | null; onCancel?: () => void; onReload: () => void; onSave: (profile: CandidateProfileView) => void }) {
  const [fields, setFields] = useState<CandidateProfileFields>(() => structuredClone(profile?.fields || {}));
  const [relocationText, setRelocationText] = useState((profile?.fields.relocation_cities || []).join(", "));
  const [preferenceText, setPreferenceText] = useState((profile?.fields.preferences || []).join("\n"));
  const [processing, setProcessing] = useState(profile?.consents.processing || false);
  const [pool, setPool] = useState(profile?.consents.pool || false);
  const [busy, setBusy] = useState(false), [error, setError] = useState<string | null>(null);
  function update<K extends keyof CandidateProfileFields>(key: K, value: CandidateProfileFields[K]) { setFields(current => ({ ...current, [key]: value })); }
  function updateNarrative(group: typeof narrativeGroups[number], index: number, value: Partial<CandidateDeclaredNarrative>) { update(group, (fields[group] || []).map((item, position) => position === index ? { ...item, ...value } : item)); }
  function updateSkill(group: "domains" | "stacks" | "soft_skills", index: number, value: Partial<CandidateDeclaredSkill>) { update(group, (fields[group] || []).map((item, position) => position === index ? { ...item, ...value } : item)); }
  async function save(event: FormEvent) {
    event.preventDefault(); if (busy) return;
    if (fields.salary && fields.salary.to_rub < fields.salary.from_rub) { setError("Верхняя граница зарплаты должна быть не меньше нижней."); return; }
    if (pool && !processing) { setError("Для участия в подборе нужно согласие на обработку данных."); return; }
    setBusy(true); setError(null);
    const editableFields = { ...fields }; delete editableFields.fsp_id; delete editableFields.fsp_claim_id;
    const body: CandidateProfileMutation = { expected_revision: profile?.revision || 0, fields: { ...editableFields, relocation_cities: relocationText.split(",").map(value => value.trim()).filter(Boolean), preferences: preferenceText.split("\n").map(value => value.trim()).filter(Boolean) } };
    if (!profile || processing !== profile.consents.processing || pool !== profile.consents.pool) body.consent = { processing, pool, version: profile?.consents.version || "profile-v1", training: Boolean(profile?.consents.training && processing && pool), ...(profile?.consents.training && processing && pool ? { training_version: "training-v1" } : {}) };
    try { onSave(await api.saveCandidateProfile(body)); } catch (reason) { setError(errorText(reason)); } finally { setBusy(false); }
  }
  return <Reveal className="candidate-profile-editor"><PageTitle title={profile ? "Редактировать резюме" : "Создать резюме"} back={onCancel} /><form onSubmit={save}><fieldset disabled={busy}><div className="form-grid"><div className="form-row"><Field label="Ваше имя"><input required maxLength={160} value={fields.display_name || ""} onChange={event => update("display_name", event.target.value)} /></Field><Field label="Город"><input maxLength={160} value={fields.city || ""} onChange={event => update("city", event.target.value)} /></Field></div>
    <div className="form-row"><Field label="Направление"><Select aria-label="Направление" value={fields.primary_direction || ""} onChange={event => { const value = event.target.value; update("primary_direction", SPECIALIZATIONS.find(item => item === value) || null); }}><option value="">Не выбрано</option>{SPECIALIZATIONS.map(value => <option value={value} key={value}>{directions[value]}</option>)}</Select></Field><Field label="Общая категория опыта" hint="Нужна для категории подбора. Уровень каждого навыка задаётся отдельно ниже."><Select aria-label="Указанный вами грейд" value={fields.declared_grade || ""} onChange={event => update("declared_grade", GRADES.find(item => item === event.target.value) || null)}><option value="">Не выбран</option>{GRADES.map(value => <option key={value} value={value}>{grades[value]}</option>)}</Select></Field></div>
    <Field label="О себе"><textarea maxLength={10000} value={fields.description || ""} onChange={event => update("description", event.target.value)} /></Field><div className="form-row"><Field label="Телефон" hint="Контакты открываются работодателю после принятия приглашения."><input type="tel" maxLength={64} value={fields.phone || ""} onChange={event => update("phone", event.target.value || null)} /></Field><Field label="ID участника ФСП" hint="Меняется через отдельную проверку, которая пока недоступна в этом кабинете."><input readOnly value={profile?.fields.fsp_id || "Не указан"} /></Field></div>
    <div className="form-row"><Field label="Зарплата от, ₽"><input type="number" min={0} step={1} value={fields.salary?.from_rub ?? ""} onChange={event => update("salary", event.target.value === "" ? null : { from_rub: Number(event.target.value), to_rub: fields.salary?.to_rub ?? Number(event.target.value) })} /></Field><Field label="Зарплата до, ₽"><input type="number" min={fields.salary?.from_rub || 0} step={1} value={fields.salary?.to_rub ?? ""} onChange={event => update("salary", event.target.value === "" ? null : { from_rub: fields.salary?.from_rub ?? 0, to_rub: Number(event.target.value) })} /></Field></div>
    <div className="candidate-checks">{(["onsite", "remote", "hybrid"] as const).map(value => <label key={value}><input type="checkbox" checked={fields.work_types?.includes(value) || false} onChange={event => update("work_types", event.target.checked ? [...(fields.work_types || []), value] : (fields.work_types || []).filter(item => item !== value))} />{workTypes[value]}</label>)}</div>
    <label className="candidate-checks"><input type="checkbox" checked={fields.relocation_ready || false} onChange={event => update("relocation_ready", event.target.checked)} />Готовность к переезду</label>
    <Field label="Города для переезда, через запятую"><input value={relocationText} onChange={event => setRelocationText(event.target.value)} /></Field>
    <Field label="Предпочтения, каждое с новой строки"><textarea value={preferenceText} onChange={event => setPreferenceText(event.target.value)} /></Field>
    {(["domains", "stacks", "soft_skills"] as const).map(group => <section className="candidate-editor-group" key={group}><h2>{group === "domains" ? "Области знаний" : group === "stacks" ? "Технологии" : "Гибкие навыки"}</h2>{(fields[group] || []).map((skill, index) => <div className="candidate-skill-edit" key={index}><Field label="Навык"><input required maxLength={100} pattern="[a-z][a-z0-9_-]*" title="Введите название латиницей, например python" value={skill.key} onChange={event => updateSkill(group, index, { key: event.target.value })} /></Field><Field label="Уровень от 0 до 7"><input type="number" min={0} max={7} step={1} required value={skill.level} onChange={event => updateSkill(group, index, { level: Number(event.target.value) })} /></Field><Action type="button" onClick={() => update(group, (fields[group] || []).filter((_, position) => position !== index))} aria-label={`Удалить навык ${skill.key || index + 1}`}>Удалить</Action></div>)}<Action type="button" onClick={() => update(group, [...(fields[group] || []), { key: "", level: 3 }])}>Добавить навык</Action></section>)}
    {narrativeGroups.map(group => <section className="candidate-editor-group" key={group}><h2>{groupLabels[group]}</h2>{(fields[group] || []).map((item, index) => <div className="panel candidate-narrative-edit" key={item.id || index}><Field label="Название"><input required maxLength={300} value={item.title} onChange={event => updateNarrative(group, index, { title: event.target.value })} /></Field><Field label="Организация"><input maxLength={300} value={item.organization || ""} onChange={event => updateNarrative(group, index, { organization: event.target.value })} /></Field><Field label="Описание"><textarea maxLength={10000} value={item.description || ""} onChange={event => updateNarrative(group, index, { description: event.target.value })} /></Field><Field label="Ссылка"><input type="url" maxLength={2000} pattern="https?://.*" value={item.link || ""} onChange={event => updateNarrative(group, index, { link: event.target.value || null })} /></Field><Action type="button" onClick={() => update(group, (fields[group] || []).filter((_, position) => position !== index))}>Удалить запись</Action></div>)}<Action type="button" onClick={() => update(group, [...(fields[group] || []), { title: "", description: "" }])}>Добавить запись</Action></section>)}
    <section className="panel candidate-consent"><h2>Согласие и видимость</h2><p>Это отдельные настройки резюме. Согласие при регистрации их не заменяет.</p>{profile?.consents.training && <p className="muted">Если отключить обработку данных или показ работодателям, согласие на использование данных для обучения тоже будет отозвано.</p>}<label><input type="checkbox" checked={processing} onChange={event => setProcessing(event.target.checked)} />Согласен(а) на обработку данных резюме</label><label><input type="checkbox" checked={pool} onChange={event => setPool(event.target.checked)} />Показывать резюме работодателям в подборе</label><a href="#/legal/processing-consent" target="_blank" rel="noopener noreferrer">Прочитать условия обработки данных</a></section>
    {error && <div><p className="form-error" role="alert">{error}</p><Action type="button" onClick={onReload}>Загрузить актуальное резюме</Action></div>}<div className="form-actions">{onCancel && <Action type="button" onClick={onCancel}>Отмена</Action>}<Action variant="primary" type="submit" disabled={busy}>{busy ? "Сохраняем…" : "Сохранить резюме"}</Action></div></div></fieldset></form></Reveal>;
}

/** Owner-scoped invitation list; all salary and lifecycle data come from immutable API terms. */
export function OffersScreen({ navigate, query = "" }: { navigate: Navigate; query?: string }) {
  const resource = useResource(readInvitations);
  const [filter, setFilter] = useState("all");
  if (resource.loading || resource.error) return <State loading={resource.loading} error={resource.error} retry={resource.refresh} />;
  const items = (resource.data || []).filter(item => item.terms && (filter === "all" || (filter === "new" ? item.state === "sent" || item.state === "viewed" : ["accepted", "offer_sent", "hired"].includes(item.state))) && (!query || `${item.terms.company_name} ${item.terms.vacancy_title || ""}`.toLocaleLowerCase("ru-RU").includes(query.toLocaleLowerCase("ru-RU"))));
  return <Reveal><PageTitle title="Входящие предложения" /><div className="candidate-offer-filters"><Select aria-label="Показать предложения" value={filter} onChange={event => setFilter(event.target.value)}><option value="all">Все предложения</option><option value="new">Новые приглашения</option><option value="accepted">Принятые приглашения</option></Select><span className="muted">{items.length} предложений</span></div>{items.length ? <div className="candidate-offer-grid">{items.map(item => <InvitationCard key={item.id} item={item} navigate={navigate} />)}</div> : <State empty={query ? "По этому запросу предложений нет" : "Здесь появятся предложения работодателей"} />}</Reveal>;
}
function InvitationCard({ item, navigate }: { item: RecruitmentView; navigate: Navigate }) {
  const terms = item.terms; if (!terms) return null;
  return <article className="candidate-offer-card"><div className="candidate-offer-card-head"><span className="candidate-offer-status">{states[item.state]}</span><span className="candidate-company-initial" aria-hidden="true">{initials(terms.company_name)}</span></div><button className="candidate-offer-open" onClick={() => navigate(`/offer/${item.id}`)} aria-label={`Открыть предложение ${terms.company_name}`}><h2>{terms.vacancy_title || "Предложение о работе"}</h2><p>{terms.company_name}</p></button><Tags className="candidate-chips" variant="blue" values={[terms.city, workTypes[terms.work_type as keyof typeof workTypes] ?? terms.work_type, terms.daily]} /><div className="candidate-offer-salary">{salary(terms.salary.from_rub, terms.salary.to_rub)}</div><footer><span><Icon name="clock" />{date(item.invited_at || item.created_at)}</span><Action className="candidate-offer-arrow" onClick={() => navigate(`/offer/${item.id}`)} aria-label={`Подробнее о предложении ${terms.company_name}`}><Icon name="vacancy-chevron" /></Action></footer></article>;
}

/** Invitation details enforce server allowed_actions, revisions, and stable retry identities for every mutation. */
export function OfferScreen({ id, navigate }: { id: string; navigate: Navigate }) {
  const resource = useResource(() => api.getRecruitment("candidate", id), [id]);
  const [busy, setBusy] = useState(false), [error, setError] = useState<string | null>(null), [declining, setDeclining] = useState(false), [reason, setReason] = useState("");
  const identities = useRef(new Map<string, string>());
  const viewed = useRef(new Set<string>());
  const [decliningInterview, setDecliningInterview] = useState<string | null>(null);
  const currentId = useRef(id); currentId.current = id;
  useEffect(() => { setBusy(false); setError(null); setDeclining(false); setDecliningInterview(null); setReason(""); }, [id]);
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
  return <Reveal><PageTitle title={`${terms.company_name} · ${terms.vacancy_title || "Предложение о работе"}`} back={() => navigate("/offers")} /><div className="candidate-offer-detail"><section className="panel candidate-offer-terms"><span className="candidate-offer-status">{states[item.state]}</span><h2>{terms.company_name}</h2><div className="candidate-offer-salary">{salary(terms.salary.from_rub, terms.salary.to_rub)}</div><Tags className="candidate-chips" variant="blue" values={[terms.city, workTypes[terms.work_type as keyof typeof workTypes] ?? terms.work_type, terms.daily]} /><p>{terms.description}</p><small>Приглашение от {date(item.invited_at || item.created_at)}</small><div className="candidate-offer-actions">{item.allowed_actions.includes("accept") && <Action variant="primary" disabled={busy} onClick={() => void act("accept")}>Принять приглашение</Action>}{item.allowed_actions.includes("decline") && <Action disabled={busy} onClick={() => setDeclining(true)}>Отклонить приглашение</Action>}<Action disabled={busy} onClick={resource.refresh}>Обновить</Action></div>{declining && <form className="candidate-decline" onSubmit={event => { event.preventDefault(); void act("decline", { reason: reason.trim() }); }}><Field label="Причина отказа"><textarea required maxLength={2000} value={reason} onChange={event => setReason(event.target.value)} disabled={busy} /></Field><div className="candidate-offer-actions"><Action type="button" disabled={busy} onClick={() => setDeclining(false)}>Отмена</Action><Action type="submit" disabled={busy}>Подтвердить отказ</Action></div></form>}{error && <p role="alert" className="form-error">{error}</p>}</section>
    {(item.interviews.length > 0 || item.offers.length > 0) && <section className="panel candidate-offer-progress"><h2>Собеседования и офферы</h2>{item.interviews.map(interview => <article key={interview.id}><h3>{interview.kind === "technical" ? "Техническое собеседование" : "Собеседование"}</h3><p>{new Intl.DateTimeFormat("ru-RU", { dateStyle: "long", timeStyle: "short", timeZone: interview.time_zone }).format(new Date(interview.starts_at))} · {interview.time_zone}</p><p>{interview.location}</p>{interview.state === "proposed" && item.allowed_actions.includes("interview_accept") && <Action disabled={busy} onClick={() => void act("interview_accept", { interview_id: interview.id })}>Подтвердить время</Action>}{interview.state === "proposed" && item.allowed_actions.includes("interview_decline") && <Action disabled={busy} onClick={() => { setDecliningInterview(interview.id); setReason(""); }}>Отклонить время собеседования</Action>}{decliningInterview === interview.id && <form className="candidate-decline" onSubmit={event => { event.preventDefault(); void act("interview_decline", { interview_id: interview.id, reason: reason.trim() }); }}><Field label="Почему время не подходит?"><textarea required maxLength={2000} value={reason} onChange={event => setReason(event.target.value)} disabled={busy} /></Field><div className="candidate-offer-actions"><Action type="button" disabled={busy} onClick={() => setDecliningInterview(null)}>Отмена</Action><Action type="submit" disabled={busy}>Отправить ответ</Action></div></form>}</article>)}{item.offers.map(offer => <article key={offer.id}><h3>Предложение о работе</h3><p>{new Intl.NumberFormat("ru-RU").format(offer.salary_rub)} ₽</p><p>{offer.description}</p><small>Действует до {date(offer.expires_at)}</small>{offer.state === "sent" && item.allowed_actions.includes("offer_accept") && <Action variant="primary" disabled={busy} onClick={() => void act("offer_accept", { offer_id: offer.id })}>Принять оффер</Action>}{offer.state === "sent" && item.allowed_actions.includes("offer_decline") && <Action disabled={busy} onClick={() => void act("offer_decline", { offer_id: offer.id })}>Отклонить оффер</Action>}</article>)}</section>}
    {item.chat_available ? <ProcessChat item={item} /> : <section className="panel"><h2>Чат с работодателем</h2><p>Переписка по этому приглашению пока недоступна.</p></section>}</div></Reveal>;
}

const candidateConversationTransport: ConversationTransport = {
  getMessages: (id, after, limit) => api.getChatMessages("candidate", id, after, limit),
  sendMessage: (id, body) => api.sendChatMessage("candidate", id, body),
  uploadFile: (id, body) => api.uploadChatFile("candidate", id, body),
  downloadFile: (id, fileId) => api.downloadChatFile("candidate", id, fileId),
};

/** Candidate-scoped wrapper of the exact employer conversation component. */
function ProcessChat({ item }: { item: RecruitmentView }) {
  return <section className="candidate-process-chat"><h2>Чат с работодателем</h2><RecruitmentConversation key={item.id} process={item} accountId={item.candidate_id} transport={candidateConversationTransport} errorText={errorText} emptyText="Напишите работодателю, чтобы начать разговор." messageLabel="Сообщение работодателю" /></section>;
}

/** Owner dashboard with shared grid rows; only declared per-skill levels are charted. */
export function StatisticsScreen({ navigate }: { navigate: Navigate }) {
  const invitations = useResource(readInvitations), profile = useResource(readProfile);
  const items = (invitations.data || []).filter(item => item.terms);
  const cutoff = Date.now() - 30 * 24 * 60 * 60 * 1000;
  const recent = items.filter(item => Date.parse(item.invited_at || item.created_at) >= cutoff);
  const skills = (["domains", "stacks", "soft_skills"] as const).flatMap(group => (profile.data?.fields[group] || []).map(skill => ({ ...skill, group })));
  return <Reveal><PageTitle title="Твой профиль в цифрах" back={() => navigate("/resume")} /><div className="candidate-statistics">
    <section className="panel candidate-stat-counts"><div><strong>—</strong><span>показов в подборе</span><small>Данные пока недоступны</small></div><div><strong>{invitations.loading || invitations.error ? "—" : recent.length}</strong><span>приглашений за 30 дней</span></div><div><strong>—</strong><span>рейтинг профиля</span><small>Данные пока недоступны</small></div></section>
    <section className="panel candidate-stat-history"><h2>История оценок и тестов</h2><p className="muted">История тестирования пока недоступна.</p><p>Каждое направление, стек и гибкий навык оцениваются отдельно.</p></section>
    <section className="panel candidate-stat-market"><h2>Рынок З/П</h2><p className="muted">Рыночные зарплаты по категориям пока недоступны.</p><div className="candidate-market-salary"><div><p>Рыночная вилка</p><Tags className="candidate-chips" shape="pill" values={["Данные недоступны"]} /></div><div><p>Ваши ожидания</p><Tags className="candidate-chips" shape="pill" values={[profile.data?.fields.salary ? salary(profile.data.fields.salary.from_rub, profile.data.fields.salary.to_rub) : "Не указаны"]} /></div></div></section>
    <section className="panel candidate-stat-offers"><h2>Приглашения работодателей</h2>{invitations.loading || invitations.error ? <State loading={invitations.loading} error={invitations.error} retry={invitations.refresh} /> : <><div className="candidate-stat-offer-count"><strong>{items.filter(item => ["accepted", "offer_sent", "hired"].includes(item.state)).length}</strong><span>принятых приглашений</span></div><div className="candidate-stat-offer-count"><strong>{items.filter(item => item.state === "sent" || item.state === "viewed").length}</strong><span>ожидают ответа</span></div><Action onClick={() => navigate("/offers")}>Посмотреть предложения</Action></>}</section>
    <section className="panel candidate-stat-skills" aria-label="Диаграмма навыков">{profile.loading || profile.error ? <State loading={profile.loading} error={profile.error} retry={profile.refresh} /> : <svg className="candidate-skill-map" viewBox={`0 0 500 ${Math.max(1, skills.length) * 42}`} role="img" aria-label={skills.length ? "Указанные вами уровни навыков, шкала от 0 до 7" : "Навыки пока не указаны"}>{skills.map((skill, index) => <g key={`${skill.group}:${skill.key}`} role="img" aria-label={`${skill.key}: ${skill.level} из 7, указано вами`}><rect className="candidate-skill-track" x="0" y={index * 42} width="500" height="24" rx="12" /><rect className="candidate-skill-value" x="0" y={index * 42} width={500 * skill.level / 7} height="24" rx="12" /></g>)}</svg>}</section>
  </div></Reveal>;
}
