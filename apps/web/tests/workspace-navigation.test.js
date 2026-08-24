import assert from "node:assert/strict";
import test from "node:test";

import { startWorkspaceNavigation } from "../src/ui/workspace-navigation.js";
import { installFakeDom } from "./helpers/fake-dom.js";

function historyBrowser(initialHash) {
  const listeners = [];
  const entries = [initialHash];
  let position = 0;
  const browser = {
    location: { hash: initialHash },
    addEventListener(type, listener) {
      if (type === "hashchange") {
        listeners.push(listener);
      }
    },
    visit(hash) {
      entries.splice(position + 1);
      entries.push(hash);
      position += 1;
      this.location.hash = hash;
      listeners.forEach((listener) => listener());
    },
    back() {
      position = Math.max(0, position - 1);
      this.location.hash = entries[position];
      listeners.forEach((listener) => listener());
    },
    forward() {
      position = Math.min(entries.length - 1, position + 1);
      this.location.hash = entries[position];
      listeners.forEach((listener) => listener());
    },
  };
  return browser;
}

test("skip-link preserva Documentos e back/forward restaura só hashes de área", () => {
  const restore = installFakeDom();
  try {
    const elements = new Map(
      [
        "analysis",
        "documents",
        "analysis-navigation",
        "documents-navigation",
        "workspace-content",
      ].map((id) => [id, document.createElement(id === "workspace-content" ? "main" : "div")]),
    );
    const browser = historyBrowser("");
    startWorkspaceNavigation({
      browser,
      findElement: (id) => elements.get(id),
    });

    browser.visit("#documents");
    assert.equal(elements.get("analysis").hasAttribute("hidden"), true);
    assert.equal(elements.get("documents").hasAttribute("hidden"), false);
    assert.equal(
      elements.get("documents-navigation").getAttribute("aria-current"),
      "page",
    );

    browser.visit("#workspace-content");
    elements.get("workspace-content").focus();
    assert.equal(elements.get("documents").hasAttribute("hidden"), false);
    assert.equal(document.activeElement, elements.get("workspace-content"));

    browser.back();
    assert.equal(browser.location.hash, "#documents");
    assert.equal(elements.get("documents").hasAttribute("hidden"), false);
    assert.equal(document.activeElement, elements.get("documents"));

    browser.back();
    assert.equal(browser.location.hash, "");
    assert.equal(elements.get("analysis").hasAttribute("hidden"), false);
    assert.equal(document.activeElement, elements.get("analysis"));

    browser.forward();
    assert.equal(browser.location.hash, "#documents");
    assert.equal(elements.get("documents").hasAttribute("hidden"), false);

    browser.forward();
    elements.get("workspace-content").focus();
    assert.equal(browser.location.hash, "#workspace-content");
    assert.equal(elements.get("documents").hasAttribute("hidden"), false);
    assert.equal(document.activeElement, elements.get("workspace-content"));
  } finally {
    restore();
  }
});
