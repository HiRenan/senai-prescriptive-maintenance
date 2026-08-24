import { Moon, Sun } from "lucide-react";
import { Button } from "../ui/Button";
import { useTheme } from "./useTheme";

/**
 * One-tap switch between light and dark. The label always names the action,
 * not the current state.
 */
export function ThemeToggle() {
  const { theme, setTheme } = useTheme();
  const dark = theme === "dark";
  return (
    <Button
      variant="ghost"
      iconOnly
      aria-label={dark ? "Ativar tema claro" : "Ativar tema escuro"}
      onClick={() => {
        setTheme(dark ? "light" : "dark");
      }}
    >
      {dark ? <Sun size={18} aria-hidden /> : <Moon size={18} aria-hidden />}
    </Button>
  );
}
