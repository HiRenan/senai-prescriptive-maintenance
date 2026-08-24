import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import type { ReactNode } from "react";
import { Check, CircleAlert, Info, TriangleAlert, X } from "lucide-react";

export type AnnounceTone = "info" | "settled" | "withheld" | "failed";

export interface AnnounceOptions {
  tone?: AnnounceTone;
  /** Milliseconds before auto-dismiss; defaults by tone. */
  duration?: number;
}

export interface StatusAnnouncer {
  announce: (message: string, options?: AnnounceOptions) => void;
}

interface ToastEntry {
  id: number;
  message: string;
  tone: AnnounceTone;
  duration: number;
}

const MAX_VISIBLE_TOASTS = 3;
const DEFAULT_DURATION = 6000;
const ATTENTION_DURATION = 10000;

const TONE_ICONS: Record<AnnounceTone, typeof Info> = {
  info: Info,
  settled: Check,
  withheld: TriangleAlert,
  failed: CircleAlert,
};

const StatusContext = createContext<StatusAnnouncer | null>(null);

export function useStatus(): StatusAnnouncer {
  const context = useContext(StatusContext);
  if (context === null) {
    throw new Error("useStatus requires a StatusProvider ancestor.");
  }
  return context;
}

function Toast({
  toast,
  onDismiss,
}: {
  toast: ToastEntry;
  onDismiss: (id: number) => void;
}) {
  const [paused, setPaused] = useState(false);

  useEffect(() => {
    if (paused) {
      return undefined;
    }
    const timer = setTimeout(() => {
      onDismiss(toast.id);
    }, toast.duration);
    return () => {
      clearTimeout(timer);
    };
  }, [paused, toast, onDismiss]);

  const Icon = TONE_ICONS[toast.tone];
  return (
    <div
      className="toast"
      data-tone={toast.tone === "info" ? "info" : toast.tone}
      onMouseEnter={() => {
        setPaused(true);
      }}
      onMouseLeave={() => {
        setPaused(false);
      }}
      onFocus={() => {
        setPaused(true);
      }}
      onBlur={() => {
        setPaused(false);
      }}
    >
      <span className="toast-icon">
        <Icon size={16} aria-hidden />
      </span>
      <p className="toast-message">{toast.message}</p>
      <button
        type="button"
        className="toast-dismiss"
        aria-label="Dispensar aviso"
        onClick={() => {
          onDismiss(toast.id);
        }}
      >
        <X size={16} aria-hidden />
      </button>
    </div>
  );
}

/**
 * Single announcement channel for the app.
 *
 * Screen readers hear the visually hidden `#run-status` polite region (the
 * pre-existing announcement contract); sighted users see the transient toast
 * stack. The stack carries no live-region semantics so messages are never
 * announced twice, but it stays in the accessibility tree so the dismiss
 * buttons remain keyboard-reachable.
 */
export function StatusProvider({ children }: { children: ReactNode }) {
  const [liveMessage, setLiveMessage] = useState("");
  const [toasts, setToasts] = useState<readonly ToastEntry[]>([]);
  const nextId = useRef(1);

  const dismiss = useCallback((id: number) => {
    setToasts((current) => current.filter((toast) => toast.id !== id));
  }, []);

  const announce = useCallback((message: string, options?: AnnounceOptions) => {
    const tone = options?.tone ?? "info";
    const duration =
      options?.duration ??
      (tone === "failed" || tone === "withheld"
        ? ATTENTION_DURATION
        : DEFAULT_DURATION);
    setLiveMessage(message);
    setToasts((current) => {
      const entry: ToastEntry = {
        id: nextId.current,
        message,
        tone,
        duration,
      };
      nextId.current += 1;
      return [...current.slice(-(MAX_VISIBLE_TOASTS - 1)), entry];
    });
  }, []);

  const value = useMemo<StatusAnnouncer>(() => ({ announce }), [announce]);

  return (
    <StatusContext.Provider value={value}>
      {children}
      <p
        id="run-status"
        className="visually-hidden"
        role="status"
        aria-live="polite"
      >
        {liveMessage}
      </p>
      <div className="toaster">
        {toasts.map((toast) => (
          <Toast key={toast.id} toast={toast} onDismiss={dismiss} />
        ))}
      </div>
    </StatusContext.Provider>
  );
}
