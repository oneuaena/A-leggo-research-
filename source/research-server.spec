# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules, collect_data_files, copy_metadata

root = Path(SPECPATH)
study = root / "aleggo" / "study"
prefixes = ("research", "model_registry", "math_", "lean_", "proof_review", "external_cas", "settings", "store", "standalone_config")
hidden = ["aleggo.study." + p.stem for p in study.glob("*.py") if p.stem.startswith(prefixes)]
hidden += ["aleggo.api.routers.research", "aleggo.api.routers.research_extensions", "aleggo.api.routers.self_improvement"]
hidden += collect_submodules("aleggo.study.self_improvement")
hidden += collect_submodules("uvicorn") + collect_submodules("sympy", filter=lambda name: ".tests" not in name)
datas = [(str(root / "web" / "out"), "web-assets"), (str(study / "evals"), "aleggo/study/evals"),
         (str(study / "self_improvement" / "benchmark_problems.json"), "aleggo/study/self_improvement"),
         (str(study / "math_worker.py"), "aleggo/study")]
datas += collect_data_files("sympy")
datas += collect_data_files("certifi")
for package in ("sympy", "numpy", "scipy", "z3-solver", "certifi"):
    try:
        datas += copy_metadata(package)
    except Exception:
        pass
a = Analysis([str(root / "standalone_server.py")], pathex=[str(root)], binaries=[], datas=datas,
             hiddenimports=hidden, hookspath=[], hooksconfig={}, runtime_hooks=[],
             excludes=["torch", "tensorflow", "transformers", "sentence_transformers", "manim", "docling", "IPython", "notebook", "tkinter"],
             noarchive=False, optimize=0)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="aleggo-research-server", debug=False,
          bootloader_ignore_signals=False, strip=False, upx=False, console=True)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="aleggo-research-server")
