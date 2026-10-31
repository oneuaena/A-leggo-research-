import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { Readable } from "node:stream";
import test from "node:test";

test("Next proxy retains a full permitted research package request body", async () => {
  const config = readFileSync("next.config.js", "utf8");
  const match = config.match(/proxyClientMaxBodySize:\s*(\d+)\s*\*\s*1024\s*\*\s*1024/);
  assert.ok(match);
  const cap = Number(match[1]) * 1024 * 1024;
  // 250 MB ZIP plus multipart overhead, bounded by the backend request limit.
  const requestSize = 260_000_000;
  assert.ok(cap >= requestSize && cap <= 300_000_000);
  const { getCloneableBody } = require("next/dist/server/body-streams") as { getCloneableBody: (body: Readable, cap: number) => { cloneBodyStream: () => Readable } };
  const chunk = Buffer.alloc(1024 * 1024, 65);
  function* requestChunks() {
    let remaining = requestSize;
    while (remaining) {
      const size = Math.min(remaining, chunk.length);
      yield chunk.subarray(0, size);
      remaining -= size;
    }
  }
  const source = Readable.from(requestChunks(), { objectMode: false });
  const clone = getCloneableBody(source, cap).cloneBodyStream();
  let received = 0;
  for await (const data of clone) received += (data as Buffer).length;
  assert.equal(received, requestSize);
});
