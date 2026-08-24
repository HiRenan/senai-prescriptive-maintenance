import assert from "node:assert/strict";
import test from "node:test";

import {
  createDocumentClient,
  isDocumentId,
  readDocument,
  readDocumentList,
} from "../src/api/document-client.js";
import {
  documentExample,
  documentStatusNames,
} from "./helpers/contract-fixtures.js";

function response(status, body) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function registration() {
  const document = documentExample("received");
  return {
    filename: document.filename,
    media_type: document.media_type,
    size_bytes: document.size_bytes,
    sha256: document.sha256,
  };
}

test("o cliente aceita exatamente as sete variantes documentais", () => {
  for (const status of documentStatusNames) {
    const document = documentExample(status);
    assert.deepEqual(readDocument(document), document, status);
  }
  assert.deepEqual(
    readDocumentList({ items: documentStatusNames.map(documentExample) }),
    documentStatusNames.map(documentExample),
  );
});

test("estado desconhecido, membro extra e variante incompleta são recusados", () => {
  const unknown = documentExample("received");
  unknown.status = "unknown";
  assert.equal(readDocument(unknown), null);

  const extra = documentExample("approved");
  extra.processing_version = "invented";
  assert.equal(readDocument(extra), null);

  const incomplete = documentExample("failed");
  delete incomplete.failure;
  assert.equal(readDocument(incomplete), null);
  assert.equal(readDocumentList({ items: [incomplete] }), null);
});

test("as seis chamadas usam somente as rotas same-origin e os corpos publicados", async () => {
  const calls = [];
  const received = documentExample("received");
  const pending = documentExample("pending_approval");
  const approved = documentExample("approved");
  const rejected = documentExample("rejected");
  const processing = documentExample("processing");
  const answers = [
    response(200, { items: [received] }),
    response(201, received),
    response(200, pending),
    response(200, approved),
    response(200, rejected),
    response(200, processing),
  ];
  const fetchImpl = async (url, init) => {
    calls.push({ url, init });
    return answers.shift();
  };
  const client = createDocumentClient({ fetchImpl, timeoutMs: 500 });

  assert.equal((await client.listDocuments()).ok, true);
  assert.equal((await client.registerDocument(registration())).ok, true);
  assert.equal((await client.getDocument(pending.document_id)).ok, true);
  assert.equal((await client.approveDocument(pending.document_id, null)).ok, true);
  assert.equal(
    (await client.rejectDocument(pending.document_id, "Motivo sintético.")).ok,
    true,
  );
  assert.equal((await client.reprocessDocument(rejected.document_id)).ok, true);

  assert.deepEqual(
    calls.map(({ url, init }) => [url, init.method]),
    [
      ["/api/documents", "GET"],
      ["/api/documents", "POST"],
      [`/api/documents/${pending.document_id}`, "GET"],
      [`/api/documents/${pending.document_id}/approve`, "POST"],
      [`/api/documents/${pending.document_id}/reject`, "POST"],
      [`/api/documents/${rejected.document_id}/reprocess`, "POST"],
    ],
  );
  assert.equal(calls[0].init.body, undefined);
  assert.deepEqual(JSON.parse(calls[1].init.body), registration());
  assert.deepEqual(JSON.parse(calls[3].init.body), {});
  assert.deepEqual(JSON.parse(calls[4].init.body), {
    reason: "Motivo sintético.",
  });
  assert.equal(calls[5].init.body, undefined);
});

test("identificadores fora do padrão integral são recusados antes do fetch", async () => {
  let calls = 0;
  const client = createDocumentClient({
    fetchImpl: async () => {
      calls += 1;
      return response(200, documentExample("received"));
    },
  });
  for (const documentId of [
    "doc_ab",
    "doc_valid/path",
    "doc_valid?query=1",
    "doc_valid\n",
    "../doc_valid",
  ]) {
    assert.equal(isDocumentId(documentId), false, documentId);
    const output = await client.getDocument(documentId);
    assert.equal(output.ok, false);
    assert.equal(output.failure.kind, "refused");
  }
  assert.equal(calls, 0);
});

test("erros publicados são distinguidos e sucesso fora da operação é recusado", async () => {
  const envelope = {
    error: {
      code: "document_conflict",
      message: "Estado sintético incompatível.",
      issues: [],
    },
  };
  const conflict = await createDocumentClient({
    fetchImpl: async () => response(409, envelope),
  }).approveDocument("doc_synthetic_pending", null);
  assert.equal(conflict.ok, false);
  assert.equal(conflict.failure.kind, "conflict");
  assert.equal(conflict.failure.detail, envelope.error.message);

  const wrongSuccess = await createDocumentClient({
    fetchImpl: async () => response(201, documentExample("received")),
  }).getDocument("doc_synthetic_received");
  assert.equal(wrongSuccess.ok, false);
  assert.equal(wrongSuccess.failure.kind, "unexpected");
});

test("401 e 403 são classificados como autenticação nas operações documentais", async () => {
  for (const status of [401, 403]) {
    const output = await createDocumentClient({
      fetchImpl: async () => response(status, {}),
    }).listDocuments();
    assert.equal(output.ok, false);
    assert.equal(output.failure.kind, "authentication");
    assert.equal(output.failure.status, status);
  }
});
