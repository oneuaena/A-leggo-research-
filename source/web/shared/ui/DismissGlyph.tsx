import { GlyphFrame, type LucideIcon } from "./GlyphFrame";

/** Loaded independently by global notifications, before any feature UI opens. */
export const X: LucideIcon = function DismissGlyph({ children, ...props }) {
  return <GlyphFrame aleggoName="X" {...props}><path d="m6 6 12 12M18 6L6 18" />{children}</GlyphFrame>;
};
