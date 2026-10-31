"""Fixed entry point for sandboxed Python source in the desktop bundle."""

from __future__ import annotations

import argparse
from contextlib import ExitStack
import importlib
import os
from pathlib import Path
import runpy
import sys


def main(argv: list[str] | None = None) -> int:
    """Run a source file with ordinary script argv, imports, stdin and exits.

    The sandbox owns policy, workspace and process limits. This entry point
    supplies the bundled interpreter; it does not start the desktop server.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stdin-file", type=Path)
    parser.add_argument("--packages-dir", type=Path)
    parser.add_argument("script", type=Path)
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    # The desktop bundle supplies a headless renderer for saved figures.
    # Scripts can still explicitly select a different installed backend.
    os.environ.setdefault("MPLBACKEND", "Agg")
    script = args.script.resolve(strict=True)
    if not script.is_file():
        parser.error("script must be a file")
    previous_argv, previous_stdin, previous_path = sys.argv, sys.stdin, sys.path[:]
    try:
        with ExitStack() as stack:
            if args.stdin_file is not None:
                sys.stdin = stack.enter_context(args.stdin_file.open("r", encoding="utf-8"))
            sys.argv = [str(script), *args.arguments]
            if args.packages_dir is not None and args.packages_dir.is_dir():
                sys.path.insert(0, str(args.packages_dir.resolve()))
                importlib.invalidate_caches()
            sys.path.insert(0, str(script.parent))
            runpy.run_path(str(script), run_name="__main__")
    finally:
        sys.argv, sys.stdin = previous_argv, previous_stdin
        sys.path[:] = previous_path
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through subprocess
    raise SystemExit(main())
