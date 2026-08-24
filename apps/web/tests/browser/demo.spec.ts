import { createHash } from "node:crypto";

import { expect, test } from "@playwright/test";
import type { Page, Route } from "@playwright/test";

import { PKCE_STORAGE_KEY } from "../../src/auth/pkce";
import { requestExample, responseExample } from "../helpers/contract-fixtures";
import { deferred } from "../helpers/async";

const PUBLISHED_ORIGIN = "https://senai.maib.com.br";
const API_ORIGIN = "https://abc123def4.execute-api.us-east-1.amazonaws.com";
const COGNITO_ORIGIN =
  "https://senai-pm-demo-a1b2c3d4.auth.us-east-1.amazoncognito.com";
const CLIENT_ID = "abc123client";
const PUBLISHED_CSP =
  "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; " +
  `connect-src 'self' ${API_ORIGIN} ${COGNITO_ORIGIN}; base-uri 'none'; ` +
  "form-action 'none'; frame-ancestors 'none'; object-src 'none'";

const OUTCOMES = [
  ["normal", "Condição normal"],
  ["documented_fault", "Falha documentada"],
  ["undocumented_fault", "Falha sem documentação"],
  ["out_of_distribution", "Fora da distribuição"],
  ["degraded", "Análise degradada"],
] as const;

async function mockEmptyDocuments(page: Page): Promise<void> {
  await page.route("**/api/documents", async (route) => {
    await route.fulfill({ status: 200, json: { items: [] } });
  });
}

function publishedRuntimeConfig() {
  return {
    schema_version: "runtime-config.v1",
    api_base_url: API_ORIGIN,
    cognito: {
      client_id: CLIENT_ID,
      hosted_ui_origin: COGNITO_ORIGIN,
      logout_uri: `${PUBLISHED_ORIGIN}/`,
      redirect_uri: `${PUBLISHED_ORIGIN}/`,
      scopes: ["openid"],
    },
  };
}

/**
 * Serve the production origin from the local static server while keeping the
 * production CSP and a controllable runtime-config response.
 */
async function routePublishedFrontend(
  page: Page,
  runtimeHandler: (route: Route) => Promise<void>,
): Promise<void> {
  await page.route(`${PUBLISHED_ORIGIN}/**`, async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === "/runtime-config.v1.json") {
      await runtimeHandler(route);
      return;
    }
    const local = await page.request.fetch(
      `http://127.0.0.1:3000${url.pathname}${url.search}`,
    );
    const headers: Record<string, string> = {
      ...local.headers(),
      "content-security-policy": PUBLISHED_CSP,
    };
    delete headers["content-length"];
    await route.fulfill({
      status: local.status(),
      headers,
      body: await local.body(),
    });
  });
}

function accessToken(): string {
  const encode = (value: unknown) =>
    Buffer.from(JSON.stringify(value)).toString("base64url");
  return `${encode({ alg: "none" })}.${encode({
    client_id: CLIENT_ID,
    exp: Math.floor(Date.now() / 1000) + 3600,
    token_use: "access",
  })}.signature`;
}

/**
 * Type a value into a React-controlled field from page context. Assigning
 * `.value` alone bypasses React's value tracker, so the native setter is used
 * and an input event is dispatched, exactly like a real keystroke.
 */
const FILL_CONTROLLED_FIELDS = (request: {
  features: Record<string, number>;
  top_k: number;
}) => {
  const write = (element: Element | null, value: string) => {
    if (element === null) {
      throw new Error("O console não declara o controle esperado.");
    }
    const prototype =
      element instanceof HTMLTextAreaElement
        ? HTMLTextAreaElement.prototype
        : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(prototype, "value")?.set;
    setter?.call(element, value);
    element.dispatchEvent(new Event("input", { bubbles: true }));
  };
  for (const [name, value] of Object.entries(request.features)) {
    write(document.querySelector(`[data-feature="${name}"]`), String(value));
  }
  write(document.querySelector("#top-k"), String(request.top_k));
};

