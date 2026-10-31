"use client";
import { Component, type ReactNode } from "react";
import MarkdownRenderer from "@/components/common/MarkdownRenderer";
import { normalizeMathText, splitJsonBlocks } from "./research-presentation";

class MathFallback extends Component<{ text: string; children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  componentDidUpdate(previous: Readonly<{ text: string; children: ReactNode }>) {
    if (previous.text !== this.props.text && this.state.failed) this.setState({ failed: false });
  }
  render() { return this.state.failed ? <code style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{this.props.text}</code> : this.props.children; }
}

export default function MathText({ text, showRaw = false }: { text: string; showRaw?: boolean }) {
  const { markdown, raw } = splitJsonBlocks(text);
  return <div className="research-math-text">
    {markdown.trim() ? <MathFallback text={markdown}><MarkdownRenderer content={normalizeMathText(markdown)} variant="compact" allowHtml={false} /></MathFallback> : null}
    {showRaw ? raw.map((value, index) => <details className="research-raw-data" key={index}>
      <summary>查看原始数据</summary><pre>{value}</pre>
    </details>) : null}
  </div>;
}
