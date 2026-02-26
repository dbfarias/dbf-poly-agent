import type { Locator, Page } from "@playwright/test";

export class RiskPage {
  readonly page: Page;
  readonly container: Locator;
  readonly categoryExposure: Locator;
  readonly categoryExposureEmpty: Locator;
  readonly riskLimitsSection: Locator;
  readonly riskLimitsTitle: Locator;

  constructor(page: Page) {
    this.page = page;
    this.container = page.getByTestId("risk-page");
    this.categoryExposure = page.getByTestId("category-exposure");
    this.categoryExposureEmpty = page.getByTestId("category-exposure-empty");
    this.riskLimitsSection = page.getByTestId("risk-limits-section");
    this.riskLimitsTitle = page.getByTestId("risk-limits-title");
  }

  statCard(id: string): Locator {
    return this.page.getByTestId(`stat-card-${id}`);
  }

  statValue(id: string): Locator {
    return this.page.getByTestId(`stat-value-${id}`);
  }

  riskLimit(index: number): Locator {
    return this.page.getByTestId(`risk-limit-${index}`);
  }

  async goto(): Promise<void> {
    await this.page.goto("/risk");
  }
}
