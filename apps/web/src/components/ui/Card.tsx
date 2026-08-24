import type { HTMLAttributes } from "react";

interface CardProps extends HTMLAttributes<HTMLElement> {
  as?: "section" | "article" | "div" | "aside";
  padding?: "md" | "lg" | "none";
  raised?: boolean;
}

/**
 * The only elevated surface vocabulary: raised background, subtle border, and
 * a level-1 shadow. Nesting cards is not allowed by design.
 */
export function Card({
  as: Tag = "div",
  padding = "md",
  raised = true,
  className,
  children,
  ...rest
}: CardProps) {
  const classes = ["card"];
  if (padding !== "none") {
    classes.push(`card-pad-${padding}`);
  }
  if (!raised) {
    classes.push("card-flat");
  }
  if (className !== undefined) {
    classes.push(className);
  }
  return (
    <Tag {...rest} className={classes.join(" ")}>
      {children}
    </Tag>
  );
}
