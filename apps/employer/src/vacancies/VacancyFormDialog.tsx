import type { AgentsDraftFields_Input, AgentsDraftView } from "@comit/shared";
import { SPECIALIZATIONS, type Specialization } from "@comit/shared";
import { Button, Dialog, ProgressBar, SelectField, TextArea, TextField } from "@comit/ui-kit";
import { useState, type FormEvent } from "react";
import { api, describeError, isUnauthorized } from "../api";
import { useSession } from "../auth/session";
import { SPECIALIZATION_LABEL, WORK_TYPE_LABEL } from "../lib/format";

type WorkType = keyof typeof WORK_TYPE_LABEL;

interface FormValues {
  title: string;
  specialization: Specialization | "";
  /** Thousands of rubles, as typed in the design's «Вилка З/П от, тыс ₽». */
  salaryFrom: string;
  salaryTo: string;
  city: string;
  workType: WorkType | "";
  daily: string;
  bio: string;
}

const EMPTY: FormValues = { title: "", specialization: "", salaryFrom: "", salaryTo: "", city: "", workType: "", daily: "", bio: "" };

/** Approval checklist mirrored from agents-service `_missing`: everything except the description is required. */
const REQUIRED: ReadonlyArray<keyof FormValues> = ["title", "specialization", "salaryFrom", "salaryTo", "city", "workType", "daily"];

const MISSING_LABEL: Record<string, string> = {
  title: "название",
  zp_min: "зарплата «от»",
  zp_max: "зарплата «до»",
  daily: "график",
  city: "город",
  type: "формат работы",
  class_or_domens_or_stacks: "направление",
};

function parseThousands(raw: string): number | null {
  const value = Number(raw.replace(/\s/g, "").replace(",", "."));
  return raw.trim() && Number.isFinite(value) && value >= 0 ? Math.round(value * 1000) : null;
}

/** Validate locally what the server would reject anyway, so errors appear next to the field. */
function validate(values: FormValues): Partial<Record<keyof FormValues, string>> {
  const errors: Partial<Record<keyof FormValues, string>> = {};
  for (const key of REQUIRED) if (!values[key].trim()) errors[key] = "Обязательное поле";
  const from = parseThousands(values.salaryFrom);
  const to = parseThousands(values.salaryTo);
  if (values.salaryFrom.trim() && from === null) errors.salaryFrom = "Введите число";
  if (values.salaryTo.trim() && to === null) errors.salaryTo = "Введите число";
  if (from !== null && to !== null && to < from) errors.salaryTo = "Верхняя граница меньше нижней";
  return errors;
}

function toFields(values: FormValues): AgentsDraftFields_Input {
  return {
    title: values.title.trim(),
    class: values.specialization || null,
    zp_min: parseThousands(values.salaryFrom),
    zp_max: parseThousands(values.salaryTo),
    city: values.city.trim(),
    type: values.workType || null,
    daily: values.daily.trim(),
    bio: values.bio.trim() || null,
  };
}

type Step = "idle" | "creating" | "approving" | "publishing";

const STEP_LABEL: Record<Step, string> = {
  idle: "Опубликовать вакансию",
  creating: "Сохраняем черновик…",
  approving: "Проверяем поля…",
  publishing: "Публикуем…",
};

function fromDraft(draft: AgentsDraftView): FormValues {
  const { fields } = draft;
  return {
    title: fields.title ?? "",
    specialization: fields.class ?? "",
    salaryFrom: fields.zp_min == null ? "" : String(fields.zp_min / 1000),
    salaryTo: fields.zp_max == null ? "" : String(fields.zp_max / 1000),
    city: fields.city ?? "",
    workType: fields.type ?? "",
    daily: fields.daily ?? "",
    bio: fields.bio ?? "",
  };
}

interface VacancyFormDialogProps {
  /** Existing vacancy to edit; omitted for «Добавить вакансию». */
  initial?: AgentsDraftView;
  /** Draft changed on the server (saved, approved or published) — lists should refresh. */
  onChanged: () => void;
  /** Publication accepted; receives the published draft. */
  onPublished: (draft: AgentsDraftView) => void;
  onClose: () => void;
}

/**
 * Vacancy form dialog (manual mode, Figma «СОЗДАТЬ ВАКАНСИЮ»). Publishing is the existing three-step
 * contract: save draft (create or CAS patch) → approve exact revision into a summary → publish that
 * summary hash. A failure after saving keeps the draft, so the employer can retry from the vacancy page.
 */
