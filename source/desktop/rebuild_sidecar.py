"""Portable sidecar rebuilder (lives in the source tree at desktop/).

PyInstaller's stock ``--collect-all aleggo`` does not follow aleggo's
lazily-imported third-party packages under an editable install, which produced a
sidecar that crashed at boot with ``ModuleNotFoundError: No module named 'rich'``.
This script generates a spec that (a) forces analysis of the server entry chain
via hiddenimports and (b) collect_all's the empirical runtime closure (rich,
faiss, lxml, PIL, ...), while excluding the huge optional ML stacks (torch,
manim, docling, sentence_transformers) so the bundle stays lean and boots.

All paths derive from this file's location (work/desktop) so the source tree is
self-contained; ALEGGO_BUILD_ROOT overrides the scratch/build root.
"""
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))            # .../work/desktop
PROJECT_ROOT = os.path.dirname(HERE)                          # .../work
PY = sys.executable
STANDALONE = os.path.join(PROJECT_ROOT, "web", ".next-aleggo", "standalone")
ENTRY = os.path.join(HERE, "runtime_server.py")
BUILD_ROOT = os.environ.get("ALEGGO_BUILD_ROOT") or os.path.join(PROJECT_ROOT, "build")
DIST = os.path.join(BUILD_ROOT, "desktop-pyinstaller")
WORK = os.path.join(BUILD_ROOT, "pyinstaller-work")
SPEC_DIR = os.path.join(BUILD_ROOT, "pyinstaller-spec")
SPEC = os.path.join(SPEC_DIR, "aleggo-next-server.spec")
SIDECAR_DEST = os.path.join(HERE, "src-tauri", "binaries",
                            "aleggo-next-server-x86_64-pc-windows-msvc.exe")

node = shutil.which("node") or r"C:\Program Files\nodejs\node.exe"
assert os.path.isfile(node), f"node not found: {node}"
assert os.path.isfile(os.path.join(STANDALONE, "server.js")), \
    f"standalone server.js missing under {STANDALONE} (run the Next build first)"

ENTRY_HIDDEN = [
    "aleggo.api.main", "aleggo.runtime.launcher", "aleggo.runtime.banner",
    "aleggo.services.config", "aleggo.services.setup",
]
CURATED = [
    "rich", "uvicorn", "fastapi", "starlette", "pydantic", "pydantic_core",
    "pydantic_settings", "httpx", "aiohttp", "anyio", "loguru", "yaml",
    "pygments", "click", "jinja2", "jsonschema", "json_repair", "defusedxml",
    "python_multipart", "multipart", "dotenv", "sniffio", "idna", "yarl",
    "multidict", "frozenlist", "attrs", "attr", "referencing", "rpds",
    "typing_extensions", "typing_inspection", "annotated_types", "propcache",
    "aiosignal", "aiohappyeyeballs", "watchfiles", "brotli", "certifi",
    "charset_normalizer", "requests",
    "openai", "anthropic", "dashscope", "tiktoken", "tenacity", "websockets",
    "msgpack", "croniter", "nest_asyncio", "psutil", "prompt_toolkit",
    "aiosqlite", "redis", "bcrypt", "jose", "qrcode",
    "fitz", "pymupdf", "pypdf", "pdfplumber", "numpy", "PIL", "faiss",
    "sympy", "scipy", "mpmath", "z3", "reportlab", "matplotlib",
]
EXCLUDES = ["torch", "sentence_transformers", "manim", "docling",
            "tkinter", "PyQt5", "PySide6", "IPython", "notebook"]

spec = [
    "# -*- mode: python ; coding: utf-8 -*-",
    "from PyInstaller.utils.hooks import collect_all, copy_metadata",
    "datas = [(r'%s', 'aleggo_web')]" % STANDALONE,
    "datas += copy_metadata('aleggo-next')",
    "binaries = [(r'%s', '.')]" % node,
    "hiddenimports = []",
    "def _ca(pkg):\n"
    "    try:\n"
    "        d, b, h = collect_all(pkg)\n"
    "        datas.extend(d); binaries.extend(b); hiddenimports.extend(h)\n"
    "    except Exception as e:\n"
    "        print('collect_all skip', pkg, repr(e))",
    "for _p in ['aleggo', 'aleggo_cli']: _ca(_p)",
    "hiddenimports += %r" % (ENTRY_HIDDEN,),
    "for _p in %r: _ca(_p)" % (CURATED,),
    "a = Analysis([r'%s'], pathex=[], binaries=binaries, datas=datas, "
    "hiddenimports=hiddenimports, hookspath=[], hooksconfig={}, runtime_hooks=[], "
    "excludes=%r, noarchive=False, optimize=0)" % (ENTRY, EXCLUDES),
    "pyz = PYZ(a.pure)",
    "exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name='aleggo-next-server', "
    "debug=False, bootloader_ignore_signals=False, strip=False, upx=False, "
    "upx_exclude=[], runtime_tmpdir=None, console=True, disable_windowed_traceback=False, "
    "argv_emulation=False, target_arch=None, codesign_identity=None, entitlements_file=None)",
]

os.makedirs(SPEC_DIR, exist_ok=True)
with open(SPEC, "w", encoding="utf-8") as fh:
    fh.write("\n".join(spec) + "\n")
print("[sidecar] spec:", SPEC)

cmd = [PY, "-m", "PyInstaller", "--noconfirm", "--clean",
       "--distpath", DIST, "--workpath", WORK, SPEC]
rc = subprocess.call(cmd)
print("[sidecar] PyInstaller EXIT=", rc)
if rc != 0:
    raise SystemExit(rc)

built = os.path.join(DIST, "aleggo-next-server.exe")
print("[sidecar] built %.1f MB" % (os.path.getsize(built) / 1e6))
os.makedirs(os.path.dirname(SIDECAR_DEST), exist_ok=True)
shutil.copy2(built, SIDECAR_DEST)
print("[sidecar] staged:", SIDECAR_DEST)
print("[sidecar] DONE")
