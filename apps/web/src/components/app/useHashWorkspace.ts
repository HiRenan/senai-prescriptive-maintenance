import { useEffect, useRef, useState } from "react";

export type WorkspaceName = "analysis" | "documents";

export const WORKSPACES: readonly WorkspaceName[] = Object.freeze([
  "analysis",
  "documents",
]);

function isWorkspace(value: string): value is WorkspaceName {
  return (WORKSPACES as readonly string[]).includes(value);
}

/**
 * Resolve a location hash against the current workspace. Returns null when
 * the hash must be ignored (unknown fragment while a workspace is active),
 * matching the previous panel: internal fragments such as the skip-link
 * target never change the active area.
 */
function resolveWorkspace(
  hash: string,
  current: WorkspaceName | null,
): WorkspaceName | null {
  const requested = hash.slice(1);
  if (requested === "") {
    return "analysis";
  }
  if (!isWorkspace(requested)) {
    return current === null ? "analysis" : null;
  }
  return requested;
}

/**
 * Hash-driven workspace selection with the previous panel's exact focus
 * semantics: no focus on initial resolution; on every accepted hashchange the
 * active section receives focus, including re-activations of the same
 * workspace.
 */
export function useHashWorkspace(): WorkspaceName {
  const [active, setActive] = useState<WorkspaceName>(
    () => resolveWorkspace(window.location.hash, null) ?? "analysis",
  );
  const [focusTick, setFocusTick] = useState(0);
  const activeRef = useRef(active);

  useEffect(() => {
    activeRef.current = active;
  }, [active]);

  useEffect(() => {
    const onHashChange = () => {
      const next = resolveWorkspace(window.location.hash, activeRef.current);
      if (next === null) {
        return;
      }
      setActive(next);
      setFocusTick((tick) => tick + 1);
    };
    window.addEventListener("hashchange", onHashChange);
    return () => {
      window.removeEventListener("hashchange", onHashChange);
    };
  }, []);

  useEffect(() => {
    if (focusTick === 0) {
      return;
    }
    document.getElementById(active)?.focus();
  }, [focusTick, active]);

  return active;
}
