import assert from "node:assert/strict";
import { test } from "vitest";

import {
  createAuthenticatedFetch,
  isAllowedApiRequest,
} from "../src/api/authenticated-fetch";

const API_ORIGIN = "https://abc123def4.execute-api.us-east-1.amazonaws.com";

test("a allowlist contém análise, assistente e operações documentais publicadas", () => {
  assert.equal(isAllowedApiRequest("POST", "/analysis"), true);
  assert.equal(isAllowedApiRequest("POST", "/assistant/query"), true);
  assert.equal(isAllowedApiRequest("GET", "/documents"), true);
  assert.equal(isAllowedApiRequest("POST", "/documents"), true);
  assert.equal(isAllowedApiRequest("GET", "/documents/doc_example"), true);
  assert.equal(isAllowedApiRequest("POST", "/documents/doc_example/approve"), true);
  assert.equal(isAllowedApiRequest("POST", "/documents/doc_example/reject"), true);
  assert.equal(isAllowedApiRequest("POST", "/documents/doc_example/reprocess"), true);
  assert.equal(isAllowedApiRequest("DELETE", "/documents"), false);
  assert.equal(isAllowedApiRequest("POST", "/admin"), false);
  assert.equal(isAllowedApiRequest("GET", "/assistant/query"), false);
  assert.equal(isAllowedApiRequest("POST", "/assistant/query/extra"), false);
  assert.equal(isAllowedApiRequest("GET", "/documents/%2e%2e/admin"), false);
});

test("bearer sai apenas para a origem/path/método exatos e sem credenciais", async () => {
  const calls: any[] = [];
  const transport = createAuthenticatedFetch({
    apiBaseUrl: API_ORIGIN,
    session: { getAccessToken: () => "access-value", clear() {} },
    async fetchImpl(input: any, init: any) {
      calls.push({ input, init });
      return Response.json({}, { status: 422 });
    },
  });
  await transport(`${API_ORIGIN}/analysis`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: "{}",
    cache: "force-cache",
  });
  assert.equal(calls.length, 1);
  assert.equal(calls[0].init.headers.get("authorization"), "Bearer access-value");
  assert.equal(calls[0].init.credentials, "omit");
  assert.equal(calls[0].init.redirect, "manual");
  assert.equal(calls[0].init.cache, "no-store");

  for (const [url, method] of [
    ["https://evil.example/analysis", "POST"],
    [`${API_ORIGIN}/analysis?copy=1`, "POST"],
    [`${API_ORIGIN}/analysis`, "GET"],
    [`${API_ORIGIN}/unknown`, "POST"],
  ]) {
    await assert.rejects(() => transport(url, { method }), /allowlist/);
  }
  assert.equal(calls.length, 1);
});

test("401/403 limpa a sessão, pede login e jamais repete a requisição", async () => {
  let calls = 0;
  let clears = 0;
  let requestsLogin = 0;
  const transport = createAuthenticatedFetch({
    apiBaseUrl: API_ORIGIN,
    session: {
      getAccessToken: () => "access-value",
      clear() {
        clears += 1;
      },
    },
    onAuthenticationRequired() {
      requestsLogin += 1;
    },
    async fetchImpl() {
      calls += 1;
      return Response.json({}, { status: 403 });
    },
  });
  const response = await transport(`${API_ORIGIN}/analysis`, { method: "POST" });
  assert.equal(response.status, 403);
  assert.deepEqual({ calls, clears, requestsLogin }, { calls: 1, clears: 1, requestsLogin: 1 });
});

test("sessão ausente e redirect são recusados sem vazamento ou replay", async () => {
  let calls = 0;
  const anonymous = createAuthenticatedFetch({
    apiBaseUrl: API_ORIGIN,
    session: { getAccessToken: () => null, clear() {} },
    async fetchImpl() {
      calls += 1;
      return new Response();
    },
  });
  assert.equal(
    (await anonymous(`${API_ORIGIN}/documents`, { method: "GET" })).status,
    401,
  );
  assert.equal(calls, 0);

  const redirected = createAuthenticatedFetch({
    apiBaseUrl: API_ORIGIN,
    session: { getAccessToken: () => "access-value", clear() {} },
    async fetchImpl() {
      calls += 1;
      return new Response(null, { status: 302, headers: { location: "https://evil.example" } });
    },
  });
  await assert.rejects(
    () => redirected(`${API_ORIGIN}/documents`, { method: "GET" }),
    /redirects are refused/,
  );
  assert.equal(calls, 1);
});
