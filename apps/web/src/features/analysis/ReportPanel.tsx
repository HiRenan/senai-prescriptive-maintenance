import { useEffect, useRef } from "react";
import type { ReactNode } from "react";

import { API_CONTRACT_VERSION } from "../../generated/analysis-contract.js";
import type { AnalysisFeatures } from "../../generated/analysis-contract.js";
import type { ReportView } from "../../core/presentation";
import { Button } from "../../components/ui/Button";
import { Skeleton } from "../../components/ui/Skeleton";
import { NextStepCallout, ReportBlocks } from "./ReportBlocks";
import type { ReportSource } from "./ReportBlocks";

export interface ReportAction {
  label: string;
  run: () => void;
}

export interface CompletedAnalysis {
  report: ReportView;
  features: AnalysisFeatures;
  source: ReportSource;
  executedAt: Date;
}

export type ReportPhase =
  | { kind: "idle" }
  | { kind: "loading"; source: ReportSource }
  | { kind: "current" }
  | {
      kind: "failure";
      report: ReportView;
      source: ReportSource;
      action: ReportAction | null;
      executedAt: Date;
    };

interface ReportPanelProps {
  phase: ReportPhase;
  lastValid: CompletedAnalysis | null;
  /** Incremented by the workspace whenever the report must receive focus. */
  focusSignal: number;
}

function IdleState() {
  return (
    <div className="report-idle">
      <span className="report-idle-mark" aria-hidden="true">
        <svg
          viewBox="0 0 24 24"
          width={20}
          height={20}
          fill="none"
          stroke="currentColor"
          strokeWidth={1.75}
          strokeLinecap="round"
          strokeLinejoin="round"
          focusable="false"
        >
          <path d="M4 12h4l2.5-5.5 3 11 2.5-5.5h4" />
        </svg>
      </span>
      <p className="verdict-kicker overline">{`Contrato v${API_CONTRACT_VERSION}`}</p>
      <h2 className="report-idle-title" id="report-heading">
        Nenhuma análise executada
      </h2>
      <p className="report-idle-text">
        Preencha as 18 features do contrato ou carregue um exemplo sintético e
        execute a análise. O resultado aparece aqui com diagnóstico, suporte,
        vizinhos, citações e a disponibilidade da prescrição.
      </p>
    </div>
  );
}

function LoadingState({ source }: { source: ReportSource }) {
  return (
    <div className="report-loading">
      <p className="verdict-kicker overline">
        {source === "offline" ? "Preparando fixture offline" : "Executando"}
      </p>
      <h2 className="report-loading-title" id="report-heading">
        Analisando a leitura enviada
      </h2>
      <Skeleton lines={["85%", "100%", "55%"]} />
    </div>
  );
}

function AttemptPanel({
  kicker,
  title,
  statement,
  nextStep,
  tone,
  details,
  action,
}: {
  kicker: string;
  title: string;
  statement: string;
  nextStep: string;
  tone: "degraded" | "failed";
  details?: readonly string[];
  action?: ReportAction | null;
}) {
  return (
    <section className="report-state" data-tone={tone}>
      <p className="verdict-kicker overline">{kicker}</p>
      <h2 className="report-state-title" tabIndex={-1} data-report-focus="">
        {title}
      </h2>
      <p className="report-state-statement">{statement}</p>
      {details === undefined || details.length === 0 ? null : (
        <ul className="report-state-details">
          {details.map((detail) => (
            <li key={detail}>{detail}</li>
          ))}
        </ul>
      )}
      <NextStepCallout text={nextStep} />
      {action == null ? null : (
        <div className="report-actions">
          <Button variant="primary" onClick={action.run}>
            {action.label}
          </Button>
        </div>
      )}
    </section>
  );
}

/**
 * The report surface. State transitions mirror the previous panel exactly:
 * an idle teaser, a loading state that keeps the last concluded report
 * visible and labelled, the current report, and failures that never erase
 * the last valid result.
 */
export function ReportPanel({ phase, lastValid, focusSignal }: ReportPanelProps) {
  const rootRef = useRef<HTMLElement>(null);

  useEffect(() => {
    if (focusSignal === 0) {
      return;
    }
    const root = rootRef.current;
    if (root === null) {
      return;
    }
    const target = root.querySelector("[data-report-focus]");
    (target instanceof HTMLElement ? target : root).focus();
  }, [focusSignal]);

  let tone = "idle";
  let busy = false;
  let previous = false;
  let content: ReactNode = null;

  if (phase.kind === "idle") {
    content = <IdleState />;
  } else if (phase.kind === "loading") {
    if (lastValid !== null) {
      tone = lastValid.report.tone;
      busy = true;
      previous = true;
      content = (
        <>
          <AttemptPanel
            kicker={phase.source === "offline" ? "Modo offline" : "Nova solicitação"}
            title="Nova análise em andamento"
            statement="O laudo abaixo continua visível, mas é o último resultado concluído e não descreve a leitura em processamento."
            nextStep="Aguarde a conclusão antes de usar o novo resultado."
            tone="degraded"
          />
          <ReportBlocks
            report={lastValid.report}
            features={lastValid.features}
            source={lastValid.source}
            previous
            executedAt={lastValid.executedAt}
          />
        </>
      );
    } else {
      busy = true;
      content = <LoadingState source={phase.source} />;
    }
  } else if (phase.kind === "current") {
    if (lastValid !== null) {
      tone = lastValid.report.tone;
      content = (
        <ReportBlocks
          report={lastValid.report}
          features={lastValid.features}
          source={lastValid.source}
          previous={false}
          executedAt={lastValid.executedAt}
        />
      );
    }
  } else {
    if (lastValid !== null) {
      tone = lastValid.report.tone;
      previous = true;
      const details = [
        ...phase.report.identifiers.map(
          (entry) => `${entry.label}: ${entry.value}`,
        ),
        ...phase.report.issues.map((issue) => `${issue.label}: ${issue.code}`),
        ...phase.report.integrity,
      ];
      content = (
        <>
          <AttemptPanel
            kicker="Tentativa sem novo resultado"
            title={phase.report.title}
            statement={phase.report.statement}
            nextStep={phase.report.nextStep}
            tone="failed"
            details={details}
            action={phase.action}
          />
          <ReportBlocks
            report={lastValid.report}
            features={lastValid.features}
            source={lastValid.source}
            previous
            executedAt={lastValid.executedAt}
          />
        </>
      );
    } else {
      tone = phase.report.tone;
      content = (
        <>
          <ReportBlocks
            report={phase.report}
            features={null}
            source={phase.source}
            previous={false}
            executedAt={phase.executedAt}
          />
          {phase.action === null ? null : (
            <div className="report-actions">
              <Button variant="primary" onClick={phase.action.run}>
                {phase.action.label}
              </Button>
            </div>
          )}
        </>
      );
    }
  }

  return (
    <section
      ref={rootRef}
      className="report"
      id="report"
      aria-labelledby="report-heading"
      tabIndex={-1}
      data-tone={tone}
      aria-busy={busy ? "true" : "false"}
      data-previous={previous ? "" : undefined}
    >
      <div className="report-enter" key={`${phase.kind}:${focusSignal}`}>
        {content}
      </div>
    </section>
  );
}
