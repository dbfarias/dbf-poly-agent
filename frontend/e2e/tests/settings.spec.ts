import { expect, test } from "@playwright/test";
import { mockAllApis, mockWebSocket } from "../fixtures/api-mocks";
import { SettingsPage } from "../pages/settings.page";

test.describe("Settings", () => {
  let settings: SettingsPage;

  test.beforeEach(async ({ page }) => {
    await mockWebSocket(page);
    await mockAllApis(page);
    settings = new SettingsPage(page);
    await settings.goto();
  });

  test("displays Settings heading", async ({ page }) => {
    await expect(page.getByRole("heading", { name: "Settings" })).toBeVisible();
  });

  test("shows trading controls section", async () => {
    await expect(settings.tradingControls).toBeVisible();
  });

  test("shows trading mode as PAPER", async () => {
    await expect(settings.tradingMode).toHaveText("PAPER");
  });

  test("shows trading status as RUNNING when not paused", async () => {
    await expect(settings.tradingStatus).toHaveText("RUNNING");
  });

  test("shows Pause button when running", async () => {
    await expect(settings.pauseBtn).toBeVisible();
    await expect(settings.pauseBtn).toHaveText("Pause");
  });

  test("shows Resume button when paused", async ({ page }) => {
    await page.unrouteAll();
    await mockAllApis(page, { riskMetrics: { is_paused: true } });
    await settings.goto();
    await expect(settings.resumeBtn).toBeVisible();
    await expect(settings.resumeBtn).toHaveText("Resume");
    await expect(settings.tradingStatus).toHaveText("PAUSED");
  });

  test("pause button sends POST to correct endpoint", async ({ page }) => {
    const requestPromise = page.waitForRequest("**/api/config/trading/pause");
    await settings.pauseBtn.click();
    const request = await requestPromise;
    expect(request.method()).toBe("POST");
  });

  test("shows config inputs", async () => {
    await expect(settings.inputScanInterval).toBeVisible();
    await expect(settings.inputMaxDailyLoss).toBeVisible();
    await expect(settings.inputMaxDrawdown).toBeVisible();
  });

  test("save button sends PUT to config endpoint", async ({ page }) => {
    await settings.inputScanInterval.fill("600");
    const requestPromise = page.waitForRequest((req) =>
      req.url().includes("/api/config/") && req.method() === "PUT",
    );
    await settings.saveConfigBtn.click();
    const request = await requestPromise;
    expect(request.method()).toBe("PUT");
    const body = request.postDataJSON();
    expect(body.scan_interval_seconds).toBe(600);
  });

  test("shows system info section", async () => {
    await expect(settings.systemInfo).toBeVisible();
    await expect(settings.systemStatus).toHaveText("healthy");
    await expect(settings.systemUptime).toHaveText("2.0h");
    await expect(settings.systemCycleCount).toHaveText("42");
    await expect(settings.systemEngine).toHaveText("Running");
  });

  test("shows stopped engine status", async ({ page }) => {
    await page.unrouteAll();
    await mockAllApis(page, { health: { engine_running: false } });
    await settings.goto();
    await expect(settings.systemEngine).toHaveText("Stopped");
  });
});
