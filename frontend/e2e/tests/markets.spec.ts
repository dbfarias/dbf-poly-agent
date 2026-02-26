import { expect, test } from "@playwright/test";
import { mockAllApis, mockWebSocket } from "../fixtures/api-mocks";
import { createMarketList } from "../fixtures/mock-data/markets";
import { MarketsPage } from "../pages/markets.page";

test.describe("Markets", () => {
  let markets: MarketsPage;

  test.beforeEach(async ({ page }) => {
    await mockWebSocket(page);
    await mockAllApis(page);
    markets = new MarketsPage(page);
    await markets.goto();
  });

  test("displays Market Scanner heading", async ({ page }) => {
    await expect(page.getByRole("heading", { name: "Market Scanner" })).toBeVisible();
  });

  test("shows markets table with rows", async () => {
    await expect(markets.table).toBeVisible();
    const count = await markets.getRowCount();
    expect(count).toBe(5);
  });

  test("displays market row content", async () => {
    await expect(markets.row(0)).toBeVisible();
    await expect(markets.row(0)).toContainText("Will SpaceX launch Starship successfully?");
  });

  test("shows edge percentage for markets with signal", async () => {
    await expect(markets.edge(0)).toContainText("2.0%");
  });

  test("shows dash for markets without edge", async () => {
    // Market index 3 and 4 have zero edge
    await expect(markets.edge(3)).toHaveText("—");
  });

  test("shows green color for high edge", async () => {
    // Market index 2: edge = 0.05 > 0.03
    await expect(markets.edge(2)).toHaveClass(/text-green-400/);
  });

  test("shows yellow color for medium edge", async () => {
    // Market index 0: edge = 0.02 > 0.01 but <= 0.03
    await expect(markets.edge(0)).toHaveClass(/text-yellow-400/);
  });

  test("shows empty state when no markets", async ({ page }) => {
    await page.unrouteAll();
    await mockAllApis(page, { markets: [] });
    await markets.goto();
    await expect(markets.empty).toBeVisible();
    await expect(markets.empty).toHaveText("No opportunities found");
  });

  test("handles null hours_to_resolution", async () => {
    // Market index 2 has null hours_to_resolution
    await expect(markets.row(2)).toContainText("—");
  });
});
