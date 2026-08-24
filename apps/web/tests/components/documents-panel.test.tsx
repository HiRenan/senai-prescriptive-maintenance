// @vitest-environment happy-dom
import assert from "node:assert/strict";
import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, test } from "vitest";

import { DocumentsPanel } from "../../src/features/documents/DocumentsPanel";
import { deferred } from "../helpers/async";
import { documentExample, documentStatusNames } from "../helpers/contract-fixtures";

afterEach(cleanup);

function success(value: unknown) {
  return { ok: true, value };
}

function failed(kind = "network") {
  return {
    ok: false,
    failure: { kind, status: null, detail: null, issues: [] },
  };
}

function clientWith(overrides: Record<string, unknown> = {}): any {
  return {
    listDocuments: async () => success([]),
    getDocument: async () => success(documentExample("received")),
    registerDocument: async () => success(documentExample("received")),
    approveDocument: async () => success(documentExample("approved")),
    rejectDocument: async () => success(documentExample("rejected")),
    reprocessDocument: async () => success(documentExample("processing")),
    ...overrides,
  };
}

function panel(container: HTMLElement): HTMLElement {
  const root = container.querySelector(".documents");
  assert.ok(root instanceof HTMLElement);
  return root;
}

function text(container: HTMLElement): string {
  return panel(container).textContent ?? "";
}

function byAttribute(
  container: HTMLElement,
  attribute: string,
  value: string,
): HTMLElement | null {
  const found = container.querySelector(`[${attribute}="${value}"]`);
  return found instanceof HTMLElement ? found : null;
}

function require(
  container: HTMLElement,
  attribute: string,
  value: string,
): HTMLElement {
  const found = byAttribute(container, attribute, value);
  assert.ok(found !== null, `elemento ausente: [${attribute}="${value}"]`);
  return found;
}

function interactiveControls(container: HTMLElement): HTMLElement[] {
  return [...container.querySelectorAll("button, input, select, textarea")].filter(
    (element): element is HTMLElement => element instanceof HTMLElement,
  );
}

function renderPanel(client: unknown, offline = false) {
  const announcements: string[] = [];
  const view = render(
    <DocumentsPanel
      client={client as never}
      offline={offline}
      announce={(message) => {
        announcements.push(message);
      }}
    />,
  );
  return { ...view, announcements };
}

test("loading é explícito e nunca afirma o vazio antes da resposta", async () => {
  const listing = deferred<unknown>();
  const { container } = renderPanel(
    clientWith({ listDocuments: () => listing.promise }),
  );

  assert.match(text(container), /Lendo o ciclo documental/);
  assert.doesNotMatch(text(container), /A API não retornou nenhum documento/);
  assert.equal(panel(container).getAttribute("aria-busy"), "true");

  listing.resolve(success([]));
  await waitFor(() => {
    assert.equal(panel(container).getAttribute("aria-busy"), "false");
  });
  assert.doesNotMatch(text(container), /Lendo o ciclo documental/);
  assert.match(text(container), /A API não retornou nenhum documento/);
});

test("uma falha de refresh preserva e rotula a última lista válida", async () => {
  const approved = documentExample("approved");
  let calls = 0;
  const { container } = renderPanel(
    clientWith({
      listDocuments: async () => {
        calls += 1;
        return calls === 1 ? success([approved]) : failed();
      },
    }),
  );
  await waitFor(() => {
    assert.match(text(container), new RegExp(approved.filename));
  });

  fireEvent.click(require(container, "data-refresh", ""));
  await waitFor(() => {
    assert.equal(calls, 2);
    assert.match(text(container), /anterior\(es\) preservado\(s\)/);
  });

  assert.match(text(container), new RegExp(approved.filename));
  assert.match(text(container), /podem estar desatualizados/);
});

test("o modo offline documental faz zero chamadas e explica o próximo passo", async () => {
  let calls = 0;
  const noNetwork = async () => {
    calls += 1;
    return failed();
  };
  const { container } = renderPanel(
    clientWith({
      listDocuments: noNetwork,
      getDocument: noNetwork,
      registerDocument: noNetwork,
      approveDocument: noNetwork,
      rejectDocument: noNetwork,
      reprocessDocument: noNetwork,
    }),
    true,
  );

  fireEvent.click(require(container, "data-refresh", ""));
  await waitFor(() => {
    assert.match(text(container), /Gestão documental indisponível offline/);
  });

  assert.equal(calls, 0);
  assert.match(text(container), /Próximo passo/);
  assert.ok(
    interactiveControls(panel(container)).every((control) =>
      control.hasAttribute("disabled"),
    ),
  );
});

test("a tela distingue os sete estados e expõe atualização, vigência e falha", async () => {
  const documents = documentStatusNames.map(documentExample);
  const { container } = renderPanel(
    clientWith({ listDocuments: async () => success(documents) }),
  );
  await waitFor(() => {
    assert.equal(panel(container).querySelectorAll("[data-card]").length, 7);
  });

  const statuses = [...panel(container).querySelectorAll("[data-card]")].map(
    (card) => card.getAttribute("data-status"),
  );
  assert.deepEqual([...statuses].sort(), [...documentStatusNames].sort());
  assert.match(text(container), /Última atualização/);
  assert.match(text(container), /Vigente/);
  assert.match(text(container), /Sem vigência/);
  assert.match(text(container), /Falha do processamento/);
  assert.match(text(container), /synthetic_processing_failure/);
});

