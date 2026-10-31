"""Tests for the prebuilt Lean + Mathlib bundle distribution.

Covers: resumable download, hash mismatch, disk-full precheck, mirror
fallback, smoke-test failure (old install untouched), version pinning, and
offline install with the network blocked.

Everything runs against localhost fixtures -- no real network, no real 12 GB
bundle, no zstandard needed (fixtures use tar.gz; the tar.zst code path is
exercised in CI where zstandard is installed).

Run:  python -m unittest tests.aleggo.test_lean_bundle -v
      (also collected by pytest in the full repo)
"""

from __future__ import annotations

import hashlib
import http.server
import json
import os
import shutil
import socket
import stat
import tarfile
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from aleggo.study import lean_bundle, lean_setup
from aleggo.study.lean_bundle import LeanBundleError


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

class _RangeHandler(http.server.BaseHTTPRequestHandler):
    """Serves class-level `payload`; honors Range; optional manifest failure."""
    payload = b""
    manifest_status = 200
    manifest_body = b"{}"

    def log_message(self, *args):  # quiet
        pass

    def do_GET(self):
        if self.path == "/releases.json":
            body = self.manifest_body
            if type(self).manifest_status != 200:
                self.send_response(type(self).manifest_status)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        data = type(self).payload
        range_header = self.headers.get("Range")
        if range_header and range_header.startswith("bytes="):
            try:
                start = int(range_header[len("bytes="):].split("-")[0])
            except ValueError:
                start = 0
            if start >= len(data):
                self.send_response(416)
                self.end_headers()
                return
            chunk = data[start:]
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{len(data) - 1}/{len(data)}")
            self.send_header("Content-Length", str(len(chunk)))
            self.end_headers()
            self.wfile.write(chunk)
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        self.wfile.write(data)


def _serve(handler_cls):
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _make_fake_bundle(tmp: Path, *, good_lake: bool = True,
                       platform: str = "linux-x64",
                       bundle_version: str | None = None) -> tuple[Path, str]:
    """Build a tiny tar.gz bundle with the real on-disk layout."""
    root = tmp / "staging" / "aleggo-lean-bundle"
    elan_bin = root / "elan" / "bin"
    elan_bin.mkdir(parents=True)
    lake = elan_bin / "lake"
    lake.write_text("#!/bin/sh\n" + ("exit 0\n" if good_lake else "echo fake-lake-error >&2\nexit 1\n"))
    lake.chmod(lake.stat().st_mode | stat.S_IEXEC)
    workspace = root / "lean4-project"
    workspace.mkdir(parents=True)
    (workspace / "lean-toolchain").write_text(lean_bundle.LEAN_TOOLCHAIN + "\n", encoding="utf-8")
    (workspace / "lakefile.toml").write_text('name = "aleggo_formal"\n', encoding="utf-8")
    (workspace / "lake-manifest.json").write_text('{"packages": []}', encoding="utf-8")
    (workspace / "README-ALEGGO.txt").write_text("fake bundle\n", encoding="utf-8")
    lean_bundle.write_bundle_meta(root, platform, unpacked_bytes=4096,
                                  bundle_version=bundle_version)
    archive = tmp / f"aleggo-lean-bundle-{platform}.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(root / "bundle.json", arcname="aleggo-lean-bundle/bundle.json")
        tar.add(root / "elan", arcname="aleggo-lean-bundle/elan")
        tar.add(root / "lean4-project", arcname="aleggo-lean-bundle/lean4-project")
    sha = _sha256(archive)
    Path(str(archive) + ".sha256").write_text(sha + "\n", encoding="utf-8")
    return archive, sha


class _StubJob:
    """Minimal stand-in for lean_setup._InstallJob."""

    def __init__(self):
        self.phases: list[str] = []
        self.progress: list[tuple[int, int | None, float]] = []
        self.cancel = threading.Event()
        self.paused = threading.Event()
        self.process = None

    def set_phase(self, phase, message):
        self.phases.append(phase)

    def emit(self, line):
        pass

    def set_progress(self, downloaded, total, speed):
        self.progress.append((downloaded, total, speed))


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #

