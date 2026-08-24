import {
  ANALYSIS_OUTCOMES,
  NEIGHBOR_DISTANCE_NOTE,
  PRESCRIPTION_PRIORITIES,
  SUPPORT_SCORE_NOTE,
} from "../generated/analysis-contract.js";
import { fieldLabel } from "./features";
import type {
  AnalysisResponse,
  AnalysisWarning,
  Citation,
  OpaqueNeighbor,
  OutcomeContract,
  Prescription,
  PrescriptionPriority,
} from "../generated/analysis-contract.js";
import type { AnalysisFailure } from "../api/analysis-client";

/**
 * Prescription availability is a closed set. Anything that is not `issued`
 * must render as unavailable, whatever else the payload contains.
 */
export const PRESCRIPTION_STATE = Object.freeze({
  issued: "issued",
  notApplicable: "not_applicable",
  withheld: "withheld",
  inconsistent: "inconsistent",
});

const OUTCOME_COPY = Object.freeze({
  normal: {
    tone: "settled",
    title: "Condição normal",
    statement: "O modelo não identificou falha na leitura enviada.",
    nextStep:
      "Nenhuma ação corretiva é indicada. Repita a análise no próximo ciclo de medição.",
  },
  documented_fault: {
    tone: "prescribed",
    title: "Falha documentada",
    statement: "O diagnóstico tem documentação aprovada e uma prescrição foi emitida.",
    nextStep: "Confira as citações antes de intervir e execute as ações na ordem indicada.",
  },
  undocumented_fault: {
    tone: "withheld",
    title: "Falha sem documentação",
    statement: "Há diagnóstico, mas nenhuma documentação aprovada sustenta uma prescrição.",
    nextStep:
      "Registre e aprove a documentação da falha diagnosticada e execute a análise de novo.",
  },
  out_of_distribution: {
    tone: "outside",
    title: "Fora da distribuição",
    statement: "A leitura está fora da faixa que o modelo sustenta, então não há diagnóstico.",
    nextStep:
      "Confira as 18 features enviadas e colete uma nova leitura dentro da faixa suportada.",
  },
  degraded: {
    tone: "degraded",
    title: "Análise degradada",
    statement:
      "O diagnóstico saiu, mas uma dependência indisponível impediu a prescrição.",
    nextStep:
      "Repita a análise mais tarde e não trate o diagnóstico como prescrição enquanto isso.",
  },
});

const ABSTENTION_LABELS = Object.freeze({
  undocumented_fault: "Falha sem documentação aprovada",
  out_of_distribution: "Entrada fora da distribuição",
  dependency_unavailable: "Dependência indisponível",
});

const PRIORITY_LABELS = Object.freeze({
  routine: "Rotina",
  scheduled: "Programada",
  urgent: "Urgente",
});

const SUPPORT_LABELS = Object.freeze({
  sufficient: "Suficiente",
  insufficient: "Insuficiente",
});

const FAILURE_COPY = Object.freeze({
  authentication: {
    title: "Autenticação necessária",
    statement: "A sessão não existe mais ou foi recusada antes de produzir resultado.",
    nextStep: "Entre novamente. O painel não repetirá esta análise automaticamente.",
  },
  input: {
    title: "A análise não foi enviada",
    statement: "A entrada não passou pela validação local do contrato v1.",
    nextStep: "Corrija os campos indicados e execute a análise de novo.",
  },
  network: {
    title: "A API não respondeu",
    statement: "O navegador não conseguiu falar com a API de análise.",
    nextStep: "Confirme que a API está em execução e execute a análise de novo.",
  },
  timeout: {
    title: "A análise excedeu o tempo limite",
    statement:
      "O painel encerrou a espera local. Isso não confirma que o processamento remoto foi cancelado.",
    nextStep:
      "Confira a saúde da API antes de tentar novamente e descarte qualquer resposta tardia desta execução.",
  },
  validation: {
    title: "A API recusou a requisição",
    statement: "A entrada não passou na validação do contrato v1.",
    nextStep: "Corrija os campos apontados abaixo e execute a análise de novo.",
  },
  unavailable: {
    title: "A API não produziu resultado seguro",
    statement: "O modelo está indisponível e nenhuma prescrição foi emitida.",
    nextStep: "Aguarde e execute a análise de novo. Não use este pedido como diagnóstico.",
  },
  unexpected: {
    title: "Resposta inesperada da API",
    statement: "A API respondeu com um status fora do contrato v1.",
    nextStep: "Registre o status recebido e verifique a versão da API publicada.",
  },
  malformed: {
    title: "Resposta fora do contrato",
    statement: "O corpo devolvido pela API não corresponde ao contrato v1.",
    nextStep: "Verifique se a API publicada é a v1 e execute a análise de novo.",
  },
  offline: {
    title: "A entrada não corresponde a uma fixture offline",
    statement:
      "O modo offline não inferiu nem inventou um desfecho para a leitura alterada.",
    nextStep:
      "Carregue um dos cinco exemplos sintéticos do contrato e execute a análise de novo.",
  },
});

