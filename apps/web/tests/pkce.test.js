import assert from "node:assert/strict";
import test from "node:test";

import {
  PKCE_MAX_AGE_MS,
  PKCE_STORAGE_KEY,
  beginPkce,
  consumePkce,
} from "../src/auth/pkce.js";

class MemoryStorage {
  values = new Map();

  getItem(key) {
    return this.values.get(key) ?? null;
  }

  setItem(key, value) {
    this.values.set(key, value);
  }

  removeItem(key) {
    this.values.delete(key);
  }
}

test("PKCE usa S256 e o verifier/state são consumidos uma única vez", async () => {
  const storage = new MemoryStorage();
  const started = await beginPkce({ storage, now: 1000 });
  assert.match(started.state, /^[A-Za-z0-9_-]{43}$/);
  assert.match(started.challenge, /^[A-Za-z0-9_-]{43}$/);
  assert.ok(storage.getItem(PKCE_STORAGE_KEY)?.includes("verifier"));

  const consumed = consumePkce({ storage, state: started.state, now: 2000 });
  assert.equal(consumed.ok, true);
  assert.equal(storage.getItem(PKCE_STORAGE_KEY), null);
  assert.deepEqual(consumePkce({ storage, state: started.state, now: 2000 }), {
    ok: false,
    reason: "missing",
  });
});

test("state divergente e timestamp expirado falham depois de apagar o material", async () => {
  const storage = new MemoryStorage();
  const first = await beginPkce({ storage, now: 1000 });
  assert.deepEqual(consumePkce({ storage, state: `${first.state.slice(0, -1)}x`, now: 1001 }), {
    ok: false,
    reason: "state",
  });
  assert.equal(storage.getItem(PKCE_STORAGE_KEY), null);

  const second = await beginPkce({ storage, now: 1000 });
  assert.deepEqual(
    consumePkce({ storage, state: second.state, now: 1000 + PKCE_MAX_AGE_MS + 1 }),
    { ok: false, reason: "expired" },
  );
  assert.equal(storage.getItem(PKCE_STORAGE_KEY), null);
});
