import type { AgentsDraftView } from "@comit/shared";
import { describe, expect, it } from "vitest";
import { candidateLabel, formatSalaryRange, topRequirements, vacancyBadge, vacancyTitle } from "./format";

function draft(patch: Partial<AgentsDraftView>): AgentsDraftView {
  return {
    draft_id: "d",
    vacancy_id: "d",
    owner_id: "o",
    revision: 1,
    state: "editing",
    fields: {},
    business_status: "draft",
    archived: false,
    ...patch,
  };
}

describe("formatSalaryRange", () => {
  it("shows the mandatory range in thousands of rubles", () => {
    expect(formatSalaryRange(180_000, 240_000)).toBe("180–240к ₽");
  });

  it("collapses an equal range and handles missing bounds honestly", () => {
    expect(formatSalaryRange(150_000, 150_000)).toBe("150к ₽");
    expect(formatSalaryRange(null, 200_000)).toBe("до 200к ₽");
    expect(formatSalaryRange(null, null)).toBe("ЗП не указана");
  });
});

describe("topRequirements", () => {
  it("orders requirement keys by declared level", () => {
    expect(topRequirements({ go: 3, postgresql: 7, grpc: 5 }, 2)).toEqual(["postgresql", "grpc"]);
    expect(topRequirements(undefined, 3)).toEqual([]);
  });
});

describe("vacancyBadge", () => {
  it("maps business status and publication state", () => {
    expect(vacancyBadge(draft({ business_status: "open", state: "created" }))).toEqual({ label: "Активная", tone: "success" });
    expect(vacancyBadge(draft({ business_status: "open", state: "editing" })).tone).toBe("warning");
    expect(vacancyBadge(draft({ business_status: "closed" })).label).toBe("Закрыта");
    expect(vacancyBadge(draft({ state: "creating" })).label).toBe("Публикуется");
    expect(vacancyBadge(draft({ state: "failed" })).tone).toBe("danger");
    expect(vacancyBadge(draft({}))).toEqual({ label: "Черновик", tone: "muted" });
  });
});

describe("vacancyTitle", () => {
  it("falls back for untitled drafts", () => {
    expect(vacancyTitle(draft({ fields: { title: "  " } }))).toBe("Вакансия без названия");
    expect(vacancyTitle(draft({ fields: { title: "Go-разработчик" } }))).toBe("Go-разработчик");
  });
});

describe("candidateLabel", () => {
  it("turns the anonymous matching label into a short handle and keeps real names", () => {
    expect(candidateLabel("Candidate 5e39d59e-cf2f-4f41-890b-0f24ee8eaf1f", "5e39d59e-cf2f-4f41-890b-0f24ee8eaf1f")).toBe("Кандидат 5E39");
    expect(candidateLabel("Анна Орлова", "x")).toBe("Анна Орлова");
  });
});