export interface PrescriptionView {
  state: string;
  heading: string;
  explanation: string;
  summary: string | null;
  priority: string | null;
  priorityLabel: string | null;
  actions: readonly string[];
}

export interface ReportView {
  kind: "result" | "failure";
  tone: string;
  outcome: string | null;
  title: string;
  statement: string;
  nextStep: string;
  identifiers: readonly { label: string; value: string }[];
  diagnosis: { code: string; summary: string } | null;
  support: { level: string; label: string; score: number; note: string } | null;
  abstention: { reason: string; label: string; message: string } | null;
  prescription: PrescriptionView;
  citations: readonly Citation[];
  neighbors: readonly OpaqueNeighbor[];
  neighborNote: string;
  warnings: readonly AnalysisWarning[];
  integrity: readonly string[];
  issues: readonly { label: string; code: string }[];
}

function outcomeContract(outcome: string): OutcomeContract | undefined {
  return ANALYSIS_OUTCOMES.find((entry) => entry.outcome === outcome);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/**
 * Decide whether a payload really carries a usable prescription.
 */
function resolvePrescriptionState(
  response: AnalysisResponse,
  contract: OutcomeContract,
): { state: string; integrity: readonly string[] } {
  const prescription: Prescription | null | undefined = response.prescription;
  if (!contract.prescribes) {
    if (prescription !== null && prescription !== undefined) {
      return {
        state: PRESCRIPTION_STATE.inconsistent,
        integrity: [
          `O contrato v1 não emite prescrição em ${contract.outcome}, mas o corpo trouxe uma.`,
        ],
      };
    }
    const state = contract.hasAbstention
      ? PRESCRIPTION_STATE.withheld
      : PRESCRIPTION_STATE.notApplicable;
    return { state, integrity: [] };
  }
  if (!isRecord(prescription)) {
    return {
      state: PRESCRIPTION_STATE.inconsistent,
      integrity: [
        `O contrato v1 exige prescrição em ${contract.outcome}, mas o corpo não trouxe uma.`,
      ],
    };
  }
  const priority = prescription.priority;
  const actions = prescription.actions;
  const validPriority =
    typeof priority === "string" &&
    PRESCRIPTION_PRIORITIES.includes(priority as PrescriptionPriority);
  const validActions = Array.isArray(actions) && actions.length > 0;
  if (typeof prescription.summary !== "string" || !validPriority || !validActions) {
    return {
      state: PRESCRIPTION_STATE.inconsistent,
      integrity: ["A prescrição recebida não segue o formato do contrato v1."],
    };
  }
  return { state: PRESCRIPTION_STATE.issued, integrity: [] };
}

function prescriptionCopy(
  state: string,
  outcome: string,
): { heading: string; explanation: string } {
  if (state === PRESCRIPTION_STATE.issued) {
    return {
      heading: "Prescrição emitida",
      explanation: "Emitida com documentação aprovada e citada abaixo.",
    };
  }
  if (state === PRESCRIPTION_STATE.notApplicable) {
    return {
      heading: "Prescrição não se aplica",
      explanation: "Nenhuma prescrição é emitida quando a condição é normal.",
    };
  }
  if (state === PRESCRIPTION_STATE.withheld) {
    return {
      heading: "Prescrição retida",
      explanation: `A API absteve-se de prescrever neste resultado (${outcome}).`,
    };
  }
  return {
    heading: "Prescrição indisponível",
    explanation:
      "O corpo recebido contradiz o contrato v1, então nada é apresentado como prescrição.",
  };
}

/**
 * Build the auditable report shown for one contract result.
 */
export function presentAnalysis(response: AnalysisResponse): ReportView {
  const contract = outcomeContract(response.outcome);
  if (contract === undefined) {
    return presentFailure({
      kind: "malformed",
      status: 200,
      detail: `A API devolveu o desfecho "${response.outcome}", fora do contrato v1.`,
      issues: [],
    });
  }
  const copy = OUTCOME_COPY[response.outcome];
  const resolved = resolvePrescriptionState(response, contract);
  const prescription = resolved.state === PRESCRIPTION_STATE.issued ? response.prescription : null;
  const copyForState = prescriptionCopy(resolved.state, response.outcome);
  const abstention = response.abstention;
  const support = response.support;

  return {
    kind: "result",
    tone: copy.tone,
    outcome: response.outcome,
    title: copy.title,
    statement: copy.statement,
    nextStep: copy.nextStep,
    identifiers: Object.freeze([
      { label: "Análise", value: response.analysis_id },
      { label: "Modelo", value: response.model_id },
    ]),
    diagnosis:
      response.diagnosis === null
        ? null
        : { code: response.diagnosis.code, summary: response.diagnosis.summary },
    support: {
      level: support.level,
      label: SUPPORT_LABELS[support.level],
      score: support.support_score,
      note: SUPPORT_SCORE_NOTE,
    },
    abstention:
      abstention === null
        ? null
        : {
            reason: abstention.reason,
            label: ABSTENTION_LABELS[abstention.reason],
            message: abstention.message,
          },
    prescription: {
      state: resolved.state,
      heading: copyForState.heading,
      explanation: copyForState.explanation,
      summary: prescription === null ? null : prescription.summary,
      priority: prescription === null ? null : prescription.priority,
      priorityLabel: prescription === null ? null : PRIORITY_LABELS[prescription.priority],
      actions: prescription === null ? Object.freeze([]) : prescription.actions,
    },
    citations: response.citations,
    neighbors: response.neighbors,
    neighborNote: NEIGHBOR_DISTANCE_NOTE,
    warnings: response.warnings,
    integrity: resolved.integrity,
    issues: Object.freeze([]),
  };
}

/**
 * Build the report shown when no contract result was obtained.
 */
export function presentFailure(failure: AnalysisFailure): ReportView {
  const copy = FAILURE_COPY[failure.kind];
  const detail = failure.detail === null ? [] : [failure.detail];
  const identifiers: { label: string; value: string }[] = [];
  if (failure.status !== null) {
    identifiers.push({ label: "Status HTTP", value: String(failure.status) });
  }
  return {
    kind: "failure",
    tone: "failed",
    outcome: null,
    title: copy.title,
    statement: copy.statement,
    nextStep: copy.nextStep,
    identifiers: Object.freeze(identifiers),
    diagnosis: null,
    support: null,
    abstention: null,
    prescription: {
      state: PRESCRIPTION_STATE.inconsistent,
      heading: "Prescrição indisponível",
      explanation: "Nenhum resultado do contrato v1 foi obtido nesta execução.",
      summary: null,
      priority: null,
      priorityLabel: null,
      actions: Object.freeze([]),
    },
    citations: Object.freeze([]),
    neighbors: Object.freeze([]),
    neighborNote: NEIGHBOR_DISTANCE_NOTE,
    warnings: Object.freeze([]),
    integrity: Object.freeze(detail),
    issues: Object.freeze(
      failure.issues.map((issue) => ({
        label: fieldLabel(issue.field),
        code: issue.code,
      })),
    ),
  };
}
