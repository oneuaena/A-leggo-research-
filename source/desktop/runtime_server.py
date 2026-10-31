"""A-leggo Next's local desktop runtime sidecar.

The sidecar contains the A-leggo Next Python service and the production Next
standalone server.  It deliberately starts no browser: Tauri owns the window.
"""

from __future__ import annotations

import contextlib
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time


def _bundle_root() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))


def dispatch_math_worker(arguments: list[str]) -> bool:
    """Run the isolated JSON worker before starting any service or user setup."""
    if not arguments or arguments[0] != "--aleggo-math-worker":
        return False
    if len(arguments) != 1:
        raise SystemExit("The mathematical worker accepts its request through stdin only.")
    from aleggo.study.math_worker import entrypoint
    entrypoint()
    return True


def announce_frontend_ready(frontend: subprocess.Popen, port: int, timeout: float = 60) -> None:
    """Announce the actual configured port only after this Node process listens."""
    if not 1 <= port <= 65535:
        raise ValueError("The frontend port is outside the allowed range.")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if frontend.poll() is not None:
            raise RuntimeError("A-leggo Next frontend stopped during startup")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                print(f"ALEGGO_FRONTEND_READY_PORT={port}", flush=True)
                return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError("A-leggo Next frontend did not start within 60 seconds")


def _ensure_firewall_rule(port: int) -> None:
    """Best effort: installed setups get the rule from the NSIS hook; the
    portable exe tries once here and stays silent without elevation."""

    if os.name != "nt":
        return
    try:
        subprocess.run(
            [
                "netsh", "advfirewall", "firewall", "add", "rule",
                "name=A-leggo Next LAN", "dir=in", "action=allow",
                "protocol=TCP", f"localport={port}", "enable=yes",
            ],
            capture_output=True,
            timeout=15,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:
        pass


def _repair_runtime_acls(runtime: Path) -> None:
    """Best effort: keep the interactive user in control of the runtime tree.

    A run started elevated (installer "run after setup", right-click) creates
    Administrator-owned directories inside Windows' OWNER RIGHTS-hardened
    per-user tree; later non-elevated runs then cannot open their SQLite
    databases ("unable to open database file").  Taking ownership and
    granting the current user FullControl repairs the whole tree at startup.
    Without elevation the commands fail silently and the run proceeds as
    before.
    """

    if os.name != "nt":
        return
    domain = os.environ.get("USERDOMAIN")
    user = os.environ.get("USERNAME")
    if not domain or not user:
        return
    account = f"{domain}\\{user}"
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    for command in (
        ["takeown", "/F", str(runtime), "/R", "/D", "Y"],
        ["icacls", str(runtime), "/setowner", account, "/T", "/C", "/Q"],
        ["icacls", str(runtime), "/grant", f"{account}:(OI)(CI)F", "/T", "/C", "/Q"],
    ):
        with contextlib.suppress(Exception):
            subprocess.run(
                command,
                capture_output=True,
                timeout=120,
                check=False,
                creationflags=flags,
            )


def main() -> None:
    root = _bundle_root()
    # launcher._resolve_frontend deliberately uses PATH to locate Node.  The
    # packaged node.exe lives alongside this sidecar, not in the user's PATH.
    os.environ["PATH"] = str(root) + os.pathsep + os.environ.get("PATH", "")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8:replace")
    runtime = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "A-leggo Next"
    runtime.mkdir(parents=True, exist_ok=True)
    _repair_runtime_acls(runtime)

    # Do not call launcher.start() from a one-file executable: its normal
    # backend child command is ``sys.executable -m uvicorn`` and would recurse
    # into this sidecar.  This is the same formal FastAPI + standalone-Next
    # runtime, supervised in-process instead.
    os.environ["ALEGGO_HOME"] = str(runtime)
    from aleggo.runtime.python_runtime import configure_optional_packages

    configure_optional_packages()
    from aleggo.runtime.launcher import _reset_runtime_singletons
    from aleggo.services.config import (
        HTTP_KEEP_ALIVE_TIMEOUT,
        ensure_runtime_settings_files,
        export_runtime_settings_to_env,
        get_ws_max_size,
        load_auth_settings,
        load_launch_settings,
    )
    from aleggo.services.setup import init_user_directories
    import uvicorn

    _reset_runtime_singletons()
    init_user_directories(runtime)
    ensure_runtime_settings_files()
    settings = load_launch_settings(runtime)
    backend_port, frontend_port = settings.backend_port, settings.frontend_port
    runtime_env = export_runtime_settings_to_env(overwrite=True)
    auth_enabled = bool(load_auth_settings()["enabled"])
    backend_url = f"http://127.0.0.1:{backend_port}"
    runtime_env.update(
        {
            "ALEGGO_HOME": str(runtime),
            "ALEGGO_API_BASE_URL": backend_url,
            "NEXT_PUBLIC_API_BASE": backend_url,
            "NEXT_PUBLIC_AUTH_ENABLED": "true" if auth_enabled else "false",
            "ALEGGO_AUTH_ENABLED": "true" if auth_enabled else "false",
            "PYTHONUNBUFFERED": "1",
            "PYTHONIOENCODING": "utf-8:replace",
        }
    )
    config = uvicorn.Config(
        "aleggo.api.main:app",
        host="127.0.0.1",
        port=backend_port,
        log_level="info",
        access_log=False,
        ws_max_size=get_ws_max_size(),
        timeout_keep_alive=HTTP_KEEP_ALIVE_TIMEOUT,
    )
    server = uvicorn.Server(config)
    backend_thread = threading.Thread(target=server.run, daemon=True)
    backend_thread.start()
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", backend_port), timeout=0.5):
                break
        except OSError:
            if not backend_thread.is_alive():
                raise RuntimeError("A-leggo Next backend stopped during startup")
            time.sleep(0.25)
    else:
        raise RuntimeError("A-leggo Next backend did not start within 60 seconds")

    web_root = root / "aleggo_web"
    server_js = web_root / "server.js"
    if not server_js.is_file():
        raise RuntimeError("Packaged A-leggo Next frontend is missing server.js")
    node = root / ("node.exe" if os.name == "nt" else "node")
    if not node.is_file():
        raise RuntimeError("Packaged Node runtime is missing")
    web_env = os.environ.copy()
    web_env.update(runtime_env)
    web_env["PORT"] = str(frontend_port)
    # Bind the frontend to every interface so phones on the same LAN can load
    # the mobile app / PWA.  API and WebSocket traffic still reaches the
    # backend through proxy.ts rewrites on this same server, so the backend
    # itself stays loopback-only.
    web_env["HOSTNAME"] = "0.0.0.0"
    _ensure_firewall_rule(frontend_port)
    frontend = subprocess.Popen([str(node), str(server_js)], cwd=web_root, env=web_env)
    try:
        announce_frontend_ready(frontend, frontend_port)
        raise SystemExit(frontend.wait())
    finally:
        server.should_exit = True
        if frontend.poll() is None:
            frontend.terminate()


if __name__ == "__main__":
    # PyInstaller overrides freeze_support to dispatch internal worker children;
    # SciPy may use those processes without starting another API/Next server.
    import multiprocessing
    multiprocessing.freeze_support()
    from aleggo.runtime.python_runtime import dispatch_module_worker

    if not dispatch_module_worker(sys.argv[1:]) and not dispatch_math_worker(sys.argv[1:]):
        if sys.argv[1:]:
            raise SystemExit("Unsupported A-leggo desktop arguments. This executable is not a generic Python interpreter.")
        main()
