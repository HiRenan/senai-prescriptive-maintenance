import { beginPkce, clearPkce, consumePkce } from "./pkce.js";

const CALLBACK_KEYS = Object.freeze(["code", "error", "error_description", "state"]);
const MAX_CODE_LENGTH = 2048;
const MAX_ERROR_LENGTH = 128;
const MAX_ERROR_DESCRIPTION_LENGTH = 512;
const STATE_PATTERN = /^[A-Za-z0-9_-]{43}$/;
export const DEFAULT_TOKEN_TIMEOUT_MS = 10000;
export const DEFAULT_REVOCATION_TIMEOUT_MS = 3000;

/**
 * Capture the OAuth answer and remove every sensitive/error parameter from the
 * address bar synchronously, before any token exchange or configuration load.
 *
 * @param {Location | URL} location
 * @param {Pick<History, "replaceState">} history
 * @returns {{
 *   code: string | null,
 *   state: string | null,
 *   error: string | null,
 *   invalid: boolean,
 * }}
 */
export function readAndCleanOAuthCallback(location, history) {
  const current = new URL(location.href);
  const values = Object.fromEntries(
    CALLBACK_KEYS.map((key) => [key, current.searchParams.getAll(key)]),
  );
  const callback = {
    code: values.code[0] ?? null,
    state: values.state[0] ?? null,
    error: values.error[0] ?? null,
    invalid: false,
  };
  if (CALLBACK_KEYS.some((key) => current.searchParams.has(key))) {
    for (const key of CALLBACK_KEYS) {
      current.searchParams.delete(key);
    }
    const query = current.searchParams.toString();
    history.replaceState(
      null,
      "",
      `${current.pathname}${query === "" ? "" : `?${query}`}${current.hash}`,
    );
  }
  callback.invalid =
    CALLBACK_KEYS.some((key) => values[key].length > 1) ||
    (callback.code !== null && callback.error !== null) ||
    (callback.code !== null &&
      (callback.code.length < 1 || callback.code.length > MAX_CODE_LENGTH)) ||
    (callback.state !== null && !STATE_PATTERN.test(callback.state)) ||
    (callback.error !== null &&
      (callback.error.length < 1 || callback.error.length > MAX_ERROR_LENGTH)) ||
    (values.error_description[0]?.length ?? 0) > MAX_ERROR_DESCRIPTION_LENGTH;
  return Object.freeze(
    callback.invalid
      ? { code: null, state: null, error: null, invalid: true }
      : callback,
  );
}

/** @param {Response} response @returns {boolean} */
function isRedirect(response) {
  return (
    response.redirected ||
    response.type === "opaqueredirect" ||
    (response.status >= 300 && response.status < 400)
  );
}

/**
 * @param {object} options
 * @param {{
 *   clientId: string,
 *   hostedUiOrigin: string,
 *   redirectUri: string,
 *   logoutUri: string,
 *   scopes: readonly string[],
 * }} options.config
 * @param {{
 *   setTokens: (tokens: unknown) => boolean,
 *   clear: () => void,
 *   clearForLogout: () => string | null,
 * }} options.session
 * @param {Storage} options.storage
 * @param {typeof fetch} [options.fetchImpl]
 * @param {Crypto} [options.cryptoImpl]
 * @param {() => number} [options.now]
 * @param {(url: string) => void} [options.navigate]
 * @param {number} [options.tokenTimeoutMs]
 * @param {number} [options.revocationTimeoutMs]
 * @returns {{
 *   login: () => Promise<void>,
 *   handleCallback: (callback: ReturnType<typeof readAndCleanOAuthCallback>) => Promise<
 *     { ok: true, handled: boolean }
 *     | { ok: false, reason: "provider" | "callback" | "state" | "expired" | "network" | "token" }
 *   >,
 *   logout: () => Promise<void>,
 * }}
 */
