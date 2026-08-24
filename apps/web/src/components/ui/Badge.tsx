import type { ReactNode } from "react";

interface BadgeProps {
  /** Outcome tone name; sets the tone tokens via data-tone. */
  tone?: string;
  /** Document status name; sets the tone tokens via data-status. */
  status?: string;
  mark?: ReactNode;
  className?: string;
  children: ReactNode;
}

/**
 * Compact tonal label. Colour comes from the tone tokens; the optional mark
 * keeps the state readable without colour.
 */
export function Badge({ tone, status, mark, className, children }: BadgeProps) {
  return (
    <span
      className={className === undefined ? "badge" : `badge ${className}`}
      data-tone={tone}
      data-status={status}
    >
      {mark}
      {children}
    </span>
  );
}
