import { describe, expect, it } from "vitest";
import { FUNNEL_STEPS, TERMINAL_STATES, funnelStep } from "./labels";

describe("funnelStep", () => {
  it("places processes on the Резюме stepper", () => {
    expect(FUNNEL_STEPS[funnelStep("sent", false)]).toBe("Тиндер");
    expect(FUNNEL_STEPS[funnelStep("accepted", true)]).toBe("Собеседование");
    expect(FUNNEL_STEPS[funnelStep("offer_sent", true)]).toBe("Оффер");
    expect(FUNNEL_STEPS[funnelStep("hired", true)]).toBe("Приём");
  });

  it("treats closed outcomes as terminal", () => {
    expect(TERMINAL_STATES.has("withdrawn")).toBe(true);
    expect(TERMINAL_STATES.has("accepted")).toBe(false);
  });
});
