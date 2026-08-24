import type { ReactNode } from "react";
import { ChevronDown } from "lucide-react";

interface DisclosureProps {
  summary: ReactNode;
  defaultOpen?: boolean;
  className?: string;
  children: ReactNode;
}

/**
 * Progressive disclosure over the native <details> element, used to layer
 * dense contractual copy without deleting it.
 */
export function Disclosure({
  summary,
  defaultOpen = false,
  className,
  children,
}: DisclosureProps) {
  return (
    <details
      className={
        className === undefined ? "disclosure" : `disclosure ${className}`
      }
      open={defaultOpen || undefined}
    >
      <summary className="disclosure-summary">
        <ChevronDown className="disclosure-chevron" size={16} aria-hidden />
        <span>{summary}</span>
      </summary>
      <div className="disclosure-body">{children}</div>
    </details>
  );
}
