import { createReadStream } from "node:fs";
import { stat } from "node:fs/promises";
import { createServer } from "node:http";
import { join, normalize, resolve, sep } from "node:path";

import { ANALYSIS_STATUSES } from "./src/generated/analysis-contract.js";

const HEALTH_BODY = '{"status":"ok"}';
const HOST = process.env.HOST ?? "0.0.0.0";
const rawPort = process.env.PORT ?? "3000";
const API_BASE_URL = process.env.API_BASE_URL ?? "http://127.0.0.1:8000";
const ANALYSIS_ROUTE = "/api/analysis";
const JSON_CONTENT_TYPE = "application/json";
const MAX_REQUEST_BYTES = 64 * 1024;
const MAX_RESPONSE_BYTES = 256 * 1024;
const DRAIN_FACTOR = 8;

// A deadline exists to end a stuck exchange, so anything past two minutes is
// not a deadline in practice. The cap also keeps the value far from the point
// where `setTimeout` wraps a huge delay into an immediate one.
const MAX_TIMEOUT_MS = 120000;

/**
 * @param {string | undefined} raw
 * @param {number} fallback
 * @returns {number}
 */
function parseTimeout(raw, fallback) {
  if (raw === undefined || !/^\d{1,10}$/.test(raw)) {
    return fallback;
  }
  const parsed = Number.parseInt(raw, 10);
  if (!Number.isSafeInteger(parsed) || parsed < 1 || parsed > MAX_TIMEOUT_MS) {
    return fallback;
  }
  return parsed;
}

/**
 * Accept the contract media type with its optional parameters, and nothing
 * that merely starts with the same letters.
 *
 * @param {string} value
 * @returns {boolean}
 */
function isJsonMedia(value) {
  const [type] = value.split(";");
  return type.trim().toLowerCase() === JSON_CONTENT_TYPE;
}

const REQUEST_TIMEOUT_MS = parseTimeout(process.env.WEB_REQUEST_TIMEOUT_MS, 15000);
const UPSTREAM_TIMEOUT_MS = parseTimeout(process.env.WEB_UPSTREAM_TIMEOUT_MS, 20000);

const CONTENT_TYPES = new Map([
  [".css", "text/css; charset=utf-8"],
  [".html", "text/html; charset=utf-8"],
  [".js", "text/javascript; charset=utf-8"],
]);

const SECURITY_HEADERS = {
  "content-security-policy":
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; " +
    "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
  "referrer-policy": "no-referrer",
  "x-content-type-options": "nosniff",
  "x-frame-options": "DENY",
};

if (!/^\d+$/.test(rawPort)) {
  throw new Error("PORT must be an integer between 1 and 65535.");
}

const port = Number.parseInt(rawPort, 10);
if (port < 1 || port > 65535) {
  throw new Error("PORT must be an integer between 1 and 65535.");
}

let apiOrigin;
try {
  apiOrigin = new URL(API_BASE_URL);
} catch {
  throw new Error("API_BASE_URL must be an absolute http or https URL.");
}
if (apiOrigin.protocol !== "http:" && apiOrigin.protocol !== "https:") {
  throw new Error("API_BASE_URL must be an absolute http or https URL.");
}

const staticRoot = resolve(import.meta.dirname, "src");
const analysisEndpoint = new URL("/analysis", apiOrigin).toString();

/**
 * Resolve one request path inside the static root, or reject it.
 *
 * @param {string} pathname
 * @returns {string | null}
 */
function resolveStaticPath(pathname) {
  let decoded;
  try {
    decoded = decodeURIComponent(pathname);
  } catch {
    return null;
  }
  if (decoded.includes("\0") || decoded.includes("\\")) {
    return null;
  }
  const relative = decoded === "/" ? "index.html" : decoded.replace(/^\/+/, "");
  const candidate = resolve(join(staticRoot, normalize(relative)));
  if (candidate !== staticRoot && !candidate.startsWith(staticRoot + sep)) {
    return null;
  }
  const extension = candidate.slice(candidate.lastIndexOf("."));
  return CONTENT_TYPES.has(extension) ? candidate : null;
}

/**
 * @param {import("node:http").ServerResponse} response
 * @param {number} status
 * @param {string} code
 * @param {string} message
 * @param {boolean} [close]
 * @returns {void}
 */
function sendError(response, status, code, message, close = false) {
  const body = JSON.stringify({ error: { code, message, issues: [] } });
  /** @type {Record<string, string | number>} */
  const headers = {
    ...SECURITY_HEADERS,
    "cache-control": "no-store",
    "content-length": Buffer.byteLength(body),
    "content-type": JSON_CONTENT_TYPE,
  };
  if (close) {
    headers.connection = "close";
  }
  response.writeHead(status, headers);
  response.end(body);
}

