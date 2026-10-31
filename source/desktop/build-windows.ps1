param(
  [string]$Python = "python",
  [switch]$SkipFrontendBuild
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$buildRoot = if ($env:ALEGGO_BUILD_ROOT) { $env:ALEGGO_BUILD_ROOT } else { Join-Path $projectRoot "work" }
$webRoot = Join-Path $projectRoot "web"
$standalone = Join-Path $webRoot ".next-aleggo\standalone"
$targetTriple = "x86_64-pc-windows-msvc"
$sidecar = Join-Path $projectRoot "desktop\src-tauri\binaries\aleggo-next-server-$targetTriple.exe"
New-Item -ItemType Directory -Force -Path $buildRoot | Out-Null

if (-not $SkipFrontendBuild) {
  Push-Location $webRoot
  try {
    $env:ALEGGO_NEXT_DIST_DIR = ".next-aleggo"
    npm.cmd run build
    if ($LASTEXITCODE -ne 0) { throw "Frontend build failed with $LASTEXITCODE" }
  } finally { Pop-Location }
}
if (-not (Test-Path (Join-Path $standalone "server.js"))) { throw "Missing Next standalone bundle: $standalone" }
# SkipFrontendBuild must still package the current sources, not an older
# successful build from another dist directory.
& node (Join-Path $webRoot "scripts\build-fingerprint.mjs") ".next-aleggo"
if ($LASTEXITCODE -ne 0) { throw "Stale frontend bundle; rebuild web with ALEGGO_NEXT_DIST_DIR=.next-aleggo" }

# When the production dist directory is hosted through a junction on a
# low-space workstation, Next can copy that junction into standalone.  It
# points back to the dist root and would make PyInstaller recurse forever.
$nestedNext = Join-Path $standalone ".next-aleggo"
$nextDist = Join-Path $webRoot ".next-aleggo"
if (Test-Path -LiteralPath $nestedNext) {
  $nestedItem = Get-Item -LiteralPath $nestedNext
  $expectedNext = (Resolve-Path $nextDist).Path
  if ($nestedItem.LinkType -eq "Junction" -and $nestedItem.Target -eq $expectedNext) {
    Remove-Item -LiteralPath $nestedNext -Force
  }
}
if (-not (Test-Path -LiteralPath (Join-Path $nestedNext "BUILD_ID"))) {
  # Next's standalone server resolves manifests below .next-aleggo. Copy
  # only those runtime files, never the standalone directory itself.
  New-Item -ItemType Directory -Force -Path $nestedNext | Out-Null
  Get-ChildItem -LiteralPath $nextDist -File -Force |
    Copy-Item -Destination $nestedNext -Force
  Get-ChildItem -LiteralPath $nextDist -Directory -Force |
    Where-Object { $_.Name -notin @("standalone", "node_modules", "cache", "diagnostics", "types") } |
    ForEach-Object {
      Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $nestedNext $_.Name) -Recurse -Force
    }
}

# Next's standalone output intentionally omits these two public trees. Copy
# them into the staging bundle directly; calling the runtime launcher's copy
# helper here would resolve a D: junction back onto itself on low-space C:
# workstations and report WinError 32.
$standaloneStatic = Join-Path $standalone ".next-aleggo\static"
if (-not (Test-Path $standaloneStatic)) {
  Copy-Item -LiteralPath (Join-Path $webRoot ".next-aleggo\static") -Destination $standaloneStatic -Recurse -Force
}
$publicTarget = Join-Path $standalone "public"
New-Item -ItemType Directory -Force -Path $publicTarget | Out-Null
Copy-Item -Path (Join-Path $webRoot "public\*") -Destination $publicTarget -Recurse -Force

# PyInstaller is driven by rebuild_sidecar.py, whose spec forces analysis of the
# server entry chain and collects the full runtime dependency closure. The plain
# --collect-all aleggo invocation missed lazily-imported third-party packages
# (rich, faiss, ...) and produced a sidecar that crashed at boot.
& $Python (Join-Path $PSScriptRoot "rebuild_sidecar.py")
if ($LASTEXITCODE -ne 0) { throw "rebuild_sidecar.py failed with $LASTEXITCODE" }

Push-Location (Join-Path $projectRoot "desktop")
try {
  npm.cmd ci
  if ($LASTEXITCODE -ne 0) { throw "Desktop dependency install failed with $LASTEXITCODE" }
  $env:PATH = "$env:USERPROFILE\.cargo\bin;$env:PATH"
  $env:CARGO_TARGET_DIR = if ($env:ALEGGO_TAURI_TARGET_DIR) { $env:ALEGGO_TAURI_TARGET_DIR } else { Join-Path $buildRoot "tauri-target" }
  npm.cmd run tauri build -- --bundles nsis
  if ($LASTEXITCODE -ne 0) { throw "Tauri build failed with $LASTEXITCODE" }
} finally { Pop-Location }
