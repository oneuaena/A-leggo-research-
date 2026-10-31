import test from "node:test";
import assert from "node:assert/strict";

import type { ResearchThreadItem } from "../lib/research-api";
import {
  THREAD_INTENTS, formatElapsed, groupThreadItems, mergeThreadItems, queueLabelFor, stabilizeRows, toolGroupSummary,
} from "../lib/research-thread";

const base = (id: string, at: string) => ({ id, cursor: `${at}|${id}`, createdAt: at });
const user = (id: string, at: string): ResearchThreadItem => ({ ...base(id, at), kind: "user", intervention: "human_note", text: id, truncated: false, branch: "", queueId: "" });
const tool = (id: string, at: string, ok = true): ResearchThreadItem => ({ ...base(id, at), kind: "tool", tool: "python", status: "VERIFIED", ok, input: "", inputTruncated: false, output: "4", outputTruncated: false, elapsedMs: 1200 });
const answer = (id: string, at: string): ResearchThreadItem => ({ ...base(id, at), kind: "answer", title: "t", role: "explorer", status: "x", text: "hi", truncated: false, artifactId: id });

test("merging keeps chronological order, dedupes, and keeps identity when nothing is new", () => {
  const current = [user("a", "2026-01-01T00:00:01"), user("b", "2026-01-01T00:00:03")];
  const same = mergeThreadItems(current, [current[1], current[0]]);
  assert.equal(same, current, "no new rows must return the same array so React skips rendering");
  const merged = mergeThreadItems(current, [answer("c", "2026-01-01T00:00:04"), user("old", "2026-01-01T00:00:00")]);
  assert.deepEqual(merged.map((item) => item.id), ["old", "a", "b", "c"]);
  assert.equal(merged[1], current[0], "existing rows keep their object identity");
});

test("consecutive real tool calls collapse into one row; other kinds break the run", () => {
  const rows = groupThreadItems([user("u", "1"), tool("t1", "2"), tool("t2", "3"), answer("a", "4"), tool("t3", "5")]);
  assert.deepEqual(rows.map((row) => row.type === "tools" ? `tools:${row.items.length}` : row.item.kind), ["user", "tools:2", "answer", "tools:1"]);
});

test("stabilizing rows reuses unchanged row objects and replaces grown tool groups", () => {
  const first = groupThreadItems([user("u", "1"), tool("t1", "2")]);
  const previous = new Map(first.map((row) => [row.key, row]));
  const items = [first[0].type === "item" ? first[0].item : user("u", "1"), first[1].type === "tools" ? first[1].items[0] : tool("t1", "2")];
  const same = stabilizeRows(previous, groupThreadItems(items));
  assert.equal(same[0], first[0]);
  assert.equal(same[1], first[1]);
  const grown = stabilizeRows(previous, groupThreadItems([...items, tool("t2", "3")]));
  assert.equal(grown[0], first[0]);
  assert.notEqual(grown[1], first[1]);
  assert.equal(grown[1].type === "tools" ? grown[1].items.length : 0, 2);
});

test("labels and intent map are consistent", () => {
  assert.equal(toolGroupSummary([{ ok: true }, { ok: true }, { ok: false }]), "已运行 3 个工具调用 · 1 个未成功");
  assert.equal(toolGroupSummary([{ ok: true }]), "已运行 1 个工具调用");
  assert.equal(formatElapsed(0), "");
  assert.equal(formatElapsed(350), "350 ms");
  assert.equal(formatElapsed(1200), "1.2 s");
  assert.equal(formatElapsed(125000), "2 min 5 s");
  assert.equal(THREAD_INTENTS[0].kind, "discuss");
  assert.equal(THREAD_INTENTS.find((intent) => intent.id === "note")?.kind, "human_note");
  assert.equal(THREAD_INTENTS.find((intent) => intent.id === "direction")?.kind, "add_human_idea");
  assert.ok(THREAD_INTENTS.filter((intent) => !["discuss", "note", "direction"].includes(intent.id)).every((intent) => intent.kind.startsWith("request_")));
  assert.equal(new Set(THREAD_INTENTS.map((intent) => intent.id)).size, THREAD_INTENTS.length);
  assert.equal(queueLabelFor("discuss", "RUNNING"), "AI 思考中…");
  assert.equal(queueLabelFor("discuss", "COMPLETED"), "");
  assert.equal(queueLabelFor("discuss", "FAILED"), "回复失败，可重新发送");
  assert.equal(queueLabelFor("request_proof", "QUEUED"), "已排队，等待下一个安全步骤");
  assert.equal(queueLabelFor("request_proof", ""), "");
});