export function createCognitoAuth(options) {
  const fetchImpl = options.fetchImpl ?? globalThis.fetch.bind(globalThis);
  const navigate = options.navigate ?? ((url) => window.location.assign(url));
  const now = options.now ?? Date.now;

  async function login() {
    const material = await beginPkce({
      storage: options.storage,
      cryptoImpl: options.cryptoImpl,
      now: now(),
    });
    const authorize = new URL("/oauth2/authorize", options.config.hostedUiOrigin);
    authorize.searchParams.set("client_id", options.config.clientId);
    authorize.searchParams.set("code_challenge", material.challenge);
    authorize.searchParams.set("code_challenge_method", "S256");
    authorize.searchParams.set("redirect_uri", options.config.redirectUri);
    authorize.searchParams.set("response_type", "code");
    authorize.searchParams.set("scope", options.config.scopes.join(" "));
    authorize.searchParams.set("state", material.state);
    navigate(authorize.href);
  }

  /**
   * @param {ReturnType<typeof readAndCleanOAuthCallback>} callback
   * @returns {Promise<
   *   { ok: true, handled: boolean }
   *   | { ok: false, reason: "provider" | "callback" | "state" | "expired" | "network" | "token" }
   * >}
   */
  async function handleCallback(callback) {
    if (callback.invalid) {
      clearPkce(options.storage);
      options.session.clear();
      return { ok: false, reason: "callback" };
    }
    if (callback.error !== null) {
      clearPkce(options.storage);
      options.session.clear();
      return { ok: false, reason: "provider" };
    }
    if (callback.code === null && callback.state === null) {
      return { ok: true, handled: false };
    }
    if (
      callback.code === null ||
      callback.code === "" ||
      callback.code.length > MAX_CODE_LENGTH ||
      callback.state === null ||
      !STATE_PATTERN.test(callback.state)
    ) {
      clearPkce(options.storage);
      options.session.clear();
      return { ok: false, reason: "callback" };
    }

    const consumed = consumePkce({
      storage: options.storage,
      state: callback.state,
      now: now(),
    });
    if (!consumed.ok) {
      options.session.clear();
      return {
        ok: false,
        reason: consumed.reason === "expired" ? "expired" : "state",
      };
    }

    const body = new URLSearchParams({
      client_id: options.config.clientId,
      code: callback.code,
      code_verifier: consumed.verifier,
      grant_type: "authorization_code",
      redirect_uri: options.config.redirectUri,
    });
    const controller = new AbortController();
    const timer = setTimeout(
      () => controller.abort(),
      options.tokenTimeoutMs ?? DEFAULT_TOKEN_TIMEOUT_MS,
    );
    try {
      const response = await fetchImpl(
        new URL("/oauth2/token", options.config.hostedUiOrigin),
        {
          method: "POST",
          headers: {
            accept: "application/json",
            "content-type": "application/x-www-form-urlencoded",
          },
          body,
          cache: "no-store",
          credentials: "omit",
          redirect: "manual",
          signal: controller.signal,
        },
      );
      if (response.status !== 200 || isRedirect(response)) {
        await response.body?.cancel();
        options.session.clear();
        return { ok: false, reason: "token" };
      }
      const tokens = await response.json();
      if (!options.session.setTokens(tokens)) {
        return { ok: false, reason: "token" };
      }
      return { ok: true, handled: true };
    } catch {
      options.session.clear();
      return {
        ok: false,
        reason: controller.signal.aborted ? "network" : "token",
      };
    } finally {
      clearTimeout(timer);
    }
  }

  async function logout() {
    clearPkce(options.storage);
    const token = options.session.clearForLogout();
    if (token !== null) {
      const body = new URLSearchParams({
        client_id: options.config.clientId,
        token,
      });
      const controller = new AbortController();
      const timer = setTimeout(
        () => controller.abort(),
        options.revocationTimeoutMs ?? DEFAULT_REVOCATION_TIMEOUT_MS,
      );
      try {
        const response = await fetchImpl(
          new URL("/oauth2/revoke", options.config.hostedUiOrigin),
          {
            method: "POST",
            headers: { "content-type": "application/x-www-form-urlencoded" },
            body,
            cache: "no-store",
            credentials: "omit",
            redirect: "manual",
            signal: controller.signal,
          },
        );
        await response.body?.cancel();
      } catch {
        // Logout still leaves browser memory empty and completes at Cognito.
      } finally {
        clearTimeout(timer);
      }
    }
    const endpoint = new URL("/logout", options.config.hostedUiOrigin);
    endpoint.searchParams.set("client_id", options.config.clientId);
    endpoint.searchParams.set("logout_uri", options.config.logoutUri);
    navigate(endpoint.href);
  }

  return Object.freeze({ login, handleCallback, logout });
}
