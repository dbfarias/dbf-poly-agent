import { expect, test } from "@playwright/test";
import { mockAllApis, mockWebSocket } from "../fixtures/api-mocks";
import { StrategiesPage } from "../pages/strategies.page";

test.describe("Strategies", () => {
  let strategies: StrategiesPage;

  test.beforeEach(async ({ page }) => {
    await mockWebSocket(page);
    await mockAllApis(page);
    strategies = new StrategiesPage(page);
    await strategies.goto();
  });

  test("displays Strategy Performance heading", async ({ page }) => {
    await expect(page.getByRole("heading", { name: "Strategy Performance" })).toBeVisible();
  });

  test("shows all 4 strategy cards", async () => {
    for (const name of ["time_decay", "arbitrage", "value_betting", "market_making"]) {
      await expect(strategies.strategyCard(name)).toBeVisible();
    }
  });

  test("shows Active status for time_decay with trades", async () => {
    await expect(strategies.strategyStatus("time_decay")).toHaveText("Active");
  });

  test("shows Active status for arbitrage with trades", async () => {
    await expect(strategies.strategyStatus("arbitrage")).toHaveText("Active");
  });

  test("shows Waiting status for value_betting with no trades", async () => {
    await expect(strategies.strategyStatus("value_betting")).toHaveText("Waiting");
  });

  test("shows metrics grid for active strategy", async () => {
    await expect(strategies.strategyMetrics("time_decay")).toBeVisible();
    await expect(strategies.strategyMetrics("time_decay")).toContainText("18");
    await expect(strategies.strategyMetrics("time_decay")).toContainText("67%");
  });

  test("shows empty message for inactive strategy", async () => {
    await expect(strategies.strategyEmpty("value_betting")).toBeVisible();
    await expect(strategies.strategyEmpty("value_betting")).toContainText("No trades yet");
  });

  test("displays tier labels on strategy cards", async () => {
    await expect(strategies.strategyCard("time_decay")).toContainText("Tier 1+");
    await expect(strategies.strategyCard("value_betting")).toContainText("Tier 2+");
    await expect(strategies.strategyCard("market_making")).toContainText("Tier 3+");
  });
});
