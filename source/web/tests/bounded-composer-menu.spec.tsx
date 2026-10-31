import { act, render, screen } from "@testing-library/react";
import { useRef } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useBoundedComposerMenu } from "@/components/chat/home/useBoundedComposerMenu";

function Picker() {
  const trigger = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  const layout = useBoundedComposerMenu(true, trigger, menu);
  return <div className="al-study-session-grid">
    <button ref={trigger}>Mode</button>
    <div ref={menu} role="menu" data-placement={layout.placement} style={{maxHeight:layout.maxHeight,overflowY:"auto"}}>
      {Array.from({length:30},(_,index)=><button role="menuitem" key={index}>Option {index + 1}</button>)}
    </div>
  </div>;
}

function geometry(triggerTop: number, paneTop: number, paneBottom: number) {
  vi.spyOn(HTMLElement.prototype,"getBoundingClientRect").mockImplementation(function() {
    const top = this.tagName === "BUTTON" ? triggerTop : paneTop;
    const bottom = this.tagName === "BUTTON" ? top + 32 : paneBottom;
    return {top,bottom,left:20,right:300,width:280,height:bottom-top,x:20,y:top,toJSON:()=>({})} as DOMRect;
  });
  vi.spyOn(HTMLElement.prototype,"scrollHeight","get").mockReturnValue(600);
}

describe("bounded composer mode menu", () => {
  afterEach(() => vi.unstubAllGlobals());
  it("opens below a centered composer and retains every mode in the scrollable menu", () => {
    geometry(378,136,800);
    render(<Picker />);
    expect(screen.getByRole("menu")).toHaveAttribute("data-placement","bottom");
    expect(screen.getByRole("menu")).toHaveStyle({maxHeight:"320px",overflowY:"auto"});
    expect(screen.getAllByRole("menuitem")).toHaveLength(30);
  });

  it("opens upward near the bottom and stays within the clipping pane", () => {
    geometry(700,136,800);
    render(<Picker />);
    expect(screen.getByRole("menu")).toHaveAttribute("data-placement","top");
    expect(screen.getByRole("menu")).toHaveStyle({maxHeight:"320px"});
  });

  it("shrinks and changes direction after the available pane is resized", () => {
    geometry(378,136,800);
    render(<Picker />);
    geometry(220,136,400);
    act(()=>window.dispatchEvent(new Event("resize")));
    expect(screen.getByRole("menu")).toHaveAttribute("data-placement","bottom");
    expect(screen.getByRole("menu")).toHaveStyle({maxHeight:"140px"});
  });

  it("remeasures after the toolbar wraps later than the viewport resize event", () => {
    let notifyResize = () => {};
    vi.stubGlobal("ResizeObserver", class {
      constructor(callback: () => void) { notifyResize = callback; }
      observe() {}
      disconnect() {}
    });
    geometry(280,170,600);
    render(<Picker />);
    geometry(304,170,600);
    act(()=>notifyResize());
    expect(screen.getByRole("menu")).toHaveStyle({maxHeight:"256px"});
  });
});
