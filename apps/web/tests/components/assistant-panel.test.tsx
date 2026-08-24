// @vitest-environment happy-dom
import assert from "node:assert/strict";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, test, vi } from "vitest";

import type { AssistantOutput } from "../../src/api/assistant-client";
import { AssistantPanel } from "../../src/features/assistant/AssistantPanel";
import { SYNTHETIC_ASSISTANT_EXAMPLES } from "../../src/generated/assistant-contract.js";

afterEach(cleanup);

function output(status: "answered" | "insufficient_evidence"): AssistantOutput {
  const fixture = SYNTHETIC_ASSISTANT_EXAMPLES.find((item) => item.name === status);
  assert.ok(fixture);
  return { ok: true, response: fixture.response };
}

async function submit(question: string) {
  const field = screen.getByLabelText("Pergunta");
  fireEvent.change(field, { target: { value: question } });
  fireEvent.click(screen.getByRole("button", { name: "Enviar pergunta" }));
}

test("exibe resposta extrativa, score explicado e citação", async () => {
  const query = vi.fn(async () => output("answered"));
  render(<AssistantPanel client={{ query }} offline={false} ready />);

  await submit("Como verificar vibração radial elevada na bomba?");

  await waitFor(() => assert.equal(query.mock.calls.length, 1));
  assert.match(screen.getByText(/DEMONSTRAÇÃO SINTÉTICA/).textContent ?? "", /Bomba/);
  assert.ok(screen.getByText(/Similaridade .* limiar/));
  assert.ok(screen.getByRole("heading", { name: "Fonte recuperável" }));
});

test("apresenta abstenção sem lista de fontes", async () => {
  const query = vi.fn(async () => output("insufficient_evidence"));
  render(<AssistantPanel client={{ query }} offline={false} ready />);

  await submit("Qual é a previsão do tempo para amanhã?");

  await waitFor(() => assert.equal(query.mock.calls.length, 1));
  assert.ok(screen.getByText(/Não há evidência aprovada e vigente/));
  assert.equal(screen.queryByRole("heading", { name: "Fonte recuperável" }), null);
  assert.ok(screen.getByText(/nenhuma orientação ou citação foi produzida/));
});

test("modo offline é explícito e nunca chama o cliente", () => {
  const query = vi.fn(async () => output("answered"));
  render(<AssistantPanel client={{ query }} offline ready />);

  assert.ok(screen.getByText("Indisponível no modo offline"));
  assert.equal(screen.getByLabelText("Pergunta").hasAttribute("disabled"), true);
  fireEvent.click(screen.getByRole("button", { name: "Enviar pergunta" }));
  assert.equal(query.mock.calls.length, 0);
});

test("retry reutiliza a mesma pergunta sem duplicar a mensagem", async () => {
  const query = vi
    .fn<() => Promise<AssistantOutput>>()
    .mockResolvedValueOnce({
      ok: false,
      failure: { kind: "network", status: null, detail: null, issues: [] },
    })
    .mockResolvedValueOnce(output("answered"));
  render(<AssistantPanel client={{ query }} offline={false} ready />);

  await submit("Como verificar vibração radial elevada na bomba?");
  const retry = await screen.findByRole("button", { name: "Tentar novamente" });
  fireEvent.click(retry);
  await screen.findByRole("heading", { name: "Fonte recuperável" });

  assert.equal(query.mock.calls.length, 2);
  assert.equal(screen.getAllByText("Você").length, 1);
});
