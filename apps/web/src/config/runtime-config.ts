export const RUNTIME_CONFIG_PATH = "./runtime-config.v1.json";
export const RUNTIME_CONFIG_VERSION = "runtime-config.v1";
export const PUBLISHED_FRONTEND_ORIGIN = "https://senai.maib.com.br";
export const DEFAULT_RUNTIME_CONFIG_TIMEOUT_MS = 5000;

const API_HOST = /^[a-z0-9]{10}\.execute-api\.us-east-1\.amazonaws\.com$/;
const COGNITO_HOST =
  /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.auth\.us-east-1\.amazoncognito\.com$/;
const CLIENT_ID = /^[a-z0-9]{1,128}$/;
const COGNITO_SCOPES: readonly ["openid"] = Object.freeze(["openid"] as const);

export function isPublishedFrontendOrigin(origin: string): boolean {
  return origin === PUBLISHED_FRONTEND_ORIGIN;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function hasExactKeys(
  value: Record<string, unknown>,
  expected: readonly string[],
): boolean {
  const actual = Object.keys(value).sort();
  return (
    actual.length === expected.length &&
    [...expected].sort().every((key, index) => actual[index] === key)
  );
}

function readOrigin(value: unknown, hostnamePattern: RegExp): string | null {
  if (typeof value !== "string") {
    return null;
  }
  let parsed;
  try {
    parsed = new URL(value);
  } catch {
    return null;
  }
  if (
    parsed.protocol !== "https:" ||
    parsed.username !== "" ||
    parsed.password !== "" ||
    parsed.port !== "" ||
    parsed.pathname !== "/" ||
    parsed.search !== "" ||
    parsed.hash !== "" ||
    !hostnamePattern.test(parsed.hostname) ||
    value !== parsed.origin
  ) {
    return null;
  }
  return parsed.origin;
}

/**
 * Decode the public post-apply configuration as a closed, secret-free contract.
 */
export function readRuntimeConfig(value: unknown): {
  schemaVersion: "runtime-config.v1";
  apiBaseUrl: string;
  cognito: {
    clientId: string;
    hostedUiOrigin: string;
    redirectUri: "https://senai.maib.com.br/";
    logoutUri: "https://senai.maib.com.br/";
    scopes: readonly ["openid"];
  };
} | null {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, ["api_base_url", "cognito", "schema_version"]) ||
    value.schema_version !== RUNTIME_CONFIG_VERSION ||
    !isRecord(value.cognito) ||
    !hasExactKeys(value.cognito, [
      "client_id",
      "hosted_ui_origin",
      "logout_uri",
      "redirect_uri",
      "scopes",
    ])
  ) {
    return null;
  }

  const apiBaseUrl = readOrigin(value.api_base_url, API_HOST);
  const hostedUiOrigin = readOrigin(value.cognito.hosted_ui_origin, COGNITO_HOST);
  if (
    apiBaseUrl === null ||
    hostedUiOrigin === null ||
    typeof value.cognito.client_id !== "string" ||
    !CLIENT_ID.test(value.cognito.client_id) ||
    value.cognito.redirect_uri !== `${PUBLISHED_FRONTEND_ORIGIN}/` ||
    value.cognito.logout_uri !== `${PUBLISHED_FRONTEND_ORIGIN}/` ||
    !Array.isArray(value.cognito.scopes) ||
    value.cognito.scopes.length !== 1 ||
    value.cognito.scopes[0] !== "openid"
  ) {
    return null;
  }

  return Object.freeze({
    schemaVersion: RUNTIME_CONFIG_VERSION,
    apiBaseUrl,
    cognito: Object.freeze({
      clientId: value.cognito.client_id,
      hostedUiOrigin,
      redirectUri: `${PUBLISHED_FRONTEND_ORIGIN}/` as const,
      logoutUri: `${PUBLISHED_FRONTEND_ORIGIN}/` as const,
      scopes: COGNITO_SCOPES,
    }),
  });
}

export async function loadRuntimeConfig(
  options: {
    fetchImpl?: typeof fetch;
    path?: string;
    timeoutMs?: number;
  } = {},
): Promise<
  | { ok: true; config: NonNullable<ReturnType<typeof readRuntimeConfig>> }
  | { ok: false; reason: "network" | "timeout" | "status" | "media" | "invalid" }
> {
  const fetchImpl = options.fetchImpl ?? globalThis.fetch.bind(globalThis);
  const controller = new AbortController();
  const timer = setTimeout(
    () => controller.abort(),
    options.timeoutMs ?? DEFAULT_RUNTIME_CONFIG_TIMEOUT_MS,
  );
  let response;
  try {
    response = await fetchImpl(options.path ?? RUNTIME_CONFIG_PATH, {
      method: "GET",
      headers: { accept: "application/json" },
      cache: "no-store",
      credentials: "omit",
      redirect: "error",
      signal: controller.signal,
    });
  } catch {
    clearTimeout(timer);
    return { ok: false, reason: controller.signal.aborted ? "timeout" : "network" };
  }
  try {
    if (response.status !== 200) {
      await response.body?.cancel();
      return { ok: false, reason: "status" };
    }
    const mediaType = (response.headers.get("content-type") ?? "")
      .split(";", 1)[0]
      .trim()
      .toLowerCase();
    if (mediaType !== "application/json") {
      await response.body?.cancel();
      return { ok: false, reason: "media" };
    }
    let value;
    try {
      value = await response.json();
    } catch {
      return {
        ok: false,
        reason: controller.signal.aborted ? "timeout" : "invalid",
      };
    }
    const config = readRuntimeConfig(value);
    return config === null
      ? { ok: false, reason: "invalid" }
      : { ok: true, config };
  } finally {
    clearTimeout(timer);
  }
}
