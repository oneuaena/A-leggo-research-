"use client";

import { ActionButton } from "@/shared/ui/ActionButton";
import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from "react";
import { Tooltip } from "./Tooltip";
import { cn } from "./styles";

export interface IconButtonProps extends Omit<
  ButtonHTMLAttributes<HTMLButtonElement>,
  "children"
> {
  label: string;
  icon: ReactNode;
  size?: "sm" | "md" | "lg";
  variant?: "ghost" | "secondary" | "primary" | "soft";
  selected?: boolean;
  tooltipSide?: "top" | "right" | "bottom" | "left";
}

const sizes = {
  sm: "h-7 w-7 rounded-[9px]",
  md: "h-8 w-8 rounded-[10px]",
  lg: "h-10 w-10 rounded-[12px]",
};

const variants = {
  ghost: "bg-transparent text-[var(--al-text-muted)] hover:bg-[var(--al-hover)] hover:text-[var(--al-text)]",
  secondary:
    "border border-[var(--al-border-strong)] bg-[var(--al-surface)] text-[var(--al-text)] hover:bg-[var(--al-hover)]",
  primary: "bg-[var(--al-accent)] text-[var(--al-accent-contrast)] hover:bg-[var(--al-accent-strong)]",
  soft:
    "border border-[var(--al-accent-border)] bg-[var(--al-accent-soft)] text-[var(--al-accent-text)] hover:bg-[var(--al-accent-soft-strong)]",
};

const selectedStyle =
  "!border-[var(--al-accent-border)] !bg-[var(--al-accent-soft)] !text-[var(--al-accent-text)]";

export const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(
  function IconButton(
    {
      label,
      icon,
      size = "md",
      variant = "ghost",
      selected = false,
      tooltipSide = "bottom",
      className,
      type = "button",
      ...props
    },
    ref,
  ) {
    return (
      <Tooltip label={label} side={tooltipSide} disabled={props.disabled}>
        <ActionButton
          data-al-tone={variant === "ghost" ? "quiet" : variant === "soft" ? "secondary" : variant}
          ref={ref}
          type={type}
          aria-label={label}
          aria-pressed={selected || undefined}
          className={cn(
            "al-btn inline-flex shrink-0 items-center justify-center transition-[background-color,color,border-color,transform] duration-[var(--motion-fast)] active:scale-[0.96]",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--al-ring)]",
            "disabled:pointer-events-none disabled:opacity-45",
            sizes[size],
            variants[variant],
            selected && selectedStyle,
            className,
          )}
          {...props}
        >
          {icon}
        </ActionButton>
      </Tooltip>
    );
  },
);
