import { useCallback, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { flushSync } from "react-dom";

import { API_CONTRACT_VERSION } from "./generated/analysis-contract.js";
import type { AnalysisRequest } from "./generated/analysis-contract.js";
import { DOCUMENT_CONTRACT_VERSION } from "./generated/document-contract.js";
import { createAnalysisClient } from "./api/analysis-client";
import type { AnalysisOutput } from "./api/analysis-client";
import { createAuthenticatedFetch } from "./api/authenticated-fetch";
import { createDocumentClient } from "./api/document-client";
import { createOfflineAnalysisClient } from "./api/offline-analysis-client";
import { createCognitoAuth } from "./auth/cognito";
import type { readAndCleanOAuthCallback } from "./auth/cognito";
import { clearPkce } from "./auth/pkce";
import { createMemorySession } from "./auth/session";
import {
  isPublishedFrontendOrigin,
  loadRuntimeConfig,
} from "./config/runtime-config";
import { AppShell } from "./components/app/AppShell";
import { AuthPanel } from "./components/app/AuthPanel";
import type { AuthState } from "./components/app/AuthPanel";
import { useStatus } from "./components/ui/StatusToaster";
import { AnalysisWorkspace } from "./features/analysis/AnalysisWorkspace";
import { DocumentsPanel } from "./features/documents/DocumentsPanel";

type OAuthCallback = ReturnType<typeof readAndCleanOAuthCallback>;
type DocumentClient = ReturnType<typeof createDocumentClient>;

const EXAMPLE_PLACEHOLDER = "escolha um exemplo sintético";

const AUTH_COPY: Record<AuthState, { status: string; detail: string }> = {
  authenticated: {
    status: "Sessão autenticada",
    detail:
      "O access token e o refresh token existem somente na memória desta página; não há renovação automática.",
  },
  required: {
    status: "Login necessário",
    detail:
      "Entre pelo Cognito para usar a API. Nenhuma requisição protegida foi enviada.",
  },
  invalid: {
    status: "Callback de login recusado",
    detail:
      "O callback expirou, não corresponde ao state iniciado ou foi recusado. Inicie um login novo.",
  },
  config: {
    status: "Configuração de publicação indisponível",
    detail:
      "O painel recusou a configuração pública. Publique novamente o runtime config canônico antes de usar a API.",
  },
};

const REVOCATION_NOTE =
  "No perfil AWS, ao sair, o painel tenta revogar o refresh token quando " +
  "disponível, mas o access token já emitido pode seguir válido até expirar.";

function blockedAnalysisOutput(): AnalysisOutput {
  return {
    ok: false,
    failure: {
      kind: "authentication",
      status: null,
      detail: null,
      issues: [],
    },
  };
}

interface AuthView {
  state: AuthState;
  status: string;
  detail: string;
}

interface Wiring {
  client: {
    requestAnalysis: (request: AnalysisRequest) => Promise<AnalysisOutput>;
  };
  documentsClient: DocumentClient | null;
  login: (() => void) | null;
  logout: (() => void) | null;
  runtimeBlocked: boolean;
}

type Bootstrap =
  | { kind: "pending" }
  | { kind: "ready"; wiring: Wiring }
  | { kind: "failed" };

/**
 * Profile resolution and wiring, ported from the previous panel's main
 * module: offline uses fixtures only, the local profile talks to the
 * same-origin proxy, and the published profile loads the runtime config and
 * runs the Cognito PKCE session. Any unexpected bootstrap failure blocks the
 * protected surfaces without sending a single API operation.
 */
export function App({ oauthCallback }: { oauthCallback: OAuthCallback }) {
  const { announce } = useStatus();
  const offline =
    new URL(window.location.href).searchParams.get("mode") === "offline";
  const source = offline ? "offline" : "online";
  const published = isPublishedFrontendOrigin(window.location.origin);
  const local = !published;

  const [bootstrap, setBootstrap] = useState<Bootstrap>({ kind: "pending" });
  const [protectedReady, setProtectedReady] = useState(false);
  const [documentsLoaded, setDocumentsLoaded] = useState(false);
  const markDocumentsLoaded = useCallback(() => {
    setDocumentsLoaded(true);
  }, []);
  const [authView, setAuthView] = useState<AuthView | null>(null);
  const [loginPending, setLoginPending] = useState(false);
  const [logoutDisabled, setLogoutDisabled] = useState(false);
  const started = useRef(false);

  useEffect(() => {
    if (started.current) {
      return;
    }
    started.current = true;

    const showAuth = (state: AuthState) => {
      setAuthView({ state, ...AUTH_COPY[state] });
    };

    const start = async () => {
      if (offline) {
        clearPkce(window.sessionStorage);
        setProtectedReady(true);
        setBootstrap({
          kind: "ready",
          wiring: {
            client: createOfflineAnalysisClient(),
            documentsClient: null,
            login: null,
            logout: null,
            runtimeBlocked: false,
          },
        });
        announce(
          `Modo offline pronto. Escolha ${EXAMPLE_PLACEHOLDER}; nenhuma chamada à API será feita.`,
        );
        return;
      }

      if (!published) {
        clearPkce(window.sessionStorage);
        setProtectedReady(true);
        setBootstrap({
          kind: "ready",
          wiring: {
            client: createAnalysisClient(),
            documentsClient: createDocumentClient(),
            login: null,
            logout: null,
            runtimeBlocked: false,
          },
        });
        announce(
          `Pronto. Escolha ${EXAMPLE_PLACEHOLDER} ou preencha as 18 features.`,
        );
        return;
      }

      const loaded = await loadRuntimeConfig();
      if (!loaded.ok) {
        clearPkce(window.sessionStorage);
        setProtectedReady(false);
        showAuth("config");
        setBootstrap({
          kind: "ready",
          wiring: {
            client: { requestAnalysis: async () => blockedAnalysisOutput() },
            documentsClient: null,
            login: null,
            logout: null,
            runtimeBlocked: true,
          },
        });
        announce(
          "Painel bloqueado: publique um runtime config válido antes de usar a API.",
          { tone: "failed" },
        );
        return;
      }

      const session = createMemorySession({
        clientId: loaded.config.cognito.clientId,
      });
      const auth = createCognitoAuth({
        config: loaded.config.cognito,
        session,
        storage: window.sessionStorage,
      });
      const callback = await auth.handleCallback(oauthCallback);
      const authenticated = callback.ok && session.isAuthenticated();
      setProtectedReady(authenticated);

      const showRequired = () => {
        setProtectedReady(false);
        showAuth("required");
      };

      const authenticatedFetch = createAuthenticatedFetch({
        apiBaseUrl: loaded.config.apiBaseUrl,
        session,
        onAuthenticationRequired: showRequired,
      });

      let pending = false;
      const login = () => {
        if (pending) {
          return;
        }
        pending = true;
        // Flush the busy state before the browser can deliver a second click:
        // one activation must never produce two authorization redirects.
        flushSync(() => {
          setLoginPending(true);
        });
        void auth.login().catch(() => {
          pending = false;
          setLoginPending(false);
          showRequired();
        });
      };
      const logout = () => {
        setLogoutDisabled(true);
        void auth.logout().catch(() => {
          showRequired();
          setLogoutDisabled(false);
        });
      };

      showAuth(authenticated ? "authenticated" : callback.ok ? "required" : "invalid");
      setBootstrap({
        kind: "ready",
        wiring: {
          client: createAnalysisClient({
            endpoint: `${loaded.config.apiBaseUrl}/analysis`,
            fetchImpl: authenticatedFetch,
          }),
          documentsClient: createDocumentClient({
            prefix: loaded.config.apiBaseUrl,
            fetchImpl: authenticatedFetch,
          }),
          login,
          logout,
          runtimeBlocked: false,
        },
      });
      announce(
        authenticated
          ? `Pronto. Escolha ${EXAMPLE_PLACEHOLDER} ou preencha as 18 features.`
          : callback.ok
            ? "Painel protegido: entre pelo Cognito antes de usar a API AWS."
            : "Painel bloqueado: o callback foi recusado. Inicie um login novo.",
        authenticated ? undefined : { tone: "withheld" },
      );
    };

    void start().catch(() => {
      setProtectedReady(false);
      setAuthView({
        state: "config",
        status: "Painel bloqueado com segurança",
        detail:
          "A inicialização não foi concluída. Recarregue a página; nenhuma operação da API foi enviada.",
      });
      setBootstrap({ kind: "failed" });
    });
  }, [announce, oauthCallback, offline, published]);

  const pendingBootstrap = bootstrap.kind === "pending";
  const wiring: Wiring | null = bootstrap.kind === "ready" ? bootstrap.wiring : null;
  const runtimeBlocked = wiring?.runtimeBlocked ?? false;
  const failed = bootstrap.kind === "failed";

  const formSurface = {
    ready: protectedReady && !pendingBootstrap && !failed,
    busy: pendingBootstrap,
  };

  const analysisClient = wiring?.client ?? {
    requestAnalysis: async () => blockedAnalysisOutput(),
  };

  let documentsContent: ReactNode;
  if (runtimeBlocked) {
    documentsContent = (
      <p className="documents-empty">
        A gestão documental foi bloqueada porque o runtime config público não é
        válido.
      </p>
    );
  } else if (failed) {
    documentsContent = (
      <p className="documents-empty">
        A gestão documental permaneceu bloqueada porque o painel não iniciou.
      </p>
    );
  } else if (pendingBootstrap) {
    documentsContent = null;
  } else if (!protectedReady && published) {
    documentsContent = (
      <p className="documents-empty">
        A gestão documental permanece bloqueada até uma autenticação válida.
      </p>
    );
  } else if (wiring !== null) {
    documentsContent = offline ? (
      <DocumentsPanel
        client={createDocumentClient()}
        offline
        announce={announce}
        onInitialLoad={markDocumentsLoaded}
      />
    ) : wiring.documentsClient !== null ? (
      <DocumentsPanel
        client={wiring.documentsClient}
        announce={announce}
        onInitialLoad={markDocumentsLoaded}
      />
    ) : null;
  } else {
    documentsContent = null;
  }

  // The documents surface stays locked until its first listing settles, so a
  // pending list is never mistaken for an empty cycle.
  const documentsMounted = documentsContent !== null && !pendingBootstrap;
  const documentsReady =
    formSurface.ready && (!documentsMounted || documentsLoaded);
  const documentsBusy =
    pendingBootstrap || (documentsMounted && !documentsLoaded);

  const modeDescription = offline
    ? "Offline ativo: somente as cinco fixtures sintéticas do contrato, sem chamadas à API. Entradas alteradas não recebem outcome inventado."
    : local
      ? "API local ativa: a leitura é enviada pela mesma origem do painel."
      : "API AWS autenticada: somente operações publicadas recebem o bearer em memória.";

  return (
    <AppShell
      mode={offline ? "offline" : "online"}
      modeDescription={modeDescription}
      onlineLabel={published ? "API AWS autenticada" : "API local"}
      contractVersion={`v${API_CONTRACT_VERSION}`}
      documentContractVersion={`v${DOCUMENT_CONTRACT_VERSION}`}
      overflowNote={published ? REVOCATION_NOTE : undefined}
      notice={
        authView === null ? null : (
          <AuthPanel
            state={authView.state}
            status={authView.status}
            detail={authView.detail}
            loginPending={loginPending}
            logoutDisabled={logoutDisabled}
            onLogin={() => {
              wiring?.login?.();
            }}
            onLogout={() => {
              wiring?.logout?.();
            }}
          />
        )
      }
      analysis={
        <AnalysisWorkspace
          source={source}
          client={analysisClient}
          login={wiring?.login ?? null}
          surface={formSurface}
          runtimeBlocked={runtimeBlocked || failed}
        />
      }
      documents={
        <div className="documents-shell">
          <header className="documents-intro">
            <p className="documents-kicker">Base de conhecimento governada</p>
            <h2 className="documents-heading" id="documents-heading">
              Gestão documental
            </h2>
            <p className="documents-intro-text">
              Sete estados do contrato, decisões humanas explícitas, vigência
              rastreável e falhas sanitizadas.
            </p>
          </header>
          <div
            id="documents-panel"
            inert={!documentsReady || undefined}
            aria-busy={documentsBusy ? "true" : "false"}
          >
            {documentsContent}
          </div>
        </div>
      }
    />
  );
}
