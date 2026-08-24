import assert from "node:assert/strict";
import test from "node:test";

import { createCognitoAuth, readAndCleanOAuthCallback } from "../src/auth/cognito.js";
import { PKCE_STORAGE_KEY } from "../src/auth/pkce.js";
import { createMemorySession } from "../src/auth/session.js";
import { bootstrapDashboard } from "../src/main.js";

class MemoryStorage {
  values = new Map();
  getItem(key) {
    return this.values.get(key) ?? null;
  }
  setItem(key, value) {
    this.values.set(key, value);
  }
  removeItem(key) {
    this.values.delete(key);
  }
}

function accessToken(clientId, exp) {
  const encode = (value) => Buffer.from(JSON.stringify(value)).toString("base64url");
  return `${encode({ alg: "none" })}.${encode({ client_id: clientId, exp, token_use: "access" })}.signature`;
}

const config = Object.freeze({
  clientId: "abc123client",
  hostedUiOrigin:
    "https://senai-pm-demo-a1b2c3d4.auth.us-east-1.amazoncognito.com",
  redirectUri: "https://senai.maib.com.br/",
  logoutUri: "https://senai.maib.com.br/",
  scopes: Object.freeze(["openid"]),
});

test("a sessão conserva tokens só na closure e expiração limpa tudo", () => {
  let now = 1_000_000;
  const session = createMemorySession({ clientId: config.clientId, now: () => now });
  assert.equal(
    session.setTokens({
      access_token: accessToken(config.clientId, 1010),
      refresh_token: "refresh-value",
      token_type: "Bearer",
    }),
    true,
  );
  assert.match(session.getAccessToken() ?? "", /^ey/);
  now = 1_006_000;
  assert.equal(session.getAccessToken(), null);
  assert.equal(session.clearForLogout(), null);
  assert.deepEqual(Object.keys(session), [
    "setTokens",
    "getAccessToken",
    "isAuthenticated",
    "clear",
    "clearForLogout",
  ]);
});

test("o callback é limpo da URL imediatamente e preserva parâmetros não OAuth", () => {
  let replaced = null;
  const location = new URL(
    `https://senai.maib.com.br/?mode=offline&code=secret&state=${"s".repeat(43)}#error`,
  );
  const callback = readAndCleanOAuthCallback(location, {
    replaceState(_state, _title, url) {
      replaced = url;
    },
  });
  assert.deepEqual(callback, {
    code: "secret",
    state: "s".repeat(43),
    error: null,
    invalid: false,
  });
  assert.equal(replaced, "/?mode=offline#error");
  assert.ok(!replaced.includes("secret"));
});

test("callback duplicado, misto ou grande é limpo e recusado antes da troca", () => {
  for (const query of [
    `code=one&code=two&state=${"s".repeat(43)}`,
    `code=one&error=access_denied&state=${"s".repeat(43)}`,
    `code=${"x".repeat(2049)}&state=${"s".repeat(43)}`,
  ]) {
    let replaced = null;
    const callback = readAndCleanOAuthCallback(
      new URL(`https://senai.maib.com.br/?${query}`),
      { replaceState(_state, _title, url) { replaced = url; } },
    );
    assert.deepEqual(callback, { code: null, state: null, error: null, invalid: true });
    assert.equal(replaced, "/");
  }
});

test("a troca usa o verifier já removido e não permite replay", async () => {
  const storage = new MemoryStorage();
  const session = createMemorySession({ clientId: config.clientId, now: () => 1000 });
  let destination = null;
  let exchanges = 0;
  const auth = createCognitoAuth({
    config,
    session,
    storage,
    now: () => 1000,
    navigate(url) {
      destination = url;
    },
    async fetchImpl(_input, init) {
      exchanges += 1;
      assert.equal(storage.getItem(PKCE_STORAGE_KEY), null);
      assert.equal(init.credentials, "omit");
      assert.equal(init.redirect, "manual");
      return Response.json({
        access_token: accessToken(config.clientId, 3600),
        refresh_token: "refresh-value",
        token_type: "Bearer",
      });
    },
  });
  await auth.login();
  const state = new URL(destination).searchParams.get("state");
  assert.equal(new URL(destination).searchParams.get("code_challenge_method"), "S256");
  assert.equal(
    (await auth.handleCallback({ code: "one-code", error: null, invalid: false, state })).ok,
    true,
  );
  assert.equal(session.isAuthenticated(), true);
  assert.deepEqual(
    await auth.handleCallback({ code: "one-code", error: null, invalid: false, state }),
    { ok: false, reason: "state" },
  );
  assert.equal(exchanges, 1);
});