test("config publicada pendente mantem toda superficie protegida bloqueada", async ({
  page,
}) => {
  const runtimeRelease = deferred<void>();
  const runtimeRequested = deferred<void>();
  await routePublishedFrontend(page, async (route) => {
    runtimeRequested.resolve();
    await runtimeRelease.promise;
    await route.fulfill({ status: 503, body: "" });
  });
  const apiRequests: string[] = [];
  page.on("request", (request) => {
    const endpoint = new URL(request.url());
    if (
      endpoint.origin === API_ORIGIN &&
      (endpoint.pathname === "/analysis" || endpoint.pathname === "/documents")
    ) {
      apiRequests.push(request.url());
    }
  });

  const state = "s".repeat(43);
  await page.goto(`${PUBLISHED_ORIGIN}/?code=temporary-code&state=${state}`);
  await runtimeRequested.promise;

  await expect(page).toHaveURL(`${PUBLISHED_ORIGIN}/`);
  await expect(page.locator("#analysis-form")).toHaveAttribute("inert", "");
  await expect(page.locator("#analysis-form")).toHaveAttribute("aria-busy", "true");
  await expect(page.locator("#documents-panel")).toHaveAttribute("inert", "");
  await expect(page.locator("#documents-panel")).toHaveAttribute("aria-busy", "true");
  expect(apiRequests).toEqual([]);

  runtimeRelease.resolve();
  await expect(page.locator("#auth-status")).toHaveText(
    "Configuração de publicação indisponível",
  );
  await expect(page.locator("#analysis-form")).toHaveAttribute("inert", "");
  await expect(page.locator("#analysis-form")).toHaveAttribute("aria-busy", "false");
  await expect(page.locator("#documents-panel")).toHaveAttribute("inert", "");
  expect(apiRequests).toEqual([]);
});

