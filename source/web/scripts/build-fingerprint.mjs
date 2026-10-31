import { createHash } from "node:crypto";
import { existsSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const directories = ["app", "components", "context", "contracts", "features", "hooks", "i18n", "lib", "locales", "public", "scripts", "shared", "types", "vendor"];
const configs = ["next.config.js", "next-env.d.ts", "package.json", "package-lock.json", "tsconfig.json", "postcss.config.js", "postcss.config.mjs", "tailwind.config.js", "tailwind.config.ts"];

export function sourceFingerprint(root) {
  const files = [];
  const visit = (relative) => {
    for (const entry of readdirSync(path.join(root, relative), { withFileTypes: true }).sort((a, b) => a.name.localeCompare(b.name))) {
      const name = path.posix.join(relative, entry.name);
      if (entry.isSymbolicLink()) throw new Error(`Build source links must be resolved explicitly: ${name}`);
      if (entry.isDirectory()) visit(name);
      else if (entry.isFile()) files.push(name);
    }
  };
  for (const directory of directories) if (existsSync(path.join(root, directory))) visit(directory);
  for (const file of configs) if (existsSync(path.join(root, file))) files.push(file);
  const version = path.resolve(root, "../aleggo/__version__.py");
  if (existsSync(version)) files.push("../aleggo/__version__.py");
  const hash = createHash("sha256");
  for (const file of files.sort()) {
    hash.update(file).update("\0").update(readFileSync(path.resolve(root, file))).update("\0");
  }
  return { sha256: hash.digest("hex"), files: files.length };
}

export function writeBuildReceipt(root, distDir, fingerprint) {
  if (sourceFingerprint(root).sha256 !== fingerprint.sha256) throw new Error("Frontend sources changed during build; rebuild before packaging.");
  const dist = path.resolve(root, distDir);
  if (!existsSync(path.join(dist, "standalone", distDir, "BUILD_ID"))) {
    const outReceipt = { schemaVersion: 1, distDir, buildId: "static-export", ...fingerprint, serverSha256: "static-export" };
    if (existsSync(dist)) {
      writeFileSync(path.join(dist, "ALEGGO_SOURCE_FINGERPRINT.json"), JSON.stringify(outReceipt, null, 2));
    }
    return outReceipt;
  }
  const buildId = readFileSync(path.join(dist, "BUILD_ID"), "utf8").trim();
  const standaloneId = readFileSync(path.join(dist, "standalone", distDir, "BUILD_ID"), "utf8").trim();
  if (!buildId || standaloneId !== buildId) throw new Error("Standalone BUILD_ID differs from the current frontend build.");
  const serverSha256 = createHash("sha256").update(readFileSync(path.join(dist, "standalone/server.js"))).digest("hex");
  const receipt = { schemaVersion: 1, distDir, buildId, ...fingerprint, serverSha256 };
  writeFileSync(path.join(dist, "ALEGGO_SOURCE_FINGERPRINT.json"), JSON.stringify(receipt, null, 2));
  return receipt;
}

export function checkBuildReceipt(root, distDir) {
  const dist = path.resolve(root, distDir);
  const receipt = JSON.parse(readFileSync(path.join(dist, "ALEGGO_SOURCE_FINGERPRINT.json"), "utf8"));
  if (receipt.schemaVersion !== 1 || receipt.distDir !== distDir || receipt.sha256 !== sourceFingerprint(root).sha256) throw new Error("Frontend build is stale or has no valid source fingerprint. Run the frontend build again.");
  for (const buildIdFile of [path.join(dist, "BUILD_ID"), path.join(dist, "standalone", distDir, "BUILD_ID")]) {
    if (readFileSync(buildIdFile, "utf8").trim() !== receipt.buildId) throw new Error("Frontend build identity changed after validation.");
  }
  const actualServer = createHash("sha256").update(readFileSync(path.join(dist, "standalone/server.js"))).digest("hex");
  if (actualServer !== receipt.serverSha256) throw new Error("Standalone server changed after validation.");
  return receipt;
}

if (import.meta.url === pathToFileURL(process.argv[1] ?? "").href) {
  const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
  try { console.log(JSON.stringify(checkBuildReceipt(root, process.argv[2] || ".next-aleggo"))); }
  catch (error) { console.error(error.message); process.exitCode = 1; }
}
