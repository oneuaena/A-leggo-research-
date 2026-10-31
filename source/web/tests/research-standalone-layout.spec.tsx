import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import RootLayout from "@/app/layout";
import RichCodeBlock from "@/components/common/RichCodeBlock";

describe("standalone research layout", () => {
  it("provides the context required to render a research answer with a code block", () => {
    vi.stubGlobal("matchMedia", () => ({ matches: false }));
    const html = renderToStaticMarkup(
      <RootLayout><RichCodeBlock raw={"x = 2\nprint(x)"} lang="python" /></RootLayout>,
    );
    expect(html).toContain("md-code-block");
    expect(html).toContain("python");
    vi.unstubAllGlobals();
  });
});