test("callback publicado libera uma analise exata antes dos documentos", async ({
  page,
}) => {
  await routePublishedFrontend(page, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      json: publishedRuntimeConfig(),
    });
  });
  let authorizeCalls = 0;
  let authorizeUrl: URL | null = null;
  let callbackUrl: URL | null = null;
  const authorizeRequested = deferred<void>();
  await page.route(`${COGNITO_ORIGIN}/oauth2/authorize?*`, async (route) => {
    authorizeCalls += 1;
    const request = route.request();
    authorizeUrl = new URL(request.url());
    expect(request.method()).toBe("GET");
    expect(request.headers().authorization).toBeUndefined();
    expect(authorizeUrl.origin).toBe(COGNITO_ORIGIN);
    expect(authorizeUrl.pathname).toBe("/oauth2/authorize");
    const state = authorizeUrl.searchParams.get("state");
    const challenge = authorizeUrl.searchParams.get("code_challenge");
    if (state === null || challenge === null) {
      throw new Error("The real authorize request omitted PKCE parameters.");
    }
    expect(state).toMatch(/^[A-Za-z0-9_-]{43}$/);
    expect(challenge).toMatch(/^[A-Za-z0-9_-]{43}$/);
    expect(Object.fromEntries(authorizeUrl.searchParams)).toEqual({
      client_id: CLIENT_ID,
      code_challenge: challenge,
      code_challenge_method: "S256",
      redirect_uri: `${PUBLISHED_ORIGIN}/`,
      response_type: "code",
      scope: "openid",
      state,
    });
    callbackUrl = new URL("/", PUBLISHED_ORIGIN);
    callbackUrl.searchParams.set("code", "temporary-code");
    callbackUrl.searchParams.set("state", state);
    await route.fulfill({
      status: 200,
      contentType: "text/html",
      body: "<!doctype html><title>Synthetic Cognito Hosted UI</title>",
    });
    authorizeRequested.resolve();
  });

  const expectedAccessToken = accessToken();
  const tokenRelease = deferred<void>();
  const tokenRequested = deferred<void>();
  let tokenCalls = 0;
  await page.route(`${COGNITO_ORIGIN}/oauth2/token`, async (route) => {
    tokenCalls += 1;
    const request = route.request();
    expect(request.method()).toBe("POST");
    expect(request.headers().authorization).toBeUndefined();
    const parameters = Object.fromEntries(
      new URLSearchParams(request.postData() ?? ""),
    );
    const verifier = parameters.code_verifier;
    if (typeof verifier !== "string" || authorizeUrl === null) {
      throw new Error("The real PKCE exchange did not provide verifier material.");
    }
    expect(createHash("sha256").update(verifier).digest("base64url")).toBe(
      (authorizeUrl as URL).searchParams.get("code_challenge"),
    );
    expect(parameters).toEqual({
      client_id: CLIENT_ID,
      code: "temporary-code",
      code_verifier: expect.stringMatching(/^[A-Za-z0-9._~-]{43,128}$/),
      grant_type: "authorization_code",
      redirect_uri: `${PUBLISHED_ORIGIN}/`,
    });
    tokenRequested.resolve();
    await tokenRelease.promise;
    await route.fulfill({
      status: 200,
      headers: {
        "access-control-allow-origin": PUBLISHED_ORIGIN,
        "content-type": "application/json",
      },
      json: {
        access_token: expectedAccessToken,
        refresh_token: "temporary-refresh",
        token_type: "Bearer",
      },
    });
  });

  const apiRequests: string[] = [];
  const documentsRelease = deferred<void>();
  const documentsRequested = deferred<void>();
  let analysisCalls = 0;
  await page.route(`${API_ORIGIN}/**`, async (route) => {
    const request = route.request();
    const pathname = new URL(request.url()).pathname;
    apiRequests.push(`${request.method()} ${pathname}`);
    if (request.method() === "OPTIONS") {
      expect(request.headers().authorization).toBeUndefined();
      await route.fulfill({
        status: 204,
        headers: {
          "access-control-allow-headers": "authorization,content-type",
          "access-control-allow-methods": "GET,POST,OPTIONS",
          "access-control-allow-origin": PUBLISHED_ORIGIN,
        },
      });
      return;
    }
    expect(request.headers().authorization).toBe(`Bearer ${expectedAccessToken}`);
    if (request.method() === "GET" && pathname === "/documents") {
      documentsRequested.resolve();
      await documentsRelease.promise;
      await route.fulfill({
        status: 200,
        headers: {
          "access-control-allow-origin": PUBLISHED_ORIGIN,
          "content-type": "application/json",
        },
        json: { items: [] },
      });
      return;
    }
    expect(request.method()).toBe("POST");
    expect(pathname).toBe("/analysis");
    expect(JSON.parse(request.postData() ?? "null")).toEqual(
      requestExample("normal"),
    );
    analysisCalls += 1;
    expect(analysisCalls).toBe(1);
    await route.fulfill({
      status: 200,
      headers: {
        "access-control-allow-origin": PUBLISHED_ORIGIN,
        "content-type": "application/json",
      },
      json: responseExample("normal"),
    });
  });

  await page.goto(`${PUBLISHED_ORIGIN}/`);
  await expect(page.locator("#auth-status")).toHaveText("Login necessário");
  await page.getByRole("button", { name: "Entrar com Cognito" }).click();
  await authorizeRequested.promise;
  if (callbackUrl === null) {
    throw new Error("The real authorize request did not produce a callback URL.");
  }
  await page.goto((callbackUrl as URL).href, { waitUntil: "commit" });
  await tokenRequested.promise;

  await expect(page).toHaveURL(`${PUBLISHED_ORIGIN}/`);
  await expect(page.locator("#analysis-form")).toHaveAttribute("inert", "");
  await expect(page.locator("#analysis-form")).toHaveAttribute("aria-busy", "true");
  await expect(page.locator("#documents-panel")).toHaveAttribute("inert", "");
  expect(apiRequests).toEqual([]);
  expect(authorizeCalls).toBe(1);
  expect(tokenCalls).toBe(1);
  expect(
    await page.evaluate((key) => sessionStorage.getItem(key), PKCE_STORAGE_KEY),
  ).toBeNull();

  tokenRelease.resolve();
  await page.waitForLoadState("load");
  await documentsRequested.promise;
  await expect(page.locator("#auth-status")).toHaveText("Sessão autenticada");
  await expect(page.locator("#analysis-form")).not.toHaveAttribute("inert", "");
  await expect(page.locator("#analysis-form")).toHaveAttribute("aria-busy", "false");
  await expect(page.locator("#documents-panel")).toHaveAttribute("inert", "");
  await expect(page.locator("#documents-panel")).toHaveAttribute(
    "aria-busy",
    "true",
  );
  await page.locator("#example-select").selectOption("normal");
  await page.getByRole("button", { name: "Executar análise" }).click();
  await expect(page.locator("#report-heading")).toHaveText("Condição normal");
  expect(analysisCalls).toBe(1);

  documentsRelease.resolve();
  await expect(page.locator("#documents-panel")).not.toHaveAttribute("inert", "");
  await expect(page.locator("#documents-panel")).toHaveAttribute(
    "aria-busy",
    "false",
  );
  await page.waitForTimeout(150);
  expect(analysisCalls).toBe(1);
  expect(tokenCalls).toBe(1);
  await page.goto((callbackUrl as URL).href);
  await expect(page.locator("#auth-status")).toHaveText(
    "Callback de login recusado",
  );
  expect(tokenCalls).toBe(1);
  expect(
    await page.evaluate(
      (key) => ({
        local: localStorage.getItem(key),
        session: sessionStorage.getItem(key),
      }),
      PKCE_STORAGE_KEY,
    ),
  ).toEqual({ local: null, session: null });
});

