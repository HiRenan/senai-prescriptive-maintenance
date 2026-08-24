import type { ReactNode } from "react";

interface EmptyStateProps {
  mark?: ReactNode;
  title: string;
  description?: ReactNode;
  action?: ReactNode;
  /** Tone for the mark disc; neutral by default. */
  tone?: string;
  className?: string;
}

/**
 * Empty and idle states teach the surface instead of leaving a bare
 * paragraph: glyph, one-line guidance, and an optional next action.
 */
export function EmptyState({
  mark,
  title,
  description,
  action,
  tone,
  className,
}: EmptyStateProps) {
  return (
    <div
      className={
        className === undefined ? "empty-state" : `empty-state ${className}`
      }
      data-tone={tone}
    >
      {mark !== undefined ? <span className="empty-state-mark">{mark}</span> : null}
      <p className="empty-state-title">{title}</p>
      {description !== undefined ? (
        <div className="empty-state-description">{description}</div>
      ) : null}
      {action !== undefined ? (
        <div className="empty-state-action">{action}</div>
      ) : null}
    </div>
  );
}
