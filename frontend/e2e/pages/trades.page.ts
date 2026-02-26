import type { Locator, Page } from "@playwright/test";

export class TradesPage {
  readonly page: Page;
  readonly container: Locator;
  readonly exportCsvBtn: Locator;
  readonly strategyFilter: Locator;
  readonly limitFilter: Locator;
  readonly tradeTable: Locator;
  readonly tradeTableEmpty: Locator;
  readonly tradeTableBody: Locator;

  constructor(page: Page) {
    this.page = page;
    this.container = page.getByTestId("trades-page");
    this.exportCsvBtn = page.getByTestId("export-csv-btn");
    this.strategyFilter = page.getByTestId("strategy-filter");
    this.limitFilter = page.getByTestId("limit-filter");
    this.tradeTable = page.getByTestId("trade-table");
    this.tradeTableEmpty = page.getByTestId("trade-table-empty");
    this.tradeTableBody = page.getByTestId("trade-table-body");
  }

  statValue(id: string): Locator {
    return this.page.getByTestId(`stat-value-${id}`);
  }

  tradeRow(id: number): Locator {
    return this.page.getByTestId(`trade-row-${id}`);
  }

  async selectStrategy(value: string): Promise<void> {
    await this.strategyFilter.selectOption(value);
  }

  async selectLimit(value: string): Promise<void> {
    await this.limitFilter.selectOption(value);
  }

  async goto(): Promise<void> {
    await this.page.goto("/trades");
  }
}
