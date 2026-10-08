import type { ContactsView, InterviewKind, InterviewOutcome, RecruitmentAction, RecruitmentView } from "@comit/shared";
import { Button, Dialog, SelectField, TextArea, TextField, cx } from "@comit/ui-kit";
import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { Link } from "react-router";
import { api, describeError, isUnauthorized, newIdempotencyKey } from "../api";
import { useSession } from "../auth/session";
import { formatInterviewTime, formatSalaryRange } from "../lib/format";
import { INTERVIEW_KINDS, INTERVIEW_KIND_LABEL, INTERVIEW_OUTCOMES, INTERVIEW_OUTCOME_LABEL, RECRUITMENT_STATE_LABEL, TERMINAL_STATES } from "./labels";

/** Last company name typed in an invitation — a per-browser convenience, not a company profile. */
const COMPANY_STORAGE_KEY = "comit.employer.company_name";

function rememberedCompany(): string {
  try {
    return window.localStorage.getItem(COMPANY_STORAGE_KEY) ?? "";
  } catch {
    return "";
  }
}

function rememberCompany(value: string): void {
  try {
    window.localStorage.setItem(COMPANY_STORAGE_KEY, value);
  } catch {
    // Storage may be blocked (private mode); the field simply starts empty next time.
  }
}

type Distributive<T> = T extends unknown ? Omit<T, "expected_revision" | "idempotency_key"> : never;
type ActionBody = Distributive<RecruitmentAction>;

type DialogKind = "invite" | "reject_saved" | "reject" | "interview_propose" | "interview_complete" | "offer_send";

interface RecruitmentControlsProps {
  draftId: string;
  candidateId: string;
  /** Existing process for this vacancy/candidate pair, or null before an invitation. */
  process: RecruitmentView | null;
  /** Vacancy salary interval used as the invitation default (mandatory, TZ §2.2). */
  vacancySalary: { from_rub: number; to_rub: number } | null;
  vacancyTitle: string;
  /** Protected PDF of the public résumé projection. */
  resumeUrl: string;
  /** Server state changed — reload the process. */
  onChanged: (process: RecruitmentView) => void;
}

/** Bottom action buttons of the candidate screen (design: 24px semibold, radius 20, 24px vertical padding). */
const BUTTON = "inline-flex h-14 items-center justify-center rounded-[1.25rem] px-8 text-lg font-semibold tracking-[-0.045rem] text-white transition-[filter] duration-150 hover:brightness-115 disabled:opacity-50 lg:h-[4.75rem] lg:px-[5rem] lg:text-[1.5rem]";

/**
 * Hiring controls for one candidate inside one vacancy (Figma «ПРОСМОТР»): a «Процесс найма» row in the
 * information list, then the button row — PDF, interview, reject, hire. Buttons come from the server's
 * `allowed_actions`; every mutation carries the read revision (CAS) and a UUID retry key. Contacts are
 * requested only after acceptance through the dedicated endpoint.
 */
