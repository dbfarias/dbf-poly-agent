import type { Locator, Page } from "@playwright/test";

export class DashboardPage {
  readonly page: Page;
  readonly container: Locator;
  readonly tradingModeBadge: Locator;
  readonly tierBadge: Locator;
  readonly activePositionsSection: Locator;
  readonly recentTradesSection: Locator;

  constructor(page: Page) {
    this.page = page;
    this.container = page.getByTestId("dashboard-page");
    this.tradingModeBadge = page.getByTestId("trading-mode-badge");
    this.tierBadge = page.getByTestId("tier-badge");
    this.activePositionsSection = page.getByTestId("active-positions-section");
    this.recentTradesSection = page.getByTestId("recent-trades-section");
  }

  statCard(id: string): Locator {
    return this.page.getByTestId(`stat-card-${id}`);
  }

  statValue(id: string): Locator {
    return this.page.getByTestId(`stat-value-${id}`);
  }

  positionRow(id: number): Locator {
    return this.page.getByTestId(`position-row-${id}`);
  }

  async goto(): Promise<void> {
    await this.page.goto("/");
  }
}
