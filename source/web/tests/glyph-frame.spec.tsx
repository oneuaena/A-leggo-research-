import React, { createRef } from "react";
import { fireEvent, render } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { X } from "@/shared/ui/DismissGlyph";
import { Paperclip } from "@/shared/ui/glyphs";

it("keeps SVG refs, labels, sizing and click handlers through both catalogue and critical glyphs", () => {
  const dismissRef = createRef<SVGSVGElement>();
  const attachRef = createRef<SVGSVGElement>();
  const clicked = vi.fn();
  render(<><X ref={dismissRef} aria-label="关闭" size={18} onClick={clicked} /><Paperclip ref={attachRef} aria-label="附件" size={24} /></>);
  expect(dismissRef.current).toHaveAttribute("data-aleggo-glyph", "X");
  expect(dismissRef.current).toHaveAttribute("width", "18");
  expect(dismissRef.current).not.toHaveAttribute("aria-hidden");
  expect(attachRef.current).toHaveAttribute("data-aleggo-glyph", "Paperclip");
  fireEvent.click(dismissRef.current!);
  expect(clicked).toHaveBeenCalledTimes(1);
});
