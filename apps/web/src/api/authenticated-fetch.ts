import {
  DOCUMENT_ID_PATTERN,
  DOCUMENT_OPERATIONS,
} from "../generated/document-contract.js";
import type { DocumentOperation } from "../generated/document-contract.js";
import { ASSISTANT_OPERATION } from "../generated/assistant-contract.js";

const documentIdExpression = new RegExp(DOCUMENT_ID_PATTERN);

function isDocumentId(documentId: string): boolean {
  const match = documentIdExpression.exec(documentId);
  return match !== null && match[0] === documentId;
}

function matchesDocumentOperation(
  operation: DocumentOperation,
  pathname: string,
): boolean {
  if (operation.parameters.length === 0) {
    return pathname === operation.path;
  }
  if (
    operation.parameters.length !== 1 ||
    operation.parameters[0] !== "document_id"
  ) {
    return false;
  }
  const marker = "{document_id}";
  const markerAt = operation.path.indexOf(marker);
  if (markerAt < 0) {
    return false;
  }
  const prefix = operation.path.slice(0, markerAt);
  const suffix = operation.path.slice(markerAt + marker.length);
  if (!pathname.startsWith(prefix) || !pathname.endsWith(suffix)) {
    return false;
  }
  const end = suffix === "" ? pathname.length : -suffix.length;
  return isDocumentId(pathname.slice(prefix.length, end));
}

export function isAllowedApiRequest(method: string, pathname: string): boolean {
  if (pathname.includes("%") || pathname.includes("//")) {
    return false;
  }
  if (method === "POST" && pathname === "/analysis") {
    return true;
  }
  if (
    method === ASSISTANT_OPERATION.method &&
    pathname === ASSISTANT_OPERATION.path
  ) {
    return true;
  }
  return Object.values(DOCUMENT_OPERATIONS).some(
    (operation) =>
      operation.method === method && matchesDocumentOperation(operation, pathname),
  );
}

function authenticationRequiredResponse(): Response {
  return new Response(
    JSON.stringify({
      error: {
        code: "authentication_required",
        message: "Uma nova autenticação é necessária.",
        issues: [],
      },
    }),
    {
      status: 401,
      headers: {
        "cache-control": "no-store",
        "content-type": "application/json",
      },
    },
  );
}

/**
 * Create the only cross-origin bearer transport. Its origin, paths and methods
 * are closed over the published runtime contract, so it cannot become a proxy.
 */
export function createAuthenticatedFetch(options: {
  apiBaseUrl: string;
  session: { getAccessToken: () => string | null; clear: () => void };
  fetchImpl?: typeof fetch;
  onAuthenticationRequired?: () => void;
}): typeof fetch {
  const apiOrigin = new URL(options.apiBaseUrl);
  const fetchImpl = options.fetchImpl ?? globalThis.fetch.bind(globalThis);
  const onAuthenticationRequired = options.onAuthenticationRequired ?? (() => {});

  return (async (input: RequestInfo | URL, init: RequestInit = {}) => {
    if (typeof input !== "string" && !(input instanceof URL)) {
      throw new TypeError("Only an absolute API URL is accepted.");
    }
    let endpoint: URL;
    try {
      endpoint = new URL(input.toString());
    } catch {
      throw new TypeError("Only an absolute API URL is accepted.");
    }
    const method = (init.method ?? "GET").toUpperCase();
    if (
      endpoint.origin !== apiOrigin.origin ||
      endpoint.username !== "" ||
      endpoint.password !== "" ||
      endpoint.search !== "" ||
      endpoint.hash !== "" ||
      !isAllowedApiRequest(method, endpoint.pathname)
    ) {
      throw new TypeError("The API request is outside the published allowlist.");
    }
    if (method === "GET" && init.body !== undefined && init.body !== null) {
      throw new TypeError("GET operations cannot carry a body.");
    }

    const accessToken = options.session.getAccessToken();
    if (accessToken === null) {
      onAuthenticationRequired();
      return authenticationRequiredResponse();
    }
    const headers = new Headers(init.headers);
    headers.set("authorization", `Bearer ${accessToken}`);
    const response = await fetchImpl(endpoint.href, {
      ...init,
      method,
      headers,
      cache: "no-store",
      credentials: "omit",
      redirect: "manual",
    });
    if (
      response.redirected ||
      response.type === "opaqueredirect" ||
      (response.status >= 300 && response.status < 400)
    ) {
      await response.body?.cancel();
      throw new TypeError("API redirects are refused.");
    }
    if (response.status === 401 || response.status === 403) {
      options.session.clear();
      onAuthenticationRequired();
    }
    return response;
  }) as typeof fetch;
}
