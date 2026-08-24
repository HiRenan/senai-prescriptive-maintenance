import { useEffect, useRef, useState } from "react";

import {
  REJECT_REASON_FIELD,
  documentFieldLabel,
} from "../../core/document-registration";
import type {
  DocumentAction,
  DocumentView,
} from "../../core/document-presentation";
import { Button } from "../../components/ui/Button";
import { Card } from "../../components/ui/Card";
import { Badge } from "../../components/ui/Badge";
import { DocumentMark } from "../../components/ui/document-marks";

interface ConfirmRegionProps {
  view: DocumentView;
  action: DocumentAction;
  disabled: boolean;
  reasonError: string | null;
  onConfirm: (reason: string) => void;
  onCancel: () => void;
  onReasonInput: () => void;
}

function ConfirmRegion({
  view,
  action,
  disabled,
  reasonError,
  onConfirm,
  onCancel,
  onReasonInput,
}: ConfirmRegionProps) {
  const [reason, setReason] = useState("");
  const reasonRef = useRef<HTMLTextAreaElement>(null);
  const confirmRef = useRef<HTMLButtonElement>(null);
  const reasonId = `document-reason-${view.documentId}`;
  const invalid = reasonError !== null && reasonError.length > 0;

  useEffect(() => {
    if (action.needsReason) {
      reasonRef.current?.focus();
    } else {
      confirmRef.current?.focus();
    }
  }, [action.needsReason]);

  return (
    <div
      className="document-confirm"
      role="group"
      aria-label={`${action.label}: confirmação`}
      data-confirming={view.documentId}
      // Escape leaves the confirmation exactly like the cancel button, so the
      // command is never one stray keystroke away from being sent.
      onKeyDown={(event) => {
        if (!disabled && event.key === "Escape") {
          event.preventDefault();
          onCancel();
        }
      }}
    >
      <p className="document-confirm-text">{action.confirmation}</p>
      {action.needsReason ? (
        <div className="field">
          <label className="field-label" htmlFor={reasonId}>
            {documentFieldLabel(REJECT_REASON_FIELD.name)}
          </label>
          <textarea
            id={reasonId}
            ref={reasonRef}
            className="textarea"
            rows={2}
            spellCheck={false}
            aria-describedby={`${reasonId}-error`}
            aria-invalid={invalid || undefined}
            data-reason={view.documentId}
            disabled={disabled}
            value={reason}
            onChange={(event) => {
              setReason(event.target.value);
              onReasonInput();
            }}
          />
          <p
            className="field-error"
            id={`${reasonId}-error`}
            data-error-for="reason"
            hidden={!invalid}
          >
            {invalid ? reasonError : ""}
          </p>
        </div>
      ) : null}
      <div className="run-actions">
        <Button
          ref={confirmRef}
          variant={action.name === "reject" ? "danger" : "primary"}
          size="sm"
          data-confirm={view.documentId}
          data-action={action.name}
          disabled={disabled}
          onClick={() => {
            onConfirm(reason);
          }}
        >
          {action.confirmLabel}
        </Button>
        <Button
          size="sm"
          data-cancel={view.documentId}
          disabled={disabled}
          onClick={onCancel}
        >
          Cancelar
        </Button>
      </div>
    </div>
  );
}

function Fact({
  label,
  value,
  note,
}: {
  label: string;
  value: string;
  note: string | null;
}) {
  return (
    <div className="document-fact">
      <dt>{label}</dt>
      <dd className="mono" title={value}>
        {value}
      </dd>
      {note === null ? null : <dd className="document-fact-note">{note}</dd>}
    </div>
  );
}

interface DocumentCardProps {
  view: DocumentView;
  confirming: DocumentAction | null;
  disabled: boolean;
  reasonError: string | null;
  onRead: () => void;
  onStartAction: (action: DocumentAction) => void;
  onConfirm: (reason: string) => void;
  onCancel: () => void;
  onReasonInput: () => void;
}

/**
 * One document in the cycle: state disc and badge, the contract facts, the
 * human decision or sanitized failure, and only the commands its current
 * state accepts.
 */
export function DocumentCard({
  view,
  confirming,
  disabled,
  reasonError,
  onRead,
  onStartAction,
  onConfirm,
  onCancel,
  onReasonInput,
}: DocumentCardProps) {
  return (
    <Card
      as="article"
      className="document-card"
      data-tone={view.tone}
      data-status={view.status}
      data-card={view.documentId}
    >
      <header className="document-head">
        <span className="document-mark">
          <DocumentMark status={view.status} size={20} />
        </span>
        <h4
          className="document-title"
          tabIndex={-1}
          data-card-heading={view.documentId}
          title={view.filename}
        >
          {view.filename}
        </h4>
        <Badge
          className="document-state"
          status={view.status}
          mark={<DocumentMark status={view.status} size={14} />}
        >
          {view.statusLabel}
        </Badge>
      </header>
      <p className="document-statement">{view.statement}</p>
      <dl className="document-facts">
        <Fact label="Identificador" value={view.documentId} note={null} />
        <Fact label="Última atualização" value={view.updatedAt} note={null} />
        <Fact
          label={view.currency.label}
          value={view.currency.supersededBy ?? "—"}
          note={view.currency.explanation}
        />
        {view.metadata.map((entry) => (
          <Fact
            key={entry.label}
            label={entry.label}
            value={entry.value}
            note={entry.note}
          />
        ))}
      </dl>
      {view.decision === null ? null : (
        <div className="document-decision">
          <p className="block-label">{view.decision.label}</p>
          <p>{view.decision.text}</p>
        </div>
      )}
      {view.failure === null ? null : (
        <div className="document-failure" data-tone="failed">
          <p className="block-label">Falha do processamento</p>
          <p className="mono">{view.failure.code}</p>
          <p>{view.failure.message}</p>
        </div>
      )}
      <div className="document-commands">
        <Button
          size="sm"
          data-read={view.documentId}
          disabled={disabled || confirming !== null}
          onClick={onRead}
        >
          Ver estado atual
        </Button>
        {confirming !== null ? (
          <ConfirmRegion
            view={view}
            action={confirming}
            disabled={disabled}
            reasonError={reasonError}
            onConfirm={onConfirm}
            onCancel={onCancel}
            onReasonInput={onReasonInput}
          />
        ) : view.actions.length === 0 ? (
          <p className="document-actions-note">{view.actionsNote}</p>
        ) : (
          <div
            className="document-actions"
            role="group"
            aria-label="Comandos do ciclo"
          >
            {view.actions.map((action) => (
              <Button
                key={action.name}
                size="sm"
                variant={action.name === "reject" ? "danger" : "quiet"}
                data-action={action.name}
                data-document={view.documentId}
                disabled={disabled}
                onClick={() => {
                  onStartAction(action);
                }}
              >
                {action.label}
              </Button>
            ))}
          </div>
        )}
      </div>
    </Card>
  );
}
