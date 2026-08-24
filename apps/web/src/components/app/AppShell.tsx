import type { ReactNode } from "react";
import { TopBar } from "./TopBar";
import { Footer } from "./Footer";
import type { DemoMode } from "./ModeSwitch";
import { useHashWorkspace } from "./useHashWorkspace";

interface AppShellProps {
  mode: DemoMode;
  modeDescription?: string;
  /** "API AWS autenticada" on the published origin. */
  onlineLabel?: string;
  contractVersion: string;
  documentContractVersion: string;
  authSlot?: ReactNode;
  /** Overflow caveat (e.g. the published-profile token revocation note). */
  overflowNote?: string;
  footerNote?: string;
  /** Global notice above the workspaces (e.g. the auth panel). */
  notice?: ReactNode;
  analysis: ReactNode;
  documents: ReactNode;
}

/**
 * Page skeleton shared by every profile: skip link, top bar, the two
 * workspace sections (both stay mounted; the inactive one is hidden so form
 * state survives switching), and the colophon. Section ids, tabindex and
 * labelling are part of the panel's public contract.
 */
export function AppShell({
  mode,
  modeDescription,
  onlineLabel,
  contractVersion,
  documentContractVersion,
  authSlot,
  overflowNote,
  footerNote,
  notice,
  analysis,
  documents,
}: AppShellProps) {
  const activeWorkspace = useHashWorkspace();
  return (
    <>
      <a className="skip-link" href="#workspace-content">
        Ir para o conteúdo
      </a>
      <TopBar
        activeWorkspace={activeWorkspace}
        mode={mode}
        modeDescription={modeDescription}
        onlineLabel={onlineLabel}
        contractVersion={contractVersion}
        documentContractVersion={documentContractVersion}
        authSlot={authSlot}
        note={overflowNote}
      />
      <main id="workspace-content" tabIndex={-1}>
        {notice}
        <section
          className="workspace-page"
          id="analysis"
          data-workspace="analysis"
          aria-labelledby="analysis-navigation"
          tabIndex={-1}
          hidden={activeWorkspace !== "analysis"}
        >
          {analysis}
        </section>
        <section
          className="workspace-page"
          id="documents"
          data-workspace="documents"
          aria-labelledby="documents-navigation documents-heading"
          tabIndex={-1}
          hidden={activeWorkspace !== "documents"}
        >
          {documents}
        </section>
      </main>
      <Footer note={footerNote} />
    </>
  );
}
