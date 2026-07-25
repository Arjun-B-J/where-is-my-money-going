import path from "node:path";

import { type Page, test } from "@playwright/test";

/**
 * Capture the README screenshots.
 *
 * Prerequisites: the backend running, and `wimmg demo` already completed so the
 * database holds categorised transactions.
 *
 *   npx playwright test tests/e2e/screenshots.spec.ts
 *
 * These images ship in the repository, so they must only ever be taken against
 * the synthetic dataset. docs/PRIVACY.md explains why that is stated so firmly.
 */

const SCREENS = path.resolve(__dirname, "../../../docs/screenshots");

test.describe.configure({ mode: "serial" });

test.use({
  viewport: { width: 1440, height: 900 },
  // 2x keeps the images sharp when GitHub renders the README at full width.
  deviceScaleFactor: 2,
});

/** Let animations and in-flight fetches settle before capturing. */
async function settle(page: Page, ms = 1_200) {
  await page.waitForLoadState("networkidle").catch(() => {});
  await page.waitForTimeout(ms);
}

/**
 * Grow the viewport to fit the whole page, then re-settle.
 *
 * Preferred over `fullPage: true` because fullPage resizes the page *during*
 * capture, and recharts re-measures on resize — which can catch a chart
 * mid-relayout. Resizing first and waiting lets everything settle at the final
 * width. Measured rather than hardcoded, so a card growing taller cannot silently
 * clip the screenshot.
 */
async function fitViewport(page: Page, width = 1440) {
  const height = await page.evaluate(() =>
    Math.ceil(document.documentElement.scrollHeight),
  );
  await page.setViewportSize({ width, height: Math.min(height + 24, 8000) });
  await settle(page, 1_500);
}

test("landing", async ({ page }) => {
  await page.goto("/");
  // The hero canvas animates continuously; this is long enough for particles to
  // have spread across all six streams.
  await settle(page, 2_500);
  await page.screenshot({ path: path.join(SCREENS, "landing.png") });
});

test("dashboard", async ({ page }) => {
  // Insight cards are a model call, slow on a cold model.
  test.setTimeout(240_000);
  await page.goto("/dashboard");
  await page.getByText("Spent", { exact: true }).first().waitFor({ timeout: 30_000 });
  await page
    .getByText("Nothing to show yet")
    .waitFor({ state: "detached", timeout: 180_000 })
    .catch(() => {});
  await settle(page, 2_000);
  await fitViewport(page);
  await page.screenshot({ path: path.join(SCREENS, "dashboard.png") });
});

test("transactions", async ({ page }) => {
  await page.goto("/transactions");
  await page.getByRole("heading", { name: "Transactions" }).waitFor({ timeout: 20_000 });
  await settle(page);
  await page.screenshot({ path: path.join(SCREENS, "transactions.png") });
});

test("people", async ({ page }) => {
  await page.goto("/people");
  await page.getByRole("heading", { name: "People" }).waitFor({ timeout: 20_000 });
  await settle(page);
  await page.screenshot({ path: path.join(SCREENS, "people.png") });
});

test("pipeline", async ({ page }) => {
  await page.goto("/pipeline");
  await settle(page, 1_500);
  await fitViewport(page);
  await page.screenshot({ path: path.join(SCREENS, "pipeline.png") });
});

test("statements", async ({ page }) => {
  await page.goto("/ingest");
  await settle(page);
  await page.screenshot({ path: path.join(SCREENS, "ingest.png") });
});

test("chat", async ({ page }) => {
  await page.goto("/chat");
  await settle(page);
  await page.screenshot({ path: path.join(SCREENS, "chat.png") });
});

test("receipt", async ({ page }) => {
  await page.goto("/receipt");
  await settle(page);
  await page.screenshot({ path: path.join(SCREENS, "receipt.png") });
});
