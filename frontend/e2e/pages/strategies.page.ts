import type { Locator, Page } from "@playwright/test";

export class StrategiesPage {
  readonly page: Page;
  readonly container: Locator;
  readonly loading: Locator;

  constructor(page: Page) {
    this.page = page;
    this.container = page.getByTestId("strategies-page");
    this.loading = page.getByTestId("strategies-loading");
  }

  strategyCard(name: string): Locator {
    return this.page.getByTestId(`strategy-card-${name}`);
  }

  strategyStatus(name: string): Locator {
    return this.page.getByTestId(`strategy-status-${name}`);
  }

  strategyMetrics(name: string): Locator {
    return this.page.getByTestId(`strategy-metrics-${name}`);
  }

  strategyEmpty(name: string): Locator {
    return this.page.getByTestId(`strategy-empty-${name}`);
  }

  async goto(): Promise<void> {
    await this.page.goto("/strategies");
  }
}
