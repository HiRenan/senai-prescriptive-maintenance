import assert from "node:assert/strict";
import test from "node:test";

import { createAnalysisClient } from "../src/api/analysis-client.js";
import { RESPONSE_SCHEMAS } from "../src/generated/analysis-contract.js";
import {
  outcomeNames,
  requestExample,
  responseExample,
} from "./helpers/contract-fixtures.js";

/**
 * @param {number} status
 * @param {unknown} body
 * @param {object} [options]
 */
function respondWith(status, body, options = {}) {
  return async () =>
    new Response(options.raw ?? JSON.stringify(body), {
      status,
      headers: { "content-type": "application/json" },
    });
}

/**
 * @param {(input: any, init: any) => Promise<Response>} fetchImpl
 */
function client(fetchImpl) {
  return createAnalysisClient({ fetchImpl, endpoint: "/api/analysis", timeoutMs: 500 });
}

test("uma análise bem-sucedida devolve o resultado do contrato", async () => {
  const example = responseExample("documented_fault");
  const output = await client(respondWith(200, example)).requestAnalysis(
    requestExample("documented_fault"),
  );
  assert.equal(output.ok, true);
  assert.deepEqual(output.response, example);
});

test("a requisição usa POST, JSON e o endpoint da mesma origem", async () => {
  /** @type {any} */
  let seen = null;
  const fetchImpl = async (/** @type {any} */ url, /** @type {any} */ init) => {
    seen = { url, init };
    return new Response(JSON.stringify(responseExample("normal")), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  };
  const request = requestExample("normal");
  await client(fetchImpl).requestAnalysis(request);

  assert.equal(seen.url, "/api/analysis");
  assert.equal(seen.init.method, "POST");
  assert.equal(seen.init.headers["content-type"], "application/json");
  assert.deepEqual(JSON.parse(seen.init.body), request);
});

test("o 422 preserva os campos recusados pelo contrato", async () => {
  const envelope = {
    error: {
      code: "validation_error",
      message: "Requisição inválida.",
      issues: [{ field: "features.rpm", code: "missing" }],
    },
  };
  const output = await client(respondWith(422, envelope)).requestAnalysis(
    requestExample("normal"),
  );
  assert.equal(output.ok, false);
  assert.equal(output.failure.kind, "validation");
  assert.equal(output.failure.status, 422);
  assert.equal(output.failure.detail, "Requisição inválida.");
  assert.deepEqual([...output.failure.issues], envelope.error.issues);
});

test("o 503 é reportado como indisponibilidade, não como resultado", async () => {
  const output = await client(
    respondWith(503, { error: { code: "unavailable", message: "Sem resultado.", issues: [] } }),
  ).requestAnalysis(requestExample("normal"));
  assert.equal(output.ok, false);
  assert.equal(output.failure.kind, "unavailable");
  assert.equal(output.failure.status, 503);
});

test("os status de gateway indicam que a API não foi alcançada", async () => {
  for (const status of [502, 504]) {
    const output = await client(respondWith(status, {})).requestAnalysis(
      requestExample("normal"),
    );
    assert.equal(output.ok, false);
    assert.equal(output.failure.kind, "network");
  }
});

test("um status fora do contrato é reportado como inesperado", async () => {
  const output = await client(respondWith(418, {})).requestAnalysis(
    requestExample("normal"),
  );
  assert.equal(output.ok, false);
  assert.equal(output.failure.kind, "unexpected");
  assert.equal(output.failure.status, 418);
});

test("uma falha de rede não é confundida com resposta da API", async () => {
  const output = await client(async () => {
    throw new TypeError("failed to fetch");
  }).requestAnalysis(requestExample("normal"));
  assert.equal(output.ok, false);
  assert.equal(output.failure.kind, "network");
});

test("o tempo limite aborta a requisição e é reportado como timeout", async () => {
  const output = await client(
    (/** @type {any} */ _url, /** @type {any} */ init) =>
      new Promise((_fulfil, reject) => {
        init.signal.addEventListener("abort", () => {
          const error = new Error("aborted");
          error.name = "AbortError";
          reject(error);
        });
      }),
  ).requestAnalysis(requestExample("normal"));
  assert.equal(output.ok, false);
  assert.equal(output.failure.kind, "timeout");
});

test("uma implementação que ignora abort não aplica a resposta tardia", async () => {
  const lateClient = createAnalysisClient({
    endpoint: "/api/analysis",
    timeoutMs: 5,
    fetchImpl: async () => {
      await new Promise((fulfil) => setTimeout(fulfil, 15));
      return new Response(JSON.stringify(responseExample("normal")), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    },
  });

  const output = await lateClient.requestAnalysis(requestExample("normal"));

  assert.equal(output.ok, false);
  assert.equal(output.failure.kind, "timeout");
});

test("um corpo 200 que não é JSON é recusado", async () => {
  const output = await client(
    respondWith(200, null, { raw: "não é json" }),
  ).requestAnalysis(requestExample("normal"));
  assert.equal(output.ok, false);
  assert.equal(output.failure.kind, "malformed");
});

test("um corpo 200 incompleto é recusado antes de chegar ao laudo", async () => {
  for (const missing of ["analysis_id", "model_id", "support", "neighbors", "warnings"]) {
    const broken = responseExample("normal");
    delete broken[missing];
    const output = await client(respondWith(200, broken)).requestAnalysis(
      requestExample("normal"),
    );
    assert.equal(output.ok, false, `deveria recusar sem ${missing}`);
    assert.equal(output.failure.kind, "malformed");
  }
});

/**
 * Apply one mutation to a synthetic contract example and report how the client
 * classified the resulting `200`.
 *
 * @param {string} outcome
 * @param {(body: any) => void} mutate
 * @returns {Promise<string>}
 */
async function kindFor(outcome, mutate) {
  const body = responseExample(outcome);
  mutate(body);
  const output = await client(respondWith(200, body)).requestAnalysis(
    requestExample(outcome),
  );
  return output.ok ? "ok" : output.failure.kind;
}

/**
 * @param {readonly [string, string, (body: any) => void][]} cases
 */
async function assertAllMalformed(cases) {
  for (const [label, outcome, mutate] of cases) {
    assert.equal(await kindFor(outcome, mutate), "malformed", label);
  }
}

test("os cinco exemplos do contrato continuam sendo aceitos", async () => {
  for (const outcome of outcomeNames) {
    const example = responseExample(outcome);
    const output = await client(respondWith(200, example)).requestAnalysis(
      requestExample(outcome),
    );
    assert.equal(output.ok, true, `o exemplo ${outcome} deveria ser aceito`);
    assert.deepEqual(output.response, example);
  }
});

test("um desfecho fora do contrato não vira laudo", async () => {
  await assertAllMalformed([
    ["desfecho desconhecido", "normal", (body) => (body.outcome = "maintenance_due")],
    ["desfecho não textual", "normal", (body) => (body.outcome = 3)],
  ]);
});

test("identificadores ausentes ou de outro tipo derrubam o corpo 200", async () => {
  await assertAllMalformed([
    ["análise ausente", "normal", (body) => delete body.analysis_id],
    ["análise não textual", "normal", (body) => (body.analysis_id = 42)],
    ["modelo nulo", "normal", (body) => (body.model_id = null)],
  ]);
});

test("o suporte é recusado fora do nível e da faixa do desfecho", async () => {
  await assertAllMalformed([
    ["nível divergente do desfecho", "normal", (body) => (body.support.level = "insufficient")],
    ["nível fora do enum", "normal", (body) => (body.support.level = "partial")],
    ["escore acima do máximo", "normal", (body) => (body.support.support_score = 1.4)],
    ["escore abaixo do mínimo", "normal", (body) => (body.support.support_score = -0.2)],
    ["escore em texto", "normal", (body) => (body.support.support_score = "0.9")],
    ["suporte ausente", "normal", (body) => (body.support = null)],
  ]);
});

test("o diagnóstico segue a presença e o formato do desfecho", async () => {
  await assertAllMalformed([
    ["ausente onde o contrato diagnostica", "normal", (body) => (body.diagnosis = null)],
    ["código vazio", "documented_fault", (body) => (body.diagnosis.code = "")],
    ["código acima do limite", "normal", (body) => (body.diagnosis.code = "C".repeat(81))],
    ["resumo acima do limite", "normal", (body) => (body.diagnosis.summary = "r".repeat(501))],
    ["resumo não textual", "degraded", (body) => (body.diagnosis.summary = 7)],
    [
      "presente onde o contrato não diagnostica",
      "out_of_distribution",
      (body) => (body.diagnosis = { code: "FLT-001", summary: "Inventado." }),
    ],
  ]);
});

test("a abstenção precisa casar com o motivo do próprio desfecho", async () => {
  await assertAllMalformed([
    ["ausente onde o contrato se abstém", "undocumented_fault", (body) => (body.abstention = null)],
    [
      "motivo de outro desfecho",
      "degraded",
      (body) => (body.abstention.reason = "out_of_distribution"),
    ],
    ["motivo fora do enum", "out_of_distribution", (body) => (body.abstention.reason = "unknown")],
    ["mensagem vazia", "undocumented_fault", (body) => (body.abstention.message = "")],
    [
      "presente onde o contrato não se abstém",
      "normal",
      (body) => (body.abstention = { reason: "undocumented_fault", message: "Inventada." }),
    ],
  ]);
});

test("a prescrição só passa quando o desfecho prescreve e o formato fecha", async () => {
  const prescription = responseExample("documented_fault").prescription;
  await assertAllMalformed([
    ["ausente em documented_fault", "documented_fault", (body) => (body.prescription = null)],
    [
      "presente em normal",
      "normal",
      (body) => (body.prescription = structuredClone(prescription)),
    ],
    [
      "presente em degraded",
      "degraded",
      (body) => (body.prescription = structuredClone(prescription)),
    ],
    [
      "prioridade fora do enum",
      "documented_fault",
      (body) => (body.prescription.priority = "immediate"),
    ],
    ["ações vazias", "documented_fault", (body) => (body.prescription.actions = [])],
    [
      "ações acima do limite",
      "documented_fault",
      (body) => (body.prescription.actions = ["a", "b", "c", "d", "e", "f"]),
    ],
    [
      "ação acima do comprimento",
      "documented_fault",
      (body) => (body.prescription.actions = ["a".repeat(301)]),
    ],
    [
      "ação não textual",
      "documented_fault",
      (body) => (body.prescription.actions = ["Trocar o rolamento.", 12]),
    ],
    ["resumo vazio", "documented_fault", (body) => (body.prescription.summary = "")],
  ]);
});

test("vizinhos fora de posto, distância, tipo ou quantidade são recusados", async () => {
  const neighbor = responseExample("normal").neighbors[0];
  await assertAllMalformed([
    ["lista ausente", "normal", (body) => (body.neighbors = null)],
    ["posto abaixo do mínimo", "normal", (body) => (body.neighbors[0].rank = 0)],
    ["posto acima do máximo", "normal", (body) => (body.neighbors[0].rank = 11)],
    ["posto fracionário", "normal", (body) => (body.neighbors[0].rank = 2.5)],
    ["distância negativa", "normal", (body) => (body.neighbors[0].distance = -0.1)],
    ["distância não finita", "normal", (body) => (body.neighbors[0].distance = "0.4")],
    ["referência não textual", "normal", (body) => (body.neighbors[0].neighbor_ref = 5)],
    ["código de falha vazio", "normal", (body) => (body.neighbors[0].fault_code = "")],
    ["código de falha ausente", "normal", (body) => delete body.neighbors[0].fault_code],
    ["item que não é objeto", "normal", (body) => (body.neighbors[1] = "vizinho")],
    ["lista vazia onde há mínimo", "documented_fault", (body) => (body.neighbors = [])],
    [
      "lista acima do máximo",
      "normal",
      (body) => {
        body.neighbors = Array.from({ length: 11 }, () => structuredClone(neighbor));
      },
    ],
  ]);
});

test("citações fora de página, de campo ou de quantidade são recusadas", async () => {
  const citation = responseExample("documented_fault").citations[0];
  await assertAllMalformed([
    ["lista ausente", "documented_fault", (body) => (body.citations = null)],
    ["página abaixo do mínimo", "documented_fault", (body) => (body.citations[0].page_number = 0)],
    [
      "página fracionária",
      "documented_fault",
      (body) => (body.citations[0].page_number = 3.5),
    ],
    ["versão ausente", "documented_fault", (body) => delete body.citations[0].document_version],
    ["trecho não textual", "documented_fault", (body) => (body.citations[0].chunk = null)],
    ["lista vazia onde há mínimo", "documented_fault", (body) => (body.citations = [])],
    [
      "citação acima do limite do desfecho",
      "normal",
      (body) => (body.citations = [structuredClone(citation)]),
    ],
  ]);
});

test("avisos malformados ou ausentes não chegam ao laudo", async () => {
  await assertAllMalformed([
    ["lista ausente", "normal", (body) => (body.warnings = null)],
    ["item que não é objeto", "normal", (body) => (body.warnings = ["atenção"])],
    ["código vazio", "normal", (body) => (body.warnings = [{ code: "", message: "Texto." }])],
    [
      "mensagem não textual",
      "normal",
      (body) => (body.warnings = [{ code: "W-1", message: 3 }]),
    ],
    ["lista vazia onde há mínimo", "degraded", (body) => (body.warnings = [])],
  ]);
});

test("propriedades fora do contrato são recusadas em qualquer nível", async () => {
  await assertAllMalformed([
    ["extra na raiz", "normal", (body) => (body.confidence = 0.9)],
    ["extra no diagnóstico", "normal", (body) => (body.diagnosis.severity = "alta")],
    ["extra no suporte", "normal", (body) => (body.support.margin = 0.1)],
    ["extra na abstenção", "degraded", (body) => (body.abstention.retry_after = 30)],
    [
      "extra na prescrição",
      "documented_fault",
      (body) => (body.prescription.owner = "manutenção"),
    ],
    ["extra no vizinho", "normal", (body) => (body.neighbors[0].asset = "A-1")],
    ["extra na citação", "documented_fault", (body) => (body.citations[0].score = 0.4)],
    ["extra no aviso", "degraded", (body) => (body.warnings[0].hint = "veja o log")],
  ]);
});

test("somente o status de sucesso do contrato vira resultado", async () => {
  const example = responseExample("normal");
  for (const status of [201, 202, 206]) {
    const output = await client(respondWith(status, example)).requestAnalysis(
      requestExample("normal"),
    );
    assert.equal(output.ok, false, `o status ${status} não pode virar laudo`);
    assert.equal(output.failure.kind, "unexpected");
    assert.equal(output.failure.status, status);
  }

  const empty = await client(async () => new Response(null, { status: 204 }))
    .requestAnalysis(requestExample("normal"));
  assert.equal(empty.ok, false);
  assert.equal(empty.failure.kind, "unexpected");
  assert.equal(empty.failure.status, 204);
});

test("um corpo que nunca termina cai no tempo limite, não em laudo", async () => {
  const fetchImpl = async (/** @type {any} */ _url, /** @type {any} */ init) => ({
    status: 200,
    json: () =>
      new Promise((_fulfil, reject) => {
        init.signal.addEventListener("abort", () => {
          const error = new Error("aborted");
          error.name = "AbortError";
          reject(error);
        });
      }),
  });
  const output = await client(fetchImpl).requestAnalysis(requestExample("normal"));
  assert.equal(output.ok, false);
  assert.equal(output.failure.kind, "timeout");
});

test("o contrato gerado publica o padrão de cada família de identificador", () => {
  /** @type {[string, string, string][]} */
  const declared = [
    ["NormalAnalysisResult", "analysis_id", "^ana_"],
    ["NormalAnalysisResult", "model_id", "^model_"],
    ["OpaqueNeighbor", "neighbor_ref", "^neighbor_"],
    ["OpaqueNeighbor", "fault_code", "^[a-z0-9]"],
    ["Citation", "document_id", "^doc_"],
    ["Citation", "document_version", "^docver_"],
    ["Citation", "chunk", "^chunk_"],
  ];
  for (const [schema, field, prefix] of declared) {
    const node = RESPONSE_SCHEMAS[schema].properties[field];
    assert.equal(node.kind, "string", `${schema}.${field} deveria ser texto`);
    assert.ok(
      typeof node.pattern === "string" && node.pattern.startsWith(prefix),
      `${schema}.${field} deveria publicar o padrão do contrato`,
    );
  }
});

test("uma string no tamanho certo, mas fora do padrão, não vira laudo", async () => {
  await assertAllMalformed([
    ["análise sem o prefixo", "normal", (body) => (body.analysis_id = "ana-0001")],
    ["análise com maiúscula", "normal", (body) => (body.analysis_id = "ana_ABC123")],
    ["modelo sem o prefixo", "normal", (body) => (body.model_id = "modelo_knn_v2")],
    ["modelo com espaço", "normal", (body) => (body.model_id = "model_knn v2")],
    [
      "vizinho sem o prefixo",
      "normal",
      (body) => (body.neighbors[0].neighbor_ref = "vizinho_0001"),
    ],
    [
      "código de falha com hífen",
      "normal",
      (body) => (body.neighbors[0].fault_code = "bearing-wear"),
    ],
    [
      "documento sem o prefixo",
      "documented_fault",
      (body) => (body.citations[0].document_id = "documento_0001"),
    ],
    [
      "versão sem o prefixo",
      "documented_fault",
      (body) => (body.citations[0].document_version = "v1"),
    ],
    [
      "trecho sem o prefixo",
      "documented_fault",
      (body) => (body.citations[0].chunk = "trecho_0001"),
    ],
    [
      "trecho com quebra de linha antes do fim",
      "documented_fault",
      (body) => (body.citations[0].chunk = "chunk_0001\n"),
    ],
  ]);
});
