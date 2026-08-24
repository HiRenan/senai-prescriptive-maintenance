// @vitest-environment happy-dom
import assert from "node:assert/strict";
import { cleanup, render } from "@testing-library/react";
import { afterEach, test } from "vitest";

import { presentAnalysis, presentFailure } from "../../src/core/presentation";
import { ReportPanel } from "../../src/features/analysis/ReportPanel";
import type {
  CompletedAnalysis,
  ReportPhase,
} from "../../src/features/analysis/ReportPanel";
import { requestExample, responseExample } from "../helpers/contract-fixtures";

afterEach(cleanup);

function completed(
  name: string,
  source: "online" | "offline",
): CompletedAnalysis {
  return {
    report: presentAnalysis(responseExample(name)),
    features: requestExample(name).features,
    source,
    executedAt: new Date("2030-01-02T03:04:05Z"),
  };
}

function panelText(container: HTMLElement): string {
  return container.textContent ?? "";
}

test("loading e erro mantêm o último laudo válido claramente anterior", () => {
  const lastValid = completed("normal", "online");
  const loading: ReportPhase = { kind: "loading", source: "online" };
  const { container, rerender } = render(
    <ReportPanel phase={loading} lastValid={lastValid} focusSignal={0} />,
  );

  assert.match(panelText(container), /Nova análise em andamento/);
  assert.match(panelText(container), /Resultado anterior preservado/);
  assert.match(panelText(container), /Condição normal/);

  const failure: ReportPhase = {
    kind: "failure",
    report: presentFailure({
      kind: "network",
      status: null,
      detail: null,
      issues: [],
    }),
    source: "online",
    action: null,
    executedAt: new Date("2030-01-02T03:05:05Z"),
  };
  rerender(
    <ReportPanel phase={failure} lastValid={lastValid} focusSignal={0} />,
  );

  assert.match(panelText(container), /A API não respondeu/);
  assert.match(panelText(container), /Resultado anterior preservado/);
  assert.match(panelText(container), /Condição normal/);
});

test("um resultado novo substitui o anterior e registra a origem offline", () => {
  const { container, rerender } = render(
    <ReportPanel
      phase={{ kind: "current" }}
      lastValid={completed("normal", "online")}
      focusSignal={1}
    />,
  );
  rerender(
    <ReportPanel
      phase={{ kind: "current" }}
      lastValid={completed("degraded", "offline")}
      focusSignal={2}
    />,
  );

  assert.match(panelText(container), /Análise degradada/);
  assert.match(panelText(container), /Fixture sintética offline/);
  assert.doesNotMatch(panelText(container), /Resultado anterior preservado/);
  assert.doesNotMatch(panelText(container), /Condição normal/);
});

test("o estado de espera e a falha expõem tom, ocupação e foco do contrato", () => {
  const lastValid = completed("normal", "online");
  const { container } = render(
    <ReportPanel
      phase={{ kind: "loading", source: "online" }}
      lastValid={lastValid}
      focusSignal={0}
    />,
  );
  const root = container.querySelector("#report");
  assert.ok(root instanceof HTMLElement);
  assert.equal(root.getAttribute("aria-busy"), "true");
  assert.equal(root.getAttribute("data-tone"), "settled");
  assert.equal(root.hasAttribute("data-previous"), true);
  assert.ok(root.querySelector("[data-report-focus]") instanceof HTMLElement);
  assert.ok(root.querySelector("#report-heading") instanceof HTMLElement);
});
