import { expect, test } from "@playwright/test";
import { mockAllApis, mockWebSocket } from "../fixtures/api-mocks";
import { LayoutPage } from "../pages/layout.page";

test.describe("Layout & Navigation", () => {
  let layout: LayoutPage;

  test.beforeEach(async ({ page }) => {
    await mockWebSocket(page);
    await mockAllApis(page);
    await page.goto("/");
    layout = new LayoutPage(page);
  });

  test("displays sidebar with PolyBot title", async () => {
    await expect(layout.sidebar).toBeVisible();
    await expect(layout.sidebarTitle).toHaveText("PolyBot");
  });

  test("shows all 6 navigation links", async () => {
    for (const label of ["dashboard", "trades", "strategies", "markets", "risk", "settings"]) {
      await expect(layout.navLink(label)).toBeVisible();
    }
  });

  test("highlights Dashboard as active by default", async ({ page }) => {
    const dashboardLink = layout.navLink("dashboard");
    await expect(dashboardLink).toHaveClass(/text-indigo-400/);
  });

  test("navigates to Trades page", async ({ page }) => {
    await layout.navigateTo("trades");
    await expect(page).toHaveURL(/\/trades/);
    await expect(page.getByTestId("trades-page")).toBeVisible();
  });

  test("navigates to Strategies page", async ({ page }) => {
    await layout.navigateTo("strategies");
    await expect(page).toHaveURL(/\/strategies/);
    await expect(page.getByTestId("strategies-page")).toBeVisible();
  });

  test("navigates to Settings page", async ({ page }) => {
    await layout.navigateTo("settings");
    await expect(page).toHaveURL(/\/settings/);
    await expect(page.getByTestId("settings-page")).toBeVisible();
  });

  test("shows WebSocket connected indicator", async () => {
    await expect(layout.wsStatusText).toHaveText("Connected");
    await expect(layout.wsIndicator).toHaveClass(/bg-green-500/);
  });
});
