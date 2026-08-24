import assert from "node:assert/strict";
import { test } from "vitest";

import {
  isPublishedFrontendOrigin,
  loadRuntimeConfig,
  readRuntimeConfig,
} from "../src/config/runtime-config";

test("somente a origem HTTPS final seleciona o perfil AWS publicado", () => {
  assert.equal(isPublishedFrontendOrigin("https://senai.maib.com.br"), true);
  assert.equal(isPublishedFrontendOrigin("http://senai.maib.com.br"), false);
  assert.equal(isPublishedFrontendOrigin("http://192.168.1.20:3000"), false);
  assert.equal(isPublishedFrontendOrigin("https://preview.example"), false);
});

function validConfig() {
  return {
    schema_version: "runtime-config.v1",
    api_base_url: "https://abc123def4.execute-api.us-east-1.amazonaws.com",
    cognito: {
      client_id: "abc123client",
      hosted_ui_origin:
        "https://senai-pm-demo-a1b2c3d4.auth.us-east-1.amazoncognito.com",
      logout_uri: "https://senai.maib.com.br/",
      redirect_uri: "https://senai.maib.com.br/",
      scopes: ["openid"],
    },
  };
}

test("o runtime config público aceita somente o contrato canônico", () => {
  const decoded = readRuntimeConfig(validConfig());
  assert.equal(
    decoded?.apiBaseUrl,
    "https://abc123def4.execute-api.us-east-1.amazonaws.com",
  );
  assert.deepEqual(decoded?.cognito.scopes, ["openid"]);

  for (const mutate of [
    (value: any) => (value.secret = "never"),
    (value: any) => (value.api_base_url = "https://evil.example"),
    (value: any) => (value.api_base_url += "/stage"),
    (value: any) => (value.cognito.redirect_uri = "https://senai.maib.com.br/callback"),
    (value: any) => (value.cognito.scopes = ["openid", "aws.cognito.signin.user.admin"]),
    (value: any) =>
      (value.cognito.hosted_ui_origin =
        "https://prefix.auth.us-west-2.amazoncognito.com"),
  ]) {
    const value = validConfig();
    mutate(value);
    assert.equal(readRuntimeConfig(value), null);
  }
});

test("o carregamento omite credenciais, recusa redirect e exige JSON válido", async () => {
  let request: any;
  const output = await loadRuntimeConfig({
    async fetchImpl(input: any, init: any) {
      request = { input, init };
      return Response.json(validConfig(), {
        headers: { "content-type": "application/json; charset=utf-8" },
      });
    },
  });
  assert.equal(output.ok, true);
  assert.equal(request.input, "./runtime-config.v1.json");
  assert.equal(request.init.credentials, "omit");
  assert.equal(request.init.redirect, "error");
  assert.equal(request.init.cache, "no-store");

  const refused = await loadRuntimeConfig({
    async fetchImpl() {
      return new Response(JSON.stringify(validConfig()), {
        headers: { "content-type": "text/plain" },
      });
    },
  });
  assert.deepEqual(refused, { ok: false, reason: "media" });
});

test("runtime config pendurado é abortado no deadline", async () => {
  const output = await loadRuntimeConfig({
    timeoutMs: 5,
    fetchImpl(_input: any, init: any) {
      return new Promise((_resolve, reject) => {
        init.signal.addEventListener("abort", () => reject(new Error("aborted")));
      });
    },
  });
  assert.deepEqual(output, { ok: false, reason: "timeout" });
});
