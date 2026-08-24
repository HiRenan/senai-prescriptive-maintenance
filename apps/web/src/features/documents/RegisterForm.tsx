import { SYNTHETIC_DOCUMENT_EXAMPLES } from "../../generated/document-contract.js";
import {
  REGISTER_INPUTS,
  REGISTER_MEDIA_TYPE,
  documentFieldHint,
  documentFieldLabel,
} from "../../core/document-registration";
import type { ValidationIssue } from "../../core/features";
import { Button } from "../../components/ui/Button";
import { Card } from "../../components/ui/Card";
import { Field } from "../../components/ui/Field";

const REGISTER_NOTE =
  "Nenhum arquivo é lido nem enviado por esta tela. Informe os metadados ou carregue " +
  "um exemplo sintético do contrato, usado apenas para demonstração offline.";

const EXAMPLE_PLACEHOLDER = "Selecione um exemplo";

interface RegisterFormProps {
  values: Readonly<Record<string, string>>;
  issues: readonly ValidationIssue[];
  disabled: boolean;
  exampleValue: string;
  onExampleChange: (name: string) => void;
  onFieldChange: (name: string, value: string) => void;
  onSubmit: () => void;
}

/**
 * Registration of the four contract metadata fields. The API never receives
 * the PDF itself, so the form says so and offers only synthetic examples.
 */
export function RegisterForm({
  values,
  issues,
  disabled,
  exampleValue,
  onExampleChange,
  onFieldChange,
  onSubmit,
}: RegisterFormProps) {
  const errorFor = (field: string): string | null => {
    const issue = issues.find((entry) => entry.field === field);
    return issue === undefined ? null : issue.message;
  };

  return (
    <Card as="section" className="register" aria-labelledby="register-heading">
      <form
        id="document-register-form"
        noValidate
        onSubmit={(event) => {
          event.preventDefault();
          onSubmit();
        }}
      >
        <h3 className="block-label" id="register-heading">
          Registrar metadados de um PDF
        </h3>
        <p className="documents-note">{REGISTER_NOTE}</p>

        <Field id="document-example" label="Exemplo sintético do contrato">
          {(aria) => (
            <select
              id={aria.inputId}
              className="select"
              aria-describedby={aria.describedBy}
              disabled={disabled}
              value={exampleValue}
              onChange={(event) => {
                onExampleChange(event.target.value);
              }}
            >
              <option value="">{EXAMPLE_PLACEHOLDER}</option>
              {SYNTHETIC_DOCUMENT_EXAMPLES.map((example) => (
                <option key={example.name} value={example.name}>
                  {example.summary}
                </option>
              ))}
            </select>
          )}
        </Field>

        <div className="document-fields">
          {REGISTER_INPUTS.map((field) => {
            const hint = documentFieldHint(field.name);
            return (
              <Field
                key={field.name}
                id={`document-${field.name}`}
                label={documentFieldLabel(field.name)}
                hint={hint ?? undefined}
                error={errorFor(field.name)}
                errorData={{ "data-error-for": field.name }}
              >
                {(aria) => (
                  <input
                    id={aria.inputId}
                    name={field.name}
                    className="input mono"
                    type="text"
                    inputMode={field.node.kind === "integer" ? "numeric" : "text"}
                    autoComplete="off"
                    spellCheck={false}
                    data-register={field.name}
                    aria-describedby={aria.describedBy}
                    aria-invalid={aria.invalid || undefined}
                    disabled={disabled}
                    value={values[field.name] ?? ""}
                    onChange={(event) => {
                      onFieldChange(field.name, event.target.value);
                    }}
                  />
                )}
              </Field>
            );
          })}
        </div>

        <p className="documents-note">
          {`${documentFieldLabel(REGISTER_MEDIA_TYPE.name)}: ${REGISTER_MEDIA_TYPE.value}, fixado pelo contrato v1.`}
        </p>
        <div className="run-actions">
          <Button type="submit" variant="primary" disabled={disabled}>
            Registrar metadados
          </Button>
        </div>
      </form>
    </Card>
  );
}
