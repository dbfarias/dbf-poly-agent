import { expect, test } from "@playwright/test";
import { mockAllApis, mockWebSocket } from "../fixtures/api-mocks";

test.describe("Error States", () => {
  test.beforeEach(async ({ page }) => {
    await mockWebSocket(page);
  });

  test("dashboard handles portfolio API failure gracefully", async ({ page }) => {
    await mockAllApis(page, { failEndpoints: ["**/api/portfolio/overview"] });
    await page.goto("/");
    // Page should still render without crashing
    await expect(page.getByRole("heading", { name: "Dashboard" })).toBeVisible();
  });

  test("dashboard handles trade stats failure gracefully", async ({ page }) => {
    await mockAllApis(page, { failEndpoints: ["**/api/trades/stats"] });
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "Dashboard" })).toBeVisible();
  });

  test("trades page handles trades API failure gracefully", async ({ page }) => {
    await mockAllApis(page, { failEndpoints: ["**/api/trades/history*"] });
    await page.goto("/trades");
    await expect(page.getByRole("heading", { name: "Trade History" })).toBeVisible();
  });

  test("strategies page handles strategies API failure gracefully", async ({ page }) => {
    await mockAllApis(page, { failEndpoints: ["**/api/strategies/performance"] });
    await page.goto("/strategies");
    // Should still show the page (strategies defaults to empty array)
    await expect(page.getByTestId("strategies-page")).toBeVisible();
  });

  test("markets page handles markets API failure gracefully", async ({ page }) => {
    await mockAllApis(page, { failEndpoints: ["**/api/markets/scanner*"] });
    await page.goto("/markets");
    await expect(page.getByRole("heading", { name: "Market Scanner" })).toBeVisible();
  });

  test("risk page handles risk metrics failure gracefully", async ({ page }) => {
    await mockAllApis(page, { failEndpoints: ["**/api/risk/metrics"] });
    await page.goto("/risk");
    await expect(page.getByRole("heading", { name: "Risk Management" })).toBeVisible();
  });

  test("settings page handles config failure gracefully", async ({ page }) => {
    await mockAllApis(page, { failEndpoints: ["**/api/config/"] });
    await page.goto("/settings");
    await expect(page.getByRole("heading", { name: "Settings" })).toBeVisible();
  });

  test("settings page handles health failure gracefully", async ({ page }) => {
    await mockAllApis(page, { failEndpoints: ["**/api/health"] });
    await page.goto("/settings");
    await expect(page.getByRole("heading", { name: "Settings" })).toBeVisible();
  });
});
