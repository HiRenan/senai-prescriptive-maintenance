import { expect, test } from "@playwright/test";

import { requestExample, responseExample } from "../helpers/contract-fixtures.js";

const OUTCOMES = [
  ["normal", "Condição normal"],
  ["documented_fault", "Falha documentada"],
  ["undocumented_fault", "Falha sem documentação"],
  ["out_of_distribution", "Fora da distribuição"],
  ["degraded", "Análise degradada"],
];

async function mockEmptyDocuments(page) {
  await page.route("**/api/documents", async (route) => {
    await route.fulfill({ status: 200, json: { items: [] } });
  });
}

test("offline demonstra cinco outcomes e toda a navegação sem chamar a API", async ({
  page,
}) => {
  const apiRequests = [];
  page.on("request", (request) => {
    const path = new URL(request.url()).pathname;
    if (
      path === "/api/analysis" ||
      path === "/api/documents" ||
      path.startsWith("/api/documents/")
    ) {
      apiRequests.push(request.url());
    }
  });

  await page.goto("/?mode=offline#analysis");
  await expect(page.locator("#offline-mode")).toHaveAttribute("aria-current", "page");
  await expect(page.locator("#mode-description")).toContainText("sem chamadas à API");

  for (const [outcome, title] of OUTCOMES) {
    await page.locator("#example-select").selectOption(outcome);
    await page.getByRole("button", { name: "Executar análise" }).click();
    await expect(page.locator("#report-heading")).toHaveText(title);
    await expect(page.locator("#report")).toContainText("Fixture sintética offline");
  }

  await page.locator("#documents-navigation").focus();
  await page.keyboard.press("Enter");
  await expect(page.locator("#documents")).toBeFocused();
  await expect(page.locator("#documents-panel")).toContainText(
    "Gestão documental indisponível offline",
  );
  expect(apiRequests).toEqual([]);
});

test("teclado, foco e erros associados cobrem análise e troca de área", async ({ page }) => {
  await page.goto("/?mode=offline");

  await page.keyboard.press("Tab");
  await expect(page.locator(".skip-link")).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.locator("#workspace-content")).toBeFocused();

  await page.getByRole("button", { name: "Executar análise" }).focus();
  await page.keyboard.press("Enter");
  const firstFeature = page.locator('[data-feature="z_rms_velocity_mm_s"]');
  await expect(firstFeature).toBeFocused();
  await expect(firstFeature).toHaveAttribute("aria-invalid", "true");
  const errorId = await firstFeature.getAttribute("aria-describedby");
  await expect(page.locator(`#${errorId}`)).not.toBeEmpty();

  await page.locator("#example-select").focus();
  await page.keyboard.press("ArrowDown");
  await page.keyboard.press("Enter");
  await page.getByRole("button", { name: "Executar análise" }).focus();
  await page.keyboard.press("Enter");
  await expect(page.locator("#report-heading")).toBeFocused();

  await page.locator("#documents-navigation").focus();
  await page.keyboard.press("Enter");
  await expect(page.locator("#documents")).toBeFocused();
  await page.locator("#analysis-navigation").focus();
  await page.keyboard.press("Enter");
  await expect(page.locator("#analysis")).toBeFocused();
});

test("loading, erro e retry preservam o último resultado válido", async ({ page }) => {
  await mockEmptyDocuments(page);
  let calls = 0;
  let releaseFailure;
  const delayedFailure = new Promise((resolve) => {
    releaseFailure = resolve;
  });
  await page.route("**/api/analysis", async (route) => {
    calls += 1;
    if (calls === 1) {
      await route.fulfill({ status: 200, json: responseExample("normal") });
      return;
    }
    if (calls === 2) {
      await delayedFailure;
      await route.fulfill({ status: 502, json: {} });
      return;
    }
    await route.fulfill({ status: 200, json: responseExample("documented_fault") });
  });
  await page.goto("/#analysis");
  await page.locator("#example-select").selectOption("normal");
  await page.getByRole("button", { name: "Executar análise" }).click();
  await expect(page.locator("#report-heading")).toHaveText("Condição normal");

  await page.locator("#example-select").selectOption("documented_fault");
  await page.getByRole("button", { name: "Executar análise" }).click();
  await expect(page.locator(".report-state-title")).toHaveText(
    "Nova análise em andamento",
  );
  await expect(page.locator("#report")).toContainText("Resultado anterior preservado");
  await expect(page.locator("#report-heading")).toHaveText("Condição normal");

  releaseFailure();
  await expect(page.locator(".report-state-title")).toHaveText("A API não respondeu");
  await expect(page.locator("#report-heading")).toHaveText("Condição normal");
  await page.getByRole("button", { name: "Tentar novamente" }).click();
  await expect(page.locator("#report-heading")).toHaveText("Falha documentada");
  await expect(page.locator("#report")).not.toContainText("Resultado anterior preservado");
});

