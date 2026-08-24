import assert from "node:assert/strict";
import { test } from "vitest";

import { ANALYSIS_OUTCOMES } from "../src/generated/analysis-contract.js";
import type { AnalysisFailure } from "../src/api/analysis-client";
import {
  PRESCRIPTION_STATE,
  presentAnalysis,
  presentFailure,
} from "../src/core/presentation";
import { outcomeNames, responseExample } from "./helpers/contract-fixtures";

const FAILURE_KINDS: readonly AnalysisFailure["kind"][] = [
  "authentication",
  "input",
  "network",
  "timeout",
  "validation",
  "unavailable",
  "unexpected",
  "malformed",
  "offline",
];

function failure(
  kind: AnalysisFailure["kind"],
  overrides: object = {},
): AnalysisFailure {
  return { kind, status: null, detail: null, issues: [], ...overrides };
}

test("os cinco desfechos do contrato têm apresentação declarada", () => {
  assert.deepEqual(
    ANALYSIS_OUTCOMES.map((entry) => entry.outcome).sort(),
    [...outcomeNames].sort(),
  );
  for (const name of outcomeNames) {
    const report = presentAnalysis(responseExample(name));
    assert.equal(report.kind, "result");
    assert.equal(report.outcome, name);
    assert.ok(report.title.length > 0);
    assert.ok(report.statement.length > 0);
    assert.ok(report.nextStep.length > 0, `sem próximo passo: ${name}`);
  }
});

test("cada desfecho recebe um tom e um título distintos", () => {
  const reports = outcomeNames.map((name) => presentAnalysis(responseExample(name)));
  const tones = new Set(reports.map((report) => report.tone));
  const titles = new Set(reports.map((report) => report.title));
  assert.equal(tones.size, outcomeNames.length);
  assert.equal(titles.size, outcomeNames.length);
});

test("somente documented_fault apresenta prescrição emitida", () => {
  for (const name of outcomeNames) {
    const report = presentAnalysis(responseExample(name));
    const issued = report.prescription.state === PRESCRIPTION_STATE.issued;
    assert.equal(issued, name === "documented_fault", `estado errado em ${name}`);
    if (!issued) {
      assert.equal(report.prescription.summary, null);
      assert.equal(report.prescription.priority, null);
      assert.equal(report.prescription.actions.length, 0);
      assert.ok(report.prescription.heading.length > 0);
      assert.ok(report.prescription.explanation.length > 0);
    }
  }
});

test("a prescrição emitida traz resumo, prioridade e ações", () => {
  const example = responseExample("documented_fault");
  const report = presentAnalysis(example);
  assert.equal(report.prescription.state, PRESCRIPTION_STATE.issued);
  assert.equal(report.prescription.summary, example.prescription.summary);
  assert.equal(report.prescription.priority, example.prescription.priority);
  assert.equal(report.prescription.priorityLabel, "Programada");
  assert.deepEqual([...report.prescription.actions], example.prescription.actions);
});

test("normal distingue prescrição inaplicável de prescrição retida", () => {
  const normal = presentAnalysis(responseExample("normal"));
  assert.equal(normal.prescription.state, PRESCRIPTION_STATE.notApplicable);
  assert.match(normal.prescription.heading, /não se aplica/i);

  for (const name of ["undocumented_fault", "out_of_distribution", "degraded"]) {
    const report = presentAnalysis(responseExample(name));
    assert.equal(report.prescription.state, PRESCRIPTION_STATE.withheld);
    assert.match(report.prescription.heading, /retida/i);
  }
});

test("degraded exibe citações sem que a prescrição pareça disponível", () => {
  const example = responseExample("degraded");
  assert.ok(example.citations.length > 0);
  const report = presentAnalysis(example);
  assert.equal(report.citations.length, example.citations.length);
  assert.equal(report.prescription.state, PRESCRIPTION_STATE.withheld);
  assert.equal(report.prescription.summary, null);
});

test("uma prescrição fora do desfecho previsto nunca é exibida como válida", () => {
  const forged = responseExample("normal");
  forged.prescription = {
    summary: "Prescrição forjada.",
    priority: "urgent",
    actions: ["Ação forjada."],
  };
  const report = presentAnalysis(forged);
  assert.equal(report.prescription.state, PRESCRIPTION_STATE.inconsistent);
  assert.equal(report.prescription.summary, null);
  assert.equal(report.integrity.length, 1);
});

