import type { Locator, Page } from "@playwright/test";

export class MarketsPage {
  readonly page: Page;
  readonly container: Locator;
  readonly loading: Locator;
  readonly empty: Locator;
  readonly table: Locator;

  constructor(page: Page) {
    this.page = page;
    this.container = page.getByTestId("markets-page");
    this.loading = page.getByTestId("markets-loading");
    this.empty = page.getByTestId("markets-empty");
    this.table = page.getByTestId("markets-table");
  }

  row(index: number): Locator {
    return this.page.getByTestId(`market-row-${index}`);
  }

  edge(index: number): Locator {
    return this.page.getByTestId(`market-edge-${index}`);
  }

  async getRowCount(): Promise<number> {
    return this.page.locator("[data-testid^='market-row-']").count();
  }

  async goto(): Promise<void> {
    await this.page.goto("/markets");
  }
}
