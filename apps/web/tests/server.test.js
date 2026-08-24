import assert from "node:assert/strict";
import { createServer } from "node:http";
import { after, before, test } from "node:test";

import { requestExample, responseExample } from "./helpers/contract-fixtures.js";

/** @type {any} */
let upstream;
/** @type {any} */
let web;
/** @type {string} */
let origin;
/** @type {any} */
let parseTimeout;
/** @type {boolean} */
let upstreamReleased = false;
/** @type {any[]} */
let received = [];
/**
 * @type {{
 *   status: number,
 *   body: string,
 *   contentType?: string,
 *   hang?: boolean,
 *   flood?: boolean,
 *   redirectTo?: string,
 * }}
 */
let upstreamReply;

/**
 * @param {any} server
 * @returns {Promise<number>}
 */
function listen(server) {
  return new Promise((fulfil) => {
    server.listen(0, "127.0.0.1", () => fulfil(server.address().port));
  });
}

before(async () => {
  upstream = createServer((request, response) => {
    /** @type {Buffer[]} */
    const chunks = [];
    request.on("data", (chunk) => chunks.push(chunk));
    request.on("end", () => {
      received.push({
        method: request.method,
        url: request.url,
        body: Buffer.concat(chunks).toString("utf-8"),
      });
      if (upstreamReply.redirectTo !== undefined) {
        response.writeHead(302, { location: upstreamReply.redirectTo });
        response.end();
        return;
      }
      response.writeHead(upstreamReply.status, {
        "content-type": upstreamReply.contentType ?? "application/json",
      });
      if (upstreamReply.hang === true) {
        // Headers and a partial body, then silence: the panel must not wait
        // for it forever just because the answer started.
        response.write("{");
        setTimeout(() => response.end("}"), 2000).unref();
        return;
      }
      if (upstreamReply.flood === true) {
        // Headers first, then an endless body: a refusal has to let go of the
        // connection instead of letting it stream on.
        upstreamReleased = false;
        const pump = setInterval(() => response.write("x".repeat(4096)), 5);
        pump.unref();
        response.on("close", () => {
          clearInterval(pump);
          upstreamReleased = true;
        });
        return;
      }
      response.end(upstreamReply.body);
    });
  });
  const upstreamPort = await listen(upstream);

  process.env.WEB_SERVER_AUTOSTART = "off";
  process.env.API_BASE_URL = `http://127.0.0.1:${upstreamPort}`;
  process.env.WEB_REQUEST_TIMEOUT_MS = "500";
  process.env.WEB_UPSTREAM_TIMEOUT_MS = "800";
  const module = await import("../server.mjs");
  web = module.server;
  parseTimeout = module.parseTimeout;
  const webPort = await listen(web);
  origin = `http://127.0.0.1:${webPort}`;
});

after(async () => {
  await new Promise((fulfil) => web.close(fulfil));
  await new Promise((fulfil) => upstream.close(fulfil));
});

test("a liveness continua idêntica ao contrato do healthcheck", async () => {
  const response = await fetch(`${origin}/health/live`);
  assert.equal(response.status, 200);
  assert.equal(response.headers.get("content-type"), "application/json");
  assert.equal(await response.text(), '{"status":"ok"}');
});

test("a raiz serve o documento do painel com cabeçalhos de segurança", async () => {
  const response = await fetch(`${origin}/`);
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type") ?? "", /text\/html/);
  assert.equal(response.headers.get("x-content-type-options"), "nosniff");
  assert.match(response.headers.get("content-security-policy") ?? "", /default-src 'none'/);
  const body = await response.text();
  assert.match(body, /Análise da leitura/);
});

test("os módulos e o estilo do painel são servidos com o tipo correto", async () => {
  const script = await fetch(`${origin}/main.js`);
  assert.equal(script.status, 200);
  assert.match(script.headers.get("content-type") ?? "", /text\/javascript/);

  const styles = await fetch(`${origin}/styles.css`);
  assert.equal(styles.status, 200);
  assert.match(styles.headers.get("content-type") ?? "", /text\/css/);

  const generated = await fetch(`${origin}/generated/analysis-contract.js`);
  assert.equal(generated.status, 200);
});

test("caminhos fora da raiz estática são recusados", async () => {
  for (const path of [
    "/../package.json",
    "/../../pyproject.toml",
    "/%2e%2e/package.json",
    "/generated/analysis-contract.d.ts",
    "/nao-existe.js",
  ]) {
    const response = await fetch(`${origin}${path}`, { redirect: "manual" });
    assert.equal(response.status, 404, `deveria recusar: ${path}`);
  }
});

test("a análise é encaminhada para POST /analysis da API", async () => {
  received = [];
  const example = responseExample("documented_fault");
  upstreamReply = { status: 200, body: JSON.stringify(example) };
  const request = requestExample("documented_fault");

  const response = await fetch(`${origin}/api/analysis`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(request),
  });

  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), example);
  assert.equal(received.length, 1);
  assert.equal(received[0].method, "POST");
  assert.equal(received[0].url, "/analysis");
  assert.deepEqual(JSON.parse(received[0].body), request);
});

test("o status de erro da API é preservado sem reinterpretação", async () => {
  received = [];
  const envelope = {
    error: { code: "validation_error", message: "Requisição inválida.", issues: [] },
  };
  upstreamReply = { status: 422, body: JSON.stringify(envelope) };

  const response = await fetch(`${origin}/api/analysis`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(requestExample("normal")),
  });

  assert.equal(response.status, 422);
  assert.deepEqual(await response.json(), envelope);
});