test("troca de código pendurada é abortada sem replay", async () => {
  const storage = new MemoryStorage();
  const session = createMemorySession({ clientId: config.clientId, now: () => 1000 });
  let destination = null;
  let calls = 0;
  const auth = createCognitoAuth({
    config,
    session,
    storage,
    now: () => 1000,
    tokenTimeoutMs: 5,
    navigate(url) { destination = url; },
    fetchImpl(_input, init) {
      calls += 1;
      return new Promise((_resolve, reject) => {
        init.signal.addEventListener("abort", () => reject(new Error("aborted")));
      });
    },
  });
  await auth.login();
  const state = new URL(destination).searchParams.get("state");
  assert.deepEqual(
    await auth.handleCallback({ code: "one-code", error: null, invalid: false, state }),
    { ok: false, reason: "network" },
  );
  assert.equal(calls, 1);
  assert.equal(storage.getItem(PKCE_STORAGE_KEY), null);
});

test("logout limpa memória antes da revogação e conclui no endpoint Cognito", async () => {
  const storage = new MemoryStorage();
  const session = createMemorySession({ clientId: config.clientId, now: () => 1000 });
  session.setTokens({
    access_token: accessToken(config.clientId, 3600),
    refresh_token: "refresh-value",
    token_type: "Bearer",
  });
  let destination = null;
  let revocationBody = null;
  const auth = createCognitoAuth({
    config,
    session,
    storage,
    navigate(url) {
      destination = url;
    },
    async fetchImpl(input, init) {
      assert.equal(new URL(input).pathname, "/oauth2/revoke");
      assert.equal(session.getAccessToken(), null);
      revocationBody = init.body.toString();
      return new Response(null, { status: 200 });
    },
  });
  await auth.logout();
  assert.match(revocationBody, /token=refresh-value/);
  const logout = new URL(destination);
  assert.equal(logout.pathname, "/logout");
  assert.equal(logout.searchParams.get("logout_uri"), config.logoutUri);
});

test("recusa da revogação é best-effort e não impede o logout Cognito", async () => {
  const storage = new MemoryStorage();
  const session = createMemorySession({ clientId: config.clientId, now: () => 1000 });
  session.setTokens({
    access_token: accessToken(config.clientId, 3600),
    refresh_token: "refresh-value",
    token_type: "Bearer",
  });
  let destination = null;
  let calls = 0;
  const auth = createCognitoAuth({
    config,
    session,
    storage,
    navigate(url) { destination = url; },
    async fetchImpl() {
      calls += 1;
      return Response.json({}, { status: 400 });
    },
  });
  await auth.logout();
  assert.equal(calls, 1);
  assert.equal(session.getAccessToken(), null);
  assert.equal(new URL(destination).pathname, "/logout");
});

test("revogação pendurada respeita deadline e não impede logout Cognito", async () => {
  const storage = new MemoryStorage();
  const session = createMemorySession({ clientId: config.clientId, now: () => 1000 });
  session.setTokens({
    access_token: accessToken(config.clientId, 3600),
    refresh_token: "refresh-value",
    token_type: "Bearer",
  });
  let destination = null;
  const auth = createCognitoAuth({
    config,
    session,
    storage,
    revocationTimeoutMs: 5,
    navigate(url) { destination = url; },
    fetchImpl(_input, init) {
      return new Promise((_resolve, reject) => {
        init.signal.addEventListener("abort", () => reject(new Error("aborted")));
      });
    },
  });
  await auth.logout();
  assert.equal(new URL(destination).pathname, "/logout");
  assert.equal(session.getAccessToken(), null);
});

test("falha inesperada do bootstrap é capturada por um boundary sanitizado", async () => {
  const controls = ["input", "button", "select", "textarea"].map((tagName) => ({
    disabled: false,
    tagName,
  }));
  const authLogin = { disabled: false };
  const authLogout = { disabled: false };
  const authPanel = {
    dataset: {},
    removeAttribute() {},
  };
  const authStatus = { textContent: "" };
  const authDetail = { textContent: "" };
  const documents = {
    children: [],
    append(node) { this.children.push(node); },
    replaceChildren() { this.children = []; },
  };
  const surface = (extra = {}) => ({
    attributes: new Map(),
    setAttribute(name, value) { this.attributes.set(name, value); },
    removeAttribute(name) { this.attributes.delete(name); },
    ...extra,
  });
  const elements = new Map([
    ["analysis-form", surface({ elements: controls })],
    ["auth-detail", authDetail],
    ["auth-login", authLogin],
    ["auth-logout", authLogout],
    ["auth-panel", authPanel],
    ["auth-status", authStatus],
    ["documents-panel", surface(documents)],
  ]);
  const documentImpl = {
    createElement() { return { className: "", textContent: "" }; },
    getElementById(id) { return elements.get(id) ?? null; },
  };
  let apiCalls = 0;
  await bootstrapDashboard(
    { code: null, error: null, invalid: false, state: null },
    {
      async start() {
        throw new Error("sensitive detail");
      },
      documentImpl,
    },
  );
  assert.deepEqual(
    controls.map((control) => [control.tagName, control.disabled]),
    [
      ["input", true],
      ["button", true],
      ["select", true],
      ["textarea", true],
    ],
  );
  assert.equal(authLogin.disabled, true);
  assert.equal(authLogout.disabled, true);
  assert.equal(authStatus.textContent, "Painel bloqueado com segurança");
  assert.equal(apiCalls, 0);
});
