import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import AnimatedGreeting from "@/components/chat/home/AnimatedGreeting";

describe("animated study greeting", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(2026, 9, 4, 9, 0));
  });
  afterEach(() => vi.useRealTimers());

  it("keeps the initial welcome stable for ten minutes and then rotates without repeating", () => {
    render(<AnimatedGreeting />);
    expect(screen.getByRole("heading")).toHaveTextContent("今天，想聊哪一道题？");
    act(() => vi.advanceTimersByTime(599999));
    expect(screen.getByRole("heading")).toHaveTextContent("今天，想聊哪一道题？");
    act(() => vi.advanceTimersByTime(1));
    expect(screen.getByRole("heading")).toHaveTextContent("早上好，今天想学点什么？");
    act(() => vi.advanceTimersByTime(600000));
    expect(screen.getByRole("heading")).toHaveTextContent("一起把今天的问题弄明白。");
  });

  it("uses the local evening bucket and retains the translation adapter", () => {
    vi.setSystemTime(new Date(2026, 9, 4, 19, 0));
    render(<AnimatedGreeting translate={(value) => `translated: ${value}`} />);
    act(() => vi.advanceTimersByTime(600000));
    expect(screen.getByRole("heading")).toHaveTextContent("translated: 晚上好，今天想学点什么？");
  });

  it("stops the presentation timer when the welcome is unmounted", () => {
    const { unmount } = render(<AnimatedGreeting />);
    expect(vi.getTimerCount()).toBe(1);
    unmount();
    expect(vi.getTimerCount()).toBe(0);
  });
});
