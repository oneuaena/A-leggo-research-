"""Prebuilt Lean + Mathlib distribution: version pinning, mirrors, resumable downloads.

This module is the single source of truth for the "out of the box" Lean
distribution:

* version pinning (``LEAN_TOOLCHAIN`` / ``MATHLIB_REV`` / ``BUNDLE_VERSION``);
* the release manifest (``releases.json``) served from a China mirror with
  fallback to the official source;
* resumable, speed-limited downloads with SHA-256 verification;
* extraction of the prebuilt bundle into the managed tools directory;
* a Chinese error catalogue (``LEAN_ERRORS``) so the UI never shows a raw
  English traceback to a teacher.

Deliberately **stdlib-only**: the download path uses ``urllib`` so a broken
third-party install can never block a teacher's first launch.  The only new
dependency of the whole feature is ``zstandard`` (needed to unpack the
``tar.zst`` bundles); it is imported lazily and its absence produces a clear
Chinese error instead of an ImportError.
"""

from __future__ import annotations

import hashlib
import http.client
import io
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import time
import urllib.error
import urllib.request
from typing import Any, Callable

# --------------------------------------------------------------------------- #
# Version pinning: the single source of truth.
# lean_formal.PINNED_LEAN / PINNED_MATHLIB re-export these for compatibility.
# --------------------------------------------------------------------------- #

LEAN_TOOLCHAIN = "leanprover/lean4:v4.32.0"
MATHLIB_REV = "v4.32.0"
# Bump whenever the toolchain, the Mathlib rev, or the bundle contents change.
# Format: "<mathlib-rev>-<build-number>".
BUNDLE_VERSION = "v4.32.0-1"
BUNDLE_FORMAT = "tar.zst"

# A fresh bundle install transiently holds the archive (~4 GB) plus the
# unpacked tree (~12 GB).  The old 8 GB check contradicted the measured
# 12 GB install size; 16 GB is the honest minimum.
REQUIRED_DISK_BYTES = 16 * 1024 ** 3
# Atomic upgrade needs the old install (12 GB) + the new archive (4 GB) +
# the new unpacked tree (12 GB) to coexist briefly.
UPGRADE_REQUIRED_DISK_BYTES = 28 * 1024 ** 3

# Placeholder: operations fills in the real CDN address before release.
# Overridable at runtime with ALEGGO_LEAN_MIRROR_URL (comma-separated).
DEFAULT_MIRROR_URL = "https://cdn.example.com/aleggo/lean/releases.json"
# Official fallback (GitHub).  Set ALEGGO_LEAN_OFFICIAL_FALLBACK=0 to disable
# (e.g. an intranet-only computer lab that must never touch the internet).
OFFICIAL_MANIFEST_URL = "https://raw.githubusercontent.com/aleggo/lean-bundles/main/releases.json"

DOWNLOAD_STATE_NAME = ".lean-bundle.download.json"
INSTALL_STATE_NAME = "lean-install-state.json"
INSTALLED_BUNDLE_NAME = "lean-bundle.json"

PLATFORMS = ("windows-x64", "macos-arm64", "macos-x64", "linux-x64")


