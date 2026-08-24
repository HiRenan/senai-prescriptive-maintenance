// @vitest-environment happy-dom
import assert from "node:assert/strict";
import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, beforeEach, test } from "vitest";

import { AppShell } from "../../src/components/app/AppShell";

afterEach(cleanup);

beforeEach(() => {
  window.location.hash = "";
});

function renderShell() {
  return render(
    <AppShell
      mode="online"
      contractVersion="v1"
      documentContractVersion="v1"
      analysis={<p>Área de análise</p>}
      documents={
        <>
          <h2 id="documents-heading">Gestão documental</h2>
          <p>Área documental</p>
        </>
      }
    />,
  );
}

function visit(hash: string) {
  window.location.hash = hash;
  fireEvent(window, new Event("hashchange"));
}

function section(id: string): HTMLElement {
  const element = document.getElementById(id);
  assert.ok(element instanceof HTMLElement, `seção ausente: ${id}`);
  return element;
}

test("skip-link preserva Documentos e back/forward restaura só hashes de área", () => {
  renderShell();
  assert.equal(section("analysis").hasAttribute("hidden"), false);
  assert.equal(section("documents").hasAttribute("hidden"), true);

  visit("#documents");
  assert.equal(section("analysis").hasAttribute("hidden"), true);
  assert.equal(section("documents").hasAttribute("hidden"), false);
  assert.equal(
    section("documents-navigation").getAttribute("aria-current"),
    "page",
  );
  assert.equal(document.activeElement, section("documents"));

  // Internal fragment (the skip-link target): the active area and the native
  // focus must stay untouched.
  visit("#workspace-content");
  section("workspace-content").focus();
  assert.equal(section("documents").hasAttribute("hidden"), false);
  assert.equal(document.activeElement, section("workspace-content"));

  visit("#documents");
  assert.equal(section("documents").hasAttribute("hidden"), false);
  assert.equal(document.activeElement, section("documents"));

  visit("");
  assert.equal(section("analysis").hasAttribute("hidden"), false);
  assert.equal(section("documents").hasAttribute("hidden"), true);
  assert.equal(document.activeElement, section("analysis"));
});

test("um hash desconhecido no carregamento inicial cai na análise", () => {
  window.location.hash = "#desconhecido";
  renderShell();
  assert.equal(section("analysis").hasAttribute("hidden"), false);
  assert.equal(
    section("analysis-navigation").getAttribute("aria-current"),
    "page",
  );
});
