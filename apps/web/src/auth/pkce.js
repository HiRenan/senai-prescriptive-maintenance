export const PKCE_STORAGE_KEY = "senai-pm.oauth.pkce.v1";
export const PKCE_MAX_AGE_MS = 10 * 60 * 1000;

const VERIFIER_PATTERN = /^[A-Za-z0-9._~-]{43,128}$/;
const STATE_PATTERN = /^[A-Za-z0-9_-]{43}$/;

/** @param {Uint8Array} bytes @returns {string} */
function base64Url(bytes) {
  let binary = "";
  for (const byte of bytes) {
    binary += String.fromCharCode(byte);
  }
  return btoa(binary).replaceAll("+", "-").replaceAll("/", "_").replace(/=+$/u, "");
}

/**
 * @param {Crypto} cryptoImpl
 * @param {number} length
 * @returns {string}
 */
function randomValue(cryptoImpl, length) {
  const bytes = new Uint8Array(length);
  cryptoImpl.getRandomValues(bytes);
  return base64Url(bytes);
}

/**
 * Create and persist the redirect-only PKCE material. The verifier, state and
 * timestamp live in one sessionStorage entry and are never returned in a URL.
 *
 * @param {object} options
 * @param {Storage} options.storage
 * @param {Crypto} [options.cryptoImpl]
 * @param {number} [options.now]
 * @returns {Promise<{ state: string, challenge: string }>}
 */
export async function beginPkce(options) {
  const cryptoImpl = options.cryptoImpl ?? globalThis.crypto;
  const verifier = randomValue(cryptoImpl, 64);
  const state = randomValue(cryptoImpl, 32);
  const timestamp = options.now ?? Date.now();
  const digest = await cryptoImpl.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(verifier),
  );
  options.storage.setItem(
    PKCE_STORAGE_KEY,
    JSON.stringify({ state, timestamp, verifier }),
  );
  return { state, challenge: base64Url(new Uint8Array(digest)) };
}

/**
 * Consume redirect material before it can be validated or used for exchange.
 * Every return path is therefore single-use, including malformed and expired
 * callbacks.
 *
 * @param {object} options
 * @param {Storage} options.storage
 * @param {string} options.state
 * @param {number} [options.now]
 * @param {number} [options.maxAgeMs]
 * @returns {{ ok: true, verifier: string }
 *   | { ok: false, reason: "missing" | "malformed" | "state" | "expired" }}
 */
export function consumePkce(options) {
  const serialized = options.storage.getItem(PKCE_STORAGE_KEY);
  options.storage.removeItem(PKCE_STORAGE_KEY);
  if (serialized === null) {
    return { ok: false, reason: "missing" };
  }

  let value;
  try {
    value = JSON.parse(serialized);
  } catch {
    return { ok: false, reason: "malformed" };
  }
  if (
    typeof value !== "object" ||
    value === null ||
    Array.isArray(value) ||
    Object.keys(value).sort().join(",") !== "state,timestamp,verifier" ||
    typeof value.state !== "string" ||
    !STATE_PATTERN.test(value.state) ||
    typeof value.verifier !== "string" ||
    !VERIFIER_PATTERN.test(value.verifier) ||
    !Number.isSafeInteger(value.timestamp)
  ) {
    return { ok: false, reason: "malformed" };
  }
  if (value.state !== options.state) {
    return { ok: false, reason: "state" };
  }
  const age = (options.now ?? Date.now()) - value.timestamp;
  if (age < 0 || age > (options.maxAgeMs ?? PKCE_MAX_AGE_MS)) {
    return { ok: false, reason: "expired" };
  }
  return { ok: true, verifier: value.verifier };
}

/** @param {Storage} storage @returns {void} */
export function clearPkce(storage) {
  storage.removeItem(PKCE_STORAGE_KEY);
}