export function RecruitmentControls({ draftId, candidateId, process, vacancySalary, vacancyTitle, resumeUrl, onChanged }: RecruitmentControlsProps) {
  const [dialog, setDialog] = useState<DialogKind | null>(null);
  const [interviewId, setInterviewId] = useState<string | null>(null);
  const allowed = new Set(process?.allowed_actions ?? []);
  const terminal = process ? TERMINAL_STATES.has(process.state) : false;

  return (
    <>
      <section className="rounded-[2rem] bg-light px-5 py-5 text-on-chip lg:px-[1.5625rem] lg:py-[1.75rem]">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h4 className="text-xl font-medium tracking-[-0.04875rem] lg:text-[1.625rem]">Процесс найма · {vacancyTitle}</h4>
          <span className="rounded-full bg-black/5 px-4 py-2 text-base font-medium lg:text-[1.25rem]">
            {process ? RECRUITMENT_STATE_LABEL[process.state] : "В шортлисте вакансии"}
          </span>
        </div>
        <div className="mt-4 flex flex-col gap-3 text-base lg:text-[1.25rem]">
          {process?.terms ? (
            <p>
              {process.terms.company_name} · {formatSalaryRange(process.terms.salary.from_rub, process.terms.salary.to_rub)} в месяц
            </p>
          ) : (
            !process && <p className="text-on-chip/70">Приглашение ещё не отправлено: кандидат не знает о шортлисте.</p>
          )}
          {process?.closure && (
            <p className="text-on-chip/70">
              Причина закрытия ({process.closure.actor === "employer" ? "работодатель" : "кандидат"}): {process.closure.reason}
            </p>
          )}
          {process && process.interviews.length > 0 && (
            <ul className="flex flex-col gap-2">
              {process.interviews.map((interview) => (
                <li key={interview.id} className="flex flex-wrap items-center justify-between gap-2 rounded-[1.25rem] bg-black/5 px-4 py-3">
                  <span>
                    {INTERVIEW_KIND_LABEL[interview.kind]} · <span className="first-letter:uppercase">{formatInterviewTime(interview)}</span> ·{" "}
                    {interview.outcome
                      ? INTERVIEW_OUTCOME_LABEL[interview.outcome]
                      : interview.state === "proposed"
                        ? "ждёт подтверждения"
                        : interview.state === "scheduled"
                          ? "назначено"
                          : interview.state}
                  </span>
                  {interview.state === "scheduled" && allowed.has("interview_complete") && (
                    <Button
                      size="sm"
                      variant="dark"
                      onClick={() => {
                        setInterviewId(interview.id);
                        setDialog("interview_complete");
                      }}
                    >
                      Заполнить ОС
                    </Button>
                  )}
                </li>
              ))}
            </ul>
          )}
          {process && <ContactsBlock process={process} />}
          {process?.chat_available && (
            <Link to={`/chats/${process.id}`} className="w-fit font-semibold underline underline-offset-4">
              Открыть чат с кандидатом
            </Link>
          )}
        </div>
      </section>

      <div className="mt-4 flex flex-wrap items-center justify-center gap-3 lg:mt-[2.875rem] lg:gap-[1.5rem]">
        <a href={resumeUrl} target="_blank" rel="noreferrer" className={cx(BUTTON, "bg-dark-button lg:px-[3.75rem]")}>
          PDF Резюме
        </a>
        {!terminal && !process && (
          <>
            <button type="button" className={cx(BUTTON, "bg-dark-button")} onClick={() => setDialog("invite")}>
              Пригласить
            </button>
            <button type="button" className={cx(BUTTON, "bg-danger")} onClick={() => setDialog("reject_saved")}>
              Отклонить
            </button>
          </>
        )}
        {allowed.has("interview_propose") && (
          <button type="button" className={cx(BUTTON, "bg-dark-button")} onClick={() => setDialog("interview_propose")}>
            Собеседование
          </button>
        )}
        {allowed.has("reject") && (
          <button type="button" className={cx(BUTTON, "bg-danger")} onClick={() => setDialog("reject")}>
            Отклонить
          </button>
        )}
        {allowed.has("offer_send") && (
          <button type="button" className={cx(BUTTON, "bg-accent-gradient lg:w-[28.9375rem]")} onClick={() => setDialog("offer_send")}>
            Взять на работу
          </button>
        )}
      </div>

      {dialog && (
        <ActionDialog
          kind={dialog}
          draftId={draftId}
          candidateId={candidateId}
          process={process}
          interviewId={interviewId}
          vacancySalary={vacancySalary}
          onClose={() => setDialog(null)}
          onDone={(next) => {
            setDialog(null);
            onChanged(next);
          }}
        />
      )}
    </>
  );
}

/** Candidate contacts: requested only for accepted processes (api-core enforces the same rule). */
function ContactsBlock({ process }: { process: RecruitmentView }) {
  const [contacts, setContacts] = useState<ContactsView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const visible = process.state === "accepted" || process.state === "offer_sent" || process.state === "hired";

  useEffect(() => {
    if (!visible) return;
    let cancelled = false;
    api.getRecruitmentContacts(process.id).then(
      (value) => !cancelled && setContacts(value),
      (failure: unknown) => !cancelled && setError(describeError(failure)),
    );
    return () => {
      cancelled = true;
    };
  }, [process.id, visible]);

  if (!visible) {
    return <p className="text-on-chip/70">Контакты кандидата откроются после принятия приглашения.</p>;
  }
  if (error) return <p className="text-danger">Контакты недоступны: {error}</p>;
  if (!contacts) return <p className="text-on-chip/70">Загружаем контакты…</p>;
  return (
    <dl className="grid w-fit grid-cols-[auto_1fr] gap-x-4 gap-y-1 rounded-[1.25rem] bg-black/5 px-4 py-3">
      <dt className="text-on-chip/60">Имя</dt>
      <dd>{contacts.display_name}</dd>
      <dt className="text-on-chip/60">Email</dt>
      <dd>
        <a className="underline" href={`mailto:${contacts.email}`}>
          {contacts.email}
        </a>
      </dd>
      {contacts.phone && (
        <>
          <dt className="text-on-chip/60">Телефон</dt>
          <dd>
            <a className="underline" href={`tel:${contacts.phone}`}>
              {contacts.phone}
            </a>
          </dd>
        </>
      )}
    </dl>
  );
}

interface ActionDialogProps {
  kind: DialogKind;
  draftId: string;
  candidateId: string;
  process: RecruitmentView | null;
  interviewId: string | null;
  vacancySalary: { from_rub: number; to_rub: number } | null;
  onClose: () => void;
  onDone: (process: RecruitmentView) => void;
}

