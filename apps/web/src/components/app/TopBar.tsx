import type { ReactNode } from "react";
import { Wordmark } from "./Wordmark";
import { WorkspaceNav } from "./WorkspaceNav";
import { ModeSwitch, DEFAULT_MODE_DESCRIPTION } from "./ModeSwitch";
import type { DemoMode } from "./ModeSwitch";
import { ThemeToggle } from "./ThemeToggle";
import { OverflowMenu } from "./OverflowMenu";
import type { WorkspaceName } from "./useHashWorkspace";

interface TopBarProps {
  activeWorkspace: WorkspaceName;
  mode: DemoMode;
  modeDescription?: string;
  /** "API AWS autenticada" on the published origin. */
  onlineLabel?: string;
  contractVersion: string;
  documentContractVersion: string;
  /** Authentication chip / actions (published profile only). */
  authSlot?: ReactNode;
  /** Extra caveat for the overflow panel. */
  note?: string;
}

/**
 * The single chrome bar: identity, workspace navigation, demo mode, theme,
 * and the overflow with demoted metadata. Below 64rem it wraps into stacked
 * rows so every target keeps its full hit area.
 */
export function TopBar({
  activeWorkspace,
  mode,
  modeDescription = DEFAULT_MODE_DESCRIPTION,
  onlineLabel,
  contractVersion,
  documentContractVersion,
  authSlot,
  note,
}: TopBarProps) {
  return (
    <header className="topbar">
      <div className="topbar-inner">
        <div className="topbar-brand">
          <Wordmark />
        </div>
        <WorkspaceNav active={activeWorkspace} />
        <ModeSwitch
          mode={mode}
          description={modeDescription}
          onlineLabel={onlineLabel}
        />
        <div className="topbar-tools">
          {authSlot}
          <ThemeToggle />
          <OverflowMenu
            contractVersion={contractVersion}
            documentContractVersion={documentContractVersion}
            modeDescription={modeDescription}
            note={note}
          />
        </div>
      </div>
    </header>
  );
}
