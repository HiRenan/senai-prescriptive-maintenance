import { useCallback, useEffect, useSyncExternalStore } from "react";

export type Theme = "light" | "dark";
export type ThemeSource = "user" | "system";

const THEME_STORAGE_KEY = "pm.theme";
const DARK_QUERY = "(prefers-color-scheme: dark)";

function systemTheme(): Theme {
  try {
    return window.matchMedia(DARK_QUERY).matches ? "dark" : "light";
  } catch {
    return "light";
  }
}

function appliedTheme(): Theme {
  return document.documentElement.dataset.theme === "dark" ? "dark" : "light";
}

function appliedSource(): ThemeSource {
  return document.documentElement.dataset.themeSource === "user"
    ? "user"
    : "system";
}

type Listener = () => void;

const listeners = new Set<Listener>();

function notify(): void {
  for (const listener of listeners) {
    listener();
  }
}

function apply(theme: Theme, source: ThemeSource): void {
  document.documentElement.dataset.theme = theme;
  document.documentElement.dataset.themeSource = source;
  notify();
}

function subscribe(listener: Listener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/**
 * Theme state shared across the app. The pre-paint script in
 * `public/theme-init.js` resolves the initial value; this hook only reacts to
 * user choices and, while following the system, to preference changes.
 */
export function useTheme(): {
  theme: Theme;
  source: ThemeSource;
  setTheme: (theme: Theme) => void;
  useSystemTheme: () => void;
} {
  const theme = useSyncExternalStore(subscribe, appliedTheme);
  const source = useSyncExternalStore(subscribe, appliedSource);

  useEffect(() => {
    if (source !== "system") {
      return undefined;
    }
    let media: MediaQueryList;
    try {
      media = window.matchMedia(DARK_QUERY);
    } catch {
      return undefined;
    }
    const onChange = () => {
      apply(systemTheme(), "system");
    };
    media.addEventListener("change", onChange);
    return () => {
      media.removeEventListener("change", onChange);
    };
  }, [source]);

  const setTheme = useCallback((next: Theme) => {
    try {
      window.localStorage.setItem(THEME_STORAGE_KEY, next);
    } catch {
      // Persistence is a convenience; the in-page choice still applies.
    }
    apply(next, "user");
  }, []);

  const useSystemTheme = useCallback(() => {
    try {
      window.localStorage.removeItem(THEME_STORAGE_KEY);
    } catch {
      // Ignore: storage may be unavailable.
    }
    apply(systemTheme(), "system");
  }, []);

  return { theme, source, setTheme, useSystemTheme };
}
