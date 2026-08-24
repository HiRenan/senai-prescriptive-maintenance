import type { ButtonHTMLAttributes, ReactNode, Ref } from "react";

export type ButtonVariant = "primary" | "quiet" | "ghost" | "danger";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: "md" | "sm";
  busy?: boolean;
  iconStart?: ReactNode;
  iconOnly?: boolean;
  ref?: Ref<HTMLButtonElement>;
}

/**
 * The single button vocabulary of the panel. `busy` keeps the label visible
 * beside an inline spinner and blocks further activation without removing the
 * control from the accessibility tree.
 */
export function Button({
  variant = "quiet",
  size = "md",
  busy = false,
  iconStart,
  iconOnly = false,
  disabled,
  children,
  type = "button",
  className,
  ...rest
}: ButtonProps) {
  const classes = ["button", `button-${variant}`];
  if (size === "sm") {
    classes.push("button-sm");
  }
  if (iconOnly) {
    classes.push("button-icon");
  }
  if (className !== undefined) {
    classes.push(className);
  }
  return (
    <button
      {...rest}
      type={type}
      className={classes.join(" ")}
      disabled={disabled === true || busy}
      aria-busy={busy || undefined}
      data-busy={busy || undefined}
    >
      {busy ? (
        <svg
          className="button-spinner"
          viewBox="0 0 16 16"
          width={14}
          height={14}
          aria-hidden="true"
          focusable="false"
        >
          <circle
            cx="8"
            cy="8"
            r="6"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeDasharray="28"
            strokeDashoffset="20"
          />
        </svg>
      ) : (
        iconStart
      )}
      {children}
    </button>
  );
}