test("a rota de análise só aceita POST", async () => {
  const response = await fetch(`${origin}/api/analysis`);
  assert.equal(response.status, 405);
  assert.equal(response.headers.get("allow"), "POST");
});

test("um corpo acima do limite é recusado sem chegar à API", async () => {
  received = [];
  upstreamReply = { status: 200, body: "{}" };
  const response = await fetch(`${origin}/api/analysis`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: "x".repeat(70 * 1024),
  });
  assert.equal(response.status, 413);
  assert.equal(received.length, 0);
});

/**
 * @param {any} body
 * @returns {Promise<Response>}
 */
function postAnalysis(body) {
  return fetch(`${origin}/api/analysis`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
}

test("uma resposta acima do limite não é encaminhada ao painel", async () => {
  received = [];
  upstreamReply = {
    status: 200,
    body: JSON.stringify({ padding: "x".repeat(300 * 1024) }),
  };
  const response = await postAnalysis(requestExample("normal"));
  assert.equal(response.status, 502);
  const body = await response.json();
  assert.equal(body.error.code, "api_invalid_response");
  assert.equal(response.headers.get("content-type"), "application/json");
});

test("um tipo de mídia fora do contrato vira falha de gateway", async () => {
  received = [];
  upstreamReply = {
    status: 200,
    body: "<html>não é o contrato</html>",
    contentType: "text/html; charset=utf-8",
  };
  const response = await postAnalysis(requestExample("normal"));
  assert.equal(response.status, 502);
  assert.equal(response.headers.get("content-type"), "application/json");
  const body = await response.json();
  assert.equal(body.error.code, "api_invalid_response");
});

test("um status fora do contrato não é repassado como resultado", async () => {
  for (const status of [201, 500]) {
    received = [];
    upstreamReply = { status, body: JSON.stringify(responseExample("normal")) };
    const response = await postAnalysis(requestExample("normal"));
    assert.equal(response.status, 502, `o status ${status} não pode ser repassado`);
    const body = await response.json();
    assert.equal(body.error.code, "api_invalid_response");
  }
});

test("um corpo da API que nunca termina cai no tempo limite", async () => {
  received = [];
  upstreamReply = { status: 200, body: "", hang: true };
  const response = await postAnalysis(requestExample("normal"));
  assert.equal(response.status, 504);
  const body = await response.json();
  assert.equal(body.error.code, "api_timeout");
});

test("um cliente lento recebe 408 sem que a API seja chamada", async () => {
  received = [];
  upstreamReply = { status: 200, body: JSON.stringify(responseExample("normal")) };
  /** @type {any} */
  let controller;
  const stream = new ReadableStream({
    start(streamController) {
      controller = streamController;
      streamController.enqueue(new TextEncoder().encode('{"features":'));
    },
  });
  const response = await fetch(`${origin}/api/analysis`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: stream,
    duplex: "half",
  });
  assert.equal(response.status, 408);
  const body = await response.json();
  assert.equal(body.error.code, "request_timeout");
  assert.equal(received.length, 0);
  try {
    controller.close();
  } catch {
    // The connection is already closed; nothing else to release.
  }
});

test("um tipo de mídia parecido com o do contrato não é aceito", async () => {
  received = [];
  upstreamReply = {
    status: 200,
    body: JSON.stringify(responseExample("normal")),
    contentType: "application/jsonmalicious",
  };
  const refused = await postAnalysis(requestExample("normal"));
  assert.equal(refused.status, 502);
  assert.equal((await refused.json()).error.code, "api_invalid_response");

  upstreamReply = {
    status: 200,
    body: JSON.stringify(responseExample("normal")),
    contentType: "Application/JSON; charset=utf-8",
  };
  const accepted = await postAnalysis(requestExample("normal"));
  assert.equal(accepted.status, 200);
  assert.equal(accepted.headers.get("content-type"), "application/json");
});

test("um redirecionamento da API não é seguido em silêncio", async () => {
  received = [];
  upstreamReply = { status: 200, body: "{}", redirectTo: "http://127.0.0.1:9/outro" };
  const response = await postAnalysis(requestExample("normal"));
  assert.equal(response.status, 502);
  assert.equal((await response.json()).error.code, "api_unreachable");
});

test("um tempo limite fora da faixa segura cai no padrão", () => {
  const limit = 120000;
  assert.equal(parseTimeout(String(limit), 15000), limit);
  assert.equal(parseTimeout(String(limit + 1), 15000), 15000);
  assert.equal(parseTimeout("1", 15000), 1);
  assert.equal(parseTimeout("2500", 15000), 2500);

  for (const raw of [
    undefined,
    "",
    " 2500",
    "0",
    "-1",
    "1e9",
    "2500.5",
    "99999999999999999999",
    String(2 ** 31 - 1),
    String(Number.MAX_SAFE_INTEGER),
  ]) {
    assert.equal(parseTimeout(raw, 15000), 15000, `deveria recusar: ${String(raw)}`);
  }
});

test("uma resposta recusada é cancelada em vez de continuar chegando", async () => {
  received = [];
  upstreamReply = {
    status: 200,
    body: "",
    contentType: "text/html; charset=utf-8",
    flood: true,
  };
  const response = await postAnalysis(requestExample("normal"));
  assert.equal(response.status, 502);
  const body = await response.text();
  assert.equal(JSON.parse(body).error.code, "api_invalid_response");
  assert.ok(!body.includes("xxxx"), "o corpo recusado não pode vazar para o painel");

  const deadline = Date.now() + 2000;
  while (!upstreamReleased && Date.now() < deadline) {
    await new Promise((fulfil) => setTimeout(fulfil, 20));
  }
  assert.equal(upstreamReleased, true, "a conexão com a API deveria ter sido liberada");
});
