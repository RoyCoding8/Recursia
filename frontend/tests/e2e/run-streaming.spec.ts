// @ts-nocheck
import { expect, test } from "@playwright/test";

test.describe("Run streaming UX", () => {
  test.beforeEach(async ({ page }) => {
    await page.addInitScript(() => {
      const originalFetch = window.fetch.bind(window);

      class MockEventSource {
        static instances = [];

        constructor(url) {
          this.url = String(url);
          this.readyState = 0;
          this.onopen = null;
          this.onmessage = null;
          this.onerror = null;
          MockEventSource.instances.push(this);
        }

        close() {
          this.readyState = 2;
        }
      }

      // @ts-ignore
      window.EventSource = MockEventSource;

      // @ts-ignore
      window.__mockSSECount = () => MockEventSource.instances.length;

      // @ts-ignore
      window.__mockSSEOpen = (urlFragment) => {
        for (const source of MockEventSource.instances) {
          if (!urlFragment || source.url.includes(urlFragment)) {
            source.readyState = 1;
            source.onopen?.();
          }
        }
      };

      // @ts-ignore
      window.__mockSSEError = (urlFragment, message) => {
        for (const source of MockEventSource.instances) {
          if (!urlFragment || source.url.includes(urlFragment)) {
            source.onerror?.(new Error(message || "mock sse error"));
          }
        }
      };

      // @ts-ignore
      window.__mockSSEEmit = (urlFragment, eventEnvelope) => {
        for (const source of MockEventSource.instances) {
          if (!urlFragment || source.url.includes(urlFragment)) {
            source.onmessage?.({ data: JSON.stringify(eventEnvelope) });
          }
        }
      };

      window.fetch = async (input, init) => {
        const url = typeof input === "string" ? input : input.url;
        const method = (init?.method ?? "GET").toUpperCase();

        if (method === "POST" && url.includes("/api/runs")) {
          return new Response(
            JSON.stringify({
              run_id: "run-e2e-1",
              status: "running",
              root_node_id: "node-root",
            }),
            { status: 201, headers: { "content-type": "application/json" } },
          );
        }

        if (method === "GET" && url.includes("/api/runs/run-e2e-1")) {
          return new Response(
            JSON.stringify({
              run: {
                run_id: "run-e2e-1",
                objective: "Test streaming updates",
                status: "running",
                root_node_id: "node-root",
              },
              nodes: [
                {
                  node_id: "node-root",
                  run_id: "run-e2e-1",
                  objective: "Root objective",
                  persona_id: "orchestrator",
                  status: "running",
                  depth: 0,
                },
              ],
              edges: [],
            }),
            { status: 200, headers: { "content-type": "application/json" } },
          );
        }

        return originalFetch(input, init);
      };
    });
  });

  test("shows reconnecting/connected states and applies streaming node updates", async ({ page }) => {
    await page.goto("/");

    await page.getByLabel("Describe the root object and intended outcome").fill("Ship MVP checklist");
    await page.getByRole("button", { name: "Start Run" }).click();

    await expect.poll(async () =>
      page.evaluate(() => {
        // @ts-ignore
        return window.__mockSSECount();
      }),
    ).toBeGreaterThan(0);

    await expect(page.locator(".streamMetric .metricValue")).toHaveText("Reconnecting");

    await page.evaluate(() => {
      // @ts-ignore
      window.__mockSSEOpen();
    });

    await expect(page.locator(".streamMetric .metricValue")).toHaveText("Connected");

    await page.evaluate(() => {
      // @ts-ignore
      window.__mockSSEEmit(undefined, {
        event_id: "evt-1",
        run_id: "run-e2e-1",
        node_id: "node-root",
        seq: 1,
        type: "node.ttft_recorded",
        ts: "2026-04-06T00:00:00.000Z",
        payload: { ttft_ms: 410 },
      });
    });

    await expect(
      page
        .locator(".metricItem", { has: page.getByText("Avg TTFT", { exact: true }) })
        .locator(".metricValue"),
    ).toHaveText("410 ms");

    await page.evaluate(() => {
      // @ts-ignore
      window.__mockSSEEmit(undefined, {
        event_id: "evt-2",
        run_id: "run-e2e-1",
        node_id: "node-root",
        seq: 2,
        type: "node.blocked_human",
        ts: "2026-04-06T00:00:01.000Z",
        payload: { reason: "checker failed", retryCount: 3 },
      });
    });

    await expect(
      page
        .locator(".metricItem", { has: page.getByText("Blocked", { exact: true }) })
        .locator(".metricValue"),
    ).toHaveText("1");
    await expect(page.getByText("Node is eligible for intervention (blocked_human)")).toBeVisible();

    await page.evaluate(() => {
      // @ts-ignore
      window.__mockSSEError(undefined, "network drop");
    });

    await expect(page.locator(".streamMetric .metricValue")).toHaveText("Reconnecting");
    await expect(page.getByText("network drop")).toBeVisible();
  });
});
