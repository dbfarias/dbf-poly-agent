import type { Locator, Page } from "@playwright/test";

export class SettingsPage {
  readonly page: Page;
  readonly container: Locator;
  readonly tradingControls: Locator;
  readonly tradingMode: Locator;
  readonly tradingStatus: Locator;
  readonly pauseBtn: Locator;
  readonly resumeBtn: Locator;
  readonly inputScanInterval: Locator;
  readonly inputMaxDailyLoss: Locator;
  readonly inputMaxDrawdown: Locator;
  readonly saveConfigBtn: Locator;
  readonly systemInfo: Locator;
  readonly systemStatus: Locator;
  readonly systemUptime: Locator;
  readonly systemCycleCount: Locator;
  readonly systemEngine: Locator;

  constructor(page: Page) {
    this.page = page;
    this.container = page.getByTestId("settings-page");
    this.tradingControls = page.getByTestId("trading-controls");
    this.tradingMode = page.getByTestId("trading-mode");
    this.tradingStatus = page.getByTestId("trading-status");
    this.pauseBtn = page.getByTestId("pause-btn");
    this.resumeBtn = page.getByTestId("resume-btn");
    this.inputScanInterval = page.getByTestId("input-scan-interval");
    this.inputMaxDailyLoss = page.getByTestId("input-max-daily-loss");
    this.inputMaxDrawdown = page.getByTestId("input-max-drawdown");
    this.saveConfigBtn = page.getByTestId("save-config-btn");
    this.systemInfo = page.getByTestId("system-info");
    this.systemStatus = page.getByTestId("system-status");
    this.systemUptime = page.getByTestId("system-uptime");
    this.systemCycleCount = page.getByTestId("system-cycle-count");
    this.systemEngine = page.getByTestId("system-engine");
  }

  async goto(): Promise<void> {
    await this.page.goto("/settings");
  }
}