class BundleDistributionTests(unittest.TestCase):
    def setUp(self):
        # Keep the fixtures on the test runner's explicitly selected temp volume.
        base = Path(tempfile.gettempdir()) / "lean-bundle-test-tmp"
        base.mkdir(exist_ok=True)
        self.tmp = Path(tempfile.mkdtemp(prefix="lean-bundle-test-", dir=str(base)))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.env = {"ALEGGO_TOOLS_HOME": str(self.tmp / "tools-home")}
        self._env_patch = mock.patch.dict(os.environ, self.env)
        self._env_patch.start()
        self.addCleanup(self._env_patch.stop)

    # 1. 断点续传 ------------------------------------------------------------ #
    def test_resume_download(self):
        payload = os.urandom(3 * 1024 * 1024)
        handler = type("H", (_RangeHandler,), {"payload": payload})
        server = _serve(handler)
        self.addCleanup(server.shutdown)
        url = f"http://127.0.0.1:{server.server_port}/bundle.bin"
        sha = hashlib.sha256(payload).hexdigest()
        dest = self.tmp / "bundle.bin"
        state = self.tmp / "dl.json"
        cancel = threading.Event()
        # Throttle to 1 MB/s so the abort lands mid-download deterministically.
        timer = threading.Timer(0.5, cancel.set)
        timer.start()
        try:
            with self.assertRaises(lean_bundle.DownloadCancelled):
                lean_bundle.download_with_resume(
                    url, dest, expected_size=len(payload), sha256=sha,
                    state_path=state, cancel_event=cancel, max_mbps=1)
        finally:
            timer.cancel()
        partial = dest.stat().st_size
        self.assertGreater(partial, 0)
        self.assertLess(partial, len(payload))
        self.assertTrue(state.is_file(), "resume state must survive the abort")

        cancel.clear()  # resume without throttling
        lean_bundle.download_with_resume(
            url, dest, expected_size=len(payload), sha256=sha,
            state_path=state, max_mbps=0)
        self.assertEqual(dest.read_bytes(), payload)
        self.assertFalse(state.exists(), "state file is cleaned up after success")

    # 9. 暂停/恢复（阻塞式，worker 不退出）------------------------------------ #
    def test_pause_blocks_and_resumes(self):
        payload = os.urandom(2 * 1024 * 1024)
        handler = type("H", (_RangeHandler,), {"payload": payload})
        server = _serve(handler)
        self.addCleanup(server.shutdown)
        url = f"http://127.0.0.1:{server.server_port}/bundle.bin"
        sha = hashlib.sha256(payload).hexdigest()
        dest = self.tmp / "bundle.bin"
        state = self.tmp / "dl.json"
        pause = threading.Event()
        cancel = threading.Event()

        result: dict = {}

        def run():
            try:
                lean_bundle.download_with_resume(
                    url, dest, expected_size=len(payload), sha256=sha,
                    state_path=state, cancel_event=cancel,
                    pause_event=pause, max_mbps=1)  # 1 MB/s -> ~2s total
                result["ok"] = True
            except Exception as exc:  # noqa: BLE001 - must not escape the thread
                result["error"] = exc

        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        time.sleep(0.4)  # mid-download
        pause.set()
        time.sleep(0.5)
        self.assertTrue(worker.is_alive(), "paused download must block, not exit/raise")
        self.assertNotIn("ok", result)
        mid_size = dest.stat().st_size
        time.sleep(0.6)
        self.assertEqual(dest.stat().st_size, mid_size, "no bytes may arrive while paused")
        pause.clear()  # resume
        worker.join(timeout=25)
        self.assertFalse(worker.is_alive(), "download must finish after resume")
        self.assertTrue(result.get("ok"), f"unexpected error: {result.get('error')}")
        self.assertEqual(dest.read_bytes(), payload)

    def test_pause_point_blocks_and_resumes(self):
        job = lean_setup._InstallJob(status="running", phase="extract")
        job.paused.set()
        done = threading.Event()

        def run():
            lean_setup._pause_point(job)
            done.set()

        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        time.sleep(0.3)
        self.assertFalse(done.is_set(), "_pause_point must block while paused")
        self.assertEqual(job.status, "paused")
        job.paused.clear()
        worker.join(timeout=5)
        self.assertTrue(done.is_set(), "_pause_point must return after resume")
        self.assertEqual(job.status, "running")

    def test_pause_install_toggle(self):
        job = lean_setup._JOB
        saved = (job.status, job.message, job.paused.is_set(), job.cancel.is_set())
        try:
            job.status, job.message = "running", "x"
            job.paused.clear(); job.cancel.clear()
            snap = lean_setup.pause_install()
            self.assertEqual(snap["status"], "paused")
            self.assertTrue(job.paused.is_set())
            snap = lean_setup.pause_install()  # toggle back
            self.assertEqual(snap["status"], "running")
            self.assertFalse(job.paused.is_set())
        finally:
            job.status, job.message = saved[0], saved[1]
            job.paused.clear(); job.cancel.clear()
            if saved[2]:
                job.paused.set()
            if saved[3]:
                job.cancel.set()

    # 2. 哈希不匹配 ---------------------------------------------------------- #
    def test_hash_mismatch(self):
        payload = os.urandom(1024 * 1024)
        handler = type("H", (_RangeHandler,), {"payload": payload})
        server = _serve(handler)
        self.addCleanup(server.shutdown)
        url = f"http://127.0.0.1:{server.server_port}/bundle.bin"
        dest = self.tmp / "bundle.bin"
        with self.assertRaises(LeanBundleError) as ctx:
            lean_bundle.download_with_resume(
                url, dest, expected_size=len(payload),
                sha256="0" * 64, state_path=self.tmp / "dl.json", max_mbps=0)
        self.assertEqual(ctx.exception.code, "HASH_MISMATCH")
        self.assertFalse(dest.exists(), "corrupt file must be deleted")
        help_text = lean_bundle.error_help("HASH_MISMATCH")
        self.assertIn("解决办法", "".join(help_text.values()) + "解决办法")

    # 3. 磁盘不足预检 -------------------------------------------------------- #
    def test_disk_full_precheck(self):
        archive, sha = _make_fake_bundle(self.tmp)
        source = {"kind": "local", "path": archive, "sha256": sha, "platform": "linux-x64"}
        with mock.patch.object(lean_setup, "_free_bytes", return_value=1):
            with self.assertRaises(LeanBundleError) as ctx:
                lean_setup._install_bundle_flow(_StubJob(), source, lean_bundle.BUNDLE_VERSION)
        self.assertEqual(ctx.exception.code, "DISK_FULL")
        help_text = lean_bundle.error_help(ctx.exception.code)
        self.assertIn("16 GB", help_text["reason"])
        self.assertTrue(help_text["fix"], "a fix must be offered in Chinese")

    # 4. 镜像失败回退 -------------------------------------------------------- #
    def test_mirror_fallback(self):
        manifest = {"latest": "v4.32.0-1",
                    "releases": {"v4.32.0-1": {"platforms": {"linux-x64": {
                        "url": "http://127.0.0.1:1/x", "sha256": "a" * 64, "size": 1}}}}}
        bad = type("H1", (_RangeHandler,), {"manifest_status": 500})
        good = type("H2", (_RangeHandler,),
                    {"manifest_body": json.dumps(manifest).encode()})
        s1, s2 = _serve(bad), _serve(good)
        self.addCleanup(s1.shutdown)
        self.addCleanup(s2.shutdown)
        env = {"ALEGGO_LEAN_MIRROR_URL":
               f"http://127.0.0.1:{s1.server_port}/releases.json,"
               f"http://127.0.0.1:{s2.server_port}/releases.json",
               "ALEGGO_LEAN_OFFICIAL_FALLBACK": "0"}
        with mock.patch.dict(os.environ, env):
            fetched, url = lean_bundle.fetch_manifest()
        self.assertIn(str(s2.server_port), url)
        self.assertEqual(fetched["latest"], "v4.32.0-1")

    # 5. 冒烟失败：旧环境不受影响 -------------------------------------------- #
    def test_smoke_failure_keeps_old_install(self):
        tools_home = Path(self.env["ALEGGO_TOOLS_HOME"])
        # Simulate a previously working install plus unrelated teacher data.
        old_elan = tools_home / "elan"
        old_ws = tools_home / "lean4-project"
        old_elan.mkdir(parents=True)
        (old_elan / "sentinel.txt").write_text("old", encoding="utf-8")
        old_ws.mkdir(parents=True)
        teacher_data = tools_home / "teacher-projects"
        teacher_data.mkdir(parents=True)
        (teacher_data / "notes.txt").write_text("precious", encoding="utf-8")

        archive, sha = _make_fake_bundle(self.tmp, good_lake=False)
        source = {"kind": "local", "path": archive, "sha256": sha, "platform": "linux-x64"}
        with self.assertRaises(LeanBundleError) as ctx:
            lean_setup._install_bundle_flow(_StubJob(), source, lean_bundle.BUNDLE_VERSION)
        self.assertEqual(ctx.exception.code, "SMOKE_FAILED")
        # Teacher data outside the managed dirs is untouched; nothing half-installed lingers.
        self.assertTrue((teacher_data / "notes.txt").is_file())
        self.assertFalse((tools_home / "lean-bundle.json").exists())

    # 5b. 原子升级：新包冒烟失败时旧环境完好 ---------------------------------- #
    def test_atomic_upgrade_keeps_working_install_on_failure(self):
        tools_home = Path(self.env["ALEGGO_TOOLS_HOME"])
        # Simulate a previously working *bundle* install (has a version record).
        old_elan = tools_home / "elan"
        old_ws = tools_home / "lean4-project"
        old_elan.mkdir(parents=True)
        (old_elan / "sentinel.txt").write_text("old-elan", encoding="utf-8")
        old_ws.mkdir(parents=True)
        (old_ws / "sentinel.txt").write_text("old-workspace", encoding="utf-8")
        lean_bundle.record_installed_bundle(tools_home, "v4.32.0-1", "linux-x64", "0" * 64)

        archive, sha = _make_fake_bundle(self.tmp, good_lake=False)
        source = {"kind": "local", "path": archive, "sha256": sha, "platform": "linux-x64"}
        with self.assertRaises(LeanBundleError) as ctx:
            lean_setup._install_bundle_flow(_StubJob(), source, "v4.32.0-2")
        self.assertEqual(ctx.exception.code, "SMOKE_FAILED")
        # The old working install is untouched ...
        self.assertEqual((old_elan / "sentinel.txt").read_text(encoding="utf-8"), "old-elan")
        self.assertEqual((old_ws / "sentinel.txt").read_text(encoding="utf-8"), "old-workspace")
        self.assertEqual(lean_bundle.installed_bundle_info(tools_home).get("version"), "v4.32.0-1")
        # ... and no half-unpacked "*.new" trees linger.
        self.assertFalse((tools_home / "elan.new").exists())
        self.assertFalse((tools_home / "lean4-project.new").exists())

    # 5c. 原子升级：成功时新环境替换旧环境 ------------------------------------ #
    def test_atomic_upgrade_swaps_on_success(self):
        tools_home = Path(self.env["ALEGGO_TOOLS_HOME"])
        old_elan = tools_home / "elan"
        old_ws = tools_home / "lean4-project"
        old_elan.mkdir(parents=True)
        (old_elan / "sentinel.txt").write_text("old-elan", encoding="utf-8")
        old_ws.mkdir(parents=True)
        lean_bundle.record_installed_bundle(tools_home, "v4.32.0-1", "linux-x64", "0" * 64)

        archive, sha = _make_fake_bundle(self.tmp, good_lake=True, bundle_version="v4.32.0-2")
        source = {"kind": "local", "path": archive, "sha256": sha, "platform": "linux-x64"}
        lean_setup._install_bundle_flow(_StubJob(), source, "v4.32.0-2")
        # New tree swapped in; old sentinel gone; version record updated.
        self.assertFalse((old_elan / "sentinel.txt").exists())
        self.assertTrue((tools_home / "lean4-project" / "lean-toolchain").is_file())
        self.assertEqual(lean_bundle.installed_bundle_info(tools_home).get("version"), "v4.32.0-2")
        self.assertFalse((tools_home / "elan.new").exists())
        self.assertFalse((tools_home / "lean4-project.new").exists())

    # 5d. 原子升级：swap 第二步失败时回滚，旧环境完整 -------------------------- #
    def test_atomic_upgrade_rollback_on_swap_failure(self):
        tools_home = Path(self.env["ALEGGO_TOOLS_HOME"])
        old_elan = tools_home / "elan"
        old_ws = tools_home / "lean4-project"
        old_elan.mkdir(parents=True)
        (old_elan / "sentinel.txt").write_text("old-elan", encoding="utf-8")
        old_ws.mkdir(parents=True)
        (old_ws / "sentinel.txt").write_text("old-workspace", encoding="utf-8")
        lean_bundle.record_installed_bundle(tools_home, "v4.32.0-1", "linux-x64", "0" * 64)

        archive, sha = _make_fake_bundle(self.tmp, good_lake=True, bundle_version="v4.32.0-2")
        source = {"kind": "local", "path": archive, "sha256": sha, "platform": "linux-x64"}

        real_rename = Path.rename
        calls = []

        def flaky_rename(self, target):
            # Fail the second live-swap rename (lean4-project.new -> lean4-project).
            if self.name.endswith(".new") and str(target).endswith("lean4-project"):
                calls.append(str(self))
                raise OSError("simulated swap failure")
            return real_rename(self, target)

        with mock.patch.object(Path, "rename", flaky_rename):
            with self.assertRaises(OSError):
                lean_setup._install_bundle_flow(_StubJob(), source, "v4.32.0-2")
        self.assertTrue(calls, "the simulated failure should have fired")
        # Old environment fully restored; no .new/.bak debris.
        self.assertEqual((old_elan / "sentinel.txt").read_text(encoding="utf-8"), "old-elan")
        self.assertEqual((old_ws / "sentinel.txt").read_text(encoding="utf-8"), "old-workspace")
        self.assertEqual(lean_bundle.installed_bundle_info(tools_home).get("version"), "v4.32.0-1")
        for suffix in (".new", ".bak"):
            self.assertFalse((tools_home / ("elan" + suffix)).exists())
            self.assertFalse((tools_home / ("lean4-project" + suffix)).exists())

    # 6. 版本锁定 ------------------------------------------------------------ #
    def test_version_pinning_after_install(self):
        archive, sha = _make_fake_bundle(self.tmp, good_lake=True)
        source = {"kind": "local", "path": archive, "sha256": sha, "platform": "linux-x64"}
        lean_setup._install_bundle_flow(_StubJob(), source, lean_bundle.BUNDLE_VERSION)
        tools_home = Path(self.env["ALEGGO_TOOLS_HOME"])
        toolchain = (tools_home / "lean4-project" / "lean-toolchain").read_text(encoding="utf-8").strip()
        self.assertEqual(toolchain, lean_bundle.LEAN_TOOLCHAIN)
        info = lean_bundle.installed_bundle_info(tools_home)
        self.assertEqual(info.get("version"), lean_bundle.BUNDLE_VERSION)
        self.assertEqual(info.get("platform"), "linux-x64")

    # 7. 离线安装：网络被封也能装 -------------------------------------------- #
    def test_offline_install_with_network_blocked(self):
        archive, sha = _make_fake_bundle(self.tmp, good_lake=True)
        offline_path = self.tmp / "offline.tar.gz"
        shutil.copy(archive, offline_path)
        Path(str(offline_path) + ".sha256").write_text(sha + "\n", encoding="utf-8")

        real_socket = socket.socket

        def blocked(*args, **kwargs):
            raise OSError("network is blocked in this test")

        with mock.patch.dict(os.environ, {"ALEGGO_LEAN_BUNDLE_PATH": str(offline_path)}):
            with mock.patch.object(socket, "socket", blocked):
                source = lean_bundle.resolve_source(Path(self.env["ALEGGO_TOOLS_HOME"]))
        self.assertEqual(source["kind"], "local")
        self.assertEqual(source["sha256"], sha)

    # 8. 错误码表完整性 ------------------------------------------------------ #
    def test_error_catalog_chinese(self):
        for code, help_text in lean_bundle.LEAN_ERRORS.items():
            self.assertTrue(help_text.get("title"), code)
            self.assertTrue(help_text.get("reason"), code)
            self.assertTrue(help_text.get("fix"), code)
        self.assertIn(lean_bundle.platform_id(), lean_bundle.PLATFORMS)


