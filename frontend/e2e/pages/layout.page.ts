import type { Locator, Page } from "@playwright/test";

export class LayoutPage {
  readonly sidebar: Locator;
  readonly sidebarTitle: Locator;
  readonly wsIndicator: Locator;
  readonly wsStatusText: Locator;
  readonly mainContent: Locator;

  constructor(private readonly page: Page) {
    this.sidebar = page.getByTestId("sidebar");
    this.sidebarTitle = page.getByTestId("sidebar-title");
    this.wsIndicator = page.getByTestId("ws-indicator");
    this.wsStatusText = page.getByTestId("ws-status-text");
    this.mainContent = page.getByTestId("main-content");
  }

  navLink(label: string): Locator {
    return this.page.getByTestId(`nav-${label.toLowerCase()}`);
  }

  async navigateTo(label: string): Promise<void> {
    await this.navLink(label).click();
  }
}
