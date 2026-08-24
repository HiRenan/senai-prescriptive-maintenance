import type { CSSProperties, ReactNode } from "react";
import { History } from "lucide-react";

import type { AnalysisFeatures } from "../../generated/analysis-contract.js";
import {
  COMPARISON_DISCLAIMER,
  buildFeatureComparison,
} from "../../core/comparison";
import { PRESCRIPTION_STATE } from "../../core/presentation";
import type { ReportView } from "../../core/presentation";
import {
  formatInstant,
  formatMeasurement,
  formatScore,
  formatWithUnit,
} from "../../core/format";
import { Banner } from "../../components/ui/Banner";
import { Disclosure } from "../../components/ui/Disclosure";
import { Tile, TileRow } from "../../components/ui/Tile";
import { ToneMark } from "../../components/ui/marks";

export type ReportSource = "online" | "offline";

function ratioStyle(ratio: number): CSSProperties {
  return { "--ratio": String(ratio) } as CSSProperties;
}

function Block({
  label,
  className,
  children,
}: {
  label: string;
  className?: string;
  children: ReactNode;
}) {
  return (
    <section
      className={className === undefined ? "block" : `block ${className}`}
    >
      <h3 className="block-label">{label}</h3>
      {children}
    </section>
  );
}

export function NextStepCallout({ text }: { text: string }) {
  return (
    <div className="next-step">
      <p className="next-step-label overline">Próximo passo</p>
      <p className="next-step-text">{text}</p>
    </div>
  );
}

function VerdictHeader({
  report,
  source,
  previous,
  executedAt,
}: {
  report: ReportView;
  source: ReportSource;
  previous: boolean;
  executedAt: Date;
}) {
  const kicker =
    report.kind === "result"
      ? source === "offline"
        ? "Demonstração offline do contrato v1"
        : "Resultado do contrato v1"
      : "Nenhum resultado obtido";
  return (
    <header className="verdict">
      {previous ? (
        <Banner
          tone="neutral"
          className="previous-result"
          icon={<History size={16} aria-hidden />}
        >
          <p>Resultado anterior preservado. Ele não pertence à tentativa atual.</p>
        </Banner>
      ) : null}
      <p className="verdict-kicker overline">{kicker}</p>
      <div className="verdict-head">
        <span className="verdict-mark" data-tone={report.tone}>
          <ToneMark tone={report.tone} size={22} />
        </span>
        <h2
          className="verdict-title"
          id="report-heading"
          tabIndex={-1}
          data-report-focus=""
        >
          {report.title}
        </h2>
      </div>
      <p className="verdict-statement">{report.statement}</p>
      <TileRow label="Identificação do laudo" className="identifiers">
        {report.identifiers.map((entry) => (
          <Tile key={entry.label} label={entry.label} value={entry.value} mono />
        ))}
        <Tile
          label="Origem"
          value={source === "offline" ? "Fixture sintética offline" : "API de análise"}
        />
        <Tile label="Execução local" value={formatInstant(executedAt)} mono />
      </TileRow>
    </header>
  );
}

function PrescriptionBlock({ report }: { report: ReportView }) {
  const prescription = report.prescription;
  if (prescription.state !== PRESCRIPTION_STATE.issued) {
    return (
      <Block label="Prescrição" className="prescription">
        <div className="prescription-void">
          <p className="void-heading">{prescription.heading}</p>
          <p className="void-explanation">{prescription.explanation}</p>
        </div>
      </Block>
    );
  }
  return (
    <Block label="Prescrição" className="prescription prescription-issued">
      <div className="prescription-body" data-tone="prescribed">
        <div className="prescription-head">
          <p className="prescription-heading">{prescription.heading}</p>
          <span
            className="badge priority"
            data-tone="prescribed"
            data-priority={prescription.priority ?? undefined}
          >
            {`Prioridade: ${prescription.priorityLabel ?? ""}`}
          </span>
        </div>
        <p className="prescription-summary">{prescription.summary}</p>
        <ol className="actions">
          {prescription.actions.map((action) => (
            <li key={action}>{action}</li>
          ))}
        </ol>
      </div>
    </Block>
  );
}

function DiagnosisBlock({ report }: { report: ReportView }) {
  if (report.diagnosis === null) {
    return null;
  }
  return (
    <Block label="Diagnóstico">
      <p className="diagnosis-summary">{report.diagnosis.summary}</p>
      <p className="diagnosis-code mono">{report.diagnosis.code}</p>
    </Block>
  );
}

function SupportBlock({ report }: { report: ReportView }) {
  const support = report.support;
  if (support === null) {
    return null;
  }
  const insufficient = support.level === "insufficient";
  return (
    <Block label="Suporte">
      <div className="support-head">
        <span className="support-level">{support.label}</span>
        <span className="support-score mono">{formatScore(support.score)}</span>
      </div>
      <div
        className="gauge"
        data-insufficient={insufficient ? "true" : undefined}
      >
        <div className="gauge-track">
          <div className="gauge-fill" style={ratioStyle(support.score)} />
        </div>
        <div className="gauge-scale" aria-hidden="true">
          <span className="gauge-tick" data-at="0">
            0
          </span>
          <span className="gauge-tick" data-at="50">
            0,5
          </span>
          <span className="gauge-tick" data-at="100">
            1
          </span>
        </div>
      </div>
      <p className="footnote">{support.note}</p>
    </Block>
  );
}

function AbstentionBlock({ report }: { report: ReportView }) {
  if (report.abstention === null) {
    return null;
  }
  return (
    <Block label="Abstenção">
      <p className="abstention-label">{report.abstention.label}</p>
      <p className="abstention-message">{report.abstention.message}</p>
      <p className="abstention-reason mono">{report.abstention.reason}</p>
    </Block>
  );
}

