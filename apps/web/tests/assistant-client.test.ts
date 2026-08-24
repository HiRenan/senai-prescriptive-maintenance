import assert from "node:assert/strict";
import { test } from "vitest";

import {
  createAssistantClient,
  readAssistantResponse,
} from "../src/api/assistant-client";
import { SYNTHETIC_ASSISTANT_EXAMPLES } from "../src/generated/assistant-contract.js";

function example(status: "answered" | "insufficient_evidence") {
  const found = SYNTHETIC_ASSISTANT_EXAMPLES.find((item) => item.name === status);
  assert.ok(found);
  return found;
}

test("cliente decodifica resposta e abstenção pelo contrato gerado", async () => {
  for (const status of ["answered", "insufficient_evidence"] as const) {
    const fixture = example(status);
    const calls: Array<{ input: string; body: string | null }> = [];
    const client = createAssistantClient({
      fetchImpl: async (input, init) => {
        calls.push({ input: input.toString(), body: init?.body?.toString() ?? null });
        return new Response(JSON.stringify(fixture.response), {
          status: 200,
          headers: { "content-type": "application/json" },
        });
      },
    });

    const output = await client.query(fixture.request);
    assert.equal(output.ok, true);
    if (output.ok) {
      assert.deepEqual(output.response, fixture.response);
    }
    assert.deepEqual(calls, [
      { input: "/api/assistant/query", body: JSON.stringify(fixture.request) },
    ]);
  }
});

test("decoder recusa campos extras e variantes contraditórias", () => {
  const answered = example("answered").response;
  assert.equal(readAssistantResponse({ ...answered, hidden: true }), null);
  assert.equal(
    readAssistantResponse({ ...answered, status: "insufficient_evidence" }),
    null,
  );
});

test("falha técnica preserva somente o envelope sanitizado", async () => {
  const client = createAssistantClient({
    fetchImpl: async () =>
      new Response(
        JSON.stringify({
          error: {
            code: "assistant_unavailable",
            message: "O assistente está temporariamente indisponível.",
            issues: [],
          },
        }),
        { status: 503, headers: { "content-type": "application/json" } },
      ),
  });

  const output = await client.query(example("answered").request);
  assert.equal(output.ok, false);
  if (!output.ok) {
    assert.equal(output.failure.kind, "unavailable");
    assert.equal(
      output.failure.detail,
      "O assistente está temporariamente indisponível.",
    );
  }
});
