import { useRef, useState } from "react";
import type { CSSProperties } from "react";
import { FileUp } from "lucide-react";

import { TOP_K } from "../../generated/analysis-contract.js";
import type { AnalysisRequest } from "../../generated/analysis-contract.js";
import {
  FEATURE_PAIRS,
  SINGLE_FEATURES,
  axisLabel,
} from "../../core/features";
import type { FeatureDescriptor, ValidationIssue } from "../../core/features";
import {
  checkImportSize,
  importAnalysisRequest,
} from "../../core/request-import";
import { Button } from "../../components/ui/Button";
import { Card } from "../../components/ui/Card";
import { Disclosure } from "../../components/ui/Disclosure";
import { Field } from "../../components/ui/Field";
import type { AnnounceOptions } from "../../components/ui/StatusToaster";

interface AnalysisConsoleProps {
  values: Readonly<Record<string, string>>;
  topK: string;
  issues: readonly ValidationIssue[];
  runBusy: boolean;
  surface: { ready: boolean; busy: boolean };
  exampleOptions: readonly { value: string; label: string }[];
  exampleValue: string;
  onExampleChange: (name: string) => void;
  onFeatureChange: (name: string, value: string) => void;
  onTopKChange: (value: string) => void;
  onLoadRequest: (request: AnalysisRequest) => void;
  onSubmit: () => void;
  onReset: () => void;
  announce: (message: string, options?: AnnounceOptions) => void;
}

const UNREADABLE_FILE: ValidationIssue = {
  field: "arquivo",
  code: "unreadable_file",
  message: "O arquivo não pôde ser lido. Escolha outro arquivo JSON.",
};

function affixStyle(unit: string): CSSProperties {
  return { "--affix-width": `${Math.max(unit.length, 2) * 0.6 + 0.5}rem` } as CSSProperties;
}

function FeatureField({
  descriptor,
  label,
  value,
  error,
  disabled,
  onChange,
}: {
  descriptor: FeatureDescriptor;
  label: string;
  value: string;
  error: string | null;
  disabled: boolean;
  onChange: (value: string) => void;
}) {
  const id = `feature-${descriptor.name}`;
  return (
    <Field
      id={id}
      label={label}
      error={error}
      errorData={{ "data-error-for": descriptor.name }}
    >
      {(aria) => {
        const input = (
          <input
            id={aria.inputId}
            name={descriptor.name}
            className="input mono"
            type="text"
            inputMode="decimal"
            autoComplete="off"
            spellCheck={false}
            data-feature={descriptor.name}
            aria-describedby={aria.describedBy}
            aria-invalid={aria.invalid || undefined}
            disabled={disabled}
            value={value}
            onChange={(event) => {
              onChange(event.target.value);
            }}
          />
        );
        if (descriptor.unit === null) {
          return input;
        }
        return (
          <span className="input-affix" style={affixStyle(descriptor.unit)}>
            {input}
            <span className="input-affix-unit">{descriptor.unit}</span>
          </span>
        );
      }}
    </Field>
  );
}

/**
 * The input console: the 18 contract features grouped the way they are
 * measured, the synthetic example picker, the JSON import, and the run bar.
 * Element ids and per-field error slots are the panel's public contract.
 */