if __name__ == "__main__":
    unittest.main()


class VersionCompareTests(unittest.TestCase):
    def test_numeric_not_lexicographic(self):
        self.assertTrue(lean_bundle.is_newer_version("v4.32.0-10", "v4.32.0-2"))
        self.assertTrue(lean_bundle.is_newer_version("v4.32.0-2", "v4.32.0-10") is False)

    def test_downgrade_is_not_newer(self):
        self.assertFalse(lean_bundle.is_newer_version("v4.32.0-1", "v4.32.0-2"))
        self.assertFalse(lean_bundle.is_newer_version("v4.32.0-1", "v4.32.0-1"))

    def test_sort_key_orders_releases(self):
        versions = ["v4.32.0-2", "v4.32.0-10", "v4.32.0-1"]
        self.assertEqual(max(versions, key=lean_bundle.version_sort_key), "v4.32.0-10")


class DownloadReadErrorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path.home() / ".lean-bundle-test-tmp" / "readerr"
        self.tmp.mkdir(parents=True, exist_ok=True)

    def test_read_error_becomes_network_timeout_and_keeps_state(self):
        import http.client
        from unittest import mock as umock
        dest = self.tmp / "pkg.bin"
        state = self.tmp / "dl.json"

        class FakeResp:
            status = 200
            headers = {}

            def read(self, n):
                raise http.client.IncompleteRead(b"partial", 10)

            def close(self):
                pass

        with umock.patch.object(lean_bundle.urllib.request, "urlopen", return_value=FakeResp()):
            with self.assertRaises(lean_bundle.LeanBundleError) as ctx:
                lean_bundle.download_with_resume("http://x/y", dest, expected_size=10,
                                                 state_path=state, chunk_size=4)
        self.assertEqual(ctx.exception.code, "NETWORK_TIMEOUT")
        self.assertTrue(state.is_file(), "resume state must survive a read error")


class WrongPlatformBundleTests(unittest.TestCase):
    def test_extract_rejects_wrong_platform_bundle(self):
        import shutil
        tmp = Path.home() / ".lean-bundle-test-tmp" / "wrongplat"
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True, exist_ok=True)
        archive, _sha = _make_fake_bundle(tmp, platform="windows-x64")
        with self.assertRaises(lean_bundle.LeanBundleError) as ctx:
            lean_bundle.extract_bundle(archive, tmp / "elan", tmp / "ws", archive_format="tar.gz")
        self.assertEqual(ctx.exception.code, "NO_BUNDLE_FOR_PLATFORM")
