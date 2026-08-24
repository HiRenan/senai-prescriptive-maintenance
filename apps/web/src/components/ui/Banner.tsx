import type { HTMLAttributes, ReactNode } from "react";

interface BannerProps extends HTMLAttributes<HTMLDivElement> {
  /** Tone token name: settled, withheld, failed, degraded, info, neutral… */
  tone?: string;
  title?: string;
  icon?: ReactNode;
  action?: ReactNode;
  children?: ReactNode;
}

/**
 * In-flow tonal notice: full border and wash background (never a side
 * stripe). Used for preserved-result notes, stale lists, feedback, and
 * auth notices.
 */
export function Banner({
  tone = "neutral",
  title,
  icon,
  action,
  className,
  children,
  ...rest
}: BannerProps) {
  return (
    <div
      {...rest}
      className={className === undefined ? "banner" : `banner ${className}`}
      data-tone={tone}
    >
      {icon !== undefined ? (
        <span className="banner-icon">{icon}</span>
      ) : null}
      <div className="banner-body">
        {title !== undefined ? <p className="banner-title">{title}</p> : null}
        {children}
      </div>
      {action !== undefined ? (
        <div className="banner-action">{action}</div>
      ) : null}
    </div>
  );
}