class LeanBundleError(Exception):
    """A failure with a stable ``code`` from :data:`LEAN_ERRORS`."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        help_text = LEAN_ERRORS.get(code, {})
        message = help_text.get("title", code)
        if detail:
            message += f"：{detail}"
        super().__init__(message)


class DownloadPaused(LeanBundleError):
    """Retained for backward compatibility.

    Pause is now blocking (the downloader sleeps instead of raising), so this
    exception is no longer raised by ``download_with_resume``.
    """

    def __init__(self) -> None:
        super().__init__("PAUSED")


class DownloadCancelled(LeanBundleError):
    def __init__(self) -> None:
        super().__init__("CANCELLED")


# code -> {title, reason, fix}: everything the teacher sees, in Chinese.
LEAN_ERRORS: dict[str, dict[str, str]] = {
    "DISK_FULL": {
        "title": "磁盘空间不足",
        "reason": "安装需要约 16 GB 可用空间（下载约 4 GB + 解压后约 12 GB）。",
        "fix": "清理磁盘后重试；或设置环境变量 ALEGGO_TOOLS_HOME 指向空间更大的磁盘，然后重新安装。",
    },
    "HASH_MISMATCH": {
        "title": "文件校验失败，已自动删除坏文件",
        "reason": "下载的安装包 SHA-256 与官方记录不一致，可能是网络波动导致文件损坏。",
        "fix": "点击「重新下载」即可（会从断点继续）；如果反复出现，请联系运维。",
    },
    "ALL_SOURCES_FAILED": {
        "title": "所有下载源都连不上",
        "reason": "国内镜像和官方源都下载失败，可能是学校网络限制或代理问题。",
        "fix": "检查网络 / 代理设置后重试；或找管理员索取「离线安装包」，放到应用目录旁后重新安装。",
    },
    "EXTRACT_FAILED": {
        "title": "解压失败",
        "reason": "安装包解压到一半出错，常见原因是磁盘空间不足或杀毒软件拦截。",
        "fix": "先确认磁盘仍有空间，暂时退出杀毒软件后重试；仍失败请联系运维并附上错误详情。",
    },
    "SMOKE_FAILED": {
        "title": "安装后自检没通过",
        "reason": "预编译包在本机上没能通过 Lean 内核冒烟测试，可能与系统版本不兼容。",
        "fix": "你原来的环境没有被改动。请把错误详情发给运维，我们会为你的平台重新打包。",
    },
    "NO_BUNDLE_FOR_PLATFORM": {
        "title": "该平台暂无预编译包",
        "reason": "当前操作系统 / CPU 架构还没有做好的预编译安装包。",
        "fix": "将自动改用传统方式安装（较慢，需要 git）；也可以联系运维申请为你的平台打包。",
    },
    "NETWORK_TIMEOUT": {
        "title": "下载超时，已保留进度",
        "reason": "网络波动导致下载中断，已下载的部分都保留着。",
        "fix": "点击「继续下载」会从断点续传，不需要从头开始。",
    },
    "DEPENDENCY_MISSING": {
        "title": "缺少解压组件",
        "reason": "安装包是 tar.zst 格式，需要 zstandard 组件来解压，但当前环境里没有装。",
        "fix": "请管理员执行 pip install zstandard 后重试；或联系运维获取 zip 格式的离线包。",
    },
    "PAUSED": {"title": "已暂停", "reason": "下载已暂停，进度已保存。", "fix": "点击「继续下载」可从断点恢复。"},
    "CANCELLED": {"title": "已取消", "reason": "安装已取消，临时文件已清理。", "fix": "以后可随时点击「一键安装」重新开始。"},
    "UNKNOWN": {
        "title": "安装遇到未知错误",
        "reason": "发生了预料之外的错误。",
        "fix": "请把错误详情发给运维；也可稍后重试。",
    },
}


def error_help(code: str) -> dict[str, str]:
    """Chinese {title, reason, fix} for an error code (safe for unknown codes)."""
    help_text = LEAN_ERRORS.get(code) or LEAN_ERRORS["UNKNOWN"]
    return {"code": code, "title": help_text["title"], "reason": help_text["reason"], "fix": help_text["fix"]}


def classify_exception(exc: BaseException) -> str:
    if isinstance(exc, LeanBundleError):
        return exc.code
    text = f"{type(exc).__name__}: {exc}"
    lowered = text.lower()
    if "no space" in lowered or "not enough space" in lowered or "磁盘" in text:
        return "DISK_FULL"
    if "timed out" in lowered or "timeout" in lowered:
        return "NETWORK_TIMEOUT"
    return "UNKNOWN"


# --------------------------------------------------------------------------- #
# Platform and mirrors
# --------------------------------------------------------------------------- #

def platform_id() -> str:
    system = platform.system().lower()
    machine = platform.machine().lower()
    if system == "windows":
        return "windows-x64"
    if system == "darwin":
        return "macos-arm64" if machine in ("arm64", "aarch64") else "macos-x64"
    if system == "linux":
        return "linux-x64"
    raise LeanBundleError("NO_BUNDLE_FOR_PLATFORM", f"不支持的操作系统：{platform.system()} {platform.machine()}")


def manifest_urls() -> list[str]:
    """Manifest URLs to try in order: configured mirrors, then official."""
    urls: list[str] = []
    configured = os.environ.get("ALEGGO_LEAN_MIRROR_URL", "").strip()
    urls.extend(part.strip() for part in configured.split(",") if part.strip())
    if not urls:
        urls.append(DEFAULT_MIRROR_URL)
    if os.environ.get("ALEGGO_LEAN_OFFICIAL_FALLBACK", "1").strip() != "0":
        urls.append(OFFICIAL_MANIFEST_URL)
    # Preserve order, drop duplicates.
    return list(dict.fromkeys(urls))


def _http_get(url: str, headers: dict[str, str] | None = None, timeout: float = 30) -> bytes:
    request = urllib.request.Request(url, headers=headers or {"User-Agent": "aleggo-lean-bundle/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        raise LeanBundleError("ALL_SOURCES_FAILED", f"下载源返回 HTTP {exc.code}：{url}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise LeanBundleError("NETWORK_TIMEOUT", f"连接下载源失败：{url}（{exc}）") from exc


def fetch_manifest() -> tuple[dict[str, Any], str]:
    """Fetch releases.json from the first reachable mirror. Returns (manifest, url)."""
    last_error: Exception | None = None
    for url in manifest_urls():
        try:
            manifest = json.loads(_http_get(url).decode("utf-8"))
            if not isinstance(manifest, dict) or "releases" not in manifest:
                raise ValueError("manifest 格式不正确")
            return manifest, url
        except (LeanBundleError, ValueError, OSError) as exc:
            last_error = exc
    raise LeanBundleError("ALL_SOURCES_FAILED", f"所有镜像都不可用：{last_error}")


def get_artifact(manifest: dict[str, Any], version: str, plat: str) -> dict[str, Any]:
    try:
        release = manifest["releases"][version]
        artifact = release["platforms"][plat]
    except KeyError as exc:
        raise LeanBundleError("NO_BUNDLE_FOR_PLATFORM", f"版本 {version} 没有 {plat} 的预编译包。") from exc
    for field in ("url", "sha256", "size"):
        if field not in artifact:
            raise LeanBundleError("NO_BUNDLE_FOR_PLATFORM", f"manifest 中 {plat} 缺少字段 {field}。")
    return {
        "url": str(artifact["url"]),
        "sha256": str(artifact["sha256"]).lower(),
        "size": int(artifact["size"]),
        "format": str(artifact.get("format", BUNDLE_FORMAT)),
        "lean_toolchain": str(release.get("lean_toolchain", LEAN_TOOLCHAIN)),
        "mathlib_rev": str(release.get("mathlib_rev", MATHLIB_REV)),
    }


def latest_bundle_version(manifest: dict[str, Any]) -> str:
    return str(manifest.get("latest", BUNDLE_VERSION))


def version_sort_key(version: str) -> tuple:
    """Sortable key for bundle versions like "v4.32.0-1".

    Numeric-aware: "v4.32.0-10" sorts after "v4.32.0-2" (plain string max()
    would rank them wrong).
    """
    return tuple(int(p) for p in re.findall(r"\d+", version)) or (0,)


def is_newer_version(candidate: str, current: str) -> bool:
    """True if ``candidate`` is a newer bundle version than ``current``.

    Used so "check for updates" never suggests a downgrade when the
    installed build is newer than the manifest's latest.
    """
    return version_sort_key(candidate) > version_sort_key(current)


# --------------------------------------------------------------------------- #
# Install state (auto-download consent, installed version)
# --------------------------------------------------------------------------- #

def _state_path(tools_home: Path, name: str = INSTALL_STATE_NAME) -> Path:
    return tools_home / name


def read_install_state(tools_home: Path) -> dict[str, Any]:
    path = _state_path(tools_home)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def write_install_state(tools_home: Path, **fields: Any) -> None:
    tools_home.mkdir(parents=True, exist_ok=True)
    state = read_install_state(tools_home)
    state.update(fields)
    _state_path(tools_home).write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def mark_auto_declined(tools_home: Path) -> None:
    write_install_state(tools_home, auto_declined=True)


def auto_download_allowed(tools_home: Path) -> bool:
    if os.environ.get("ALEGGO_LEAN_AUTO_DOWNLOAD", "1").strip() == "0":
        return False
    return not bool(read_install_state(tools_home).get("auto_declined"))


def installed_bundle_info(tools_home: Path) -> dict[str, Any]:
    path = tools_home / INSTALLED_BUNDLE_NAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def record_installed_bundle(tools_home: Path, version: str, plat: str, sha256: str) -> None:
    tools_home.mkdir(parents=True, exist_ok=True)
    (tools_home / INSTALLED_BUNDLE_NAME).write_text(
        json.dumps(
            {"version": version, "platform": plat, "sha256": sha256,
             "installed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "lean_toolchain": LEAN_TOOLCHAIN,
             "mathlib_rev": MATHLIB_REV},
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )


# --------------------------------------------------------------------------- #
# Local (offline) bundle discovery
# --------------------------------------------------------------------------- #

def local_bundle_candidates(plat: str) -> list[Path]:
    """Where an offline bundle may already be waiting (no network needed)."""
    name = f"aleggo-lean-bundle-{plat}.{BUNDLE_FORMAT}"
    candidates: list[Path] = []
    configured = os.environ.get("ALEGGO_LEAN_BUNDLE_PATH", "").strip()
    if configured:
        candidates.append(Path(configured).expanduser())
    # Next to the application executable / the current working directory.
    candidates.append(Path(sys.executable).resolve().parent / name)
    candidates.append(Path.cwd() / name)
    return candidates


def _read_sidecar_sha256(bundle_path: Path) -> str:
    sidecar = Path(str(bundle_path) + ".sha256")  # e.g. "...tar.zst" -> "...tar.zst.sha256"
    try:
        text = sidecar.read_text(encoding="utf-8").strip().split()[0]
    except OSError as exc:
        raise LeanBundleError("HASH_MISMATCH", f"离线包缺少同名的 .sha256 校验文件：{sidecar.name}") from exc
    if len(text) != 64 or any(c not in "0123456789abcdefABCDEF" for c in text):
        raise LeanBundleError("HASH_MISMATCH", f"离线包的 .sha256 文件内容不正确：{sidecar.name}")
    return text.lower()


def resolve_source(tools_home: Path, version: str | None = None) -> dict[str, Any]:
    """Decide where the bundle comes from: local file or a remote mirror.

    Returns {"kind": "local", "path": Path, "sha256": str}
    or {"kind": "remote", "artifact": {...}, "manifest_url": str}.
    Raises LeanBundleError when nothing is available.
    """
    plat = platform_id()
    for candidate in local_bundle_candidates(plat):
        if candidate.is_file():
            return {"kind": "local", "path": candidate, "sha256": _read_sidecar_sha256(candidate), "platform": plat}
    manifest, manifest_url = fetch_manifest()
    wanted = version or latest_bundle_version(manifest)
    artifact = get_artifact(manifest, wanted, plat)
    return {"kind": "remote", "artifact": artifact, "manifest_url": manifest_url,
            "version": wanted, "platform": plat}


# --------------------------------------------------------------------------- #
# Resumable, speed-limited download (urllib, stdlib-only)
# --------------------------------------------------------------------------- #

ProgressCallback = Callable[[int, int | None, float], None]  # downloaded, total, bytes/sec


def _load_download_state(state_path: Path) -> dict[str, Any]:
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_download_state(state_path: Path, **fields: Any) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state = _load_download_state(state_path)
    state.update(fields)
    state_path.write_text(json.dumps(state), encoding="utf-8")


def _max_mbps() -> float:
    try:
        return max(0.0, float(os.environ.get("ALEGGO_LEAN_DOWNLOAD_MBPS", "10")))
    except ValueError:
        return 10.0


def download_with_resume(
    url: str,
    dest: Path,
    *,
    expected_size: int | None = None,
    sha256: str | None = None,
    state_path: Path | None = None,
    progress_cb: ProgressCallback | None = None,
    pause_event: Any = None,   # threading.Event-like
    cancel_event: Any = None,  # threading.Event-like
    max_mbps: float | None = None,
    chunk_size: int = 1024 * 1024,
    timeout: float = 60,
) -> Path:
    """Download ``url`` to ``dest`` with HTTP Range resume and speed pacing.

    Pause is blocking: while ``pause_event`` is set the call sleeps (progress
    is kept on disk) instead of raising, so the caller thread stays alive and
    resume never needs to restart it. Raises DownloadCancelled when cancelled,
    LeanBundleError("HASH_MISMATCH") on checksum failure.
    """
    if max_mbps is None:
        max_mbps = _max_mbps()
    state_path = state_path or dest.parent / DOWNLOAD_STATE_NAME
    state = _load_download_state(state_path)

    downloaded = 0
    if state.get("url") == url and dest.is_file():
        downloaded = int(state.get("downloaded", 0) or 0)
        if downloaded > dest.stat().st_size:
            downloaded = 0  # state is stale; start over
    if downloaded:
        dest.parent.mkdir(parents=True, exist_ok=True)
        mode, headers = "r+b", {"Range": f"bytes={downloaded}-"}
    else:
        dest.parent.mkdir(parents=True, exist_ok=True)
        mode, headers = "wb", {}
    headers["User-Agent"] = "aleggo-lean-bundle/1.0"

    digest = hashlib.sha256()
    if downloaded and sha256:
        # Re-hash the prefix so the final digest covers the whole file.
        with open(dest, "rb") as existing:
            for chunk in iter(lambda: existing.read(4 * 1024 * 1024), b""):
                digest.update(chunk)

    request = urllib.request.Request(url, headers=headers)
    try:
        response = urllib.request.urlopen(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        if exc.code == 416 and expected_size and downloaded >= expected_size:
            pass  # already complete; fall through to verification
        else:
            raise LeanBundleError("NETWORK_TIMEOUT", f"下载请求失败（HTTP {exc.code}）：{url}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise LeanBundleError("NETWORK_TIMEOUT", f"连接下载源失败：{url}（{exc}）") from exc
    else:
        status = getattr(response, "status", 200)
        if status == 206:
            content_range = response.headers.get("Content-Range", "")
            # "bytes 1234-5678/9012"
            total: int | None = None
            if "/" in content_range:
                try:
                    total = int(content_range.rsplit("/", 1)[1])
                except ValueError:
                    total = None
        elif status == 200 and downloaded:
            # Server ignored Range: restart from scratch.
            downloaded, mode = 0, "wb"
            digest = hashlib.sha256()
            total = None
        else:
            total = None
        if total is None and expected_size:
            total = expected_size if not downloaded else expected_size

        # Pacing: keep the average rate at or below max_mbps from the first
        # byte (a per-second window would never engage on fast networks).
        pace_t0 = time.monotonic()
        pace_bytes0 = downloaded
        last_report = 0.0
        t0 = time.monotonic()
        bytes0 = downloaded
        with open(dest, mode) as handle:
            if mode == "r+b":
                handle.seek(downloaded)
            while True:
                if cancel_event is not None and cancel_event.is_set():
                    raise DownloadCancelled()
                if pause_event is not None and pause_event.is_set():
                    # Blocking pause: persist state, then sleep until resumed.
                    # The worker thread stays alive, so resume never needs to
                    # restart it (no thread-restart races).
                    _save_download_state(state_path, url=url, downloaded=downloaded,
                                         total=total, sha256=sha256 or "")
                    while pause_event.is_set():
                        if cancel_event is not None and cancel_event.is_set():
                            raise DownloadCancelled()
                        time.sleep(0.2)
                try:
                    chunk = response.read(chunk_size)
                except (OSError, TimeoutError, http.client.HTTPException) as exc:
                    # The server/CDN may drop the connection while paused.
                    # State is saved continuously, so the next "继续下载"
                    # resumes from here instead of starting over.
                    _save_download_state(state_path, url=url, downloaded=downloaded,
                                         total=total, sha256=sha256 or "")
                    raise LeanBundleError(
                        "NETWORK_TIMEOUT",
                        f"下载连接中断（{exc}），已保留进度，点击「继续下载」可从断点恢复。") from exc
                if not chunk:
                    break
                handle.write(chunk)
                digest.update(chunk)
                downloaded += len(chunk)
                if max_mbps > 0:
                    expected = (downloaded - pace_bytes0) / (max_mbps * 1_000_000)
                    delay = expected - (time.monotonic() - pace_t0)
                    if delay > 0:
                        time.sleep(delay)
                now = time.monotonic()
                if progress_cb is not None and now - last_report >= 1.0:
                    last_report = now
                    speed = (downloaded - bytes0) / max(now - t0, 0.001)
                    progress_cb(downloaded, total, speed)
                # Persist resume state regularly (cheap: one small JSON write).
                _save_download_state(state_path, url=url, downloaded=downloaded,
                                     total=total, sha256=sha256 or "")
        response.close()

    if expected_size and downloaded != expected_size:
        raise LeanBundleError("HASH_MISMATCH",
                              f"下载的文件大小不对（{downloaded} / {expected_size} 字节），文件已损坏。")
    if sha256:
        actual = digest.hexdigest()
        # If we resumed, digest already covers the prefix; verify by re-read.
        if downloaded and state.get("url") == url:
            actual = _sha256_of_file(dest)
        if actual != sha256.lower():
            dest.unlink(missing_ok=True)
            try:
                state_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise LeanBundleError("HASH_MISMATCH", "SHA-256 与官方记录不一致，坏文件已自动删除。")
    try:
        state_path.unlink(missing_ok=True)
    except OSError:
        pass
    return dest


def _sha256_of_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_sha256(path: Path, expected: str) -> None:
    """Raise LeanBundleError("HASH_MISMATCH") when the file does not match."""
    if _sha256_of_file(path) != expected.lower():
        raise LeanBundleError("HASH_MISMATCH", f"{path.name} 的 SHA-256 与官方记录不一致。")


# --------------------------------------------------------------------------- #
# Extraction
# --------------------------------------------------------------------------- #

def _open_tar(archive: Path, archive_format: str) -> tuple[tarfile.TarFile, Any]:
    """Open the bundle archive. ``archive_format`` is "tar.zst" or "tar.gz"."""
    if archive_format == "tar.zst":
        try:
            import zstandard
        except ImportError as exc:
            raise LeanBundleError(
                "DEPENDENCY_MISSING",
                "需要 zstandard 组件解压 tar.zst（pip install zstandard）。") from exc
        raw = open(archive, "rb")
        stream = zstandard.ZstdDecompressor().stream_reader(raw)
        # Stream mode: fine for extraction; keep `raw` to close afterwards.
        return tarfile.open(fileobj=stream, mode="r|"), (stream, raw)
    if archive_format == "tar.gz":
        return tarfile.open(archive, mode="r:gz"), None
    raise LeanBundleError("EXTRACT_FAILED", f"不支持的安装包格式：{archive_format}")


def _safe_extract(tar: tarfile.TarFile, member: tarfile.TarInfo, root: Path) -> None:
    """Extract one member under ``root``, guarding against path traversal.

    Works on old Pythons too: ``filter="data"`` when available, plus a manual
    containment check that never depends on the filter argument.
    """
    target = (root / member.name).resolve()
    if target != root.resolve() and not target.is_relative_to(root.resolve()):
        raise LeanBundleError("EXTRACT_FAILED", f"安装包内有非法路径：{member.name}")
    try:
        tar.extract(member, path=root, filter="data")
    except TypeError:
        # Python without the `filter` parameter: the manual check above still holds.
        tar.extract(member, path=root)
    except OSError as exc:
        raise LeanBundleError("EXTRACT_FAILED", f"解压 {member.name} 失败：{exc}") from exc


def extract_bundle(
    archive: Path,
    dest_elan: Path,
    dest_workspace: Path,
    *,
    archive_format: str = BUNDLE_FORMAT,
    progress_cb: Callable[[int, int], None] | None = None,  # extracted_bytes, total_bytes (0 = unknown)
    cancel_event: Any = None,
) -> dict[str, Any]:
    """Unpack a bundle archive into the managed directories.

    The archive must contain a single top-level ``aleggo-lean-bundle/`` folder
    with ``bundle.json`` (first member, written by the build script), ``elan/``
    and ``lean4-project/`` inside.  Members are processed strictly in stream
    order so ``tar.zst`` never needs random access.  Returns the parsed
    ``bundle.json``.
    """
    tar, handles = _open_tar(archive, archive_format)
    prefix = "aleggo-lean-bundle/"
    bundle_meta: dict[str, Any] = {}
    total = 0
    done = 0
    try:
        for member in tar:
            if cancel_event is not None and cancel_event.is_set():
                raise DownloadCancelled()
            name = member.name
            if not name.startswith(prefix) or name == prefix:
                continue
            relative = name[len(prefix):]
            if relative == "bundle.json":
                extracted = tar.extractfile(member)
                if extracted is not None:
                    bundle_meta = json.loads(extracted.read().decode("utf-8"))
                    packed_platform = str(bundle_meta.get("platform") or "")
                    if packed_platform and packed_platform != platform_id():
                        raise LeanBundleError(
                            "NO_BUNDLE_FOR_PLATFORM",
                            f"这个安装包是为 {packed_platform} 打的，本机是 {platform_id()}，"
                            f"请下载与本机对应的安装包。")
                    try:
                        total = int(bundle_meta.get("unpacked_bytes") or 0)
                    except (TypeError, ValueError):
                        total = 0
                continue
            if relative.startswith("elan/"):
                root, stripped = dest_elan, relative[len("elan/"):]
            elif relative.startswith("lean4-project/"):
                root, stripped = dest_workspace, relative[len("lean4-project/"):]
            else:
                continue  # ignore unexpected top-level entries
            if not stripped:
                continue
            member.name = stripped  # re-root under the destination directory
            _safe_extract(tar, member, root)
            if member.isfile():
                done += member.size
                if progress_cb is not None:
                    progress_cb(done, total)
    finally:
        tar.close()
        if handles:
            for handle in handles:
                try:
                    handle.close()
                except OSError:
                    pass
    # macOS: a downloaded archive quarantines its binaries; clear it (user-owned
    # files, no sudo needed). Best effort.
    if sys.platform == "darwin":
        try:
            subprocess.run(["xattr", "-cr", str(dest_elan)], timeout=60,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        except OSError:
            pass
    return bundle_meta


# --------------------------------------------------------------------------- #
# Build-side helpers (used by tools/build-lean-bundle.py)
# --------------------------------------------------------------------------- #

def bundle_file_name(plat: str, archive_format: str = BUNDLE_FORMAT) -> str:
    return f"aleggo-lean-bundle-{plat}.{archive_format}"


def write_bundle_meta(directory: Path, plat: str, unpacked_bytes: int = 0,
                    bundle_version: str | None = None) -> Path:
    """Write the ``bundle.json`` that ships inside the archive.

    The build script passes ``unpacked_bytes`` (total size of ``elan/`` +
    ``lean4-project/``) so the installer can show a real extraction progress
    bar while streaming the ``tar.zst``.  The file must be the **first**
    member of the archive.
    """
    directory.mkdir(parents=True, exist_ok=True)
    meta = {"bundle_version": bundle_version or BUNDLE_VERSION, "lean_toolchain": LEAN_TOOLCHAIN,
            "mathlib_rev": MATHLIB_REV, "platform": plat,
            "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "format": BUNDLE_FORMAT, "unpacked_bytes": int(unpacked_bytes)}
    path = directory / "bundle.json"
    path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def sha256_sidecar(archive: Path) -> Path:
    """Write ``<archive>.sha256`` next to the archive; return its path."""
    digest = _sha256_of_file(archive)
    sidecar = Path(str(archive) + ".sha256")
    sidecar.write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
    return sidecar
