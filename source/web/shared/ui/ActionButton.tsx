"use client";

import { forwardRef, type ButtonHTMLAttributes } from "react";

export interface ActionButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  "data-al-tone"?: "primary" | "secondary" | "quiet" | "danger";
}

/** One visual/state contract for owned controls, without changing their actions. */
export const ActionButton = forwardRef<HTMLButtonElement, ActionButtonProps>(function ActionButton(
  { className = "", children, "data-al-tone": explicitTone, ...props }, ref,
) {
  const tone = explicitTone ?? (
    /danger|destructive|research-stop|bg-red-|text-red-/.test(className) ? "danger" :
    /research-primary|research-new-project|al-btn-primary|al-sidebar-primary|bg-primary(?:\s|\/|$)|bg-\[var\(--(?:al-accent|primary|wax)\)\]|bg-(?:blue|indigo)-[567]/.test(className) ? "primary" :
    /ghost|text-button|icon-button|al-sidebar|menu-item|research-project-card|bg-transparent|border-0/.test(className) ? "quiet" : "secondary"
  );
  return <button ref={ref} className={`al-action ${className}`} data-al-tone={tone} {...props}>{children}</button>;
});
