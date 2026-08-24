import { Button } from "../ui/Button";

export type AuthState = "authenticated" | "required" | "invalid" | "config";

const STATE_TONES: Record<AuthState, string> = {
  authenticated: "settled",
  required: "info",
  invalid: "outside",
  config: "failed",
};

interface AuthPanelProps {
  state: AuthState;
  status: string;
  detail: string;
  loginPending: boolean;
  logoutDisabled: boolean;
  onLogin: () => void;
  onLogout: () => void;
}

/**
 * Session panel of the published profile. Ids, live-region wiring and the
 * hidden/disabled choreography of the two buttons are part of the panel's
 * public contract.
 */
export function AuthPanel({
  state,
  status,
  detail,
  loginPending,
  logoutDisabled,
  onLogin,
  onLogout,
}: AuthPanelProps) {
  return (
    <section
      className="auth-panel"
      id="auth-panel"
      aria-labelledby="auth-status"
      data-state={state}
      data-tone={STATE_TONES[state]}
      aria-busy={loginPending ? "true" : undefined}
    >
      <div className="auth-copy">
        <p
          className="auth-status"
          id="auth-status"
          role="status"
          aria-live="polite"
          aria-atomic="true"
        >
          {loginPending ? "Abrindo login seguro" : status}
        </p>
        <p className="auth-detail" id="auth-detail">
          {detail}
        </p>
      </div>
      <div className="auth-actions">
        <Button
          variant="primary"
          id="auth-login"
          hidden={state === "authenticated" || state === "config"}
          busy={loginPending}
          onClick={onLogin}
        >
          Entrar com Cognito
        </Button>
        <Button
          id="auth-logout"
          hidden={state !== "authenticated"}
          disabled={logoutDisabled}
          onClick={onLogout}
        >
          Encerrar sessão
        </Button>
      </div>
    </section>
  );
}
