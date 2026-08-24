const CLOCK_SKEW_SECONDS = 5;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function decodePayload(encoded: string): unknown {
  const normalized = encoded.replaceAll("-", "+").replaceAll("_", "/");
  const padding = "=".repeat((4 - (normalized.length % 4)) % 4);
  const binary = atob(normalized + padding);
  const bytes = Uint8Array.from(binary, (character) => character.charCodeAt(0));
  return JSON.parse(new TextDecoder().decode(bytes));
}

function readExpiry(token: string, clientId: string): number | null {
  const parts = token.split(".");
  if (parts.length !== 3 || parts.some((part) => part === "")) {
    return null;
  }
  let payload;
  try {
    payload = decodePayload(parts[1]);
  } catch {
    return null;
  }
  if (
    !isRecord(payload) ||
    payload.token_use !== "access" ||
    payload.client_id !== clientId ||
    typeof payload.exp !== "number" ||
    !Number.isSafeInteger(payload.exp) ||
    payload.exp <= 0
  ) {
    return null;
  }
  return payload.exp;
}

/**
 * Keep the bearer and revocation token only in closure memory. Reloading the
 * page intentionally loses the session; no refresh exchange is implemented.
 */
export function createMemorySession(options: {
  clientId: string;
  now?: () => number;
}): {
  setTokens: (tokens: unknown) => boolean;
  getAccessToken: () => string | null;
  isAuthenticated: () => boolean;
  clear: () => void;
  clearForLogout: () => string | null;
} {
  const now = options.now ?? Date.now;
  let accessToken: string | null = null;
  let refreshToken: string | null = null;
  let expiresAt: number | null = null;

  function clear() {
    accessToken = null;
    refreshToken = null;
    expiresAt = null;
  }

  function getAccessToken(): string | null {
    if (
      accessToken === null ||
      expiresAt === null ||
      now() >= (expiresAt - CLOCK_SKEW_SECONDS) * 1000
    ) {
      clear();
      return null;
    }
    return accessToken;
  }

  return Object.freeze({
    setTokens(tokens: unknown): boolean {
      if (!isRecord(tokens)) {
        clear();
        return false;
      }
      const access = tokens.access_token;
      const refresh = tokens.refresh_token;
      if (
        typeof access !== "string" ||
        access === "" ||
        tokens.token_type !== "Bearer" ||
        (refresh !== undefined &&
          (typeof refresh !== "string" || refresh === ""))
      ) {
        clear();
        return false;
      }
      const expiry = readExpiry(access, options.clientId);
      if (expiry === null || now() >= (expiry - CLOCK_SKEW_SECONDS) * 1000) {
        clear();
        return false;
      }
      accessToken = access;
      refreshToken = refresh ?? null;
      expiresAt = expiry;
      return true;
    },
    getAccessToken,
    isAuthenticated() {
      return getAccessToken() !== null;
    },
    clear,
    clearForLogout() {
      const token = refreshToken;
      clear();
      return token;
    },
  });
}
