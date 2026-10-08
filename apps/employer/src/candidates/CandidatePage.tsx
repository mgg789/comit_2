import type { PublicNarrative, PublicSkill, RecruitmentView } from "@comit/shared";
import { Avatar, Button, Chip, StateMessage, cx } from "@comit/ui-kit";
import { useState, type ReactNode } from "react";
import { useParams, useSearchParams } from "react-router";
import { api, describeError } from "../api";
import { PageHeader } from "../layout/PageHeader";
import { CLAIM_STATUS_LABEL, GRADE_LABEL } from "../lib/explain";
import { SPECIALIZATION_LABEL, WORK_TYPE_LABEL, formatSalaryRange, vacancyTitle } from "../lib/format";
import { useResource } from "../lib/use-resource";
import { RecruitmentPanel } from "../recruitment/RecruitmentPanel";

/**
 * Candidate profile (Figma «ПРОСМОТР», «К людям»). Shows only the anonymous public projection —
 * contacts stay behind an accepted invitation (TZ §2.2). With `?vacancy=<draft>` the page also
 * carries the hiring actions for that vacancy.
 */
export function CandidatePage() {
  const { candidateId = "" } = useParams();
  const [search] = useSearchParams();
  const draftId = search.get("vacancy");
  const candidate = useResource(() => api.getPublicCandidate(candidateId), [candidateId]);
  const backTo = draftId ? `/vacancies/${draftId}` : "/candidates";

  if (!candidate.data) {
    return (
      <>
        <PageHeader title={draftId ? "К вакансии" : "К людям"} backTo={backTo} />
        {candidate.status === "error" ? (
          <StateMessage
            kind="error"
            title="Профиль недоступен"
            description={describeError(candidate.error)}
            action={
              <Button variant="secondary" onClick={candidate.reload}>
                Повторить
              </Button>
            }
          />
        ) : (
          <StateMessage kind="loading" title="Загружаем профиль" />
        )}
      </>
    );
  }

  const detail = candidate.data;
  const profile = detail.public_profile;
  const tags = [
    ...profile.stacks.slice(0, 3).map((skill) => skill.key),
    `${GRADE_LABEL[detail.category.grade]}${detail.category.grade_status === "self_reported" ? " (заявлен)" : ""}`,
    detail.city,
    ...(profile.work_types ?? []).map((type) => WORK_TYPE_LABEL[type]),
    ...profile.preferences.slice(0, 2),
  ];

  return (
    <>
      <PageHeader title={draftId ? "К вакансии" : "К людям"} backTo={backTo} />
      <div className="mx-auto flex max-w-[1100px] flex-col items-center gap-6">
        <h2 className="text-center font-display text-3xl leading-tight sm:text-[56px]">{detail.display_name}</h2>
        <Avatar name={detail.display_name} size={180} />
        <p className="text-center text-lg text-subtle">
          {SPECIALIZATION_LABEL[detail.category.specialization]} · ожидания {formatSalaryRange(detail.salary.from_rub, detail.salary.to_rub)}
        </p>
        <ul className="flex max-w-[640px] flex-wrap justify-center gap-2">
          {tags.map((tag) => (
            <li key={tag}>
              <Chip>{tag}</Chip>
            </li>
          ))}
        </ul>
        {profile.description && <p className="max-w-[760px] text-center whitespace-pre-line text-subtle">{profile.description}</p>}

        <section className="flex w-full flex-col gap-4">
          <h3 className="font-display text-2xl text-white/70 sm:text-[32px]">Информация о соискателе</h3>
          <Section title="Достижения — внешний контур ФСП" empty="Подтверждённых достижений нет — это нормальное состояние профиля.">
            {profile.achievements.length > 0 && <NarrativeList items={profile.achievements} />}
          </Section>
          <Section title="Опыт работы" empty="Опыт не указан.">
            {profile.experience.length > 0 && <NarrativeList items={profile.experience} />}
          </Section>
          <Section title="Проекты" empty="Проекты не указаны.">
            {profile.projects.length > 0 && <NarrativeList items={profile.projects} />}
          </Section>
          <Section title="Тестирование и грейд" empty={null}>
            <p>
              Категория: {SPECIALIZATION_LABEL[detail.category.specialization]} {GRADE_LABEL[detail.category.grade]} —{" "}
              {detail.category.grade_status === "approved" ? "подтверждена грейд-тестом" : "заявлена кандидатом, тест не пройден"}.
            </p>
            <p className="mt-1 text-sm">Подтверждённая сила профиля: {detail.confirmed_strength}</p>
          </Section>
          <Section title="Вектор навыков" empty="Навыки не указаны.">
            {profile.stacks.length + profile.domains.length + profile.soft_skills.length > 0 && (
              <div className="grid gap-4 sm:grid-cols-3">
                <SkillColumn title="Стек" skills={profile.stacks} />
                <SkillColumn title="Домены" skills={profile.domains} />
                <SkillColumn title="Soft skills" skills={profile.soft_skills} />
              </div>
            )}
          </Section>
        </section>

        <div className="flex flex-wrap justify-center gap-3">
          <a href={detail.resume_url} target="_blank" rel="noreferrer" className="inline-flex h-14 items-center rounded-field bg-surface-raised px-8 text-lg font-semibold hover:bg-white/15">
            PDF Резюме
          </a>
        </div>

        {draftId && <VacancyHiring draftId={draftId} candidateId={candidateId} />}
      </div>
    </>
  );
}

