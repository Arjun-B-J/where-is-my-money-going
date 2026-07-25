import { test, expect } from "@playwright/test";

/**
 * End-to-end happy path: click Start → pipeline runs → dashboard loads.
 *
 * Backend must be running on :8000 with the stub Ollama (or real Ollama).
 * In CI we skip this; locally it's the canonical demo.
 */
test.describe("Where Is My Money Going? full flow", () => {
  test.skip(!!process.env.CI, "Skipping E2E in CI (no backend)");

  test("click start runs pipeline and navigates to dashboard", async ({ page }) => {
    await page.goto("/");
    await page.getByTestId("start-button").click();
    await page.waitForURL("**/dashboard", { timeout: 90_000 });
    await expect(page.getByRole("heading", { name: /Dashboard/i })).toBeVisible();
    await expect(page.getByText(/transactions over the last/i)).toBeVisible();
  });

  test("transactions page lists rows", async ({ page }) => {
    await page.goto("/transactions");
    await expect(page.getByRole("heading", { name: "Transactions" })).toBeVisible();
  });

  test("pipeline page renders graph topology", async ({ page }) => {
    await page.goto("/pipeline");
    await expect(page.getByText("LangGraph pipeline")).toBeVisible();
    await expect(page.getByText("Generate / extract")).toBeVisible();
  });
});
