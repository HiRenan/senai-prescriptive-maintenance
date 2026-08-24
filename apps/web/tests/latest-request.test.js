import assert from "node:assert/strict";
import test from "node:test";

import { createLatestRequestController } from "../src/core/latest-request.js";

function deferred() {
  let resolve;
  const promise = new Promise((fulfil) => {
    resolve = fulfil;
  });
  return { promise, resolve };
}

test("uma resposta antiga não substitui nem encerra a requisição mais recente", async () => {
  const first = deferred();
  const second = deferred();
  const events = [];
  const controller = createLatestRequestController({
    onStart: (context) => events.push(`start:${context}`),
    onApply: (output, context) => events.push(`apply:${context}:${output}`),
    onFinish: (context) => events.push(`finish:${context}`),
  });

  const firstRun = controller.run(() => first.promise, "first");
  const secondRun = controller.run(() => second.promise, "second");
  second.resolve("new");
  assert.deepEqual(await secondRun, { applied: true });
  first.resolve("old");
  assert.deepEqual(await firstRun, { applied: false });

  assert.deepEqual(events, [
    "start:first",
    "start:second",
    "apply:second:new",
    "finish:second",
  ]);
});
