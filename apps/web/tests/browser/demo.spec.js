import { expect, test } from "@playwright/test";

import { PKCE_STORAGE_KEY } from "../../src/auth/pkce.js";
import { requestExample, responseExample } from "../helpers/contract-fixtures.js";

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
];

async function mockEmptyDocuments(page) {
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
 *
 * @param {import("@playwright/test").Page} page
 * @param {(route: import("@playwright/test").Route) => Promise<void>} runtimeHandler
 */
async function routePublishedFrontend(page, runtimeHandler) {
  await page.route(`${PUBLISHED_ORIGIN}/**`, async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === "/runtime-config.v1.json") {
      await runtimeHandler(route);
      return;
    }
    const local = await page.request.fetch(
      `http://127.0.0.1:3000${url.pathname}${url.search}`,
    );
    const headers = {
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

function accessToken() {
  const encode = (value) => Buffer.from(JSON.stringify(value)).toString("base64url");
  return `${encode({ alg: "none" })}.${encode({
    client_id: CLIENT_ID,
    exp: Math.floor(Date.now() / 1000) + 3600,
    token_use: "access",
  })}.signature`;
}

test("config publicada pendente mantem toda superficie protegida bloqueada", async ({
  page,
}) => {
  let releaseRuntime;
  let markRuntimeRequested;
  const runtimeRelease = new Promise((resolve) => {
    releaseRuntime = resolve;
  });
  const runtimeRequested = new Promise((resolve) => {
    markRuntimeRequested = resolve;
  });
  await routePublishedFrontend(page, async (route) => {
    markRuntimeRequested();
    await runtimeRelease;
    await route.fulfill({ status: 503, body: "" });
  });
  const apiRequests = [];
  page.on("request", (request) => {
    if (request.url().startsWith(API_ORIGIN)) {
      apiRequests.push(request.url());
    }
  });

  const state = "s".repeat(43);
  await page.goto(`${PUBLISHED_ORIGIN}/?code=temporary-code&state=${state}`);
  await runtimeRequested;

  await expect(page).toHaveURL(`${PUBLISHED_ORIGIN}/`);
  await expect(page.locator("#analysis-form")).toHaveAttribute("inert", "");
  await expect(page.locator("#analysis-form")).toHaveAttribute("aria-busy", "true");
  await expect(page.locator("#documents-panel")).toHaveAttribute("inert", "");
  await expect(page.locator("#documents-panel")).toHaveAttribute("aria-busy", "true");
  expect(apiRequests).toEqual([]);

  releaseRuntime();
  await expect(page.locator("#auth-status")).toHaveText(
    "Configura\u00e7\u00e3o de publica\u00e7\u00e3o indispon\u00edvel",
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
  const state = "s".repeat(43);
  const verifier = "v".repeat(43);
  await page.addInitScript(
    ({ key, oauthState, pkceVerifier, timestamp }) => {
      sessionStorage.setItem(
        key,
        JSON.stringify({
          state: oauthState,
          timestamp,
          verifier: pkceVerifier,
        }),
      );
    },
    {
      key: PKCE_STORAGE_KEY,
      oauthState: state,
      pkceVerifier: verifier,
      timestamp: Date.now(),
    },
  );

  const expectedAccessToken = accessToken();
  let releaseToken;
  let markTokenRequested;
  const tokenRelease = new Promise((resolve) => {
    releaseToken = resolve;
  });
  const tokenRequested = new Promise((resolve) => {
    markTokenRequested = resolve;
  });
  let tokenCalls = 0;
  await page.route(`${COGNITO_ORIGIN}/oauth2/token`, async (route) => {
    tokenCalls += 1;
    const request = route.request();
    expect(request.method()).toBe("POST");
    expect(request.headers().authorization).toBeUndefined();
    expect(Object.fromEntries(new URLSearchParams(request.postData() ?? ""))).toEqual({
      client_id: CLIENT_ID,
      code: "temporary-code",
      code_verifier: verifier,
      grant_type: "authorization_code",
      redirect_uri: `${PUBLISHED_ORIGIN}/`,
    });
    markTokenRequested();
    await tokenRelease;
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

  const apiRequests = [];
  let releaseDocuments;
  let markDocumentsRequested;
  const documentsRelease = new Promise((resolve) => {
    releaseDocuments = resolve;
  });
  const documentsRequested = new Promise((resolve) => {
    markDocumentsRequested = resolve;
  });
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
      markDocumentsRequested();
      await documentsRelease;
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

  await page.goto(`${PUBLISHED_ORIGIN}/?code=temporary-code&state=${state}`);
  await tokenRequested;

  await expect(page).toHaveURL(`${PUBLISHED_ORIGIN}/`);
  await expect(page.locator("#analysis-form")).toHaveAttribute("inert", "");
  await expect(page.locator("#analysis-form")).toHaveAttribute("aria-busy", "true");
  await expect(page.locator("#documents-panel")).toHaveAttribute("inert", "");
  expect(apiRequests).toEqual([]);
  expect(tokenCalls).toBe(1);

  releaseToken();
  await documentsRequested;
  await expect(page.locator("#auth-status")).toHaveText("Sess\u00e3o autenticada");
  await expect(page.locator("#analysis-form")).not.toHaveAttribute("inert", "");
  await expect(page.locator("#analysis-form")).toHaveAttribute("aria-busy", "false");
  await expect(page.locator("#documents-panel")).toHaveAttribute("inert", "");
  await expect(page.locator("#documents-panel")).toHaveAttribute(
    "aria-busy",
    "true",
  );
  await page.locator("#example-select").selectOption("normal");
  await page.getByRole("button", { name: "Executar an\u00e1lise" }).click();
  await expect(page.locator("#report-heading")).toHaveText("Condi\u00e7\u00e3o normal");
  expect(analysisCalls).toBe(1);

  releaseDocuments();
  await expect(page.locator("#documents-panel")).not.toHaveAttribute("inert", "");
  await expect(page.locator("#documents-panel")).toHaveAttribute(
    "aria-busy",
    "false",
  );
  await page.waitForTimeout(150);
  expect(analysisCalls).toBe(1);
  expect(tokenCalls).toBe(1);
  expect(
    await page.evaluate((key) => ({
      local: localStorage.getItem(key),
      session: sessionStorage.getItem(key),
    }), PKCE_STORAGE_KEY),
  ).toEqual({ local: null, session: null });
});

test("login publicado permanece ocupado e produz um unico redirect PKCE", async ({
  page,
}) => {
  await page.addInitScript(() => {
    const originalDigest = crypto.subtle.digest.bind(crypto.subtle);
    /** @type {() => void} */
    let releaseDigest = () => {};
    /** @type {Promise<void>} */
    const digestRelease = new Promise((resolve) => {
      releaseDigest = resolve;
    });
    /** @type {SubtleCrypto["digest"]} */
    const delayedDigest = async (algorithm, data) => {
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
  let authorizeUrl = null;
  let markAuthorizeRequested;
  const authorizeRequested = new Promise((resolve) => {
    markAuthorizeRequested = resolve;
  });
  await page.route(`${COGNITO_ORIGIN}/oauth2/authorize?*`, async (route) => {
    authorizeCalls += 1;
    authorizeUrl = new URL(route.request().url());
    markAuthorizeRequested();
    await route.abort();
  });

  await page.goto(`${PUBLISHED_ORIGIN}/`);
  await expect(page.locator("#auth-status")).toHaveText("Login necess\u00e1rio");
  const busy = await page.locator("#auth-login").evaluate((button) => {
    button.click();
    button.click();
    return {
      buttonBusy: button.getAttribute("aria-busy"),
      disabled: button.disabled,
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
  await authorizeRequested;

  expect(authorizeCalls).toBe(1);
  expect(Object.fromEntries(authorizeUrl.searchParams)).toMatchObject({
    client_id: CLIENT_ID,
    code_challenge_method: "S256",
    redirect_uri: `${PUBLISHED_ORIGIN}/`,
    response_type: "code",
    scope: "openid",
  });
  expect(authorizeUrl.searchParams.get("state")).toMatch(/^[A-Za-z0-9_-]{43}$/);
  expect(authorizeUrl.searchParams.get("code_challenge")).toMatch(
    /^[A-Za-z0-9_-]{43}$/,
  );
});

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
