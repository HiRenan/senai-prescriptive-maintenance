import type { HTMLAttributes, Ref } from "react";

interface FocusHeadingProps extends HTMLAttributes<HTMLHeadingElement> {
  as?: "h2" | "h3" | "h4";
  ref?: Ref<HTMLHeadingElement>;
}

/**
 * Heading that can receive programmatic focus (result announcements, card
 * focus restoration) without entering the tab order.
 */
export function FocusHeading({
  as: Tag = "h3",
  ref,
  children,
  ...rest
}: FocusHeadingProps) {
  return (
    <Tag {...rest} ref={ref} tabIndex={-1}>
      {children}
    </Tag>
  );
}
