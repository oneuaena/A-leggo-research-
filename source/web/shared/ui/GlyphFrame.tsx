import type { FunctionComponent, ReactNode, RefAttributes, SVGProps } from "react";

export interface GlyphProps extends SVGProps<SVGSVGElement> {
  size?: number | string;
  absoluteStrokeWidth?: boolean;
}
export type LucideIcon = FunctionComponent<GlyphProps & RefAttributes<SVGSVGElement>>;

/** Small universal SVG frame; critical chrome does not load the entire glyph catalogue. */
export function GlyphFrame({ aleggoName, size = 20, strokeWidth = 1.8, absoluteStrokeWidth = false, children, ref, ...props }: GlyphProps & RefAttributes<SVGSVGElement> & { aleggoName: string; children?: ReactNode }) {
  return <svg ref={ref} width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
    strokeWidth={absoluteStrokeWidth && Number(size) ? Number(strokeWidth) * 24 / Number(size) : strokeWidth}
    strokeLinecap="round" strokeLinejoin="round" aria-hidden={props["aria-label"] ? undefined : true}
    focusable="false" data-aleggo-glyph={aleggoName} {...props}>
    <rect x="20" y="2.2" width="1.8" height="1.8" rx=".3" fill="currentColor" stroke="none" opacity=".45" />
    {children}
  </svg>;
}
