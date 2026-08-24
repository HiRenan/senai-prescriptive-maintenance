import {
  DOCUMENT_SCHEMAS,
  REGISTER_FIELDS,
  REJECT_FIELDS,
  SYNTHETIC_DOCUMENT_EXAMPLES,
} from "../generated/document-contract.js";
import type {
  DocumentRequestField,
  RegisterDocumentRequest,
} from "../generated/document-contract.js";
import { createSchemaMatcher } from "./contract-decode";
import type { ValidationIssue } from "./features";

const { matchesNode } = createSchemaMatcher(DOCUMENT_SCHEMAS);

// Display labels live here because the frozen contract publishes machine names
// only. Every published field must resolve, which the essential tests assert.
const FIELD_LABELS = Object.freeze({
  filename: "Nome do arquivo PDF",
  media_type: "Tipo de mídia",
  size_bytes: "Tamanho declarado (bytes)",
  sha256: "SHA-256 declarado",
  reason: "Motivo da rejeição",
  note: "Nota da aprovação (opcional)",
});

const FIELD_HINTS = Object.freeze({
  filename: "Somente o nome, sem caminho ou pasta. Deve terminar em .pdf.",
  media_type: "Fixado pelo contrato v1.",
  size_bytes: "Valor declarado por quem registra. A API v1 não recebe os bytes e não o confere.",
  sha256: "Valor declarado por quem registra. A API v1 não recalcula o hash.",
});

/**
 * The fields the console asks for: the contract order without the media type,
 * which is a constant and is sent as the contract publishes it.
 */
export const REGISTER_INPUTS = Object.freeze(
  REGISTER_FIELDS.filter((field) => field.node.kind !== "const"),
);

/** The media type the contract fixes for every registration. */
export const REGISTER_MEDIA_TYPE = (() => {
  const field = REGISTER_FIELDS.find((entry) => entry.node.kind === "const");
  if (field === undefined || field.node.kind !== "const") {
    throw new Error("O contrato de registro não fixa um tipo de mídia.");
  }
  return Object.freeze({ name: field.name, value: field.node.value });
})();

/** The bounds the rejection reason must respect, read from the contract. */
export const REJECT_REASON_FIELD = (() => {
  const field: DocumentRequestField | undefined = REJECT_FIELDS[0];
  if (field === undefined || REJECT_FIELDS.length !== 1) {
    throw new Error("O contrato de rejeição não publica um único campo.");
  }
  return field;
})();

export function documentFieldLabel(name: string): string {
  return FIELD_LABELS[name as keyof typeof FIELD_LABELS] ?? name;
}

export function documentFieldHint(name: string): string | null {
  return FIELD_HINTS[name as keyof typeof FIELD_HINTS] ?? null;
}

const MESSAGES = Object.freeze({
  filename:
    "Informe o nome do arquivo, começando por letra ou número e terminando em .pdf.",
  size_bytes: "Informe um inteiro de bytes dentro dos limites do contrato.",
  sha256: "Informe 64 caracteres hexadecimais minúsculos.",
  reason: "Informe o motivo da rejeição, com 1 a 500 caracteres.",
  note: "A nota aceita de 1 a 500 caracteres, ou pode ficar vazia.",
});

function issue(field: string, code: string): ValidationIssue {
  const message: string | undefined = MESSAGES[field as keyof typeof MESSAGES];
  return { field, code, message: message ?? "Corrija este campo." };
}

/**
 * Parse one console value against the node the contract publishes for it.
 *
 * The bounds are never restated here: the value is checked with the same table
 * the client uses to decode an answer.
 */
export function parseDocumentField(
  field: DocumentRequestField,
  rawValue: string,
): { ok: true; value: string | number } | { ok: false; issue: ValidationIssue } {
  const trimmed = rawValue.trim();
  if (trimmed === "") {
    return field.required
      ? { ok: false, issue: issue(field.name, "required") }
      : { ok: true, value: "" };
  }
  if (field.node.kind === "integer" || field.node.kind === "number") {
    if (!/^[0-9]+$/.test(trimmed)) {
      return { ok: false, issue: issue(field.name, "not_an_integer") };
    }
    const value = Number(trimmed);
    return matchesNode(value, field.node)
      ? { ok: true, value }
      : { ok: false, issue: issue(field.name, "out_of_bounds") };
  }
  return matchesNode(trimmed, field.node)
    ? { ok: true, value: trimmed }
    : { ok: false, issue: issue(field.name, "pattern_mismatch") };
}

/**
 * Build the registration body from raw console input, in contract field order.
 */
export function buildRegisterRequest(
  values: Readonly<Record<string, string>>,
):
  | { ok: true; request: RegisterDocumentRequest }
  | { ok: false; issues: readonly ValidationIssue[] } {
  const issues: ValidationIssue[] = [];
  const body: Record<string, string | number> = {};
  for (const field of REGISTER_FIELDS) {
    if (field.node.kind === "const") {
      body[field.name] = field.node.value;
      continue;
    }
    const parsed = parseDocumentField(field, values[field.name] ?? "");
    if (parsed.ok) {
      body[field.name] = parsed.value;
    } else {
      issues.push(parsed.issue);
    }
  }
  if (issues.length > 0) {
    return { ok: false, issues: Object.freeze(issues) };
  }
  return {
    ok: true,
    request: body as unknown as RegisterDocumentRequest,
  };
}

/**
 * Validate the rejection reason before any request is built.
 */
export function buildRejectReason(
  rawValue: string,
): { ok: true; reason: string } | { ok: false; issue: ValidationIssue } {
  const parsed = parseDocumentField(REJECT_REASON_FIELD, rawValue);
  return parsed.ok
    ? { ok: true, reason: String(parsed.value) }
    : { ok: false, issue: parsed.issue };
}

/**
 * Fill the console with one synthetic example published by the contract.
 *
 * These values are the contract's own demonstration fixtures. They are not a
 * real document and never touch a local file.
 */
export function syntheticRegisterValues(name: string): Record<string, string> | null {
  const example = SYNTHETIC_DOCUMENT_EXAMPLES.find((entry) => entry.name === name);
  if (example === undefined) {
    return null;
  }
  const document = example.document as unknown as Readonly<Record<string, unknown>>;
  const values: Record<string, string> = {};
  for (const field of REGISTER_INPUTS) {
    values[field.name] = String(document[field.name]);
  }
  return values;
}
