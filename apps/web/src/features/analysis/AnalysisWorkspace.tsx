import { useMemo, useRef, useState } from "react";

import {
  SYNTHETIC_ANALYSIS_EXAMPLES,
  TOP_K,
} from "../../generated/analysis-contract.js";
import type { AnalysisRequest } from "../../generated/analysis-contract.js";
import type { AnalysisOutput } from "../../api/analysis-client";
import {
  FEATURE_NAMES,
  buildAnalysisRequest,
  normalizeAnalysisField,
  requestToConsoleValues,
} from "../../core/features";
import type { FeatureName, ValidationIssue } from "../../core/features";
import { createLatestRequestController } from "../../core/latest-request";
import { presentAnalysis, presentFailure } from "../../core/presentation";
import { useStatus } from "../../components/ui/StatusToaster";
import { AnalysisConsole } from "./AnalysisConsole";
import { ReportPanel } from "./ReportPanel";
import type {
  CompletedAnalysis,
  ReportAction,
  ReportPhase,
} from "./ReportPanel";
import type { ReportSource } from "./ReportBlocks";

interface AnalysisRunner {
  requestAnalysis: (request: AnalysisRequest) => Promise<AnalysisOutput>;
}

interface AnalysisRun {
  request: AnalysisRequest;
  source: ReportSource;
}

interface AnalysisWorkspaceProps {
  source: ReportSource;
  client: AnalysisRunner;
  /** Starts a new login; null outside the published profile. */
  login: (() => void) | null;
  /** inert / aria-busy state of the protected form surface. */
  surface: { ready: boolean; busy: boolean };
  /** True when the published runtime config was refused: everything stays disabled. */
  runtimeBlocked: boolean;
}

function emptyValues(): Record<string, string> {
  const values: Record<string, string> = {};
  for (const name of FEATURE_NAMES) {
    values[name] = "";
  }
  return values;
}

function normalizeIssues(
  issues: readonly ValidationIssue[],
): ValidationIssue[] {
  return issues.map((issue) => ({
    ...issue,
    field: normalizeAnalysisField(issue.field),
  }));
}

export function focusAnalysisField(field: string): void {
  const normalized = normalizeAnalysisField(field);
  const id = normalized === "top_k" ? "top-k" : `feature-${normalized}`;
  document.getElementById(id)?.focus();
}

/**
 * The analysis workspace: input console on one side, report on the other.
 * Orchestration is a faithful port of the previous panel's main module —
 * out-of-order responses never apply, failures preserve the last valid
 * report, and every state change is announced.
 */
