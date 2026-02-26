import { expect, test } from "@playwright/test";
import { mockAllApis, mockWebSocket } from "../fixtures/api-mocks";
import { RiskPage } from "../pages/risk.page";

test.describe("Risk", () => {
  let risk: RiskPage;

  test.beforeEach(async ({ page }) => {
    await mockWebSocket(page);
    await mockAllApis(page);
    risk = new RiskPage(page);
    await risk.goto();
  });

  test("displays Risk Management heading", async ({ page }) => {
    await expect(page.getByRole("heading", { name: "Risk Management" })).toBeVisible();
  });

  test("shows Current Drawdown stat", async () => {
    await expect(risk.statCard("drawdown")).toBeVisible();
    await expect(risk.statValue("drawdown")).toHaveText("3.8%");
  });

  test("shows Daily PnL stat", async () => {
    await expect(risk.statCard("daily-pnl")).toBeVisible();
    await expect(risk.statValue("daily-pnl")).toHaveText("$0.18");
  });

  test("shows Positions stat", async () => {
    await expect(risk.statCard("risk-positions")).toBeVisible();
    await expect(risk.statValue("risk-positions")).toHaveText("2/3");
  });

  test("shows Trading Status stat as ACTIVE", async () => {
    await expect(risk.statValue("trading-status")).toHaveText("ACTIVE");
  });

  test("shows PAUSED status when paused", async ({ page }) => {
    await page.unrouteAll();
    await mockAllApis(page, { riskMetrics: { is_paused: true } });
    await risk.goto();
    await expect(risk.statValue("trading-status")).toHaveText("PAUSED");
  });

  test("shows category exposure section", async () => {
    await expect(risk.categoryExposure).toBeVisible();
  });

  test("shows empty category exposure when no positions", async ({ page }) => {
    await page.unrouteAll();
    await mockAllApis(page, { positions: [] });
    await risk.goto();
    await expect(risk.categoryExposureEmpty).toBeVisible();
    await expect(risk.categoryExposureEmpty).toHaveText("No positions");
  });

  test("shows risk limits section with tier", async () => {
    await expect(risk.riskLimitsSection).toBeVisible();
    await expect(risk.riskLimitsTitle).toContainText("TIER_1");
  });

  test("shows all 8 risk limit rows", async ({ page }) => {
    for (let i = 0; i < 8; i++) {
      await expect(risk.riskLimit(i)).toBeVisible();
    }
  });
});
