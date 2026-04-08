// @ts-nocheck
import { expect, test } from "@playwright/test";

test.describe("Intervention flow", () => {
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
        const pathname = new URL(url, window.location.origin).pathname;

        if (method === "POST" && pathname.endsWith("/api/runs")) {
          return new Response(
            JSON.stringify({
              run_id: "run-int-1",
              status: "running",
              root_node_id: "node-root",
            }),
            { status: 201, headers: { "content-type": "application/json" } },
          );
        }

        if (method === "GET" && pathname.endsWith("/api/runs/run-int-1")) {
          return new Response(
            JSON.stringify({
              run: {
                run_id: "run-int-1",
                objective: "Exercise intervention paths",
                status: "running",
                root_node_id: "node-root",
              },
              nodes: [
                {
                  node_id: "node-root",
                  run_id: "run-int-1",
                  objective: "Root planner",
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

  test("supports retry, edit_and_retry, and skip_with_justification actions", async ({ page }) => {
    const interventions = [];

    await page.route("**/api/runs/run-int-1/nodes/node-root/interventions", async (route) => {
      const req = route.request();
      const body = req.postDataJSON();
      interventions.push(body);

      const nodeStatus = body.action === "skip_with_justification" ? "completed" : "blocked_human";

      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          accepted: true,
          node_status: nodeStatus,
          intervention_id: `int-${interventions.length}`,
        }),
      });
    });

    await page.goto("/");

    await page.getByLabel("Describe the root object and intended outcome").fill("Test intervention controls");
    await page.getByRole("button", { name: "Start Run" }).click();

    await expect.poll(async () =>
      page.evaluate(() => {
        // @ts-ignore
        return window.__mockSSECount();
      }),
    ).toBeGreaterThan(0);

    await page.evaluate(() => {
      // @ts-ignore
      window.__mockSSEOpen();
      // @ts-ignore
      window.__mockSSEEmit(undefined, {
        event_id: "evt-1",
        run_id: "run-int-1",
        node_id: "node-root",
        seq: 1,
        type: "node.blocked_human",
        ts: "2026-04-06T00:00:00.000Z",
        payload: { reason: "checker failed", retryCount: 3 },
      });
    });

    await expect(page.getByText("Node is eligible for intervention (blocked_human)")).toBeVisible();

    await page.getByLabel("Retry note (optional)").fill("Try once more with current context");
    await page.getByRole("button", { name: "Retry", exact: true }).click();

    await expect.poll(() => interventions.length).toBe(1);
    await expect.poll(() => interventions[0]).toEqual({
      action: "retry",
      note: "Try once more with current context",
    });

    await page.getByLabel("Edited objective (required)").fill("Use deterministic plan and explicit assumptions");
    await page.getByLabel("Edited context (optional)").fill("Prior failures were due to vague scope");
    await page.getByLabel("Edit note (optional)").fill("tightened objective wording");
    await page.getByRole("button", { name: "Edit and retry" }).click();

    await expect.poll(() => interventions.length).toBe(2);
    await expect.poll(() => interventions[1]).toEqual({
      action: "edit_and_retry",
      edited_objective: "Use deterministic plan and explicit assumptions",
      edited_context: "Prior failures were due to vague scope",
      note: "tightened objective wording",
    });

    await page.getByLabel("Skip justification (required)").fill("Accepted known limitation to unblock downstream merge");
    await page.getByRole("button", { name: "Skip with justification" }).click();

    await expect.poll(() => interventions.length).toBe(3);
    await expect.poll(() => interventions[2]).toEqual({
      action: "skip_with_justification",
      justification: "Accepted known limitation to unblock downstream merge",
    });

    await expect(page.locator("pre", { hasText: '"action": "retry"' }).first()).toBeVisible();
    await expect(page.locator("pre", { hasText: '"action": "edit_and_retry"' }).first()).toBeVisible();
    await expect(
      page.locator("pre", { hasText: '"action": "skip_with_justification"' }).first(),
    ).toBeVisible();
  });
});