/** Loads the vacancy and the pair's process, then renders the hiring panel. */
function VacancyHiring({ draftId, candidateId }: { draftId: string; candidateId: string }) {
  const vacancy = useResource(() => api.getVacancy(draftId), [draftId]);
  const processes = useResource(() => api.getEmployerRecruitment(draftId, 50), [draftId]);
  const [override, setOverride] = useState<RecruitmentView | null>(null);

  const failure = vacancy.status === "error" ? vacancy.error : processes.status === "error" ? processes.error : null;
  if (failure !== null) {
    return <StateMessage kind="error" title="Не удалось загрузить процесс найма" description={describeError(failure)} />;
  }
  if (!vacancy.data || !processes.data) return <StateMessage kind="loading" title="Загружаем процесс найма" />;

  const process = override ?? processes.data.items.find((item) => item.candidate_id === candidateId) ?? null;
  const { fields } = vacancy.data;
  return (
    <div className="w-full max-w-[760px]">
      <RecruitmentPanel
        draftId={draftId}
        candidateId={candidateId}
        process={process}
        vacancySalary={fields.zp_min != null && fields.zp_max != null ? { from_rub: fields.zp_min, to_rub: fields.zp_max } : null}
        vacancyTitle={vacancyTitle(vacancy.data)}
        onChanged={(next) => {
          setOverride(next);
          processes.reload();
        }}
      />
    </div>
  );
}

/** Collapsible light row from the design («+» toggles the body). */
function Section({ title, empty, children }: { title: string; empty: string | null; children?: ReactNode }) {
  const [open, setOpen] = useState(false);
  const hasContent = Boolean(children) && children !== false;
  return (
    <div className="rounded-[20px] bg-light text-on-light">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
        className="flex w-full cursor-pointer items-center justify-between gap-4 px-5 py-4 text-left text-lg sm:text-xl"
      >
        {title}
        <span aria-hidden className={cx("inline-flex size-12 items-center justify-center rounded-full bg-black/5 text-2xl transition-transform duration-150", open && "rotate-45")}>
          +
        </span>
      </button>
      {open && <div className="px-5 pb-5 text-base">{hasContent ? children : <p className="text-faint">{empty}</p>}</div>}
    </div>
  );
}

function NarrativeList({ items }: { items: PublicNarrative[] }) {
  return (
    <ul className="flex flex-col gap-3">
      {items.map((item) => (
        <li key={`${item.title}-${item.description.slice(0, 16)}`}>
          <p className="font-semibold">
            {item.title} <span className="text-sm font-normal text-faint">· {CLAIM_STATUS_LABEL[item.status]}</span>
          </p>
          {item.description && <p className="whitespace-pre-line">{item.description}</p>}
        </li>
      ))}
    </ul>
  );
}

function SkillColumn({ title, skills }: { title: string; skills: PublicSkill[] }) {
  return (
    <div>
      <h4 className="mb-2 font-semibold">{title}</h4>
      {skills.length === 0 ? (
        <p className="text-sm text-faint">—</p>
      ) : (
        <ul className="flex flex-col gap-1 text-sm">
          {skills.map((skill) => (
            <li key={skill.key} className="flex justify-between gap-2">
              <span>{skill.key}</span>
              <span className="text-faint">
                {skill.level}/7 · {CLAIM_STATUS_LABEL[skill.status]}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
