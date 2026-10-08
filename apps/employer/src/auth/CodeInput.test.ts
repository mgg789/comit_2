import { describe, expect, it } from "vitest";
import { applyCellInput, OTP_LENGTH } from "./CodeInput";

describe("applyCellInput", () => {
  it("matches the api-core OTP length", () => {
    expect(OTP_LENGTH).toBe(6);
  });

  it("writes a typed digit into its cell and advances focus", () => {
    expect(applyCellInput("12", 2, "3")).toEqual({ next: "123", focus: 3 });
  });

  it("replaces an existing digit when the cell content was not selected", () => {
    expect(applyCellInput("129", 2, "93")).toEqual({ next: "123", focus: 3 });
  });

  // Regression: OS one-time-code autofill inserts the whole code into the first cell at once;
  // a maxLength=1 cell used to keep only the first digit.
  it("spreads a full autofilled code across all cells", () => {
    expect(applyCellInput("", 0, "638053")).toEqual({ next: "638053", focus: 5 });
  });

  it("ignores non-digit input", () => {
    expect(applyCellInput("12", 2, "a")).toBeNull();
  });

  it("never grows the code past its length", () => {
    expect(applyCellInput("123456", 5, "7")?.next).toBe("123457");
  });
});
