import type { ButtonHTMLAttributes } from "react";

export type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
}

/** Single button component for all four apps; extend with variants as the design system grows. */
export function Button({ variant = "primary", style, ...rest }: ButtonProps) {
  return (
    <button
      {...rest}
      style={{
        borderRadius: "var(--comit-radius)",
        border: "none",
        padding: "10px 18px",
        cursor: "pointer",
        fontWeight: 600,
        color: variant === "primary" || variant === "danger" ? "#fff" : "var(--comit-text)",
        background:
          variant === "primary"
            ? "var(--comit-primary)"
            : variant === "danger"
              ? "var(--comit-danger)"
              : variant === "secondary"
                ? "var(--comit-accent)"
                : "transparent",
        ...style,
      }}
    />
  );
}
