import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import "./styles/tokens.css";
import "./styles/base.css";

import { useTheme } from "./components/app/useTheme";

// Milestone 1 scaffold: proves the toolchain, tokens, and theme plumbing end
// to end. Replaced by the full application shell in later milestones.
function ScaffoldPreview() {
  const { theme, setTheme } = useTheme();
  return (
    <main style={{ padding: "3rem", maxWidth: "40rem", margin: "0 auto" }}>
      <h1>Manutenção Prescritiva</h1>
      <p style={{ color: "var(--text-secondary)" }}>
        Fundação do painel renovado. Tema atual: {theme}.
      </p>
      <button
        type="button"
        onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
      >
        Alternar tema
      </button>
    </main>
  );
}

const container = document.getElementById("root");
if (container === null) {
  throw new Error("Elemento raiz ausente no documento.");
}
createRoot(container).render(
  <StrictMode>
    <ScaffoldPreview />
  </StrictMode>,
);
