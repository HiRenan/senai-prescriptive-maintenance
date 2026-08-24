import assert from "node:assert/strict";
import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { createServer, type Server } from "node:http";
import type { AddressInfo } from "node:net";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterAll, beforeAll, test } from "vitest";

import {
  documentExample,
  requestExample,
  responseExample,
} from "./helpers/contract-fixtures";

let upstream: Server;
let web: Server;
let origin: string;
let staticDir: string;
let parseTimeout: (raw: string | undefined, fallback: number) => number;
let upstreamReleased = false;
let received: Array<{
  method: string | undefined;
  url: string | undefined;
  body: string;
}> = [];
let upstreamReply: {
  status: number;
  body: string;
  contentType?: string;
  hang?: boolean;
  flood?: boolean;
  redirectTo?: string;
};

function listen(server: Server): Promise<number> {
  return new Promise((fulfil) => {
    server.listen(0, "127.0.0.1", () => {
      fulfil((server.address() as AddressInfo).port);
    });
  });
}

beforeAll(async () => {
  upstream = createServer((request, response) => {
    const chunks: Buffer[] = [];
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

  // A hermetic stand-in for the Vite build output, so the suite never depends
  // on a real `dist/` bundle having been produced.
  staticDir = await mkdtemp(join(tmpdir(), "web-static-"));
  await mkdir(join(staticDir, "assets"), { recursive: true });
  await writeFile(
    join(staticDir, "index.html"),
    `<!doctype html>
<html lang="pt-BR">
  <head>
    <meta charset="utf-8" />
    <link rel="icon" href="./favicon.svg" />
    <link rel="stylesheet" href="./assets/index-abcd1234.css" />
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="./assets/index-abcd1234.js"></script>
  </body>
</html>
`,
    "utf-8",
  );
  await writeFile(
    join(staticDir, "assets", "index-abcd1234.js"),
    'console.log("painel sintético");\n',
    "utf-8",
  );
  await writeFile(
    join(staticDir, "assets", "index-abcd1234.css"),
    "#root{min-height:100vh}\n",
    "utf-8",
  );
  await writeFile(
    join(staticDir, "favicon.svg"),
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16"><rect width="16" height="16"/></svg>\n',
    "utf-8",
  );

  process.env.WEB_SERVER_AUTOSTART = "off";
  process.env.WEB_STATIC_DIR = staticDir;
  process.env.API_BASE_URL = `http://127.0.0.1:${upstreamPort}`;
  process.env.WEB_REQUEST_TIMEOUT_MS = "500";
  process.env.WEB_UPSTREAM_TIMEOUT_MS = "800";
  const module = await import("../server.mjs");
  web = module.server;
  parseTimeout = module.parseTimeout;
  const webPort = await listen(web);
  origin = `http://127.0.0.1:${webPort}`;
});

afterAll(async () => {
  await new Promise((fulfil) => web.close(fulfil));
  await new Promise((fulfil) => upstream.close(fulfil));
  await rm(staticDir, { recursive: true, force: true });
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
  assert.match(body, /id="root"/);
  assert.match(body, /<script type="module" src="\.\/assets\//);
});

test("os módulos e o estilo do painel são servidos com o tipo correto", async () => {
  const script = await fetch(`${origin}/assets/index-abcd1234.js`);
  assert.equal(script.status, 200);
  assert.equal(script.headers.get("content-type"), "text/javascript; charset=utf-8");
  assert.equal(
    script.headers.get("cache-control"),
    "public, max-age=31536000, immutable",
  );

  const styles = await fetch(`${origin}/assets/index-abcd1234.css`);
  assert.equal(styles.status, 200);
  assert.equal(styles.headers.get("content-type"), "text/css; charset=utf-8");
  assert.equal(
    styles.headers.get("cache-control"),
    "public, max-age=31536000, immutable",
  );

  const favicon = await fetch(`${origin}/favicon.svg`);
  assert.equal(favicon.status, 200);
  assert.equal(favicon.headers.get("content-type"), "image/svg+xml; charset=utf-8");
  assert.equal(favicon.headers.get("cache-control"), "no-store");
});

test("caminhos fora da raiz estática são recusados", async () => {
  for (const path of [
    "/../package.json",
    "/../../pyproject.toml",
    "/%2e%2e/package.json",
    "/vite.config.ts",
    "/index.html.map",
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

test("o proxy encaminha exatamente as seis operações documentais do contrato", async () => {
  received = [];
  const receivedDocument = documentExample("received");
  const pending = documentExample("pending_approval");
  const approved = documentExample("approved");
  const rejected = documentExample("rejected");
  const processing = documentExample("processing");
  const registration = {
    filename: receivedDocument.filename,
    media_type: receivedDocument.media_type,
    size_bytes: receivedDocument.size_bytes,
    sha256: receivedDocument.sha256,
  };
  const cases = [
    {
      local: "/api/documents",
      method: "GET",
      upstream: "/documents",
      status: 200,
      requestBody: null,
      responseBody: { items: [receivedDocument] },
    },
    {
      local: "/api/documents",
      method: "POST",
      upstream: "/documents",
      status: 201,
      requestBody: registration,
      responseBody: receivedDocument,
    },
    {
      local: `/api/documents/${pending.document_id}`,
      method: "GET",
      upstream: `/documents/${pending.document_id}`,
      status: 200,
      requestBody: null,
      responseBody: pending,
    },
    {
      local: `/api/documents/${pending.document_id}/approve`,
      method: "POST",
      upstream: `/documents/${pending.document_id}/approve`,
      status: 200,
      requestBody: {},
      responseBody: approved,
    },
    {
      local: `/api/documents/${pending.document_id}/reject`,
      method: "POST",
      upstream: `/documents/${pending.document_id}/reject`,
      status: 200,
      requestBody: { reason: "Motivo inteiramente sintético." },
      responseBody: rejected,
    },
    {
      local: `/api/documents/${rejected.document_id}/reprocess`,
      method: "POST",
      upstream: `/documents/${rejected.document_id}/reprocess`,
      status: 200,
      requestBody: null,
      responseBody: processing,
    },
  ];

  for (const item of cases) {
    upstreamReply = {
      status: item.status,
      body: JSON.stringify(item.responseBody),
    };
    const response = await fetch(`${origin}${item.local}`, {
      method: item.method,
      headers:
        item.requestBody === null
          ? undefined
          : { "content-type": "application/json" },
      body:
        item.requestBody === null ? undefined : JSON.stringify(item.requestBody),
    });
    assert.equal(response.status, item.status, item.local);
    assert.deepEqual(await response.json(), item.responseBody);
  }

  assert.deepEqual(
    received.map((request) => ({
      method: request.method,
      url: request.url,
      body: request.body === "" ? null : JSON.parse(request.body),
    })),
    cases.map((item) => ({
      method: item.method,
      url: item.upstream,
      body: item.requestBody,
    })),
  );
});

test("o proxy documental recusa path arbitrário, query e identificador parcial", async () => {
  received = [];
  const paths = [
    "/api/documents/doc_valid/delete",
    "/api/documents/doc_ab",
    "/api/documents/doc_valid%2Freprocess",
    "/api/documents/doc_valid%0A",
    "/api/documents/doc_valid/reprocess/extra",
    "/api/documents/doc_valid?view=raw",
    "/api/documents?cursor=doc_valid",
  ];
  for (const path of paths) {
    const response = await fetch(`${origin}${path}`, { redirect: "manual" });
    assert.equal(response.status, 404, path);
  }
  assert.equal(received.length, 0);
});

test("métodos fora das operações documentais recebem 405 sem chegar à API", async () => {
  received = [];
  const collection = await fetch(`${origin}/api/documents`, { method: "PUT" });
  assert.equal(collection.status, 405);
  assert.equal(collection.headers.get("allow"), "GET, POST");

  const item = await fetch(`${origin}/api/documents/doc_valid`, {
    method: "DELETE",
  });
  assert.equal(item.status, 405);
  assert.equal(item.headers.get("allow"), "GET");

  const action = await fetch(`${origin}/api/documents/doc_valid/approve`);
  assert.equal(action.status, 405);
  assert.equal(action.headers.get("allow"), "POST");
  assert.equal(received.length, 0);
});

test("operações documentais sem corpo recusam bytes e status não publicados", async () => {
  received = [];
  upstreamReply = {
    status: 200,
    body: JSON.stringify(documentExample("processing")),
  };
  const body = await fetch(
    `${origin}/api/documents/doc_synthetic_failed/reprocess`,
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: "{}",
    },
  );
  assert.equal(body.status, 400);
  assert.equal((await body.json()).error.code, "request_body_not_allowed");
  assert.equal(received.length, 0);

  upstreamReply = {
    status: 409,
    body: JSON.stringify({
      error: { code: "conflict", message: "Conflito sintético.", issues: [] },
    }),
  };
  const status = await fetch(`${origin}/api/documents`);
  assert.equal(status.status, 502);
  assert.equal((await status.json()).error.code, "api_invalid_response");
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

function postAnalysis(body: unknown): Promise<Response> {
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
  let controller!: ReadableStreamDefaultController;
  const stream = new ReadableStream({
    start(streamController) {
      controller = streamController;
      streamController.enqueue(new TextEncoder().encode('{"features":'));
    },
  });
  // Node's fetch requires `duplex` for stream bodies, but the DOM RequestInit
  // type does not declare it yet, hence the assertion.
  const response = await fetch(`${origin}/api/analysis`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: stream,
    duplex: "half",
  } as RequestInit);
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
