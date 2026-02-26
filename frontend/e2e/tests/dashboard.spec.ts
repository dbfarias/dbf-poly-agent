import { expect, test } from "@playwright/test";
import { mockAllApis, mockWebSocket } from "../fixtures/api-mocks";
import { DashboardPage } from "../pages/dashboard.page";

test.describe("Dashboard", () => {
  let dashboard: DashboardPage;

  test.beforeEach(async ({ page }) => {
    await mockWebSocket(page);
    await mockAllApis(page);
    dashboard = new DashboardPage(page);
    await dashboard.goto();
  });

  test("displays Dashboard heading", async ({ page }) => {
    await expect(page.getByRole("heading", { name: "Dashboard" })).toBeVisible();
  });

  test("shows PAPER trading mode badge", async () => {
    await expect(dashboard.tradingModeBadge).toHaveText("PAPER");
  });

  test("shows tier badge", async () => {
    await expect(dashboard.tierBadge).toHaveText("TIER_1");
  });

  test("shows LIVE badge when not paper trading", async ({ page }) => {
    await page.unrouteAll();
    await mockAllApis(page, { portfolio: { is_paper: false } });
    await dashboard.goto();
    await expect(dashboard.tradingModeBadge).toHaveText("LIVE");
  });

  test("displays Total Equity stat card", async () => {
    await expect(dashboard.statCard("total-equity")).toBeVisible();
    await expect(dashboard.statValue("total-equity")).toHaveText("$12.50");
  });

  test("displays Today's PnL stat card", async () => {
    await expect(dashboard.statCard("todays-pnl")).toBeVisible();
    await expect(dashboard.statValue("todays-pnl")).toHaveText("$0.18");
  });

  test("displays Win Rate stat card", async () => {
    await expect(dashboard.statCard("win-rate")).toBeVisible();
    await expect(dashboard.statValue("win-rate")).toHaveText("65%");
  });

  test("displays Open Positions stat card", async () => {
    await expect(dashboard.statCard("open-positions")).toBeVisible();
    await expect(dashboard.statValue("open-positions")).toHaveText("2");
  });

  test("shows equity chart", async ({ page }) => {
    await expect(page.getByTestId("equity-chart")).toBeVisible();
    await expect(page.getByTestId("equity-chart-title")).toHaveText("Equity Curve");
  });

  test("shows active positions section", async () => {
    await expect(dashboard.activePositionsSection).toBeVisible();
    await expect(dashboard.positionRow(1)).toBeVisible();
    await expect(dashboard.positionRow(2)).toBeVisible();
  });

  test("shows recent trades section", async () => {
    await expect(dashboard.recentTradesSection).toBeVisible();
  });

  test("hides positions section when empty", async ({ page }) => {
    await page.unrouteAll();
    await mockAllApis(page, { positions: [] });
    await dashboard.goto();
    await expect(dashboard.activePositionsSection).not.toBeVisible();
  });
});
