"use client";

import { useLayoutEffect, useState, type RefObject } from "react";

/** Keep the mode picker inside the visible conversation pane. */
export function useBoundedComposerMenu(
  open: boolean,
  triggerRef: RefObject<HTMLElement | null>,
  menuRef: RefObject<HTMLElement | null>,
) {
  const [layout, setLayout] = useState({ placement: "top" as "top" | "bottom", maxHeight: 320 });

  useLayoutEffect(() => {
    if (!open) return;
    const update = () => {
      const trigger = triggerRef.current;
      const menu = menuRef.current;
      if (!trigger || !menu) return;
      const rect = trigger.getBoundingClientRect();
      const pane = trigger.closest(".al-study-session-grid")?.getBoundingClientRect();
      const top = Math.max(0, pane?.top ?? 0);
      const bottom = Math.min(window.innerHeight, pane?.bottom ?? window.innerHeight);
      const above = Math.max(0, rect.top - top - 8);
      const below = Math.max(0, bottom - rect.bottom - 8);
      const desired = Math.min(320, menu.scrollHeight || 320);
      const placement = above >= desired || (below < desired && above >= below) ? "top" : "bottom";
      const maxHeight = Math.max(1, Math.floor(Math.min(320, placement === "top" ? above : below)));
      setLayout((previous) => previous.placement === placement && previous.maxHeight === maxHeight
        ? previous : { placement, maxHeight });
    };
    update();
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(update);
    const trigger = triggerRef.current;
    [trigger, menuRef.current, trigger?.closest(".al-study-editor-tools"),
      trigger?.closest(".al-study-session-composer"), trigger?.closest(".al-study-session-grid")]
      .forEach((element) => { if (element) observer?.observe(element); });
    window.addEventListener("resize", update);
    window.addEventListener("scroll", update, true);
    return () => {
      observer?.disconnect();
      window.removeEventListener("resize", update);
      window.removeEventListener("scroll", update, true);
    };
  }, [open, triggerRef, menuRef]);

  return layout;
}