test("login publicado permanece ocupado e produz um unico redirect PKCE", async ({
  page,
}) => {
  await page.addInitScript(() => {
    const originalDigest = crypto.subtle.digest.bind(crypto.subtle);
    let releaseDigest: () => void = () => {};
    const digestRelease = new Promise<void>((resolve) => {
      releaseDigest = resolve;
    });
    const delayedDigest = async (
      algorithm: AlgorithmIdentifier,
      data: BufferSource,
    ) => {
      await digestRelease;
      return originalDigest(algorithm, data);
    };
    Reflect.set(globalThis, "releasePkceDigest", releaseDigest);
    Object.defineProperty(crypto.subtle, "digest", { value: delayedDigest });
  });
  await routePublishedFrontend(page, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      json: publishedRuntimeConfig(),
    });
  });
  let authorizeCalls = 0;
  let authorizeUrl: URL | null = null;
  const authorizeRequested = deferred<void>();
  await page.route(`${COGNITO_ORIGIN}/oauth2/authorize?*`, async (route) => {
    authorizeCalls += 1;
    authorizeUrl = new URL(route.request().url());
    authorizeRequested.resolve();
    await route.abort();
  });

  await page.goto(`${PUBLISHED_ORIGIN}/`);
  await expect(page.locator("#auth-status")).toHaveText("Login necessário");
  const busy = await page.locator("#auth-login").evaluate((button) => {
    (button as HTMLButtonElement).click();
    (button as HTMLButtonElement).click();
    return {
      buttonBusy: button.getAttribute("aria-busy"),
      disabled: (button as HTMLButtonElement).disabled,
      panelBusy: document.querySelector("#auth-panel")?.getAttribute("aria-busy"),
    };
  });
  expect(busy).toEqual({
    buttonBusy: "true",
    disabled: true,
    panelBusy: "true",
  });
  expect(authorizeCalls).toBe(0);
  await page.evaluate(() => {
    const release = Reflect.get(globalThis, "releasePkceDigest");
    if (typeof release !== "function") {
      throw new Error("PKCE digest gate is unavailable.");
    }
    release();
  });
  await authorizeRequested.promise;

  expect(authorizeCalls).toBe(1);
  expect(Object.fromEntries((authorizeUrl as unknown as URL).searchParams)).toMatchObject({
    client_id: CLIENT_ID,
    code_challenge_method: "S256",
    redirect_uri: `${PUBLISHED_ORIGIN}/`,
    response_type: "code",
    scope: "openid",
  });
  expect(
    (authorizeUrl as unknown as URL).searchParams.get("state"),
  ).toMatch(/^[A-Za-z0-9_-]{43}$/);
  expect(
    (authorizeUrl as unknown as URL).searchParams.get("code_challenge"),
  ).toMatch(/^[A-Za-z0-9_-]{43}$/);
});

