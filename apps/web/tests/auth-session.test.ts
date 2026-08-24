import assert from "node:assert/strict";
import { test } from "vitest";

import { createCognitoAuth, readAndCleanOAuthCallback } from "../src/auth/cognito";
import { PKCE_STORAGE_KEY } from "../src/auth/pkce";
import { createMemorySession } from "../src/auth/session";

class MemoryStorage {
  values = new Map<string, string>();
  getItem(key: string) {
    return this.values.get(key) ?? null;
  }
  setItem(key: string, value: string) {
    this.values.set(key, value);
  }
  removeItem(key: string) {
    this.values.delete(key);
  }
}

function accessToken(clientId: string, exp: number) {
  const encode = (value: unknown) => Buffer.from(JSON.stringify(value)).toString("base64url");
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
  let replaced: any = null;
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
    let replaced: any = null;
    const callback = readAndCleanOAuthCallback(
      new URL(`https://senai.maib.com.br/?${query}`),
      { replaceState(_state, _title, url) { replaced = url; } },
    );
    assert.deepEqual(callback, { code: null, state: null, error: null, invalid: true });
    assert.equal(replaced, "/");
  }
});

test("a troca usa o verifier já removido e não permite replay", async () => {
  const storage = new MemoryStorage() as unknown as Storage;
  const session = createMemorySession({ clientId: config.clientId, now: () => 1000 });
  let destination: any = null;
  let exchanges = 0;
  const auth = createCognitoAuth({
    config,
    session,
    storage,
    now: () => 1000,
    navigate(url) {
      destination = url;
    },
    async fetchImpl(_input: any, init: any) {
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
  const storage = new MemoryStorage() as unknown as Storage;
  const session = createMemorySession({ clientId: config.clientId, now: () => 1000 });
  let destination: any = null;
  let calls = 0;
  const auth = createCognitoAuth({
    config,
    session,
    storage,
    now: () => 1000,
    tokenTimeoutMs: 5,
    navigate(url) { destination = url; },
    fetchImpl(_input: any, init: any) {
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
  const storage = new MemoryStorage() as unknown as Storage;
  const session = createMemorySession({ clientId: config.clientId, now: () => 1000 });
  session.setTokens({
    access_token: accessToken(config.clientId, 3600),
    refresh_token: "refresh-value",
    token_type: "Bearer",
  });
  let destination: any = null;
  let revocationBody: any = null;
  const auth = createCognitoAuth({
    config,
    session,
    storage,
    navigate(url) {
      destination = url;
    },
    async fetchImpl(input: any, init: any) {
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
  const storage = new MemoryStorage() as unknown as Storage;
  const session = createMemorySession({ clientId: config.clientId, now: () => 1000 });
  session.setTokens({
    access_token: accessToken(config.clientId, 3600),
    refresh_token: "refresh-value",
    token_type: "Bearer",
  });
  let destination: any = null;
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
  const storage = new MemoryStorage() as unknown as Storage;
  const session = createMemorySession({ clientId: config.clientId, now: () => 1000 });
  session.setTokens({
    access_token: accessToken(config.clientId, 3600),
    refresh_token: "refresh-value",
    token_type: "Bearer",
  });
  let destination: any = null;
  const auth = createCognitoAuth({
    config,
    session,
    storage,
    revocationTimeoutMs: 5,
    navigate(url) { destination = url; },
    fetchImpl(_input: any, init: any) {
      return new Promise((_resolve, reject) => {
        init.signal.addEventListener("abort", () => reject(new Error("aborted")));
      });
    },
  });
  await auth.logout();
  assert.equal(new URL(destination).pathname, "/logout");
  assert.equal(session.getAccessToken(), null);
});
