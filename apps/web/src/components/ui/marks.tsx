/**
 * One stroke-only mark per report tone, so the five results stay distinct for
 * readers who do not perceive the tone colour. Ported unchanged from the
 * previous panel; the text label always accompanies the mark.
 */

export type ToneName =
  | "settled"
  | "prescribed"
  | "withheld"
  | "outside"
  | "degraded"
  | "failed";

const PATHS: Readonly<Record<ToneName, readonly string[]>> = Object.freeze({
  settled: ["M4 12.5 9 17.5 20 6.5"],
  prescribed: ["M6 3h9l5 5v13H6z", "M15 3v5h5", "M9.5 13h7", "M9.5 17h4"],
  withheld: ["M6 3h9l5 5v13H6z", "M15 3v5h5", "M8 20 20 6"],
  outside: [
    "M12 12m-7 0a7 7 0 1 0 14 0a7 7 0 1 0 -14 0",
    "M12 12h.01",
    "M19.5 4.5 21 3",
  ],
  degraded: ["M4 12h5", "M13 12h2", "M19 12h1", "M12 4v3", "M12 17v3"],
  failed: ["M12 4v10", "M12 19h.01"],
});

export function ToneMark({
  tone,
  size = 24,
}: {
  tone: string;
  size?: number;
}) {
  const paths = PATHS[tone as ToneName] ?? PATHS.failed;
  return (
    <svg
      className="mark"
      viewBox="0 0 24 24"
      width={size}
      height={size}
      fill="none"
      stroke="currentColor"
      strokeWidth="1.75"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {paths.map((definition) => (
        <path key={definition} d={definition} />
      ))}
    </svg>
  );
}
