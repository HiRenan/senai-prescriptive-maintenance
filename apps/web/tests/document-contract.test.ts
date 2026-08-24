import assert from "node:assert/strict";
import { test } from "vitest";

import {
  DOCUMENT_CONTRACT_VERSION,
  DOCUMENT_ID_PATTERN,
  DOCUMENT_OPERATIONS,
  DOCUMENT_SCHEMAS,
  DOCUMENT_STATUSES,
  DOCUMENT_VARIANTS,
  REGISTER_FIELDS,
  SYNTHETIC_DOCUMENT_EXAMPLES,
} from "../src/generated/document-contract.js";
import {
  documentResponseExamples,
  documentStatusNames,
  snapshot,
} from "./helpers/contract-fixtures";

const schemas = snapshot.components.schemas;

function schemaName(reference: string): string {
  return reference.replace("#/components/schemas/", "");
}

function operationRows() {
  const rows = [];
  for (const [path, pathItem] of Object.entries<any>(snapshot.paths)) {
    if (!path.startsWith("/documents")) {
      continue;
    }
    for (const method of ["get", "post"]) {
      const operation = pathItem[method];
      if (operation === undefined) {
        continue;
      }
      const successStatus = Object.keys(operation.responses)
        .map(Number)
        .find((status) => status >= 200 && status < 300);
      rows.push({
        operationId: operation.operationId,
        method: method.toUpperCase(),
        path,
        requestSchema:
          operation.requestBody === undefined
            ? null
            : schemaName(
                operation.requestBody.content["application/json"].schema.$ref,
              ),
        successStatus,
        statuses: Object.keys(operation.responses).map(Number),
      });
    }
  }
  return rows;
}

test("o contrato documental gerado conserva versão, identificador e sete estados", () => {
  const declaredStatuses = schemas.DocumentResponse.oneOf.map(
    (entry: any) => schemas[schemaName(entry.$ref)].properties.status.const,
  );
  assert.equal(DOCUMENT_CONTRACT_VERSION, snapshot.info.version);
  assert.equal(
    DOCUMENT_ID_PATTERN,
    schemas.ReceivedDocument.properties.document_id.pattern,
  );
  assert.deepEqual([...DOCUMENT_STATUSES], declaredStatuses);
  assert.deepEqual(
    DOCUMENT_VARIANTS.map((variant) => variant.status),
    declaredStatuses,
  );
});

test("a allowlist gerada contém exatamente as seis operações documentais", () => {
  const declared = operationRows();
  const generated = Object.values(DOCUMENT_OPERATIONS);

  assert.equal(generated.length, 6);
  assert.deepEqual(
    generated.map((operation) => ({
      operationId: operation.operationId,
      method: operation.method,
      path: operation.path,
      requestSchema: operation.requestSchema,
      successStatus: operation.successStatus,
      statuses: [...operation.statuses],
    })),
    declared,
  );
});

test("o registro publica somente os quatro metadados do OpenAPI", () => {
  const declared = schemas.RegisterDocumentRequest;
  assert.deepEqual(
    REGISTER_FIELDS.map((field) => field.name),
    Object.keys(declared.properties),
  );
  assert.deepEqual(
    REGISTER_FIELDS.filter((field) => field.required).map((field) => field.name),
    declared.required,
  );
  assert.deepEqual(
    Object.keys(DOCUMENT_SCHEMAS.RegisterDocumentRequest.properties),
    ["filename", "media_type", "size_bytes", "sha256"],
  );
});

test("os exemplos documentais são os sete exemplos sintéticos do contrato", () => {
  assert.deepEqual(
    SYNTHETIC_DOCUMENT_EXAMPLES.map((example) => example.name).sort(),
    [...documentStatusNames].sort(),
  );
  for (const example of SYNTHETIC_DOCUMENT_EXAMPLES) {
    assert.equal(example.summary, documentResponseExamples[example.name].summary);
    assert.deepEqual(example.document, documentResponseExamples[example.name].value);
  }
});
