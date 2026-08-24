import assert from "node:assert/strict";
import test from "node:test";

import { syntheticRegisterValues } from "../src/core/document-registration.js";
import { createDocumentsPanel } from "../src/ui/documents-view.js";
import { documentExample, documentStatusNames } from "./helpers/contract-fixtures.js";
import {
  deferred,
  descendants,
  elementsByName,
  findByAttribute,
  installFakeDom,
  waitFor,
} from "./helpers/fake-dom.js";

function success(value) {
  return { ok: true, value };
}

function failed(kind = "network") {
  return {
    ok: false,
    failure: { kind, status: null, detail: null, issues: [] },
  };
}

function clientWith(overrides = {}) {
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

function interactiveControls(host) {
  return descendants(host).filter((element) =>
    ["button", "input", "select", "textarea"].includes(element.localName),
  );
}

test("loading é explícito e nunca afirma o vazio antes da resposta", async () => {
  const restore = installFakeDom();
  try {
    const listing = deferred();
    const host = document.createElement("div");
    const panel = createDocumentsPanel(
      host,
      { client: clientWith({ listDocuments: () => listing.promise }) },
    );

    const started = panel.start();
    assert.match(host.textContent, /Lendo o ciclo documental/);
    assert.doesNotMatch(host.textContent, /A API não retornou nenhum documento/);
    assert.equal(host.getAttribute("aria-busy"), "true");

    listing.resolve(success([]));
    await started;
    assert.doesNotMatch(host.textContent, /Lendo o ciclo documental/);
    assert.match(host.textContent, /A API não retornou nenhum documento/);
    assert.equal(host.getAttribute("aria-busy"), "false");
  } finally {
    restore();
  }
});

test("a tela distingue os sete estados e expõe atualização, vigência e falha", async () => {
  const restore = installFakeDom();
  try {
    const documents = documentStatusNames.map(documentExample);
    const host = document.createElement("div");
    const panel = createDocumentsPanel(host, {
      client: clientWith({ listDocuments: async () => success(documents) }),
    });
    await panel.start();

    const cards = descendants(host).filter((element) =>
      element.hasAttribute("data-card"),
    );
    const statuses = descendants(host)
      .map((element) => element.getAttribute("data-status"))
      .filter((status) => status !== null);
    assert.equal(cards.length, 7);
    assert.deepEqual([...statuses].sort(), [...documentStatusNames].sort());
    assert.match(host.textContent, /Última atualização/);
    assert.match(host.textContent, /Vigente/);
    assert.match(host.textContent, /Sem vigência/);
    assert.match(host.textContent, /Falha do processamento/);
    assert.match(host.textContent, /synthetic_processing_failure/);
  } finally {
    restore();
  }
});

test("busy bloqueia controles dinâmicos, duplo envio e mutação da confirmação", async () => {
  const restore = installFakeDom();
  try {
    const pending = documentExample("pending_approval");
    const approved = {
      ...documentExample("approved"),
      document_id: pending.document_id,
      filename: pending.filename,
    };
    const approval = deferred();
    const refresh = deferred();
    let listCalls = 0;
    let approveCalls = 0;
    const host = document.createElement("div");
    const panel = createDocumentsPanel(host, {
      client: clientWith({
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
    });
    await panel.start();

    findByAttribute(host, "data-action", "approve").click();
    const confirm = findByAttribute(host, "data-confirm", pending.document_id);
    const cancel = findByAttribute(host, "data-cancel", pending.document_id);
    confirm.click();

    assert.equal(approveCalls, 1);
    assert.equal(host.getAttribute("aria-busy"), "true");
    assert.ok(
      interactiveControls(host).every((control) => control.hasAttribute("disabled")),
    );
    confirm.dispatchEvent({ type: "click" });
    cancel.dispatchEvent({ type: "click" });
    assert.equal(approveCalls, 1);
    assert.notEqual(
      findByAttribute(host, "data-confirming", pending.document_id),
      null,
    );

    approval.resolve(success(approved));
    await waitFor(() => listCalls === 2);
    assert.equal(host.getAttribute("aria-busy"), "true");
    assert.ok(
      interactiveControls(host).every((control) => control.hasAttribute("disabled")),
    );

    refresh.resolve(failed());
    await waitFor(() => host.getAttribute("aria-busy") === "false");
    assert.match(host.textContent, /Aprovar registrada/);
    assert.match(host.textContent, /A API não respondeu/);
    assert.match(host.textContent, /lista não pôde ser atualizada/);
    assert.ok(
      interactiveControls(host).every((control) => !control.hasAttribute("disabled")),
    );
  } finally {
    restore();
  }
});

test("cadastro preserva o sucesso quando a atualização da lista falha", async () => {
  const restore = installFakeDom();
  try {
    const received = documentExample("received");
    const current = {
      ...documentExample("pending_approval"),
      document_id: received.document_id,
      filename: received.filename,
    };
    let listCalls = 0;
    const registrations = [];
    const host = document.createElement("div");
    const panel = createDocumentsPanel(host, {
      client: clientWith({
        listDocuments: async () => {
          listCalls += 1;
          return listCalls === 1 ? success([]) : failed();
        },
        registerDocument: async (request) => {
          registrations.push(request);
          return success(received);
        },
        getDocument: async () => success(current),
      }),
    });
    await panel.start();

    const values = syntheticRegisterValues("received");
    assert.notEqual(values, null);
    for (const [name, value] of Object.entries(values)) {
      findByAttribute(host, "data-register", name).value = value;
    }
    elementsByName(host, "form")[0].dispatchEvent({ type: "submit" });
    await waitFor(
      () => listCalls === 2 && host.getAttribute("aria-busy") === "false",
    );

    assert.equal(registrations.length, 1);
    assert.deepEqual(Object.keys(registrations[0]), [
      "filename",
      "media_type",
      "size_bytes",
      "sha256",
    ]);
    assert.match(host.textContent, /Registro de metadados confirmado pela API/);
    assert.match(host.textContent, /A API não respondeu/);
    assert.match(host.textContent, /lista não pôde ser atualizada/);
  } finally {
    restore();
  }
});

test("rejeição inválida mantém erro associado, marca e foca o textarea", async () => {
  const restore = installFakeDom();
  try {
    const pending = documentExample("pending_approval");
    let rejectCalls = 0;
    const host = document.createElement("div");
    const panel = createDocumentsPanel(host, {
      client: clientWith({
        listDocuments: async () => success([pending]),
        rejectDocument: async () => {
          rejectCalls += 1;
          return success(documentExample("rejected"));
        },
      }),
    });
    await panel.start();

    findByAttribute(host, "data-action", "reject").click();
    const reason = findByAttribute(host, "data-reason", pending.document_id);
    const confirm = findByAttribute(host, "data-confirm", pending.document_id);
    confirm.click();

    const error = findByAttribute(host, "data-error-for", "reason");
    assert.equal(rejectCalls, 0);
    assert.equal(reason.getAttribute("aria-invalid"), "true");
    assert.equal(reason.getAttribute("aria-describedby"), error.getAttribute("id"));
    assert.match(error.textContent, /Informe o motivo da rejeição/);
    assert.equal(document.activeElement, reason);

    const confirmation = findByAttribute(
      host,
      "data-confirming",
      pending.document_id,
    );
    const event = { type: "keydown", key: "Escape" };
    confirmation.dispatchEvent(event);
    assert.equal(event.defaultPrevented, true);
    assert.equal(
      findByAttribute(host, "data-confirming", pending.document_id),
      null,
    );
    assert.equal(
      document.activeElement,
      findByAttribute(host, "data-card-heading", pending.document_id),
    );
    assert.equal(rejectCalls, 0);
  } finally {
    restore();
  }
});
