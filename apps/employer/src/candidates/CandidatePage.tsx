import type { PublicNarrative, PublicSkill, RecruitmentView } from "@comit/shared";
import { Button, StateMessage, cx, plusDiscUrl, AnimatePresence, REVEAL, fadeUp, motion } from "@comit/ui-kit";
import { useState, type ReactNode } from "react";
import { useParams, useSearchParams } from "react-router";
import { api, describeError } from "../api";
import { PageHeader } from "../layout/PageHeader";
import { CLAIM_STATUS_LABEL, GRADE_LABEL } from "../lib/explain";
import { SPECIALIZATION_LABEL, WORK_TYPE_LABEL, candidateLabel, formatSalaryRange, vacancyTitle } from "../lib/format";
import { useResource } from "../lib/use-resource";
import { RecruitmentControls } from "../recruitment/RecruitmentPanel";

/**
 * Candidate profile (Figma «ПРОСМОТР», «К людям»). Shows only the anonymous public projection —
 * contacts stay behind an accepted invitation (TZ §2.2). With `?vacancy=<draft>` the page also
 * carries the hiring controls for that vacancy.
 */
export function CandidatePage() {
  const { candidateId = "" } = useParams();
  const [search] = useSearchParams();
  const draftId = search.get("vacancy");
  const candidate = useResource(() => api.getPublicCandidate(candidateId), [candidateId]);
  const backTo = draftId ? `/vacancies/${draftId}` : "/candidates";
  const backTitle = draftId ? "К вакансии" : "К людям";

  if (!candidate.data) {
    return (
      <>
        <PageHeader title={backTitle} backTo={backTo} />
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
  const name = candidateLabel(detail.display_name, detail.candidate_id);
  const tags = [
    ...profile.stacks.slice(0, 2).map((skill) => skill.key),
    `${GRADE_LABEL[detail.category.grade]}${detail.category.grade_status === "self_reported" ? " (заявлен)" : ""}`,
    detail.city,
    (profile.work_types ?? []).map((type) => WORK_TYPE_LABEL[type]).join("/"),
    SPECIALIZATION_LABEL[detail.category.specialization],
    ...profile.preferences.slice(0, 1),
  ].filter(Boolean);

  let hash = 0;
  for (const char of name) hash = (hash * 31 + char.charCodeAt(0)) >>> 0;
  const hue = hash % 360;

  return (
    <>
      <PageHeader title={backTitle} backTo={backTo} />
      <div className="mx-auto flex max-w-[95.5rem] flex-col items-center">
        <h2 className="text-center font-display text-3xl leading-none tracking-[-0.12rem] lg:text-[4rem]">{name}</h2>
        <div aria-hidden className="relative my-6 size-40 lg:my-[1.8rem] lg:size-[22.875rem]">
          <span
            className="absolute inset-[12%] rounded-full blur-[3.125rem]"
            style={{ background: `radial-gradient(circle, hsl(${hue} 70% 62%), hsl(${(hue + 50) % 360} 60% 38%) 60%, transparent 75%)` }}
          />
        </div>
        <ul className="flex max-w-[56rem] flex-wrap justify-center gap-[0.625rem]">
          {tags.map((tag) => (
            <li
              key={tag}
              className="inline-flex h-11 items-center rounded-full bg-light-strong px-5 text-base font-medium tracking-[-0.023rem] text-on-chip lg:h-[3.375rem] lg:px-[1.25rem] lg:text-[1.375rem]"
            >
              {tag}
            </li>
          ))}
        </ul>
        <p className="mt-4 text-center text-lg text-white/60 lg:mt-[1.25rem] lg:text-[1.5rem]">
          Ожидания {formatSalaryRange(detail.salary.from_rub, detail.salary.to_rub)} в месяц
        </p>

        <motion.section
          initial="hidden"
          animate="show"
          variants={{ hidden: {}, show: { transition: { staggerChildren: 0.05, delayChildren: 0.1 } } }}
          className="mt-10 flex w-full flex-col gap-[1.1875rem] lg:mt-[5.25rem]"
        >
          <h3 className="mb-3 font-display text-2xl tracking-[-0.075rem] text-white/60 lg:mb-[2.0625rem] lg:text-[2.5rem]">Информация о соискателе</h3>
          {profile.description && (
            <Section title="О себе">
              <p className="whitespace-pre-line">{profile.description}</p>
            </Section>
          )}
          <Section title="Достижения — внешний контур ФСП" empty="Подтверждённых достижений нет — это нормальное состояние профиля.">
            {profile.achievements.length > 0 && <NarrativeList items={profile.achievements} />}
          </Section>
          <Section title="Опыт работы" empty="Опыт не указан.">
            {profile.experience.length > 0 && <NarrativeList items={profile.experience} />}
          </Section>
          <Section title="Проекты" empty="Проекты не указаны.">
            {profile.projects.length > 0 && <NarrativeList items={profile.projects} />}
          </Section>
          <Section title="Тесты — переносимые">
            <p>
              {SPECIALIZATION_LABEL[detail.category.specialization]} {GRADE_LABEL[detail.category.grade]} —{" "}
              {detail.category.grade_status === "approved" ? "подтверждено грейд-тестом" : "заявлено кандидатом, грейд-тест не пройден"}.
            </p>
            <p className="mt-1 text-on-chip/70">Подтверждённая сила профиля: {detail.confirmed_strength}</p>
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
          {draftId ? (
            <VacancyHiring draftId={draftId} candidateId={candidateId} resumeUrl={detail.resume_url} />
          ) : (
            <div className="mt-4 flex justify-center lg:mt-[2.875rem]">
              <a
                href={detail.resume_url}
                target="_blank"
                rel="noreferrer"
                className="inline-flex h-14 items-center rounded-[1.25rem] bg-dark-button px-8 text-lg font-semibold tracking-[-0.045rem] text-white hover:brightness-125 lg:h-[4.75rem] lg:px-[3.75rem] lg:text-[1.5rem]"
              >
                PDF Резюме
              </a>
            </div>
          )}
        </motion.section>
      </div>
    </>
  );
}

/** Loads the vacancy and the pair's process, then renders the hiring controls. */
function VacancyHiring({ draftId, candidateId, resumeUrl }: { draftId: string; candidateId: string; resumeUrl: string }) {
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
    <RecruitmentControls
      draftId={draftId}
      candidateId={candidateId}
      process={process}
      vacancySalary={fields.zp_min != null && fields.zp_max != null ? { from_rub: fields.zp_min, to_rub: fields.zp_max } : null}
      vacancyTitle={vacancyTitle(vacancy.data)}
      resumeUrl={resumeUrl}
      onChanged={(next: RecruitmentView) => {
        setOverride(next);
        processes.reload();
      }}
    />
  );
}

/** Collapsible light row (design: #f0f0f0, radius 32, 26px text, 80px «+» disc that turns into «×»). */
function Section({ title, empty, children }: { title: string; empty?: string; children?: ReactNode }) {
  const [open, setOpen] = useState(false);
  const hasContent = Boolean(children) && children !== false;
  return (
    <motion.div variants={fadeUp} className="overflow-hidden rounded-[2rem] bg-light text-on-chip">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
        className="flex w-full cursor-pointer items-center justify-between gap-4 px-5 py-3 text-left text-xl font-medium tracking-[-0.04875rem] lg:px-[1.5625rem] lg:py-[1.25rem] lg:text-[1.625rem]"
      >
        {title}
        <img src={plusDiscUrl} alt="" className={cx("size-12 shrink-0 transition-transform duration-300 ease-out lg:size-[5rem]", open && "rotate-45")} />
      </button>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={REVEAL}
          >
            <div className="px-5 pb-6 text-base lg:px-[1.5625rem] lg:pb-[1.75rem] lg:text-[1.25rem]">
              {hasContent ? children : <p className="text-on-chip/60">{empty}</p>}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  );
}

function NarrativeList({ items }: { items: PublicNarrative[] }) {
  return (
    <ul className="flex flex-col gap-3">
      {items.map((item) => (
        <li key={`${item.title}-${item.description.slice(0, 16)}`}>
          <p className="font-semibold">
            {item.title} <span className="font-normal text-on-chip/60">· {CLAIM_STATUS_LABEL[item.status]}</span>
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
        <p className="text-on-chip/60">—</p>
      ) : (
        <ul className="flex flex-col gap-1">
          {skills.map((skill) => (
            <li key={skill.key} className="flex justify-between gap-2">
              <span>{skill.key}</span>
              <span className="text-on-chip/60">
                {skill.level}/7 · {CLAIM_STATUS_LABEL[skill.status]}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
