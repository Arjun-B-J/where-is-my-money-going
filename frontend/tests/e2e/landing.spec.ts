import { test, expect } from "@playwright/test";

test.describe("Landing page", () => {
  test("renders the hero and Start button", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("heading", { level: 1 })).toContainText(/agentically tracked/i);
    await expect(page.getByTestId("start-button")).toBeVisible();
  });

  test("shows feature cards", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByText("LangGraph pipeline")).toBeVisible();
    await expect(page.getByText("Local-first")).toBeVisible();
  });
});
