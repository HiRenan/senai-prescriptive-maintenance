import assert from "node:assert/strict";
import test from "node:test";

import { presentAnalysis, presentFailure } from "../src/core/presentation.js";
import { createReportView } from "../src/ui/report-view.js";
import { requestExample, responseExample } from "./helpers/contract-fixtures.js";
import { installFakeDom } from "./helpers/fake-dom.js";

test("loading e erro mantêm o último laudo válido claramente anterior", () => {
  const restore = installFakeDom();
  try {
    const host = document.createElement("section");
    const view = createReportView(host);
    view.showReport(
      presentAnalysis(responseExample("normal")),
      requestExample("normal").features,
      "online",
    );

    view.showLoading("online");
    assert.match(host.textContent, /Nova análise em andamento/);
    assert.match(host.textContent, /Resultado anterior preservado/);
    assert.match(host.textContent, /Condição normal/);

    view.showFailure(
      presentFailure({
        kind: "network",
        status: null,
        detail: null,
        issues: [],
      }),
      "online",
    );
    assert.match(host.textContent, /A API não respondeu/);
    assert.match(host.textContent, /Resultado anterior preservado/);
    assert.match(host.textContent, /Condição normal/);
  } finally {
    restore();
  }
});

test("um resultado novo substitui o anterior e registra a origem offline", () => {
  const restore = installFakeDom();
  try {
    const host = document.createElement("section");
    const view = createReportView(host);
    view.showReport(
      presentAnalysis(responseExample("normal")),
      requestExample("normal").features,
      "online",
    );
    view.showReport(
      presentAnalysis(responseExample("degraded")),
      requestExample("degraded").features,
      "offline",
    );

    assert.match(host.textContent, /Análise degradada/);
    assert.match(host.textContent, /Fixture sintética offline/);
    assert.doesNotMatch(host.textContent, /Resultado anterior preservado/);
    assert.doesNotMatch(host.textContent, /Condição normal/);
  } finally {
    restore();
  }
});
