import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import "./styles/tokens.css";
import "./styles/base.css";
import "./styles/primitives.css";
import "./styles/shell.css";
import "./styles/console.css";
import "./styles/report.css";
import "./styles/documents.css";

import { readAndCleanOAuthCallback } from "./auth/cognito";
import { StatusProvider } from "./components/ui/StatusToaster";
import { App } from "./App";

// The OAuth callback must leave the address bar before anything else runs;
// React only ever sees the already-cleaned result.
const oauthCallback = readAndCleanOAuthCallback(window.location, window.history);

const container = document.getElementById("root");
if (container === null) {
  throw new Error("Elemento raiz ausente no documento.");
}
createRoot(container).render(
  <StrictMode>
    <StatusProvider>
      <App oauthCallback={oauthCallback} />
    </StatusProvider>
  </StrictMode>,
);
