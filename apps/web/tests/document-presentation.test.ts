import assert from "node:assert/strict";
import { test } from "vitest";

import {
  DOCUMENT_ACTION_POLICY,
  describeRegistration,
  presentDocument,
  presentDocumentFailure,
} from "../src/core/document-presentation";
import {
  documentExample,
  documentStatusNames,
} from "./helpers/contract-fixtures";

test("os sete estados têm rótulo, mensagem e ação próprios do ciclo", () => {
  const views = documentStatusNames.map((status) =>
    presentDocument(documentExample(status)),
  );
  assert.equal(new Set(views.map((view) => view.statusLabel)).size, 7);
  assert.equal(new Set(views.map((view) => view.statement)).size, 7);
  assert.deepEqual(
    Object.fromEntries(
      views.map((view) => [
        view.status,
        view.actions.map((action) => action.name),
      ]),
    ),
    {
      received: [],
      processing: [],
      pending_approval: ["approve", "reject"],
      approved: [],
      rejected: ["reprocess"],
      failed: ["reprocess"],
      superseded: [],
    },
  );
  assert.deepEqual(DOCUMENT_ACTION_POLICY.approve, ["pending_approval"]);
});

test("vigência, atualização, decisão e falha vêm somente da resposta", () => {
  const approved = presentDocument(documentExample("approved"));
  assert.equal(approved.currency.state, "current");
  assert.match(approved.updatedAt, /02\/01\/2030/);
  assert.equal(approved.decision!.text, documentExample("approved").decision_note);

  const failed = presentDocument(documentExample("failed"));
  assert.deepEqual(failed.failure, documentExample("failed").failure);
  assert.equal(failed.currency.state, "not_current");

  const superseded = presentDocument(documentExample("superseded"));
  assert.equal(superseded.currency.state, "superseded");
  assert.equal(
    superseded.currency.supersededBy,
    documentExample("superseded").superseded_by_document_id,
  );
  const serialized = JSON.stringify([approved, failed, superseded]);
  assert.doesNotMatch(serialized, /processing_version|processed_at/);
});

test("a confirmação de cadastro não promete upload nem aprovação automática", () => {
  const message = describeRegistration(documentExample("received"));
  assert.match(message, /Registro de metadados confirmado/);
  assert.match(message, /Recebido/);
  assert.match(message, /mesmo registro/);
  assert.doesNotMatch(message, /enviado|upload|aprovado automaticamente/i);
});

test("a falha sanitizada preserva só detalhe e campos publicados", () => {
  const view = presentDocumentFailure({
    kind: "validation",
    status: 422,
    detail: "Metadado sintético inválido.",
    issues: [{ field: "filename", code: "string_pattern_mismatch" }],
  });
  assert.equal(view.status, 422);
  assert.equal(view.detail, "Metadado sintético inválido.");
  assert.deepEqual(view.issues, [
    { label: "Nome do arquivo PDF", code: "string_pattern_mismatch" },
  ]);
});

test("falha de autenticação documental exige login e releitura antes de repetir", () => {
  const view = presentDocumentFailure({
    kind: "authentication",
    status: 401,
    detail: null,
    issues: [],
  });
  assert.equal(view.title, "Autenticação necessária");
  assert.match(view.nextStep, /Entre novamente/);
  assert.match(view.nextStep, /consulte o estado atual/);
});
