"""One-click installation and diagnostics for the pinned Lean 4 + Mathlib workspace.

Ordinary mathematicians should not have to learn elan, Lake and the Mathlib
cache before they can ask for a kernel check.  This module

* reports exactly what is present and what is missing (``lean_status``);
* installs the pinned toolchain from a prebuilt, CI-produced bundle
  (``start_install``): resumable download from a China mirror with official
  fallback, SHA-256 verification, extraction and a real kernel smoke check;
* supports pause/resume (``pause_install``), cancellation (``cancel_install``)
  and version upgrades (``start_upgrade`` / ``check_updates``).

The legacy from-source installer (elan-init + ``lake update`` + ``lake exe
cache get``) is kept as ``start_install(from_source=True)`` for platforms
without a prebuilt bundle.  Nothing is added to the user's shell profile or
PATH.

Public interface (unchanged): ``lean_status``, ``start_install``,
``cancel_install``, ``wait_for_install``, ``pause_install``,
``start_upgrade``, ``check_updates``, ``LeanInstallCancelled``.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
import io
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Callable
import zipfile

from aleggo.study import lean_bundle, lean_formal
from aleggo.study.lean_bundle import LeanBundleError
from aleggo.study.lean_formal import PINNED_LEAN, PINNED_MATHLIB

ELAN_INIT_SH = "https://raw.githubusercontent.com/leanprover/elan/master/elan-init.sh"
ELAN_WINDOWS_ZIP = "https://github.com/leanprover/elan/releases/latest/download/elan-x86_64-pc-windows-msvc.zip"
MATHLIB_GIT = "https://github.com/leanprover-community/mathlib4.git"
# Legacy from-source path still needs git + a full local build.
LEGACY_REQUIRED_DISK_BYTES = 12 * 1024 ** 3
PHASE_TIMEOUTS = {"elan": 900, "toolchain": 1800, "mathlib": 4 * 3600, "cache": 4 * 3600, "verify": 900}
LAKEFILE = (
    'name = "aleggo_formal"\n'
    'defaultTargets = []\n\n'
    '[[require]]\n'
    'name = "mathlib"\n'
    f'git = "{MATHLIB_GIT}"\n'
    f'rev = "{PINNED_MATHLIB}"\n'
)
# Bundle installer phases.  The frontend renders these from the snapshot, so
# the ids are part of the UI contract; keep them stable.
_PHASES = ("check", "download", "verify", "extract", "smoke")
# Legacy from-source installer phases (only used when from_source=True).
_LEGACY_PHASES = ("check", "elan", "toolchain", "workspace", "mathlib", "cache", "verify")
_PHASE_LABELS = {
    "check": "正在检查环境",
    "download": "正在下载安装包（支持断点续传）",
    "verify": "正在校验文件完整性",
    "extract": "正在解压安装",
    "smoke": "正在做安装后检查",
    # Legacy from-source phases share the same label map.
    "elan": "正在安装 elan",
    "toolchain": "正在安装 Lean 工具链",
    "workspace": "正在创建 Lake 工程",
    "mathlib": "正在获取 Mathlib",
    "cache": "正在下载预编译缓存",
}
# The manifest is fetched at most once an hour; lean_status() must stay cheap.
_MANIFEST_CACHE: dict[str, Any] = {"at": 0.0, "manifest": None, "url": ""}


class LeanInstallCancelled(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _elan_executable() -> Path | None:
    names = ("elan.exe", "elan") if os.name == "nt" else ("elan", "elan.exe")
    for directory in lean_formal._elan_bin_candidates():
        for name in names:
            if (directory / name).is_file():
                return directory / name
    found = shutil.which("elan")
    return Path(found) if found else None


def _free_bytes(path: Path) -> int:
    probe = path
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    try:
        return int(shutil.disk_usage(probe).free)
    except OSError:
        return 0


def _workspace_report(root: Path) -> dict[str, Any]:
    olean = root / ".lake" / "packages" / "mathlib" / ".lake" / "build" / "lib" / "lean" / "Mathlib.olean"
    toolchain = root / "lean-toolchain"
    toolchain_text = toolchain.read_text(encoding="utf-8").strip() if toolchain.is_file() else ""
    return {
        "path": str(root), "exists": root.is_dir(), "toolchain": toolchain_text,
        "toolchainPinned": toolchain_text == PINNED_LEAN,
        "lakefile": (root / "lakefile.toml").is_file(), "manifest": (root / "lake-manifest.json").is_file(),
        "mathlibCache": olean.is_file(), "pinned": root.is_dir() and lean_formal._pinned_workspace(root),
    }


def _cached_manifest() -> tuple[dict[str, Any] | None, str]:
    if time.monotonic() - _MANIFEST_CACHE["at"] < 3600 and _MANIFEST_CACHE["manifest"] is not None:
        return _MANIFEST_CACHE["manifest"], _MANIFEST_CACHE["url"]
    try:
        manifest, url = lean_bundle.fetch_manifest()
    except LeanBundleError:
        return None, ""
    _MANIFEST_CACHE.update(at=time.monotonic(), manifest=manifest, url=url)
    return manifest, url


def check_updates() -> dict[str, Any]:
    """Compare the installed bundle version against the release manifest."""
    tools_home = lean_formal.tools_home()
    installed = lean_bundle.installed_bundle_info(tools_home).get("version", "")
    manifest, _ = _cached_manifest()
    latest = lean_bundle.latest_bundle_version(manifest) if manifest else ""
    return {
        "installedVersion": installed, "latestVersion": latest,
        "canUpgrade": bool(installed and latest and lean_bundle.is_newer_version(latest, installed)),
        "platform": lean_bundle.platform_id(),
    }


def lean_status() -> dict[str, Any]:
    """Diagnostics a non-specialist can act on, plus the installer job state."""
    lake = lean_formal._lake_executable()
    elan = _elan_executable()
    managed = lean_formal.managed_workspace()
    try:
        workspace = lean_formal.detect_lean_workspace()
        ready, reason, active_root = True, "", str(workspace.root)
    except lean_formal.LeanConfigurationError as exc:
        ready, reason, active_root = False, str(exc), ""
    report = _workspace_report(managed)
    git = shutil.which("git")
    tools_home = lean_formal.tools_home()
    free = _free_bytes(tools_home)
    required = lean_bundle.REQUIRED_DISK_BYTES
    bundle_version = lean_bundle.installed_bundle_info(tools_home).get("version", "")
    checks = [
        {"id": "elan", "label": "Lean 版本管理器（elan）", "ok": bool(elan or lake),
         "detail": str(elan or "") or ("找到 lake（无 elan）" if lake else "未安装")},
        {"id": "lake", "label": "Lake 构建工具", "ok": bool(lake), "detail": str(lake or "未安装")},
        {"id": "workspace", "label": f"固定版本项目（{PINNED_LEAN}，Mathlib {PINNED_MATHLIB}）",
         "ok": bool(active_root) or report["pinned"], "detail": active_root or report["path"]},
        {"id": "mathlib", "label": "Mathlib 预编译缓存", "ok": ready or report["mathlibCache"],
         "detail": "已就绪" if (ready or report["mathlibCache"]) else "未下载"},
        {"id": "disk", "label": "安装可用磁盘空间", "ok": ready or free >= required,
         "detail": f"可用 {free / 1024 ** 3:.1f} GB，约需 {required / 1024 ** 3:.0f} GB"},
    ]
    return {
        "ready": ready, "reason": reason, "activeWorkspace": active_root, "pinnedLean": PINNED_LEAN,
        "pinnedMathlib": PINNED_MATHLIB, "toolsHome": str(tools_home), "managedWorkspace": report,
        "lake": str(lake or ""), "elan": str(elan or ""), "gitAvailable": bool(git), "freeDiskBytes": free,
        "requiredDiskBytes": required, "platform": f"{platform.system()} {platform.machine()}",
        "bundleVersion": bundle_version,
        "autoStart": {"enabled": os.environ.get("ALEGGO_LEAN_AUTO_DOWNLOAD", "1").strip() != "0",
                      "declined": bool(lean_bundle.read_install_state(tools_home).get("auto_declined"))},
        "checks": checks, "install": _JOB.snapshot(),
    }


# --------------------------------------------------------------------------- #
# Background installer
# --------------------------------------------------------------------------- #

@dataclass
class _InstallJob:
    status: str = "idle"  # idle | running | paused | succeeded | failed | cancelled
    mode: str = "install"  # install | upgrade
    auto: bool = False  # started automatically on first launch
    from_source: bool = False  # legacy elan-init path
    phase: str = ""
    message: str = ""
    error: str = ""
    error_code: str = ""
    downloaded_bytes: int = 0
    total_bytes: int = 0
    speed_bps: float = 0.0
    started_at: str = ""
    finished_at: str = ""
    log: deque = field(default_factory=lambda: deque(maxlen=400))
    cancel: threading.Event = field(default_factory=threading.Event)
    paused: threading.Event = field(default_factory=threading.Event)
    process: subprocess.Popen | None = None
    thread: threading.Thread | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            phases = _LEGACY_PHASES if self.from_source else _PHASES
            index = phases.index(self.phase) if self.phase in phases else 0
            progress = 100 if self.status == "succeeded" else 0
            if self.status in ("running", "paused"):
                progress = int(100 * index / len(phases))
                if self.phase in ("download", "extract") and self.total_bytes > 0:
                    frac = min(1.0, self.downloaded_bytes / self.total_bytes)
                    progress = int(100 * index / len(phases) + 100 / len(phases) * frac)
            snapshot = {
                "status": self.status, "mode": self.mode, "phase": self.phase,
                "phaseLabel": _PHASE_LABELS.get(self.phase, ""), "message": self.message,
                "error": self.error, "errorCode": self.error_code,
                "errorHelp": lean_bundle.error_help(self.error_code) if self.error_code else None,
                "downloadedBytes": self.downloaded_bytes, "totalBytes": self.total_bytes,
                "speedBps": self.speed_bps,
                "startedAt": self.started_at, "finishedAt": self.finished_at, "progress": progress,
                "phases": list(phases), "log": list(self.log)[-80:],
            }
            return snapshot

    def emit(self, line: str) -> None:
        with self.lock:
            self.log.append(line.rstrip()[:2000])

    def set_phase(self, phase: str, message: str) -> None:
        with self.lock:
            self.phase, self.message = phase, message
            self.log.append(f"== {phase}: {message}")

    def set_progress(self, downloaded: int, total: int | None, speed: float) -> None:
        with self.lock:
            self.downloaded_bytes = downloaded
            self.total_bytes = total or 0
            self.speed_bps = speed


_JOB = _InstallJob()
# Tests replace these to exercise the installer without the network.
_download: Callable[[str], bytes] | None = None


def _fetch(url: str) -> bytes:
    if _download is not None:
        return _download(url)
    import httpx

    with httpx.Client(timeout=120, follow_redirects=True) as client:
        response = client.get(url)
        response.raise_for_status()
        return response.content


def _environment(elan_home: Path) -> dict[str, str]:
    env = dict(os.environ)
    env["ELAN_HOME"] = str(elan_home)
    env["PATH"] = str(elan_home / "bin") + os.pathsep + env.get("PATH", "")
    env.setdefault("LAKE_NO_INTERACTIVE", "1")
    # Keep Mathlib's compressed cache inside the managed tools directory (not
    # the user's home) so that it can be cleaned up after unpacking.
    env["MATHLIB_CACHE_DIR"] = str(lean_formal.tools_home() / "mathlib-cache")
    # Never prompt (git credentials, elan confirmation).
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def _run(job: _InstallJob, command: list[str], *, cwd: Path, env: dict[str, str], timeout: float) -> None:
    if job.cancel.is_set():
        raise LeanInstallCancelled()
    job.emit("$ " + " ".join(command))
    creation = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    process = subprocess.Popen(command, cwd=str(cwd), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               stdin=subprocess.DEVNULL, creationflags=creation)
    job.process = process
    deadline = time.monotonic() + timeout

    def reader() -> None:
        assert process.stdout is not None
        for raw in iter(process.stdout.readline, b""):
            job.emit(raw.decode("utf-8", errors="replace"))

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    try:
        while process.poll() is None:
            if job.cancel.is_set():
                process.kill()
                raise LeanInstallCancelled()
            _pause_point(job, on_cancel=process.kill)
            if time.monotonic() > deadline:
                process.kill()
                raise RuntimeError(f"'{command[0]}' exceeded {int(timeout)} seconds.")
            time.sleep(0.25)
    finally:
        thread.join(timeout=5)
        job.process = None
    if process.returncode != 0:
        raise RuntimeError(f"'{' '.join(command[:3])}' failed with exit code {process.returncode}.")


def _pause_point(job: _InstallJob, *, on_cancel=None) -> None:
    """Blocking pause honoured at phase boundaries (extract / smoke / subprocess steps).

    The worker thread stays alive and sleeps; resume just clears the event, so
    resume never needs to restart the thread. Raises LeanInstallCancelled when
    a cancel arrives while paused (on_cancel runs first, e.g. to kill a child).
    """
    if not job.paused.is_set() and not job.cancel.is_set():
        return
    with job.lock:
        if job.status == "running" and job.paused.is_set():
            job.status, job.message = "paused", "已暂停，进度已保存，可随时继续。"
    while True:
        if job.cancel.is_set():
            if on_cancel is not None:
                on_cancel()
            raise LeanInstallCancelled()
        if not job.paused.is_set():
            break
        time.sleep(0.2)
    with job.lock:
        if job.status == "paused":
            job.status, job.message = "running", "继续下载"


# --------------------------------------------------------------------------- #
# Legacy from-source installer (kept for platforms without a prebuilt bundle)
# --------------------------------------------------------------------------- #

def _install_elan(job: _InstallJob, elan_home: Path, env: dict[str, str]) -> None:
    elan_home.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="aleggo-elan-") as temp:
        temp_dir = Path(temp)
        if os.name == "nt":
            archive = zipfile.ZipFile(io.BytesIO(_fetch(ELAN_WINDOWS_ZIP)))
            member = next((name for name in archive.namelist() if name.lower().endswith("elan-init.exe")), "")
            if not member:
                raise RuntimeError("The downloaded elan archive does not contain elan-init.exe.")
            installer = temp_dir / "elan-init.exe"
            installer.write_bytes(archive.read(member))
            command = [str(installer)]
        else:
            installer = temp_dir / "elan-init.sh"
            installer.write_bytes(_fetch(ELAN_INIT_SH))
            command = ["sh", str(installer)]
        _run(job, command + ["-y", "--default-toolchain", "none", "--no-modify-path"], cwd=temp_dir, env=env,
             timeout=PHASE_TIMEOUTS["elan"])


def write_workspace_files(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "lean-toolchain").write_text(PINNED_LEAN + "\n", encoding="utf-8")
    lakefile = root / "lakefile.toml"
    if not lakefile.is_file() or lakefile.read_text(encoding="utf-8") != LAKEFILE:
        lakefile.write_text(LAKEFILE, encoding="utf-8")
    (root / ".gitignore").write_text("/.lake\n", encoding="utf-8")
    (root / "README-ALEGGO.txt").write_text(
        "Managed by A-leggo for bounded Lean kernel checks. Pinned: " + PINNED_LEAN + " / Mathlib " + PINNED_MATHLIB +
        ".\nDelete this folder to uninstall; the research data is unaffected.\n", encoding="utf-8")


def _smoke_check(job: _InstallJob, root: Path, lake: Path, env: dict[str, str]) -> None:
    probe_dir = root / ".aleggo-smoke"
    probe_dir.mkdir(exist_ok=True)
    probe = probe_dir / "Smoke.lean"
    probe.write_text("import Mathlib\n\nexample : (2 : ℕ) + 2 = 4 := by norm_num\n", encoding="utf-8")
    try:
        _run(job, [str(lake), "env", "lean", str(probe)], cwd=root, env=env, timeout=PHASE_TIMEOUTS["verify"])
    finally:
        shutil.rmtree(probe_dir, ignore_errors=True)


def _install_from_source(job: _InstallJob) -> None:
    """Legacy path: elan-init + toolchain + lake update + cache get."""
    elan_home = lean_formal.managed_elan_home()
    root = lean_formal.managed_workspace()
    env = _environment(elan_home)
    job.set_phase("check", "检查磁盘空间与前置条件（传统安装模式）")
    if not shutil.which("git"):
        raise RuntimeError("需要 git 来获取 Mathlib，请先安装 git（https://git-scm.com）后重试。")
    if _free_bytes(lean_formal.tools_home()) < LEGACY_REQUIRED_DISK_BYTES and not _workspace_report(root)["mathlibCache"]:
        raise RuntimeError(f"{lean_formal.tools_home()} 需要约 {LEGACY_REQUIRED_DISK_BYTES / 1024 ** 3:.0f} GB 可用磁盘空间。")
    lean_formal.tools_home().mkdir(parents=True, exist_ok=True)
    elan = _elan_executable()
    if elan is None:
        job.set_phase("elan", "正在安装 Lean 版本管理器（elan）")
        _install_elan(job, elan_home, env)
        elan = _elan_executable()
        if elan is None:
            raise RuntimeError("elan 安装完成但在 " + str(elan_home / "bin") + " 下找不到可执行文件")
    else:
        job.set_phase("elan", "检测到已安装的 elan：" + str(elan))
        env["ELAN_HOME"] = str(elan.parent.parent)
        env["PATH"] = str(elan.parent) + os.pathsep + os.environ.get("PATH", "")
    job.set_phase("toolchain", "正在安装 " + PINNED_LEAN)
    _run(job, [str(elan), "toolchain", "install", PINNED_LEAN], cwd=lean_formal.tools_home(), env=env,
         timeout=PHASE_TIMEOUTS["toolchain"])
    job.set_phase("workspace", "正在创建锁定的 Lake 工程")
    write_workspace_files(root)
    lake = elan.parent / ("lake.exe" if os.name == "nt" else "lake")
    if not lake.is_file():
        found = lean_formal._lake_executable()
        if found is None:
            raise RuntimeError("工具链安装后未找到 Lake。")
        lake = found
    job.set_phase("mathlib", "正在获取 Mathlib " + PINNED_MATHLIB + "（下载较大）")
    if not (root / "lake-manifest.json").is_file() or not (root / ".lake" / "packages" / "mathlib").is_dir():
        _run(job, [str(lake), "update"], cwd=root, env=env, timeout=PHASE_TIMEOUTS["mathlib"])
    job.set_phase("cache", "正在下载预编译的 Mathlib 缓存")
    _run(job, [str(lake), "exe", "cache", "get"], cwd=root, env=env, timeout=PHASE_TIMEOUTS["cache"])
    if not lean_formal._pinned_workspace(root):
        raise RuntimeError("Lean 项目已创建，但与锁定的 Lean/Mathlib 版本不一致。")
    job.set_phase("verify", "正在做安装后检查（Lean 内核试运行）")
    _smoke_check(job, root, lake, env)
    # The unpacked build is what Lean reads; the compressed archives are only
    # needed to unpack again and would double the disk footprint.
    shutil.rmtree(lean_formal.tools_home() / "mathlib-cache", ignore_errors=True)


# --------------------------------------------------------------------------- #
# Bundle installer (default path)
# --------------------------------------------------------------------------- #

def _bundle_archive_path(tools_home: Path, plat: str, version: str) -> Path:
    return tools_home / f"aleggo-lean-bundle-{plat}-{version}.{lean_bundle.BUNDLE_FORMAT}"


def _install_bundle_flow(job: _InstallJob, source: dict[str, Any], version: str) -> None:
    """Shared flow for install and upgrade: fetch -> verify -> extract -> smoke."""
    tools_home = lean_formal.tools_home()
    elan_home = lean_formal.managed_elan_home()
    root = lean_formal.managed_workspace()
    plat = source["platform"]
    tools_home.mkdir(parents=True, exist_ok=True)

    # Atomic upgrade: when a healthy bundle install already exists, the new
    # bundle is extracted to "*.new" directories and only swapped in after the
    # smoke test passes -- a failed upgrade never destroys the teacher's
    # working environment.  Fresh installs (no bundle record) keep the simple
    # delete-then-extract path.
    old_record = lean_bundle.installed_bundle_info(tools_home)
    atomic = bool(old_record.get("version")) and elan_home.is_dir() and root.is_dir()

    job.set_phase("check", "正在检查磁盘空间与安装包来源")
    _pause_point(job)
    required = lean_bundle.UPGRADE_REQUIRED_DISK_BYTES if atomic else lean_bundle.REQUIRED_DISK_BYTES
    if _free_bytes(tools_home) < required:
        raise LeanBundleError("DISK_FULL", f"可用空间不足，需要约 {required / 1024 ** 3:.0f} GB。")

    archive = _bundle_archive_path(tools_home, plat, version)
    sha256 = ""
    archive_format = lean_bundle.BUNDLE_FORMAT
    if source["kind"] == "local":
        job.emit("使用本地离线安装包：" + str(source["path"]))
        sha256, archive = source["sha256"], source["path"]
        if str(archive).endswith(".tar.gz"):
            archive_format = "tar.gz"  # 测试替身；正式包恒为 tar.zst
    else:
        artifact = source["artifact"]
        sha256, archive_format = artifact["sha256"], artifact.get("format", lean_bundle.BUNDLE_FORMAT)
        job.set_phase("download", f"正在下载 Lean 环境安装包（{artifact['size'] / 1024 ** 3:.1f} GB，可暂停/断点续传）")
        job.set_progress(0, artifact["size"], 0.0)
        try:
            lean_bundle.download_with_resume(
                artifact["url"], archive, expected_size=artifact["size"], sha256=sha256,
                state_path=tools_home / lean_bundle.DOWNLOAD_STATE_NAME,
                progress_cb=lambda done, total, speed: job.set_progress(done, total, speed),
                pause_event=job.paused, cancel_event=job.cancel)
        except lean_bundle.DownloadCancelled:
            raise
        except LeanBundleError:
            raise
        except Exception as exc:
            raise LeanBundleError(lean_bundle.classify_exception(exc), str(exc)) from exc
        job.emit(f"下载完成：{archive.name}")

    job.set_phase("verify", "正在校验安装包完整性（SHA-256）")
    _pause_point(job)
    try:
        lean_bundle.verify_sha256(archive, sha256)
    except LeanBundleError:
        raise
    except Exception as exc:
        raise LeanBundleError(lean_bundle.classify_exception(exc), str(exc)) from exc
    job.emit("校验通过")

    # Remove any previous (broken or outdated) managed install.  Teacher
    # projects live in the application data directory, never under tools_home.
    job.set_phase("extract", "正在解压安装（约 12 GB，请稍候）")
    _pause_point(job)
    if atomic:
        elan_target = elan_home.parent / (elan_home.name + ".new")
        root_target = root.parent / (root.name + ".new")
    else:
        elan_target, root_target = elan_home, root
    # Drop leftovers from a previously crashed upgrade attempt (any path).
    for stale in (elan_home.parent / (elan_home.name + ".new"), root.parent / (root.name + ".new"),
                  elan_home.parent / (elan_home.name + ".bak"), root.parent / (root.name + ".bak")):
        if stale.is_dir() or stale.is_symlink():
            shutil.rmtree(stale, ignore_errors=True)
    if not atomic:
        for stale in (elan_home, root):
            if stale.is_dir() or stale.is_symlink():
                shutil.rmtree(stale, ignore_errors=True)
    new_dirs = (elan_target, root_target) if atomic else None
    try:
        job.set_progress(0, 0, 0.0)
        try:
            meta = lean_bundle.extract_bundle(
                archive, elan_target, root_target, archive_format=archive_format,
                progress_cb=lambda done, total: job.set_progress(done, total, 0.0),
                cancel_event=job.cancel)
        except lean_bundle.DownloadCancelled:
            raise
        except LeanBundleError:
            raise
        except Exception as exc:
            raise LeanBundleError(lean_bundle.classify_exception(exc), str(exc)) from exc
        job.emit(f"解压完成：bundle {meta.get('bundle_version', version)}")

        job.set_phase("smoke", "正在做安装后检查（Lean 内核试运行）")
        _pause_point(job)
        env = _environment(elan_target)
        lake = elan_target / "bin" / ("lake.exe" if os.name == "nt" else "lake")
        if not lake.is_file():
            raise LeanBundleError("SMOKE_FAILED", "解压后的安装包里找不到 lake 可执行文件。")
        try:
            _smoke_check(job, root_target, lake, env)
        except LeanInstallCancelled:
            raise
        except Exception as exc:
            raise LeanBundleError("SMOKE_FAILED", f"内核冒烟测试失败：{exc}") from exc

        if atomic:
            job.emit("新环境检查通过，正在替换旧环境")
            # Two-phase swap with rollback: move the old trees aside first,
            # then move the new ones in. If the second rename fails we restore
            # the backups instead of leaving a half-swapped environment.
            swaps: list[tuple[Path, Path, Path]] = []  # (live, new, backup)
            try:
                for live, new in ((elan_home, elan_target), (root, root_target)):
                    backup = live.parent / (live.name + ".bak")
                    # Register before renaming: if either rename below fails,
                    # the pair is still rolled back.
                    swaps.append((live, new, backup))
                    live.rename(backup)
                    new.rename(live)
            except Exception:
                for live, _new, backup in reversed(swaps):
                    if not (backup.is_dir() or backup.is_symlink()):
                        continue  # first rename never ran; nothing moved
                    # live holds the new tree, or nothing if the second rename failed
                    if live.is_dir() or live.is_symlink():
                        shutil.rmtree(live, ignore_errors=True)
                    backup.rename(live)
                raise
            for _live, _new, backup in swaps:
                shutil.rmtree(backup, ignore_errors=True)
    except Exception:
        if new_dirs is not None:
            # Failed upgrade: drop the half-unpacked "*.new" trees.  The old
            # working install was never touched.
            for stale in new_dirs:
                if stale.is_dir() or stale.is_symlink():
                    shutil.rmtree(stale, ignore_errors=True)
        raise

    lean_bundle.record_installed_bundle(tools_home, str(meta.get("bundle_version") or version), plat, sha256)
    if source["kind"] == "remote":
        archive.unlink(missing_ok=True)  # unpacked tree is what Lean reads
    job.emit("Lean + Mathlib 已就绪")


def _install(job: _InstallJob) -> None:
    """Dispatch: prebuilt bundle by default, legacy source install as fallback."""
    if (os.environ.get("ALEGGO_RESEARCH_STANDALONE") == "1" and not job.from_source
            and not os.environ.get("ALEGGO_LEAN_MIRROR_URL", "").strip()
            and not os.environ.get("ALEGGO_LEAN_BUNDLE_PATH", "").strip()):
        plat = lean_bundle.platform_id()
        if not any(path.is_file() for path in lean_bundle.local_bundle_candidates(plat)):
            if job.mode == "upgrade":
                raise LeanBundleError("NO_BUNDLE_FOR_PLATFORM", "尚未配置可用的预编译包更新源。")
            job.from_source = True
            job.emit("未配置预编译包源，将使用官方 elan 和 Mathlib 安装；需要 Git 与网络。")
    if job.from_source:
        _install_from_source(job)
        return
    tools_home = lean_formal.tools_home()
    try:
        source = lean_bundle.resolve_source(tools_home)
    except LeanBundleError as exc:
        if exc.code == "NO_BUNDLE_FOR_PLATFORM":
            job.emit("该平台暂无预编译包，切换为传统安装模式")
            _install_from_source(job)
            return
        raise
    version = source.get("version") or lean_bundle.BUNDLE_VERSION
    _install_bundle_flow(job, source, version)


def _worker(job: _InstallJob) -> None:
    try:
        _install(job)
    except (LeanInstallCancelled, lean_bundle.DownloadCancelled):
        with job.lock:
            job.status, job.message = "cancelled", "安装已取消"
            job.error_code = "CANCELLED"
        if job.auto:
            lean_bundle.mark_auto_declined(lean_formal.tools_home())
    except LeanBundleError as exc:
        with job.lock:
            job.status, job.error_code = "failed", exc.code
            help_text = lean_bundle.error_help(exc.code)
            job.error = help_text["title"] + (f"：{exc.detail}" if exc.detail else "")
            job.message = "安装失败"
    except Exception as exc:  # reported to the UI with the log tail
        code = lean_bundle.classify_exception(exc)
        with job.lock:
            job.status, job.error_code = "failed", code
            help_text = lean_bundle.error_help(code)
            job.error = f"{help_text['title']}：{exc}"[:2000]
            job.message = "安装失败"
    else:
        with job.lock:
            job.status, job.message = "succeeded", "Lean 和 Mathlib 已就绪，可以开始内核检查。"
            job.error_code = ""
    finally:
        with job.lock:
            job.finished_at = _now()


def _start_job(*, mode: str, auto: bool, from_source: bool) -> dict[str, Any]:
    with _JOB.lock:
        thread = _JOB.thread
        if _JOB.status == "paused":
            # Resume a paused job. Pause is blocking (the worker thread stays
            # alive and sleeps), so no thread restart is needed here.
            _JOB.status, _JOB.message = "running", "继续下载"
            _JOB.paused.clear()
            _JOB.cancel.clear()
            thread = None  # nothing to start; fall through to snapshot
        elif not (_JOB.status == "running" and thread is not None and thread.is_alive()):
            _JOB.status, _JOB.mode, _JOB.auto, _JOB.from_source = "running", mode, auto, from_source
            _JOB.phase, _JOB.error, _JOB.error_code, _JOB.message = "check", "", "", "正在启动"
            _JOB.downloaded_bytes, _JOB.total_bytes, _JOB.speed_bps = 0, 0, 0.0
            _JOB.started_at, _JOB.finished_at = _now(), ""
            _JOB.log.clear()
            _JOB.cancel.clear()
            _JOB.paused.clear()
            _JOB.thread = threading.Thread(target=_worker, args=(_JOB,), name="aleggo-lean-install", daemon=True)
            thread = _JOB.thread
        else:
            thread = None  # already running; fall through to snapshot
    if thread is not None:
        thread.start()
    # NOTE: snapshot() takes _JOB.lock itself, so it must be called outside
    # the with-block above (threading.Lock is not reentrant).
    return _JOB.snapshot()


def start_install(*, auto: bool = False, from_source: bool = False) -> dict[str, Any]:
    """Start (or resume) the installer; idempotent while a job is running."""
    return _start_job(mode="install", auto=auto, from_source=from_source)


def start_upgrade(*, auto: bool = False) -> dict[str, Any]:
    """Upgrade to the latest bundle release (same flow, new version)."""
    return _start_job(mode="upgrade", auto=auto, from_source=False)


def pause_install() -> dict[str, Any]:
    """Pause a running job, or resume a paused one. Returns the snapshot.

    Pause is blocking: the worker thread stays alive and sleeps (at the next
    chunk boundary, phase boundary, or subprocess poll), so resume just clears
    the event and never needs to restart the thread.
    """
    with _JOB.lock:
        if _JOB.status == "running":
            _JOB.paused.set()
            _JOB.status, _JOB.message = "paused", "已暂停，进度已保存，可随时继续。"
        elif _JOB.status == "paused":
            _JOB.paused.clear()
            _JOB.cancel.clear()
            _JOB.status, _JOB.message = "running", "继续下载"
    # NOTE: snapshot() takes _JOB.lock itself; calling it inside the with-block
    # above would deadlock (threading.Lock is not reentrant).
    return _JOB.snapshot()


def cancel_install() -> dict[str, Any]:
    _JOB.cancel.set()
    _JOB.paused.clear()  # let a paused worker wake up and see the cancel
    process = _JOB.process
    if process is not None and process.poll() is None:
        process.kill()
    return _JOB.snapshot()


def wait_for_install(timeout: float | None = None) -> dict[str, Any]:
    thread = _JOB.thread
    if thread is not None:
        thread.join(timeout)
    return _JOB.snapshot()


def _cli() -> int:  # pragma: no cover - manual operator entry point
    """``python -m aleggo.study.lean_setup [status|install|upgrade]``."""
    import json

    command = sys.argv[1] if len(sys.argv) > 1 else "status"
    if command in ("install", "upgrade"):
        starter = start_upgrade if command == "upgrade" else start_install
        starter()
        last = 0
        while True:
            snapshot = wait_for_install(2)
            for line in snapshot["log"][last:]:
                print(line)
            last = len(snapshot["log"])
            if snapshot["status"] not in ("running", "paused"):
                print(snapshot["status"], snapshot["error"])
                return 0 if snapshot["status"] == "succeeded" else 1
    print(json.dumps(lean_status(), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_cli())