test("uma resposta fora de ordem não substitui a análise mais recente", async ({ page }) => {
  await mockEmptyDocuments(page);
  let calls = 0;
  let completedCalls = 0;
  let releaseOld;
  let resolveBothResponses;
  const oldResponse = new Promise((resolve) => {
    releaseOld = resolve;
  });
  const bothResponsesCompleted = new Promise((resolve) => {
    resolveBothResponses = resolve;
  });
  const markResponseCompleted = () => {
    completedCalls += 1;
    if (completedCalls === 2) {
      resolveBothResponses();
    }
  };
  await page.route("**/api/analysis", async (route) => {
    calls += 1;
    if (calls === 1) {
      await oldResponse;
      await route.fulfill({ status: 200, json: responseExample("normal") });
      markResponseCompleted();
      return;
    }
    await route.fulfill({ status: 200, json: responseExample("documented_fault") });
    markResponseCompleted();
  });
  await page.goto("/#analysis");
  await page.locator("#example-select").selectOption("normal");
  await page.getByRole("button", { name: "Executar análise" }).click();

  await page.evaluate((request) => {
    for (const [name, value] of Object.entries(request.features)) {
      document.querySelector(`[data-feature="${name}"]`).value = String(value);
    }
    document.querySelector("#top-k").value = String(request.top_k);
    document.querySelector("#analysis-form").requestSubmit();
  }, requestExample("documented_fault"));

  await expect(page.locator("#report-heading")).toHaveText("Falha documentada");
  releaseOld();
  await bothResponsesCompleted;
  await page.evaluate(
    () =>
      new Promise((resolve) => {
        requestAnimationFrame(() => requestAnimationFrame(resolve));
      }),
  );
  await expect(page.locator("#report-heading")).toHaveText("Falha documentada");
  await expect(page.locator("#report")).toContainText(
    responseExample("documented_fault").analysis_id,
  );
  await expect(page.locator("#report")).not.toContainText(
    responseExample("normal").analysis_id,
  );
});

for (const viewport of [
  { name: "desktop 1366x768", width: 1366, height: 768 },
  { name: "mobile 390", width: 390, height: 844 },
  { name: "mobile 375", width: 375, height: 812 },
  { name: "mobile 320", width: 320, height: 568 },
  { name: "equivalente a zoom 200%", width: 683, height: 768 },
  { name: "equivalente a zoom 400%", width: 342, height: 768 },
]) {
  test(`reflow sem overflow global e alvos de 44px em ${viewport.name}`, async ({
    page,
  }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await page.goto("/?mode=offline#analysis");
    await page.locator("#example-select").selectOption("documented_fault");
    await page.getByRole("button", { name: "Executar análise" }).click();
    await expect(page.locator("#report-heading")).toHaveText("Falha documentada");

    const dimensions = await page.evaluate(() => ({
      documentClient: document.documentElement.clientWidth,
      documentScroll: document.documentElement.scrollWidth,
      bodyClient: document.body.clientWidth,
      bodyScroll: document.body.scrollWidth,
    }));
    expect(dimensions.documentScroll).toBeLessThanOrEqual(dimensions.documentClient);
    expect(dimensions.bodyScroll).toBeLessThanOrEqual(dimensions.bodyClient);

    for (const selector of [
      ".skip-link",
      "#online-mode",
      "#offline-mode",
      "#analysis-navigation",
      "#documents-navigation",
      "#example-select",
      '[data-feature="z_rms_velocity_mm_s"]',
      '#analysis-form button[type="submit"]',
    ]) {
      const box = await page.locator(selector).boundingBox();
      expect(box, selector).not.toBeNull();
      expect(box.height, selector).toBeGreaterThanOrEqual(44);
      expect(box.width, selector).toBeGreaterThanOrEqual(44);
    }
  });
}

test("reduced motion remove animações funcionais longas", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/?mode=offline#analysis");
  await expect.poll(() => page.evaluate(() => matchMedia("(prefers-reduced-motion: reduce)").matches)).toBe(true);
  const duration = await page.locator("#report").evaluate(
    (element) => getComputedStyle(element).animationDuration,
  );
  const durationSeconds = duration.endsWith("ms")
    ? Number.parseFloat(duration) / 1000
    : Number.parseFloat(duration);
  expect(durationSeconds).toBeLessThanOrEqual(0.00001);
});