test("documented_fault sem prescrição é tratado como inconsistente", () => {
  const broken = responseExample("documented_fault");
  broken.prescription = null;
  const report = presentAnalysis(broken);
  assert.equal(report.prescription.state, PRESCRIPTION_STATE.inconsistent);
  assert.equal(report.prescription.summary, null);
  assert.ok(report.integrity.length > 0);
});

test("uma prescrição com prioridade fora do enum é recusada", () => {
  const broken = responseExample("documented_fault");
  broken.prescription = { summary: "Resumo.", priority: "immediate", actions: ["Ação."] };
  const report = presentAnalysis(broken);
  assert.equal(report.prescription.state, PRESCRIPTION_STATE.inconsistent);
  assert.equal(report.prescription.summary, null);
});

test("uma prescrição sem ações é recusada", () => {
  const broken = responseExample("documented_fault");
  broken.prescription = { summary: "Resumo.", priority: "routine", actions: [] };
  const report = presentAnalysis(broken);
  assert.equal(report.prescription.state, PRESCRIPTION_STATE.inconsistent);
});

test("um desfecho fora do contrato vira falha, não resultado", () => {
  const alien = responseExample("normal");
  alien.outcome = "invented_outcome";
  const report = presentAnalysis(alien);
  assert.equal(report.kind, "failure");
  assert.equal(report.prescription.state, PRESCRIPTION_STATE.inconsistent);
  assert.ok(report.nextStep.length > 0);
});

test("abstenção traz motivo, mensagem da API e próximo passo", () => {
  for (const name of ["undocumented_fault", "out_of_distribution", "degraded"]) {
    const example = responseExample(name);
    const report = presentAnalysis(example);
    assert.ok(report.abstention);
    assert.equal(report.abstention.reason, example.abstention.reason);
    assert.equal(report.abstention.message, example.abstention.message);
    assert.ok(report.abstention.label.length > 0);
    assert.ok(report.nextStep.length > 0);
  }
});

test("suporte carrega o nível, o valor e a ressalva do contrato", () => {
  const report = presentAnalysis(responseExample("out_of_distribution"));
  assert.ok(report.support);
  assert.equal(report.support.level, "insufficient");
  assert.equal(report.support.label, "Insuficiente");
  assert.equal(report.support.score, 0.05);
  assert.match(report.support.note, /não calibrada/);
});

test("vizinhos e identificadores opacos chegam ao laudo", () => {
  const example = responseExample("normal");
  const report = presentAnalysis(example);
  assert.equal(report.neighbors.length, example.neighbors.length);
  assert.deepEqual(
    report.identifiers.map((entry) => entry.value),
    [example.analysis_id, example.model_id],
  );
});

test("toda falha explica o próximo passo e nunca sugere prescrição", () => {
  for (const kind of FAILURE_KINDS) {
    const report = presentFailure(failure(kind));
    assert.equal(report.kind, "failure");
    assert.equal(report.tone, "failed");
    assert.ok(report.title.length > 0, `sem título: ${kind}`);
    assert.ok(report.nextStep.length > 0, `sem próximo passo: ${kind}`);
    assert.equal(report.prescription.state, PRESCRIPTION_STATE.inconsistent);
    assert.equal(report.prescription.summary, null);
    assert.equal(report.prescription.actions.length, 0);
  }
});

test("timeout não promete cancelamento remoto e offline não inventa outcome", () => {
  const timeout = presentFailure(failure("timeout"));
  const offline = presentFailure(failure("offline"));

  assert.match(timeout.statement, /não confirma/i);
  assert.match(timeout.statement, /cancelado/i);
  assert.match(offline.statement, /não inferiu nem inventou/i);
  assert.match(offline.nextStep, /cinco exemplos sintéticos/i);
});

test("falha de autenticação exige novo login e proíbe replay automático", () => {
  const report = presentFailure(failure("authentication", { status: 403 }));
  assert.equal(report.title, "Autenticação necessária");
  assert.match(report.nextStep, /Entre novamente/);
  assert.match(report.nextStep, /não repetirá/i);
  assert.deepEqual(report.identifiers, [{ label: "Status HTTP", value: "403" }]);
});

test("a falha de validação lista os campos recusados com rótulo legível", () => {
  const report = presentFailure(
    failure("validation", {
      status: 422,
      issues: [
        { field: "z_rms_velocity_mm_s", code: "greater_than_equal" },
        { field: "top_k", code: "less_than_equal" },
      ],
    }),
  );
  assert.deepEqual(
    report.issues.map((issue) => issue.label),
    ["Velocidade RMS (Eixo Z)", "Vizinhos solicitados"],
  );
  assert.deepEqual(
    report.identifiers.map((entry) => entry.value),
    ["422"],
  );
});
