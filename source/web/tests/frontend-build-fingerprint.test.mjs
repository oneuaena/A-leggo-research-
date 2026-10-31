import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { sourceFingerprint, writeBuildReceipt, checkBuildReceipt } from "../scripts/build-fingerprint.mjs";

function fixture(t) {
  const root = mkdtempSync(path.join(os.tmpdir(), "aleggo-build-identity-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  mkdirSync(path.join(root, "features"));
  mkdirSync(path.join(root, ".next-aleggo/standalone/.next-aleggo"), { recursive: true });
  writeFileSync(path.join(root, "features/research.tsx"), "actual research files UI");
  writeFileSync(path.join(root, "next.config.js"), "256 MiB");
  writeFileSync(path.join(root, ".next-aleggo/BUILD_ID"), "current-build");
  writeFileSync(path.join(root, ".next-aleggo/standalone/.next-aleggo/BUILD_ID"), "current-build");
  writeFileSync(path.join(root, ".next-aleggo/standalone/server.js"), "current config");
  return root;
}

test("current frontend receipt validates both build identities and source contents", t => {
  const root = fixture(t);
  const receipt = writeBuildReceipt(root, ".next-aleggo", sourceFingerprint(root));
  assert.deepEqual(checkBuildReceipt(root, ".next-aleggo"), receipt);
});
test("source edits and newly added inputs invalidate an old successful build", t => {
  const root = fixture(t);
  writeBuildReceipt(root, ".next-aleggo", sourceFingerprint(root));
  writeFileSync(path.join(root, "features/new-feature.tsx"), "new file preview");
  assert.throws(() => checkBuildReceipt(root, ".next-aleggo"), /stale/);
});
test("a source edit during build prevents generating a trusted receipt", t => {
  const root = fixture(t), before = sourceFingerprint(root);
  writeFileSync(path.join(root, "next.config.js"), "new cap");
  assert.throws(() => writeBuildReceipt(root, ".next-aleggo", before), /changed during build/);
});
test("stale nested standalone manifests and changed server are rejected", t => {
  const root = fixture(t);
  writeBuildReceipt(root, ".next-aleggo", sourceFingerprint(root));
  const nested = path.join(root, ".next-aleggo/standalone/.next-aleggo/BUILD_ID");
  writeFileSync(nested, "older-build");
  assert.throws(() => checkBuildReceipt(root, ".next-aleggo"), /identity changed/);
  writeFileSync(nested, "current-build");
  writeFileSync(path.join(root, ".next-aleggo/standalone/server.js"), "old cap");
  assert.throws(() => checkBuildReceipt(root, ".next-aleggo"), /server changed/);
});
test("desktop skip-build checks the receipt before sidecar packaging", () => {
  const script = readFileSync(new URL("../../desktop/build-windows.ps1", import.meta.url), "utf8");
  assert.ok(script.indexOf('build-fingerprint.mjs') < script.indexOf('& $Python (Join-Path $PSScriptRoot "rebuild_sidecar.py")'));
});