const DIALOG_TITLE: Record<DialogKind, string> = {
  invite: "Пригласить кандидата",
  reject_saved: "Отклонить без приглашения",
  reject: "Отказать кандидату",
  interview_propose: "Предложить собеседование",
  interview_complete: "Итоги собеседования",
  offer_send: "Отправить оффер",
};

const isInterviewKind = (value: string): value is InterviewKind => value === "technical" || value === "general";
const isOutcome = (value: string): value is InterviewOutcome => value === "passed" || value === "failed" || value === "neutral";
const isMeetingFormat = (value: string): value is "remote" | "onsite" => value === "remote" || value === "onsite";

/** Browser time zone for interview proposals; the server stores it with the absolute timestamp. */
function localTimeZone(): string {
  return Intl.DateTimeFormat().resolvedOptions().timeZone || "Europe/Moscow";
}

function ActionDialog({ kind, draftId, candidateId, process, interviewId, vacancySalary, onClose, onDone }: ActionDialogProps) {
  const { expire } = useSession();
  const [values, setValues] = useState<Record<string, string>>(() => ({
    company: rememberedCompany(),
    description: "",
    salaryFrom: vacancySalary ? String(vacancySalary.from_rub / 1000) : "",
    salaryTo: vacancySalary ? String(vacancySalary.to_rub / 1000) : "",
    reason: "",
    kind: "technical",
    startsAt: "",
    duration: "60",
    format: "remote",
    location: "",
    outcome: "passed",
    feedback: "",
    offerSalary: vacancySalary ? String(vacancySalary.to_rub / 1000) : "",
    expiresAt: "",
  }));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // One retry key per dialog instance: resubmitting after a network error replays, never duplicates.
  const [idempotencyKey] = useState(newIdempotencyKey);
  const set = (key: string) => (event: { target: { value: string } }) => setValues((previous) => ({ ...previous, [key]: event.target.value }));
  const choose = (key: string) => (next: string) => setValues((previous) => ({ ...previous, [key]: next }));
  const value = (key: string) => values[key] ?? "";
  const rub = (key: string) => Math.round(Number(value(key).replace(",", ".")) * 1000);

  async function act(body: ActionBody): Promise<RecruitmentView> {
    if (!process) throw new Error("process_missing");
    const action: RecruitmentAction = { ...body, expected_revision: process.revision, idempotency_key: idempotencyKey };
    return api.actOnRecruitment("employer", process.id, action);
  }

  function validate(): string | null {
    if (kind === "invite") {
      if (!value("company").trim()) return "Укажите компанию.";
      if (!value("description").trim()) return "Опишите предложение.";
      const from = rub("salaryFrom");
      const to = rub("salaryTo");
      if (!Number.isFinite(from) || !Number.isFinite(to) || from <= 0 || to < from) return "Вилка ЗП обязательна: «от» больше нуля и не больше «до».";
    }
    if ((kind === "reject" || kind === "reject_saved") && !value("reason").trim()) return "Причина обязательна.";
    if (kind === "interview_propose") {
      if (!value("startsAt")) return "Выберите дату и время.";
      if (Date.parse(value("startsAt")) <= Date.now()) return "Время собеседования должно быть в будущем.";
      if (!value("location").trim()) return "Укажите ссылку или адрес.";
    }
    if (kind === "interview_complete" && !value("feedback").trim()) return "Обратная связь обязательна.";
    if (kind === "offer_send") {
      if (!(rub("offerSalary") > 0)) return "Укажите зарплату в оффере.";
      if (!value("expiresAt") || Date.parse(value("expiresAt")) <= Date.now()) return "Срок ответа должен быть в будущем.";
      if (!value("description").trim()) return "Опишите условия оффера.";
    }
    return null;
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    const invalid = validate();
    if (invalid) {
      setError(invalid);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      let next: RecruitmentView;
      switch (kind) {
        case "invite":
          rememberCompany(value("company").trim());
          next = await api.inviteCandidate(draftId, {
            candidate_id: candidateId,
            idempotency_key: idempotencyKey,
            company_name: value("company").trim(),
            description: value("description").trim(),
            salary: { from_rub: rub("salaryFrom"), to_rub: rub("salaryTo") },
          });
          break;
        case "reject_saved":
          next = await api.rejectSavedCandidate(draftId, { candidate_id: candidateId, idempotency_key: idempotencyKey, reason: value("reason").trim() });
          break;
        case "reject":
          next = await act({ action: "reject", reason: value("reason").trim() });
          break;
        case "interview_propose": {
          const interviewKind = value("kind");
          const format = value("format");
          if (!isInterviewKind(interviewKind) || !isMeetingFormat(format)) throw new Error("invalid_interview_form");
          next = await act({
            action: "interview_propose",
            kind: interviewKind,
            starts_at: new Date(value("startsAt")).toISOString(),
            time_zone: localTimeZone(),
            duration_minutes: Number(value("duration")),
            format,
            location: value("location").trim(),
          });
          break;
        }
        case "interview_complete": {
          const outcome = value("outcome");
          if (!interviewId || !isOutcome(outcome)) throw new Error("invalid_interview_result");
          next = await act({ action: "interview_complete", interview_id: interviewId, outcome, feedback: value("feedback").trim() });
          break;
        }
        case "offer_send":
          next = await act({
            action: "offer_send",
            salary_rub: rub("offerSalary"),
            description: value("description").trim(),
            expires_at: new Date(value("expiresAt")).toISOString(),
          });
          break;
      }
      onDone(next);
    } catch (failure) {
      if (isUnauthorized(failure)) expire();
      setError(describeError(failure));
    } finally {
      setBusy(false);
    }
  }

  let fields: ReactNode = null;
  if (kind === "invite") {
    fields = (
      <>
        <TextField label="Компания" value={value("company")} onChange={set("company")} maxLength={200} />
        <TextArea label="Предложение" placeholder="Чем предстоит заниматься, команда, условия" value={value("description")} onChange={set("description")} rows={4} />
        <div className="grid grid-cols-2 gap-3">
          <TextField label="ЗП от, тыс ₽" inputMode="decimal" value={value("salaryFrom")} onChange={set("salaryFrom")} />
          <TextField label="до, тыс ₽" inputMode="decimal" value={value("salaryTo")} onChange={set("salaryTo")} />
        </div>
        <p className="text-xs text-muted">Кандидат увидит компанию, описание и вилку до принятия. Контакты откроются после согласия.</p>
      </>
    );
  } else if (kind === "reject" || kind === "reject_saved") {
    fields = (
      <>
        <TextArea label="Причина" value={value("reason")} onChange={set("reason")} rows={3} />
        <p className="text-xs text-muted">
          {kind === "reject_saved" ? "Кандидат не увидит этот отказ: приглашение не отправлялось." : "Причина будет видна кандидату."}
        </p>
      </>
    );
  } else if (kind === "interview_propose") {
    fields = (
      <>
        <SelectField
          label="Тип"
          value={value("kind")}
          onChange={choose("kind")}
          options={INTERVIEW_KINDS.map((key) => ({ value: key, label: INTERVIEW_KIND_LABEL[key] }))}
        />
        <TextField label={`Дата и время (${localTimeZone()})`} type="datetime-local" value={value("startsAt")} onChange={set("startsAt")} />
        <div className="grid grid-cols-2 gap-3">
          <TextField label="Длительность, мин" type="number" min={15} step={15} value={value("duration")} onChange={set("duration")} />
          <SelectField
            label="Формат"
            value={value("format")}
            onChange={choose("format")}
            options={[
              { value: "remote", label: "Онлайн" },
              { value: "onsite", label: "Очно" },
            ]}
          />
        </div>
        <TextField label={value("format") === "remote" ? "Ссылка на встречу" : "Адрес"} value={value("location")} onChange={set("location")} />
      </>
    );
  } else if (kind === "interview_complete") {
    fields = (
      <>
        <SelectField
          label="Результат"
          value={value("outcome")}
          onChange={choose("outcome")}
          options={INTERVIEW_OUTCOMES.map((key) => ({ value: key, label: INTERVIEW_OUTCOME_LABEL[key] }))}
        />
        <TextArea label="Обратная связь кандидату" value={value("feedback")} onChange={set("feedback")} rows={4} />
      </>
    );
  } else if (kind === "offer_send") {
    fields = (
      <>
        <TextField label="Зарплата, тыс ₽ в месяц" inputMode="decimal" value={value("offerSalary")} onChange={set("offerSalary")} />
        <TextField label="Ответить до" type="datetime-local" value={value("expiresAt")} onChange={set("expiresAt")} />
        <TextArea label="Условия" value={value("description")} onChange={set("description")} rows={4} />
      </>
    );
  }

  return (
    <Dialog title={DIALOG_TITLE[kind]} showTitle onClose={onClose}>
      <form onSubmit={submit} noValidate className="flex flex-col gap-3">
        {fields}
        {error && (
          <p role="alert" className="text-sm text-danger-soft">
            {error}
          </p>
        )}
        <div className="mt-2 flex gap-3">
          <Button type="submit" variant={kind === "reject" || kind === "reject_saved" ? "danger" : "primary"} loading={busy} className="flex-1">
            {DIALOG_TITLE[kind]}
          </Button>
          <Button type="button" variant="ghost" disabled={busy} onClick={onClose}>
            Отмена
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
