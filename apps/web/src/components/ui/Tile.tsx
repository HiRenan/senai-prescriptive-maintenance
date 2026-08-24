import type { ReactNode } from "react";

interface TileProps {
  label: string;
  value: ReactNode;
  mono?: boolean;
  className?: string;
}

/**
 * Quiet label/value pair for metadata rows. Deliberately not a hero metric:
 * small label above, plain value below.
 */
export function Tile({ label, value, mono = false, className }: TileProps) {
  return (
    <div className={className === undefined ? "tile" : `tile ${className}`}>
      <dt className="tile-label">{label}</dt>
      <dd className={mono ? "tile-value mono" : "tile-value"}>{value}</dd>
    </div>
  );
}

export function TileRow({
  label,
  className,
  children,
}: {
  label: string;
  className?: string;
  children: ReactNode;
}) {
  return (
    <dl
      className={className === undefined ? "tile-row" : `tile-row ${className}`}
      aria-label={label}
    >
      {children}
    </dl>
  );
}
