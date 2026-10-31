"use client";

import { ActionButton } from "@/shared/ui/ActionButton";
import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from "react";
import { cn } from "./styles";

export type ButtonVariant = "primary" | "secondary" | "danger" | "ghost" | "soft";
export type ButtonSize = "sm" | "md" | "lg";

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  loading?: boolean;
  icon?: ReactNode;
  /** Marks a toggle-style button as active (accent fill + border). */
  selected?: boolean;
  /** Icon-only buttons should state their accessible name here or via aria-label. */
  "aria-label"?: string;
}

/*
 * A-leggo v4 button language. Replaces the legacy wax-gradient primary with
 * a terracotta primary, hairline secondary, tinted soft and transparent
 * ghost variants. Motion is a 0.985 press without vertical lift; every state
 * (hover / press / keyboard focus / selected / loading / disabled) has a
 * distinct, token-driven appearance shared by all product surfaces.
 */
const base =
  "al-btn inline-flex select-none items-center justify-center gap-2 whitespace-nowrap font-semibold tracking-[0.01em] " +
  "transition-[background-color,color,border-color,box-shadow,transform,opacity] duration-[var(--motion-fast)] ease-[cubic-bezier(0.22,1,0.36,1)] " +
  "focus-visible:outline-none focus-visible:ring-[2px] focus-visible:ring-[var(--al-ring)] focus-visible:ring-offset-1 focus-visible:ring-offset-[var(--al-btn-ring-offset,transparent)] " +
  "disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-45 active:scale-[0.985]";

const variants: Record<ButtonVariant, string> = {
  primary:
    "bg-[var(--al-accent)] text-[var(--al-accent-contrast)] shadow-[var(--al-shadow-btn)] " +
    "hover:bg-[var(--al-accent-strong)] active:bg-[var(--al-accent-press)]",
  secondary:
    "border border-[var(--al-border-strong)] bg-[var(--al-surface)] text-[var(--al-text)] " +
    "hover:bg-[var(--al-hover)] hover:border-[var(--al-accent-border)] active:bg-[var(--al-active)]",
  soft:
    "bg-[var(--al-accent-soft)] text-[var(--al-accent-text)] border border-[var(--al-accent-border)] " +
    "hover:bg-[var(--al-accent-soft-strong)] active:bg-[var(--al-accent-soft-strong)]",
  danger:
    "bg-[var(--al-danger)] text-[#fff] shadow-[var(--al-shadow-btn)] hover:bg-[var(--al-danger-strong)]",
  ghost:
    "bg-transparent text-[var(--al-text-muted)] hover:bg-[var(--al-hover)] hover:text-[var(--al-text)]",
};

const selectedStyle =
  "!bg-[var(--al-accent-soft)] !text-[var(--al-accent-text)] !border-[var(--al-accent-border)] " +
  "shadow-[inset_0_0_0_1px_var(--al-accent-border)] hover:!bg-[var(--al-accent-soft-strong)]";

const sizes: Record<ButtonSize, string> = {
  sm: "min-h-[28px] rounded-[9px] px-2.5 text-[12px]",
  md: "min-h-[34px] rounded-[11px] px-3.5 text-[13px]",
  lg: "min-h-[40px] rounded-[13px] px-5 text-[14px]",
};

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  function Button(
    {
      variant = "primary",
      size = "md",
      loading = false,
      icon,
      selected = false,
      children,
      className,
      disabled,
      type = "button",
      ...props
    },
    ref,
  ) {
    return (
      <ActionButton
        data-al-tone={variant === "ghost" ? "quiet" : variant === "soft" ? "secondary" : variant}
        ref={ref}
        type={type}
        className={cn(
          base,
          variants[variant],
          sizes[size],
          selected && selectedStyle,
          className,
        )}
        disabled={disabled || loading}
        aria-busy={loading || undefined}
        aria-pressed={selected || undefined}
        {...props}
      >
        {loading ? (
          <span className="al-spinner" aria-hidden />
        ) : (
          icon
        )}
        {children}
      </ActionButton>
    );
  },
);

export default Button;
