import type { ReactNode } from "react";

export interface FieldAria {
  inputId: string;
  describedBy: string;
  errorId: string;
  hintId: string | null;
  invalid: boolean;
}

interface FieldProps {
  id: string;
  label: string;
  hint?: string;
  /** Current validation message; empty string or null means valid. */
  error?: string | null;
  /** Extra attributes for the error slot (e.g. data-error-for). */
  errorData?: Record<string, string>;
  className?: string;
  children: (aria: FieldAria) => ReactNode;
}

/**
 * Label, hint, and error wiring for one control. The error slot is always
 * rendered so `aria-describedby` stays stable; the message appears only when
 * invalid, exactly like the previous panel's per-field slots.
 */
export function Field({
  id,
  label,
  hint,
  error,
  errorData,
  className,
  children,
}: FieldProps) {
  const hintId = hint === undefined ? null : `${id}-hint`;
  const errorId = `${id}-error`;
  const invalid = typeof error === "string" && error.length > 0;
  const describedBy =
    hintId === null ? errorId : `${hintId} ${errorId}`;
  return (
    <div className={className === undefined ? "field" : `field ${className}`}>
      <label className="field-label" htmlFor={id}>
        {label}
      </label>
      {children({ inputId: id, describedBy, errorId, hintId, invalid })}
      {hintId !== null ? (
        <p className="field-hint" id={hintId}>
          {hint}
        </p>
      ) : null}
      <p className="field-error" id={errorId} {...errorData} hidden={!invalid}>
        {invalid ? error : ""}
      </p>
    </div>
  );
}
