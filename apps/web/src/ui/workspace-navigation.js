import { requireElement } from "./dom.js";

const WORKSPACES = Object.freeze(["analysis", "documents"]);

/**
 * Keep both operational areas in one shell while preserving native fragments,
 * browser history and keyboard navigation.
 *
 * Only the two navigation fragments own workspace state. Internal fragments,
 * including the skip-link target, must leave the active area and native focus
 * untouched.
 *
 * @param {object} [options]
 * @param {Window} [options.browser]
 * @param {(id: string) => HTMLElement} [options.findElement]
 * @returns {void}
 */
export function startWorkspaceNavigation(options = {}) {
  const browser = options.browser ?? window;
  const findElement = options.findElement ?? requireElement;
  /** @type {string | null} */
  let activeWorkspace = null;

  /**
   * @param {boolean} moveFocus
   * @returns {void}
   */
  function activate(moveFocus) {
    const requested = browser.location.hash.slice(1);
    if (requested === "") {
      activeWorkspace = "analysis";
    } else if (!WORKSPACES.includes(requested)) {
      if (activeWorkspace !== null) {
        return;
      }
      activeWorkspace = "analysis";
    } else {
      activeWorkspace = requested;
    }

    for (const name of WORKSPACES) {
      const page = findElement(name);
      const link = findElement(`${name}-navigation`);
      const selected = name === activeWorkspace;
      page.toggleAttribute("hidden", !selected);
      if (selected) {
        link.setAttribute("aria-current", "page");
      } else {
        link.removeAttribute("aria-current");
      }
      if (selected && moveFocus) {
        page.focus();
      }
    }
  }

  browser.addEventListener("hashchange", () => activate(true));
  activate(false);
}