export function AnalysisWorkspace({
  source,
  client,
  login,
  surface,
  runtimeBlocked,
}: AnalysisWorkspaceProps) {
  const { announce } = useStatus();
  const offline = source === "offline";

  const [values, setValues] = useState<Record<string, string>>(emptyValues);
  const [topK, setTopK] = useState<string>(String(TOP_K.fallback));
  const [issues, setIssues] = useState<readonly ValidationIssue[]>([]);
  const [exampleValue, setExampleValue] = useState("");
  const [phase, setPhase] = useState<ReportPhase>({ kind: "idle" });
  const [lastValid, setLastValid] = useState<CompletedAnalysis | null>(null);
  const [focusSignal, setFocusSignal] = useState(0);
  const [runBusy, setRunBusy] = useState(false);

  const exampleOptions = useMemo(
    () =>
      SYNTHETIC_ANALYSIS_EXAMPLES.map((example) => ({
        value: example.name,
        label: offline
          ? `${presentAnalysis(example.response).title} · fixture sintética`
          : example.summary,
      })),
    [offline],
  );

  const focusReport = () => {
    setFocusSignal((tick) => tick + 1);
  };

  const submitRef = useRef<() => void>(() => {});
  const loginRef = useRef(login);
  loginRef.current = login;

  const [controller] = useState(() =>
    createLatestRequestController<AnalysisRun, AnalysisOutput>({
      onStart(run) {
        setRunBusy(true);
        setPhase({ kind: "loading", source: run.source });
        announce(
          run.source === "offline"
            ? "Preparando o resultado da fixture sintética, sem chamada de rede."
            : "Analisando a leitura enviada.",
        );
      },
      onApply(output, run) {
        if (output.ok) {
          const report = presentAnalysis(output.response);
          setLastValid({
            report,
            features: run.request.features,
            source: run.source,
            executedAt: new Date(),
          });
          setPhase({ kind: "current" });
          announce(
            `${run.source === "offline" ? "Modo offline. " : ""}${report.title}. ${report.nextStep}`,
            { tone: "settled" },
          );
          focusReport();
          return;
        }

        const report = presentFailure(output.failure);
        const fieldIssues: ValidationIssue[] = output.failure.issues
          .map((issue) => ({
            field: normalizeAnalysisField(issue.field),
            code: issue.code,
            message: `A API recusou este valor (${issue.code}). Revise o campo.`,
          }))
          .filter(
            (issue) =>
              issue.field === "top_k" ||
              FEATURE_NAMES.includes(issue.field as FeatureName),
          );

        const failWith = (action: ReportAction | null, focus: boolean) => {
          setPhase({
            kind: "failure",
            report,
            source: run.source,
            action,
            executedAt: new Date(),
          });
          if (focus) {
            focusReport();
          }
        };

        if (output.failure.kind === "validation" && fieldIssues.length > 0) {
          const first = fieldIssues[0] as ValidationIssue;
          failWith(
            {
              label: "Revisar campos",
              run: () => {
                focusAnalysisField(first.field);
              },
            },
            false,
          );
          setIssues(fieldIssues);
          focusAnalysisField(first.field);
        } else if (output.failure.kind === "offline") {
          failWith(
            {
              label: "Escolher fixture offline",
              run: () => {
                document.getElementById("example-select")?.focus();
              },
            },
            true,
          );
        } else if (output.failure.kind === "authentication" && loginRef.current !== null) {
          failWith(
            {
              label: "Entrar novamente",
              run: () => {
                loginRef.current?.();
              },
            },
            true,
          );
        } else {
          failWith(
            {
              label: "Tentar novamente",
              run: () => {
                submitRef.current();
              },
            },
            true,
          );
        }
        announce(
          `Nenhum resultado novo foi aplicado. ${report.title}. ${report.nextStep}`,
          { tone: "failed" },
        );
      },
      onFinish() {
        setRunBusy(false);
      },
    }),
  );

  const loadRequest = (request: AnalysisRequest) => {
    const consoleValues = requestToConsoleValues(request);
    setValues({ ...consoleValues.features });
    setTopK(consoleValues.topK);
    setIssues([]);
  };

  const handleSubmit = () => {
    const built = buildAnalysisRequest(values, topK);
    if (!built.ok) {
      const first = built.issues[0];
      const report = presentFailure({
        kind: "input",
        status: null,
        detail: null,
        issues: built.issues,
      });
      setPhase({
        kind: "failure",
        report,
        source,
        action:
          first === undefined
            ? null
            : {
                label: "Revisar campos",
                run: () => {
                  focusAnalysisField(first.field);
                },
              },
        executedAt: new Date(),
      });
      const normalized = normalizeIssues(built.issues);
      setIssues(normalized);
      const firstNormalized = normalized[0];
      if (firstNormalized !== undefined) {
        focusAnalysisField(firstNormalized.field);
      }
      announce(
        `A análise não foi enviada: ${built.issues.length} campo(s) precisam de correção. O último resultado válido, quando existente, foi preservado.`,
        { tone: "failed" },
      );
      return;
    }
    setIssues([]);
    void controller.run(() => client.requestAnalysis(built.request), {
      request: built.request,
      source,
    });
  };
  submitRef.current = handleSubmit;

  const handleExampleChange = (name: string) => {
    setExampleValue(name);
    const chosen = SYNTHETIC_ANALYSIS_EXAMPLES.find(
      (example) => example.name === name,
    );
    if (chosen === undefined) {
      return;
    }
    loadRequest(structuredClone(chosen.request) as AnalysisRequest);
    announce(`Exemplo sintético carregado: ${chosen.summary}.`);
  };

  const handleReset = () => {
    setValues(emptyValues());
    setTopK(String(TOP_K.fallback));
    setExampleValue("");
    setIssues([]);
    setLastValid(null);
    setPhase({ kind: "idle" });
    announce("Console limpo.");
  };

  return (
    <div className="workbench">
      <AnalysisConsole
        values={values}
        topK={topK}
        issues={issues}
        runBusy={runBusy || runtimeBlocked}
        surface={surface}
        exampleOptions={exampleOptions}
        exampleValue={exampleValue}
        onExampleChange={handleExampleChange}
        onFeatureChange={(name, value) => {
          setValues((current) => ({ ...current, [name]: value }));
        }}
        onTopKChange={setTopK}
        onLoadRequest={loadRequest}
        onSubmit={handleSubmit}
        onReset={handleReset}
        announce={announce}
      />
      <ReportPanel phase={phase} lastValid={lastValid} focusSignal={focusSignal} />
    </div>
  );
}