function CitationsBlock({ report }: { report: ReportView }) {
  if (report.citations.length === 0) {
    return null;
  }
  return (
    <Block label="Citações">
      <ul className="citations">
        {report.citations.map((citation) => (
          <li
            className="citation"
            key={`${citation.document_id}:${citation.chunk}`}
          >
            <span className="citation-document mono">{citation.document_id}</span>
            <span className="citation-version mono">{citation.document_version}</span>
            <span className="citation-chunk mono">{citation.chunk}</span>
            <span className="citation-page">{`página ${citation.page_number}`}</span>
          </li>
        ))}
      </ul>
    </Block>
  );
}

function NeighborsBlock({ report }: { report: ReportView }) {
  if (report.neighbors.length === 0) {
    return null;
  }
  const widest = Math.max(
    ...report.neighbors.map((neighbor) => neighbor.distance),
  );
  return (
    <Block label="Vizinhos opacos">
      <div className="table-scroll">
        <table className="neighbors">
          <thead>
            <tr>
              <th scope="col" className="overline">
                #
              </th>
              <th scope="col" className="overline">
                Referência
              </th>
              <th scope="col" className="overline">
                Código de falha
              </th>
              <th scope="col" className="overline">
                Distância
              </th>
            </tr>
          </thead>
          <tbody>
            {report.neighbors.map((neighbor) => (
              <tr key={neighbor.rank}>
                <td className="mono">{String(neighbor.rank)}</td>
                <td className="mono">{neighbor.neighbor_ref}</td>
                <td className="mono">{neighbor.fault_code}</td>
                <td className="distance-cell">
                  <span className="mono">
                    {formatMeasurement(neighbor.distance)}
                  </span>
                  <div className="distance-bar">
                    <div
                      className="distance-fill"
                      style={ratioStyle(
                        widest === 0 ? 0 : neighbor.distance / widest,
                      )}
                    />
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="footnote">{report.neighborNote}</p>
    </Block>
  );
}

function WarningsBlock({ report }: { report: ReportView }) {
  if (report.warnings.length === 0) {
    return null;
  }
  return (
    <Block label="Avisos">
      <ul className="warnings">
        {report.warnings.map((warning) => (
          <li className="warning" key={warning.code}>
            <span className="warning-code mono">{warning.code}</span>
            <span className="warning-message">{warning.message}</span>
          </li>
        ))}
      </ul>
    </Block>
  );
}

function IssuesBlock({ report }: { report: ReportView }) {
  if (report.issues.length === 0) {
    return null;
  }
  return (
    <Block label="Campos recusados">
      <ul className="issues">
        {report.issues.map((issue) => (
          <li className="issue" key={`${issue.label}:${issue.code}`}>
            <span className="issue-label">{issue.label}</span>
            <span className="issue-code mono">{issue.code}</span>
          </li>
        ))}
      </ul>
    </Block>
  );
}

function IntegrityBlock({ report }: { report: ReportView }) {
  if (report.integrity.length === 0) {
    return null;
  }
  return (
    <Block label="Notas de integridade">
      <ul className="integrity">
        {report.integrity.map((note) => (
          <li key={note}>{note}</li>
        ))}
      </ul>
    </Block>
  );
}

function ComparisonBlock({ features }: { features: AnalysisFeatures }) {
  const comparison = buildFeatureComparison(features);
  return (
    <section className="block comparison">
      <Disclosure summary="Comparação das features enviadas">
        <div className="comparison-grid">
          {comparison.pairs.map((pair) => (
            <div className="comparison-metric" key={pair.label}>
              <div className="comparison-head">
                <span className="comparison-label">{pair.label}</span>
                {pair.unit === null ? null : (
                  <span className="comparison-unit">{pair.unit}</span>
                )}
              </div>
              <div className="comparison-bars">
                {pair.entries.map((entry) => (
                  <div className="comparison-row" key={entry.axis}>
                    <span className="comparison-axis">{entry.axis}</span>
                    <div className="comparison-track">
                      <div
                        className="comparison-fill"
                        data-negative={String(entry.negative)}
                        style={ratioStyle(entry.ratio)}
                      />
                    </div>
                    <span className="comparison-value mono">
                      {formatMeasurement(entry.value)}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
        <dl className="readings">
          {comparison.readings.map((reading) => (
            <div className="reading" key={reading.label}>
              <dt>{reading.label}</dt>
              <dd className="mono">
                {formatWithUnit(reading.value, reading.unit)}
              </dd>
            </div>
          ))}
        </dl>
      </Disclosure>
      <p className="footnote">{COMPARISON_DISCLAIMER}</p>
    </section>
  );
}

/**
 * The full report body in the previous panel's block order. `features` is
 * null only for failure reports, which have no submitted reading to compare.
 */
export function ReportBlocks({
  report,
  features,
  source,
  previous,
  executedAt,
}: {
  report: ReportView;
  features: AnalysisFeatures | null;
  source: ReportSource;
  previous: boolean;
  executedAt: Date;
}) {
  return (
    <>
      <VerdictHeader
        report={report}
        source={source}
        previous={previous}
        executedAt={executedAt}
      />
      <NextStepCallout text={report.nextStep} />
      <PrescriptionBlock report={report} />
      <DiagnosisBlock report={report} />
      <SupportBlock report={report} />
      <AbstentionBlock report={report} />
      <CitationsBlock report={report} />
      <NeighborsBlock report={report} />
      <WarningsBlock report={report} />
      <IssuesBlock report={report} />
      <IntegrityBlock report={report} />
      {features === null ? null : <ComparisonBlock features={features} />}
    </>
  );
}