test("busy bloqueia controles dinâmicos, duplo envio e mutação da confirmação", async () => {
  const pending = documentExample("pending_approval");
  const approved = {
    ...documentExample("approved"),
    document_id: pending.document_id,
    filename: pending.filename,
  };
  const approval = deferred<unknown>();
  const refresh = deferred<unknown>();
  let listCalls = 0;
  let approveCalls = 0;
  const { container, announcements } = renderPanel(
    clientWith({
      listDocuments: () => {
        listCalls += 1;
        return listCalls === 1
          ? Promise.resolve(success([pending]))
          : refresh.promise;
      },
      approveDocument: () => {
        approveCalls += 1;
        return approval.promise;
      },
    }),
  );
  await waitFor(() => {
    assert.ok(byAttribute(container, "data-action", "approve") !== null);
  });

  fireEvent.click(require(container, "data-action", "approve"));
  const confirm = require(container, "data-confirm", pending.document_id);
  const cancel = require(container, "data-cancel", pending.document_id);
  fireEvent.click(confirm);

  await waitFor(() => {
    assert.equal(panel(container).getAttribute("aria-busy"), "true");
  });
  assert.equal(approveCalls, 1);
  assert.ok(
    interactiveControls(panel(container)).every((control) =>
      control.hasAttribute("disabled"),
    ),
  );

  // Disabled controls must not queue a second command nor tear the
  // confirmation down while the first one is in flight.
  fireEvent.click(confirm);
  fireEvent.click(cancel);
  assert.equal(approveCalls, 1);
  assert.ok(byAttribute(container, "data-confirming", pending.document_id) !== null);

  approval.resolve(success(approved));
  await waitFor(() => {
    assert.equal(listCalls, 2);
  });
  assert.equal(panel(container).getAttribute("aria-busy"), "true");

  refresh.resolve(failed());
  await waitFor(() => {
    assert.equal(panel(container).getAttribute("aria-busy"), "false");
  });
  assert.match(text(container), /Aprovar registrada/);
  assert.match(text(container), /A API não respondeu/);
  assert.ok(
    announcements.some((message) => /lista não pôde ser atualizada/.test(message)),
  );
  assert.ok(
    interactiveControls(panel(container)).every(
      (control) => !control.hasAttribute("disabled"),
    ),
  );
});

test("cadastro preserva o sucesso quando a atualização da lista falha", async () => {
  const received = documentExample("received");
  const current = {
    ...documentExample("pending_approval"),
    document_id: received.document_id,
    filename: received.filename,
  };
  let listCalls = 0;
  const registrations: Record<string, unknown>[] = [];
  const { container, announcements } = renderPanel(
    clientWith({
      listDocuments: async () => {
        listCalls += 1;
        return listCalls === 1 ? success([]) : failed();
      },
      registerDocument: async (request: Record<string, unknown>) => {
        registrations.push(request);
        return success(received);
      },
      getDocument: async () => success(current),
    }),
  );
  await waitFor(() => {
    assert.equal(panel(container).getAttribute("aria-busy"), "false");
  });

  const select = container.querySelector("#document-example");
  assert.ok(select instanceof HTMLSelectElement);
  fireEvent.change(select, { target: { value: "received" } });

  const form = container.querySelector("#document-register-form");
  assert.ok(form instanceof HTMLFormElement);
  fireEvent.submit(form);

  await waitFor(() => {
    assert.equal(listCalls, 2);
    assert.equal(panel(container).getAttribute("aria-busy"), "false");
  });

  assert.equal(registrations.length, 1);
  assert.deepEqual(Object.keys(registrations[0] as object), [
    "filename",
    "media_type",
    "size_bytes",
    "sha256",
  ]);
  assert.match(text(container), /Registro de metadados confirmado pela API/);
  assert.match(text(container), /A API não respondeu/);
  assert.ok(
    announcements.some((message) => /lista não pôde ser atualizada/.test(message)),
  );
});

test("rejeição inválida mantém erro associado, marca e foca o textarea", async () => {
  const pending = documentExample("pending_approval");
  let rejectCalls = 0;
  const { container } = renderPanel(
    clientWith({
      listDocuments: async () => success([pending]),
      rejectDocument: async () => {
        rejectCalls += 1;
        return success(documentExample("rejected"));
      },
    }),
  );
  await waitFor(() => {
    assert.ok(byAttribute(container, "data-action", "reject") !== null);
  });

  fireEvent.click(require(container, "data-action", "reject"));
  const reason = require(container, "data-reason", pending.document_id);
  fireEvent.click(require(container, "data-confirm", pending.document_id));

  await waitFor(() => {
    assert.equal(reason.getAttribute("aria-invalid"), "true");
  });
  const error = require(container, "data-error-for", "reason");
  assert.equal(rejectCalls, 0);
  assert.equal(reason.getAttribute("aria-describedby"), error.getAttribute("id"));
  assert.match(error.textContent ?? "", /Informe o motivo da rejeição/);
  assert.equal(document.activeElement, reason);

  const confirmation = require(container, "data-confirming", pending.document_id);
  fireEvent.keyDown(confirmation, { key: "Escape" });

  await waitFor(() => {
    assert.equal(byAttribute(container, "data-confirming", pending.document_id), null);
  });
  assert.equal(
    document.activeElement,
    require(container, "data-card-heading", pending.document_id),
  );
  assert.equal(rejectCalls, 0);
});