test("offline demonstra cinco outcomes e toda a navegação sem chamar a API", async ({
  page,
}) => {
  const apiRequests: string[] = [];
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

test("teclado, foco e erros associados cobrem análise e troca de área", async ({
  page,
}) => {
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
  const delayedFailure = deferred<void>();
  await page.route("**/api/analysis", async (route) => {
    calls += 1;
    if (calls === 1) {
      await route.fulfill({ status: 200, json: responseExample("normal") });
      return;
    }
    if (calls === 2) {
      await delayedFailure.promise;
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

  delayedFailure.resolve();
  await expect(page.locator(".report-state-title")).toHaveText("A API não respondeu");
  await expect(page.locator("#report-heading")).toHaveText("Condição normal");
  await page.getByRole("button", { name: "Tentar novamente" }).click();
  await expect(page.locator("#report-heading")).toHaveText("Falha documentada");
  await expect(page.locator("#report")).not.toContainText(
    "Resultado anterior preservado",
  );
});

test("uma resposta fora de ordem não substitui a análise mais recente", async ({
  page,
}) => {
  await mockEmptyDocuments(page);
  let calls = 0;
  let completedCalls = 0;
  const oldResponse = deferred<void>();
  const bothResponsesCompleted = deferred<void>();
  const markResponseCompleted = () => {
    completedCalls += 1;
    if (completedCalls === 2) {
      bothResponsesCompleted.resolve();
    }
  };
  await page.route("**/api/analysis", async (route) => {
    calls += 1;
    if (calls === 1) {
      await oldResponse.promise;
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

  await page.evaluate(FILL_CONTROLLED_FIELDS, requestExample("documented_fault"));
  await page.evaluate(() => {
    const form = document.querySelector("#analysis-form");
    if (!(form instanceof HTMLFormElement)) {
      throw new Error("O painel não declara o formulário de análise.");
    }
    form.requestSubmit();
  });

  await expect(page.locator("#report-heading")).toHaveText("Falha documentada");
  oldResponse.resolve();
  await bothResponsesCompleted.promise;
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
      "#assistant-navigation",
      "#documents-navigation",
      "#example-select",
      '[data-feature="z_rms_velocity_mm_s"]',
      '#analysis-form button[type="submit"]',
    ]) {
      const box = await page.locator(selector).boundingBox();
      expect(box, selector).not.toBeNull();
      expect(box?.height, selector).toBeGreaterThanOrEqual(44);
      expect(box?.width, selector).toBeGreaterThanOrEqual(44);
    }
  });
}

test("reduced motion remove animações funcionais longas", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/?mode=offline#analysis");
  await expect
    .poll(() =>
      page.evaluate(() => matchMedia("(prefers-reduced-motion: reduce)").matches),
    )
    .toBe(true);
  // The report's entrance animation lives on the inner container that React
  // remounts per state, which is what the motion clamp has to neutralise.
  const duration = await page
    .locator("#report .report-enter")
    .evaluate((element) => getComputedStyle(element).animationDuration);
  const durationSeconds = duration.endsWith("ms")
    ? Number.parseFloat(duration) / 1000
    : Number.parseFloat(duration);
  expect(durationSeconds).toBeLessThanOrEqual(0.00001);
});
