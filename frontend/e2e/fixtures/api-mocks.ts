import type { Page } from "@playwright/test";
import { createBotConfig, createHealthCheck } from "./mock-data/config";
import { createMarketList } from "./mock-data/markets";
import { createPortfolioOverview, createEquityPoints, createPositions } from "./mock-data/portfolio";
import { createRiskLimits, createRiskMetrics } from "./mock-data/risk";
import { createAllStrategies } from "./mock-data/strategies";
import { createTrades, createTradeStats } from "./mock-data/trades";

export interface MockOverrides {
  portfolio?: Parameters<typeof createPortfolioOverview>[0];
  positions?: ReturnType<typeof createPositions>;
  equityCurve?: ReturnType<typeof createEquityPoints>;
  trades?: ReturnType<typeof createTrades>;
  tradeStats?: Parameters<typeof createTradeStats>[0];
  strategies?: ReturnType<typeof createAllStrategies>;
  markets?: ReturnType<typeof createMarketList>;
  riskMetrics?: Parameters<typeof createRiskMetrics>[0];
  riskLimits?: Parameters<typeof createRiskLimits>[0];
  config?: Parameters<typeof createBotConfig>[0];
  health?: Parameters<typeof createHealthCheck>[0];
  failEndpoints?: string[];
}

export async function mockAllApis(page: Page, overrides: MockOverrides = {}): Promise<void> {
  const fail = new Set(overrides.failEndpoints ?? []);

  const routes: Array<{ pattern: string; body: unknown }> = [
    {
      pattern: "**/api/portfolio/overview",
      body: createPortfolioOverview(overrides.portfolio),
    },
    {
      pattern: "**/api/portfolio/positions",
      body: overrides.positions ?? createPositions(),
    },
    {
      pattern: "**/api/portfolio/equity-curve*",
      body: overrides.equityCurve ?? createEquityPoints(),
    },
    {
      pattern: "**/api/trades/history*",
      body: overrides.trades ?? createTrades(),
    },
    {
      pattern: "**/api/trades/stats",
      body: createTradeStats(overrides.tradeStats),
    },
    {
      pattern: "**/api/strategies/performance",
      body: overrides.strategies ?? createAllStrategies(),
    },
    {
      pattern: "**/api/markets/scanner*",
      body: overrides.markets ?? createMarketList(),
    },
    {
      pattern: "**/api/risk/metrics",
      body: createRiskMetrics(overrides.riskMetrics),
    },
    {
      pattern: "**/api/risk/limits",
      body: createRiskLimits(overrides.riskLimits),
    },
    {
      pattern: "**/api/config/",
      body: createBotConfig(overrides.config),
    },
    {
      pattern: "**/api/health",
      body: createHealthCheck(overrides.health),
    },
  ];

  for (const { pattern, body } of routes) {
    await page.route(pattern, (route) => {
      if (fail.has(pattern)) {
        return route.fulfill({ status: 500, body: "Internal Server Error" });
      }
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(body),
      });
    });
  }

  // Mock pause/resume POST endpoints
  await page.route("**/api/config/trading/pause", (route) => {
    if (fail.has("**/api/config/trading/pause")) {
      return route.fulfill({ status: 500, body: "Internal Server Error" });
    }
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ status: "paused" }),
    });
  });

  await page.route("**/api/config/trading/resume", (route) => {
    if (fail.has("**/api/config/trading/resume")) {
      return route.fulfill({ status: 500, body: "Internal Server Error" });
    }
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ status: "running" }),
    });
  });

  // Mock PUT config
  await page.route("**/api/config/", (route) => {
    if (route.request().method() === "PUT") {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(createBotConfig(overrides.config)),
      });
    }
    // GET already handled above — fallback
    if (fail.has("**/api/config/")) {
      return route.fulfill({ status: 500, body: "Internal Server Error" });
    }
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(createBotConfig(overrides.config)),
    });
  });
}

export async function mockWebSocket(page: Page): Promise<void> {
  await page.addInitScript(() => {
    // Stub WebSocket to prevent real connections and avoid errors
    const OriginalWebSocket = window.WebSocket;
    class MockWebSocket extends EventTarget {
      static readonly CONNECTING = 0;
      static readonly OPEN = 1;
      static readonly CLOSING = 2;
      static readonly CLOSED = 3;

      readonly CONNECTING = 0;
      readonly OPEN = 1;
      readonly CLOSING = 2;
      readonly CLOSED = 3;

      readyState = MockWebSocket.CONNECTING;
      url: string;
      protocol = "";
      extensions = "";
      bufferedAmount = 0;
      binaryType: BinaryType = "blob";

      onopen: ((ev: Event) => void) | null = null;
      onmessage: ((ev: MessageEvent) => void) | null = null;
      onclose: ((ev: CloseEvent) => void) | null = null;
      onerror: ((ev: Event) => void) | null = null;

      constructor(url: string | URL, _protocols?: string | string[]) {
        super();
        this.url = typeof url === "string" ? url : url.toString();
        // Simulate connection after microtask
        setTimeout(() => {
          this.readyState = MockWebSocket.OPEN;
          const event = new Event("open");
          this.onopen?.(event);
          this.dispatchEvent(event);
        }, 10);
      }

      send(_data: string | ArrayBuffer | Blob | ArrayBufferView): void {
        // no-op
      }

      close(_code?: number, _reason?: string): void {
        this.readyState = MockWebSocket.CLOSED;
        const event = new CloseEvent("close", { code: 1000, reason: "mock" });
        this.onclose?.(event);
        this.dispatchEvent(event);
      }
    }

    // @ts-expect-error - replacing WebSocket with mock
    window.WebSocket = MockWebSocket;
  });
}
