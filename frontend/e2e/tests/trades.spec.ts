import { expect, test } from "@playwright/test";
import { mockAllApis, mockWebSocket } from "../fixtures/api-mocks";
import { createTrades } from "../fixtures/mock-data/trades";
import { TradesPage } from "../pages/trades.page";

test.describe("Trades", () => {
  let trades: TradesPage;

  test.beforeEach(async ({ page }) => {
    await mockWebSocket(page);
    await mockAllApis(page);
    trades = new TradesPage(page);
    await trades.goto();
  });

  test("displays Trade History heading", async ({ page }) => {
    await expect(page.getByRole("heading", { name: "Trade History" })).toBeVisible();
  });

  test("shows Total Trades stat", async () => {
    await expect(trades.statValue("total-trades")).toHaveText("34");
  });

  test("shows Win Rate stat", async () => {
    await expect(trades.statValue("trades-win-rate")).toHaveText("65%");
  });

  test("shows Total PnL stat", async () => {
    await expect(trades.statValue("total-pnl")).toHaveText("$1.85");
  });

  test("shows Winning trades stat", async () => {
    await expect(trades.statValue("winning-trades")).toHaveText("22");
  });

  test("displays trade table with rows", async () => {
    await expect(trades.tradeTable).toBeVisible();
    await expect(trades.tradeRow(1)).toBeVisible();
  });

  test("shows empty state when no trades", async ({ page }) => {
    await page.unrouteAll();
    await mockAllApis(page, { trades: [] });
    await trades.goto();
    await expect(trades.tradeTableEmpty).toBeVisible();
    await expect(trades.tradeTableEmpty).toHaveText("No trades yet");
  });

  test("strategy filter is visible with default value", async () => {
    await expect(trades.strategyFilter).toBeVisible();
    await expect(trades.strategyFilter).toHaveValue("");
  });

  test("limit filter is visible with default value", async () => {
    await expect(trades.limitFilter).toBeVisible();
    await expect(trades.limitFilter).toHaveValue("50");
  });

  test("export CSV button is visible", async () => {
    await expect(trades.exportCsvBtn).toBeVisible();
    await expect(trades.exportCsvBtn).toContainText("Export CSV");
  });
});
