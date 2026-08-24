import type { CSSProperties } from "react";

interface SkeletonProps {
  /** Width of each line as a CSS length or percentage. */
  lines?: readonly string[];
  className?: string;
}

/**
 * Loading placeholder. The shimmer collapses to static bars under reduced
 * motion via the global animation clamp.
 */
export function Skeleton({
  lines = ["70%", "100%", "45%"],
  className,
}: SkeletonProps) {
  return (
    <div
      className={className === undefined ? "skeleton" : `skeleton ${className}`}
      aria-hidden="true"
    >
      {lines.map((width, index) => (
        <span
          key={`${index}-${width}`}
          className="skeleton-line"
          style={{ "--skeleton-width": width } as CSSProperties}
        />
      ))}
    </div>
  );
}
