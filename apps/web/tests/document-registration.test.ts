import assert from "node:assert/strict";
import { test } from "vitest";

import {
  REGISTER_INPUTS,
  buildRegisterRequest,
  buildRejectReason,
  syntheticRegisterValues,
} from "../src/core/document-registration";

test("o cadastro constrói estritamente os quatro metadados do contrato", () => {
  const values = syntheticRegisterValues("received");
  assert.notEqual(values, null);
  const built = buildRegisterRequest(values!);
  assert.equal(built.ok, true);
  assert.deepEqual(Object.keys(built.request), [
    "filename",
    "media_type",
    "size_bytes",
    "sha256",
  ]);
  assert.equal(built.request.media_type, "application/pdf");
  assert.equal(typeof built.request.size_bytes, "number");
  assert.equal(REGISTER_INPUTS.some((field) => field.name === "media_type"), false);
});

test("o cadastro recusa caminho local, tipo implícito, tamanho e hash inválidos", () => {
  const base = syntheticRegisterValues("received");
  assert.notEqual(base, null);
  for (const [field, value] of [
    ["filename", "C:\\materiais\\manual.pdf"],
    ["filename", "manual.txt"],
    ["size_bytes", "0"],
    ["size_bytes", "25000001"],
    ["sha256", "A".repeat(64)],
  ]) {
    const built = buildRegisterRequest({ ...base, [field]: value });
    assert.equal(built.ok, false, `${field} deveria ser recusado`);
    assert.equal(built.issues[0].field, field);
  }
});

test("o motivo da rejeição é obrigatório e respeita o limite publicado", () => {
  assert.equal(buildRejectReason("").ok, false);
  assert.equal(buildRejectReason("x".repeat(501)).ok, false);
  assert.deepEqual(buildRejectReason("  Evidência sintética insuficiente.  "), {
    ok: true,
    reason: "Evidência sintética insuficiente.",
  });
});