export function VacancyFormDialog({ initial, onChanged, onPublished, onClose }: VacancyFormDialogProps) {
  const { expire } = useSession();
  const [values, setValues] = useState<FormValues>(() => (initial ? fromDraft(initial) : EMPTY));
  const [touched, setTouched] = useState(false);
  const [step, setStep] = useState<Step>("idle");
  const [error, setError] = useState<string | null>(null);
  const [draft, setDraft] = useState<AgentsDraftView | null>(initial ?? null);

  const errors = validate(values);
  const filled = REQUIRED.filter((key) => values[key].trim()).length + (values.bio.trim() ? 1 : 0);
  const progress = filled / (REQUIRED.length + 1);
  const set = (key: keyof FormValues) => (event: { target: { value: string } }) => setValues((previous) => ({ ...previous, [key]: event.target.value }));
  const fieldError = (key: keyof FormValues) => (touched ? errors[key] : undefined);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setTouched(true);
    if (Object.keys(errors).length > 0) return;
    setError(null);
    let current = draft;
    try {
      if (current === null) {
        setStep("creating");
        current = await api.createVacancy({ fields: toFields(values) });
      } else {
        setStep("creating");
        current = await api.patchVacancy(current.draft_id, { expected_revision: current.revision, fields: toFields(values) });
      }
      setDraft(current);
      onChanged();
      setStep("approving");
      const approved = await api.approveVacancy(current.draft_id, current.revision);
      setDraft(approved);
      if (!approved.summary) throw new Error("summary_missing");
      setStep("publishing");
      const published = await api.publishVacancy(approved.draft_id, {
        expected_revision: approved.revision,
        summary_hash: approved.summary.summary_hash,
      });
      onChanged();
      onPublished(published);
    } catch (failure) {
      if (isUnauthorized(failure)) expire();
      const missing = current?.missing_requirements ?? [];
      setError(
        missing.length > 0
          ? `Сервер просит заполнить: ${missing.map((key) => MISSING_LABEL[key] ?? key).join(", ")}.`
          : current
            ? `Черновик сохранён, но публикация не прошла: ${describeError(failure)}`
            : describeError(failure),
      );
      setStep("idle");
    }
  }

  const busy = step !== "idle";
  return (
    <Dialog title={initial ? "Редактирование вакансии" : "Новая вакансия"} onClose={onClose}>
      <form onSubmit={submit} noValidate className="flex flex-col gap-3">
        <div className="mb-2 flex flex-col gap-3">
          <div className="flex items-end justify-between">
            <span className="text-lg font-semibold">Уже заполнено</span>
            <span className="font-display text-2xl">{Math.round(progress * 100)}%</span>
          </div>
          <ProgressBar value={progress} label="Заполненность вакансии" />
        </div>

        <TextField placeholder="Название должности" aria-label="Название должности" value={values.title} onChange={set("title")} error={fieldError("title")} maxLength={200} />
        <SelectField
          aria-label="Направление"
          placeholder="Направление"
          value={values.specialization}
          onChange={set("specialization")}
          options={SPECIALIZATIONS.map((value) => ({ value, label: SPECIALIZATION_LABEL[value] }))}
          error={fieldError("specialization")}
        />
        <div className="grid grid-cols-2 gap-3">
          <TextField label="Вилка З/П от, тыс ₽" inputMode="decimal" placeholder="180" value={values.salaryFrom} onChange={set("salaryFrom")} error={fieldError("salaryFrom")} />
          <TextField label="до, тыс ₽" inputMode="decimal" placeholder="240" value={values.salaryTo} onChange={set("salaryTo")} error={fieldError("salaryTo")} />
        </div>
        <TextField placeholder="Город" aria-label="Город" value={values.city} onChange={set("city")} error={fieldError("city")} autoComplete="address-level2" />
        <SelectField
          aria-label="Формат работы"
          placeholder="Формат работы"
          value={values.workType}
          onChange={set("workType")}
          options={(Object.keys(WORK_TYPE_LABEL) as WorkType[]).map((value) => ({ value, label: WORK_TYPE_LABEL[value] }))}
          error={fieldError("workType")}
        />
        <TextField placeholder="График, например 5/2 с 10 до 19" aria-label="График работы" value={values.daily} onChange={set("daily")} error={fieldError("daily")} maxLength={500} />
        <TextArea placeholder="Описание проекта" aria-label="Описание проекта" value={values.bio} onChange={set("bio")} rows={4} />

        {error && (
          <p role="alert" className="text-sm text-danger-soft">
            {error}
          </p>
        )}
        <Button type="submit" size="lg" className="mt-2 w-full rounded-field" loading={busy}>
          {STEP_LABEL[step]}
        </Button>
      </form>
    </Dialog>
  );
}
