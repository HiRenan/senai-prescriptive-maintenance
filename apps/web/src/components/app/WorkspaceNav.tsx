import type { WorkspaceName } from "./useHashWorkspace";

const LINKS: readonly { workspace: WorkspaceName; label: string }[] = [
  { workspace: "analysis", label: "Análise" },
  { workspace: "documents", label: "Documentos" },
];

/**
 * The operational areas. Link ids and data attributes are part of the
 * panel's public contract (labelling, tests, deep links).
 */
export function WorkspaceNav({ active }: { active: WorkspaceName }) {
  return (
    <nav className="workspace-nav" aria-label="Áreas da demonstração">
      {LINKS.map(({ workspace, label }) => (
        <a
          key={workspace}
          className="workspace-link"
          id={`${workspace}-navigation`}
          href={`#${workspace}`}
          data-workspace-link={workspace}
          aria-current={active === workspace ? "page" : undefined}
        >
          {label}
        </a>
      ))}
    </nav>
  );
}
