import { describe, it, expect } from "vitest";
import { rupees, pctOf, relativeMonth, cn } from "@/lib/utils";

describe("rupees", () => {
  it("formats large numbers with INR locale", () => {
    expect(rupees(125000)).toMatch(/1,25,000/);
  });
  it("compact mode uses lakhs", () => {
    expect(rupees(250000, { compact: true })).toBe("₹2.5L");
  });
  it("compact mode uses k for thousands", () => {
    expect(rupees(5400, { compact: true })).toBe("₹5.4k");
  });
  it("handles negatives", () => {
    expect(rupees(-1000)).toMatch(/-/);
  });
});

describe("pctOf", () => {
  it("returns rounded percent", () => {
    expect(pctOf(25, 100)).toBe(25);
    expect(pctOf(1, 3)).toBe(33);
  });
  it("returns 0 when whole is 0", () => {
    expect(pctOf(10, 0)).toBe(0);
  });
});

describe("relativeMonth", () => {
  it("formats month-year", () => {
    expect(relativeMonth("2026-03")).toMatch(/Mar/);
  });
});

describe("cn", () => {
  it("merges class strings, deduping conflicts", () => {
    expect(cn("p-2", "p-4")).toBe("p-4");
    expect(cn("text-sm", undefined, "text-base")).toBe("text-base");
  });
});
