export type DemoMode = "online" | "offline";

export const DEFAULT_MODE_DESCRIPTION =
  "A API local processa a leitura pela mesma origem do painel.";

/**
 * Segmented switch between the same-origin API and the synthetic offline
 * profile. Both targets are real navigations (the offline profile is chosen
 * by query string), so these stay links, with the ids, hrefs and description
 * wiring of the previous panel.
 */
export function ModeSwitch({
  mode,
  description = DEFAULT_MODE_DESCRIPTION,
  onlineLabel = "API local",
}: {
  mode: DemoMode;
  description?: string;
  /** "API AWS autenticada" on the published origin. */
  onlineLabel?: string;
}) {
  return (
    <nav className="mode-switch" aria-label="Modo da demonstração">
      <div className="mode-links">
        <a
          className="mode-link"
          id="online-mode"
          href="./#analysis"
          aria-current={mode === "online" ? "page" : undefined}
          aria-describedby="mode-description"
        >
          {onlineLabel}
        </a>
        <a
          className="mode-link"
          id="offline-mode"
          href="?mode=offline#analysis"
          aria-current={mode === "offline" ? "page" : undefined}
          aria-describedby="mode-description"
        >
          Offline sintético
        </a>
      </div>
      <p className="visually-hidden" id="mode-description">
        {description}
      </p>
    </nav>
  );
}
