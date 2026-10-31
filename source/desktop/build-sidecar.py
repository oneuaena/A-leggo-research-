"""Build the bundled local API + PWA sidecar for any Tauri target."""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


# Match the desktop runtime closure: dynamic provider and restricted math
# imports are invisible to PyInstaller's ordinary static analysis.
RUNTIME_PACKAGES = (
    "rich", "uvicorn", "fastapi", "starlette", "pydantic", "pydantic_core",
    "pydantic_settings", "httpx", "aiohttp", "anyio", "loguru", "yaml",
    "pygments", "click", "jinja2", "jsonschema", "json_repair", "defusedxml",
    "python_multipart", "multipart", "dotenv", "sniffio", "idna", "yarl",
    "multidict", "frozenlist", "attrs", "attr", "referencing", "rpds",
    "typing_extensions", "typing_inspection", "annotated_types", "propcache",
    "aiosignal", "aiohappyeyeballs", "watchfiles", "brotli", "certifi",
    "charset_normalizer", "requests", "openai", "anthropic", "dashscope",
    "tiktoken", "tenacity", "websockets", "msgpack", "croniter", "nest_asyncio",
    "psutil", "prompt_toolkit", "aiosqlite", "redis", "bcrypt", "jose", "qrcode",
    "fitz", "pymupdf", "pypdf", "pdfplumber", "numpy", "PIL", "faiss",
    "sympy", "scipy", "mpmath", "z3", "reportlab", "matplotlib",
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", required=True)
    parser.add_argument("--python", default=sys.executable)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    web = root / "web"
    build_root = Path(os.environ.get("ALEGGO_BUILD_ROOT", root / "build"))
    standalone = web / ".next-aleggo" / "standalone"
    if not (standalone / "server.js").exists():
        raise SystemExit("Build the web app first: npm --prefix web run build")
    nested_next = standalone / ".next-aleggo"
    is_junction = getattr(os.path, "isjunction", lambda value: False)
    if nested_next.exists() and (is_junction(str(nested_next)) or nested_next.is_symlink()):
        if nested_next.resolve() == (web / ".next-aleggo").resolve():
            os.rmdir(nested_next)
    if not (nested_next / "BUILD_ID").exists():
        nested_next.mkdir(parents=True, exist_ok=True)
        excluded = {"standalone", "node_modules", "cache", "diagnostics", "types"}
        for item in (web / ".next-aleggo").iterdir():
            if item.name in excluded:
                continue
            target = nested_next / item.name
            if item.is_dir():
                shutil.copytree(item, target, dirs_exist_ok=True)
            else:
                shutil.copy2(item, target)
    static = web / ".next-aleggo" / "static"
    embedded_static = standalone / ".next-aleggo" / "static"
    if not embedded_static.exists():
        shutil.copytree(static, embedded_static)
    public = web / "public"
    embedded_public = standalone / "public"
    if public.exists() and not embedded_public.exists():
        shutil.copytree(public, embedded_public)
    node = shutil.which("node") or shutil.which("node.exe")
    if not node:
        raise SystemExit("Node.js is required to run the bundled Next server")
    dist = build_root / "sidecar-dist"
    work = build_root / "sidecar-work"
    spec = build_root / "sidecar-spec"
    for path in (dist, work, spec):
        path.mkdir(parents=True, exist_ok=True)
    separator = ";" if os.name == "nt" else ":"
    cmd = [args.python, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile",
           "--name", "aleggo-next-server", "--distpath", str(dist),
           "--workpath", str(work), "--specpath", str(spec),
           "--collect-all", "aleggo", "--collect-all", "aleggo_cli",
           "--copy-metadata", "aleggo-next",
           "--add-data", f"{standalone}{separator}aleggo_web",
           "--add-binary", f"{node}{separator}."]
    for package in RUNTIME_PACKAGES:
        cmd.extend(("--collect-all", package))
    cmd.extend(("--hidden-import", "aleggo.study.math_worker", str(root / "desktop" / "runtime_server.py")))
    subprocess.run(cmd, cwd=root, check=True)
    suffix = ".exe" if os.name == "nt" else ""
    target_dir = root / "desktop" / "src-tauri" / "binaries"
    target_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(dist / f"aleggo-next-server{suffix}",
                 target_dir / f"aleggo-next-server-{args.target}{suffix}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
