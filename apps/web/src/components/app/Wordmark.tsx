/**
 * Product identity in the top bar: the pulse-trace glyph (same drawing as the
 * favicon) beside the product name, which is also the page's single h1.
 */
export function Wordmark() {
  return (
    <div className="wordmark">
      <span className="wordmark-glyph" aria-hidden="true">
        <svg
          viewBox="0 0 24 24"
          width={16}
          height={16}
          fill="none"
          stroke="currentColor"
          strokeWidth={2}
          strokeLinecap="round"
          strokeLinejoin="round"
          focusable="false"
        >
          <path d="M4 12h4l2.5-5.5 3 11 2.5-5.5h4" />
        </svg>
      </span>
      <h1 className="wordmark-title">Manutenção prescritiva</h1>
    </div>
  );
}