/**
 * @typedef {{ status: "ok", body: Buffer }
 *   | { status: "too_large" | "timeout" | "aborted" }} BodyResult
 */

/**
 * Read the request body under a byte cap and a read deadline, settling once.
 *
 * @param {import("node:http").IncomingMessage} request
 * @returns {Promise<BodyResult>}
 */
function readBody(request) {
  return new Promise((fulfil) => {
    /** @type {Buffer[]} */
    let chunks = [];
    let size = 0;
    let settled = false;
    /** @type {NodeJS.Timeout} */
    let timer;

    /** @param {BodyResult} result */
    const settle = (result) => {
      if (settled) {
        return;
      }
      settled = true;
      clearTimeout(timer);
      request.off("data", onData);
      request.off("end", onEnd);
      request.off("error", onFailure);
      request.off("aborted", onFailure);
      chunks = [];
      fulfil(result);
    };

    /** @param {Buffer} chunk */
    const onData = (chunk) => {
      size += chunk.length;
      if (size > MAX_REQUEST_BYTES) {
        // Drop what was buffered before answering: the request is already
        // refused, so holding it would only spend memory on a rejected body.
        chunks = [];
        settle({ status: "too_large" });
        return;
      }
      chunks.push(chunk);
    };
    const onEnd = () => settle({ status: "ok", body: Buffer.concat(chunks) });
    const onFailure = () => settle({ status: "aborted" });

    timer = setTimeout(() => settle({ status: "timeout" }), REQUEST_TIMEOUT_MS);
    request.on("data", onData);
    request.on("end", onEnd);
    request.on("error", onFailure);
    request.on("aborted", onFailure);
  });
}

/**
 * Let a refused request finish arriving so the client can still read the
 * answer, without buffering it and without waiting forever.
 *
 * @param {import("node:http").IncomingMessage} request
 * @param {import("node:http").ServerResponse} response
 * @returns {void}
 */
function discardRest(request, response) {
  if (request.readableEnded || request.destroyed) {
    return;
  }
  let discarded = 0;
  const stop = () => {
    request.off("data", onData);
    request.off("end", stop);
    request.off("error", stop);
    response.off("close", stop);
  };
  /** @param {Buffer} chunk */
  const onData = (chunk) => {
    discarded += chunk.length;
    if (discarded > MAX_REQUEST_BYTES * DRAIN_FACTOR) {
      stop();
      request.destroy();
    }
  };
  request.on("data", onData);
  request.on("end", stop);
  request.on("error", stop);
  response.on("close", stop);
}

/**
 * Release an answer the proxy will not read, so a hostile or endless body is
 * not left streaming into a process that already refused it.
 *
 * @param {Response} upstream
 * @returns {Promise<void>}
 */
async function releaseUpstream(upstream) {
  try {
    await upstream.body?.cancel();
  } catch {
    // The body is already gone; there is nothing left to release.
  }
}

/**
 * Read the API answer under an explicit byte cap.
 *
 * @param {Response} upstream
 * @returns {Promise<{ status: "ok", body: Buffer } | { status: "refused" }>}
 */
async function readUpstreamBody(upstream) {
  const stream = upstream.body;
  if (stream === null) {
    return { status: "refused" };
  }
  const reader = stream.getReader();
  /** @type {Buffer[]} */
  const chunks = [];
  let size = 0;
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) {
        break;
      }
      size += value.byteLength;
      if (size > MAX_RESPONSE_BYTES) {
        await reader.cancel();
        return { status: "refused" };
      }
      chunks.push(Buffer.from(value));
    }
  } catch {
    return { status: "refused" };
  }
  return { status: "ok", body: Buffer.concat(chunks) };
}

/**
 * Forward the analysis request to the API from the same origin as the page, so
 * the browser never needs a cross-origin exception.
 *
 * @param {import("node:http").IncomingMessage} request
 * @param {import("node:http").ServerResponse} response
 * @returns {Promise<void>}
 */