export function AnalysisConsole({
  values,
  topK,
  issues,
  runBusy,
  surface,
  exampleOptions,
  exampleValue,
  onExampleChange,
  onFeatureChange,
  onTopKChange,
  onLoadRequest,
  onSubmit,
  onReset,
  announce,
}: AnalysisConsoleProps) {
  const [importText, setImportText] = useState("");
  const [importIssues, setImportIssues] = useState<readonly ValidationIssue[]>(
    [],
  );
  const [importHeading, setImportHeading] = useState("");
  const [textInvalid, setTextInvalid] = useState(false);
  const [fileInvalid, setFileInvalid] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const errorFor = (field: string): string | null => {
    const issue = issues.find((entry) => entry.field === field);
    return issue === undefined ? null : issue.message;
  };

  const refuseImport = (
    refused: readonly ValidationIssue[],
    heading: string,
  ) => {
    setImportIssues(refused);
    setImportHeading(heading);
    announce("Importação recusada.", { tone: "failed" });
  };

  const clearImportIssues = () => {
    setImportIssues([]);
    setImportHeading("");
  };

  const applyImport = (text: string) => {
    const imported = importAnalysisRequest(text);
    if (!imported.ok) {
      refuseImport(
        imported.issues,
        "A importação foi recusada. Corrija os pontos abaixo e importe de novo.",
      );
      setTextInvalid(true);
      document.getElementById("import-text")?.focus();
      return;
    }
    onLoadRequest(imported.request);
    clearImportIssues();
    setTextInvalid(false);
    setFileInvalid(false);
    announce("JSON importado. Revise os valores e execute a análise.");
  };

  const handleFile = (input: HTMLInputElement) => {
    setFileInvalid(false);
    const file = input.files?.item(0) ?? null;
    if (file === null) {
      return;
    }
    // Refuse by the declared size first: reading the file is what would spend
    // the memory, so the check has to happen before it, not after.
    const oversized = checkImportSize(file.size);
    if (oversized !== null) {
      refuseImport(oversized.issues, "A importação foi recusada.");
      setFileInvalid(true);
      input.value = "";
      input.focus();
      return;
    }
    file
      .text()
      .then((content) => {
        setImportText(content);
        applyImport(content);
      })
      .catch(() => {
        refuseImport([UNREADABLE_FILE], "A importação foi recusada.");
        setFileInvalid(true);
        input.focus();
      });
  };

  return (
    <Card
      as="section"
      id="console"
      className="console"
      padding="none"
      aria-labelledby="console-heading"
    >
      <form
        id="analysis-form"
        noValidate
        inert={!surface.ready || undefined}
        aria-busy={surface.busy ? "true" : "false"}
        onSubmit={(event) => {
          event.preventDefault();
          onSubmit();
        }}
      >
        <div className="console-body">
          <h2 className="console-heading" id="console-heading">
            Entrada da análise
          </h2>

          <Field
            id="example-select"
            label="Exemplo sintético"
            hint="Cinco leituras do contrato público, uma por desfecho."
            className="console-example"
          >
            {(aria) => (
              <select
                id={aria.inputId}
                className="select"
                aria-describedby={aria.describedBy}
                disabled={runBusy}
                value={exampleValue}
                onChange={(event) => {
                  onExampleChange(event.target.value);
                }}
              >
                <option value="">Selecione um exemplo</option>
                {exampleOptions.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            )}
          </Field>

          <Disclosure summary="Importar JSON do contrato" className="import">
            <div className="import-rows">
              <Field id="import-text" label="JSON do contrato">
                {() => (
                  <textarea
                    id="import-text"
                    className="textarea mono import-text"
                    rows={4}
                    spellCheck={false}
                    aria-describedby="import-issues"
                    aria-invalid={textInvalid || undefined}
                    disabled={runBusy}
                    placeholder={'{"features": {"rpm": 1000, ...}, "top_k": 3}'}
                    value={importText}
                    onChange={(event) => {
                      setImportText(event.target.value);
                      setTextInvalid(false);
                      clearImportIssues();
                    }}
                  />
                )}
              </Field>
              <div className="import-actions">
                <Field id="import-file" label="Arquivo JSON">
                  {() => (
                    <input
                      id="import-file"
                      ref={fileRef}
                      className="input file"
                      type="file"
                      accept="application/json,.json"
                      aria-describedby="import-issues"
                      aria-invalid={fileInvalid || undefined}
                      disabled={runBusy}
                      onChange={(event) => {
                        handleFile(event.currentTarget);
                      }}
                    />
                  )}
                </Field>
                <Button
                  id="import-apply"
                  disabled={runBusy}
                  iconStart={<FileUp size={16} aria-hidden />}
                  onClick={() => {
                    applyImport(importText);
                  }}
                >
                  Importar JSON
                </Button>
              </div>
              <div
                className="import-issues"
                id="import-issues"
                hidden={importIssues.length === 0}
              >
                {importIssues.length === 0 ? null : (
                  <>
                    <p className="import-issues-heading">{importHeading}</p>
                    <ul className="import-issues-list">
                      {importIssues.map((issue) => (
                        <li key={`${issue.field}:${issue.code}`}>
                          <span className="mono">{issue.field}</span>
                          <span>{issue.message}</span>
                        </li>
                      ))}
                    </ul>
                  </>
                )}
              </div>
            </div>
          </Disclosure>

          <div className="metrics">
            {FEATURE_PAIRS.map((pair) => (
              <fieldset className="metric" key={pair.metric}>
                <legend className="metric-title">
                  {pair.label}
                  {pair.unit === null ? null : (
                    <span className="metric-unit">{pair.unit}</span>
                  )}
                </legend>
                <div className="metric-fields">
                  {pair.axes.map((descriptor) => (
                    <FeatureField
                      key={descriptor.name}
                      descriptor={descriptor}
                      label={
                        descriptor.axis === null
                          ? descriptor.label
                          : axisLabel(descriptor.axis)
                      }
                      value={values[descriptor.name] ?? ""}
                      error={errorFor(descriptor.name)}
                      disabled={runBusy}
                      onChange={(value) => {
                        onFeatureChange(descriptor.name, value);
                      }}
                    />
                  ))}
                </div>
              </fieldset>
            ))}
            <fieldset className="metric metric-singles">
              <legend className="metric-title">Condição do processo</legend>
              <div className="metric-fields">
                {SINGLE_FEATURES.map((descriptor) => (
                  <FeatureField
                    key={descriptor.name}
                    descriptor={descriptor}
                    label={descriptor.label}
                    value={values[descriptor.name] ?? ""}
                    error={errorFor(descriptor.name)}
                    disabled={runBusy}
                    onChange={(value) => {
                      onFeatureChange(descriptor.name, value);
                    }}
                  />
                ))}
              </div>
            </fieldset>
          </div>
        </div>

        <div className="run">
          <Field
            id="top-k"
            label="Vizinhos solicitados"
            error={errorFor("top_k")}
            errorData={{ "data-error-for": "top_k" }}
            className="field-topk"
          >
            {(aria) => (
              <input
                id={aria.inputId}
                name="top_k"
                className="input mono"
                type="number"
                inputMode="numeric"
                step={1}
                min={TOP_K.minimum}
                max={TOP_K.maximum}
                autoComplete="off"
                aria-describedby={aria.describedBy}
                aria-invalid={aria.invalid || undefined}
                disabled={runBusy}
                value={topK}
                onChange={(event) => {
                  onTopKChange(event.target.value);
                }}
              />
            )}
          </Field>
          <div className="run-actions">
            <Button
              type="submit"
              variant="primary"
              busy={runBusy}
              disabled={runBusy}
            >
              Executar análise
            </Button>
            <Button
              id="console-reset"
              disabled={runBusy}
              onClick={() => {
                setImportText("");
                clearImportIssues();
                setTextInvalid(false);
                setFileInvalid(false);
                if (fileRef.current !== null) {
                  fileRef.current.value = "";
                }
                onReset();
              }}
            >
              Limpar
            </Button>
          </div>
        </div>
      </form>
    </Card>
  );
}