async function proxyAnalysis(request, response) {
  const read = await readBody(request);
  if (read.status === "aborted") {
    response.destroy();
    return;
  }
  if (read.status !== "ok") {
    const tooLarge = read.status === "too_large";
    sendError(
      response,
      tooLarge ? 413 : 408,
      tooLarge ? "request_too_large" : "request_timeout",
      tooLarge
        ? "O corpo excede o limite aceito."
        : "O corpo não chegou dentro do tempo aceito.",
      true,
    );
    discardRest(request, response);
    return;
  }

  const controller = new AbortController();
  // The deadline covers reading the answer too: headers arriving early do not
  // prove the API will ever finish the body.
  const timer = setTimeout(() => controller.abort(), UPSTREAM_TIMEOUT_MS);
  try {
    let upstream;
    try {
      upstream = await fetch(analysisEndpoint, {
        method: "POST",
        headers: { "content-type": JSON_CONTENT_TYPE, accept: JSON_CONTENT_TYPE },
        body: read.body,
        redirect: "error",
        signal: controller.signal,
      });
    } catch {
      const timedOut = controller.signal.aborted;
      sendError(
        response,
        timedOut ? 504 : 502,
        timedOut ? "api_timeout" : "api_unreachable",
        "A API de análise não respondeu.",
      );
      return;
    }

    const declared = upstream.headers.get("content-type") ?? "";
    // Only what the v1 operation publishes is relayed, and only as JSON. A
    // status or a media type the contract does not declare is an API the panel
    // cannot read, so it is reported as a gateway failure instead of passed on.
    if (!ANALYSIS_STATUSES.includes(upstream.status) || !isJsonMedia(declared)) {
      // Refuse before reading, then let go of the body and the connection: an
      // answer the proxy will never use must not keep streaming into it.
      await releaseUpstream(upstream);
      controller.abort();
      sendError(response, 502, "api_invalid_response", "A API respondeu fora do contrato.");
      return;
    }

    const answer = await readUpstreamBody(upstream);
    if (answer.status !== "ok") {
      const timedOut = controller.signal.aborted;
      controller.abort();
      sendError(
        response,
        timedOut ? 504 : 502,
        timedOut ? "api_timeout" : "api_invalid_response",
        "A API respondeu fora do contrato.",
      );
      return;
    }
    const payload = answer.body;

    // The contract status is preserved, but the media type is ours: the panel
    // only ever receives the JSON the contract publishes.
    response.writeHead(upstream.status, {
      ...SECURITY_HEADERS,
      "cache-control": "no-store",
      "content-length": payload.length,
      "content-type": JSON_CONTENT_TYPE,
    });
    response.end(payload);
  } finally {
    clearTimeout(timer);
  }
}

/**
 * @param {import("node:http").IncomingMessage} request
 * @param {import("node:http").ServerResponse} response
 * @param {string} pathname
 * @returns {Promise<void>}
 */
async function serveStatic(request, response, pathname) {
  const filePath = resolveStaticPath(pathname);
  if (filePath === null) {
    response.writeHead(404, { ...SECURITY_HEADERS, "content-length": "0" });
    response.end();
    return;
  }

  let info;
  try {
    info = await stat(filePath);
  } catch {
    response.writeHead(404, { ...SECURITY_HEADERS, "content-length": "0" });
    response.end();
    return;
  }
  if (!info.isFile()) {
    response.writeHead(404, { ...SECURITY_HEADERS, "content-length": "0" });
    response.end();
    return;
  }

  const extension = filePath.slice(filePath.lastIndexOf("."));
  response.writeHead(200, {
    ...SECURITY_HEADERS,
    "cache-control": "no-store",
    "content-length": info.size,
    "content-type": CONTENT_TYPES.get(extension),
  });
  if (request.method === "HEAD") {
    response.end();
    return;
  }
  createReadStream(filePath).pipe(response);
}

/**
 * Answer a handler that failed unexpectedly without leaving a rejection loose.
 *
 * @param {import("node:http").ServerResponse} response
 * @returns {(reason: unknown) => void}
 */
function failClosed(response) {
  return () => {
    if (response.headersSent) {
      response.destroy();
      return;
    }
    sendError(response, 500, "internal_error", "A requisição não pôde ser concluída.");
  };
}

const server = createServer((request, response) => {
  const { pathname } = new URL(request.url ?? "/", "http://localhost");

  if (request.method === "GET" && pathname === "/health/live") {
    response.writeHead(200, {
      "cache-control": "no-store",
      "content-length": Buffer.byteLength(HEALTH_BODY),
      "content-type": JSON_CONTENT_TYPE,
    });
    response.end(HEALTH_BODY);
    return;
  }

  if (pathname === ANALYSIS_ROUTE) {
    if (request.method !== "POST") {
      response.writeHead(405, {
        ...SECURITY_HEADERS,
        allow: "POST",
        "content-length": "0",
      });
      response.end();
      return;
    }
    proxyAnalysis(request, response).catch(failClosed(response));
    return;
  }

  if (request.method === "GET" || request.method === "HEAD") {
    serveStatic(request, response, pathname).catch(failClosed(response));
    return;
  }

  response.writeHead(404, { ...SECURITY_HEADERS, "content-length": "0" });
  response.end();
});

export { parseTimeout, server };

if (process.env.WEB_SERVER_AUTOSTART !== "off") {
  server.listen(port, HOST);
}
