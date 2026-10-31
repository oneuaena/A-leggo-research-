"""Round 27 standalone tests.

``lean_formal.run_kernel_check`` spawns ``lake env lean <file>``: the real
Lean elaboration runs as a *child* of the ``lake`` wrapper.  Before this
round a timeout / output-cap / cancellation only killed the wrapper, so a
hung elaboration was orphaned and kept burning CPU forever.  The check now
spawns in its own process group and the whole tree is killed on every exit
path (POSIX ``killpg``; Windows ``taskkill /T`` best effort).

Also: eval ``run_report`` sorted results with unknown problem ids *first*
(``order.get(id, 0)``); they now sort last.

unittest style (no pytest on this machine); also collected by pytest in CI.
"""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
import time
import unittest
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from aleggo.study import lean_formal, research_eval  # noqa: E402


def _pids_with_marker(marker: str) -> list[int]:
    """PIDs whose command line contains *marker* (Linux /proc scan)."""
    found = []
    try:
        entries = os.listdir("/proc")
    except OSError:
        return found
    for pid in entries:
        if not pid.isdigit():
            continue
        try:
            with open("/proc/%s/cmdline" % pid, "rb") as handle:
                cmdline = handle.read().replace(b"\0", b" ").decode("utf-8", "replace")
        except (OSError, FileNotFoundError):
            continue
        if marker in cmdline:
            found.append(int(pid))
    return found


class _StubStdout:
    """Fake stdout that EOFs once the owner is killed (models pipe close)."""

    def __init__(self, owner: "_StubProcess") -> None:
        self._owner = owner

    async def read(self, n: int) -> bytes:
        # Poll: the real pipe wakes the reader with EOF when the process
        # dies; the stub models that by noticing the kill flag.
        while not self._owner.killed:
            await asyncio.sleep(0.01)
        return b""


class _StubProcess:
    """Duck-typed stand-in for asyncio.subprocess.Process."""

    def __init__(self, *, exit_code: int = 0, wait_forever: bool = False) -> None:
        self.pid = 999999999  # bogus: killpg must fall back to .kill()
        self.killed = False
        self.kill_calls = 0
        self._exit_code = exit_code
        self._wait_forever = wait_forever
        self.stdout: _StubStdout | None = _StubStdout(self)

    def kill(self) -> None:
        self.kill_calls += 1
        self.killed = True

    async def wait(self) -> int:
        if self.killed:
            return -9
        if self._wait_forever:
            await asyncio.sleep(3600)
        return self._exit_code


class ProcessTreeTests(unittest.TestCase):
    @unittest.skipIf(os.name != "posix", "process-group kill is POSIX-only")
    def test_kill_tree_takes_parent_and_grandchild(self):
        # Models `lake env lean`: the wrapper spawns the real worker as a child.
        marker = "r27tree-%s" % uuid4().hex
        parent_code = (
            "import subprocess, sys, time; "
            "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)', sys.argv[1]]); "
            "time.sleep(120)"
        )
        proc = subprocess.Popen(
            [sys.executable, "-c", parent_code, marker],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        try:
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if len(_pids_with_marker(marker)) >= 2:
                    break
                time.sleep(0.2)
            self.assertGreaterEqual(
                len(_pids_with_marker(marker)), 2, "grandchild never appeared")
            asyncio.run(lean_formal._kill_process_tree(proc))
            deadline = time.monotonic() + 15
            remaining = _pids_with_marker(marker)
            while remaining and time.monotonic() < deadline:
                time.sleep(0.2)
                remaining = _pids_with_marker(marker)
            self.assertEqual(remaining, [], "stray processes survived: %r" % (remaining,))
        finally:
            for pid in _pids_with_marker(marker):
                try:
                    os.kill(pid, signal.SIGKILL)
                except OSError:
                    pass
            try:
                proc.wait(timeout=10)
            except Exception:
                pass


class GuardedWaitTests(unittest.TestCase):
    def _run(self, coro):
        return asyncio.run(coro)

    def test_timeout_kills_tree(self):
        proc = _StubProcess(wait_forever=True)
        timed_out, limited = self._run(lean_formal._guarded_wait(proc, bytearray(), 0.05))
        self.assertTrue(timed_out)
        self.assertFalse(limited)
        self.assertTrue(proc.killed, "timed-out process tree was not killed")

    def test_output_limit_kills_tree(self):
        proc = _StubProcess()  # wait() returns 0 immediately
        big = b"x" * (lean_formal.MAX_CHECK_OUTPUT + 100)

        class _Big:
            sent = False

            async def read(self, n: int) -> bytes:
                if not self.sent:
                    self.sent = True
                    return big
                return b""

        proc.stdout = _Big()  # type: ignore[assignment]
        timed_out, limited = self._run(lean_formal._guarded_wait(proc, bytearray(), 30))
        self.assertFalse(timed_out)
        self.assertTrue(limited, "over-cap output must be reported as output-limited")
        self.assertTrue(proc.killed, "output-limited check left the process tree alive")

    def test_drain_stdout_cap(self):
        proc = _StubProcess()
        big = b"x" * (lean_formal.MAX_CHECK_OUTPUT + 100)

        class _Big:
            sent = False

            async def read(self, n: int) -> bytes:
                if not self.sent:
                    self.sent = True
                    return big
                return b""

        proc.stdout = _Big()  # type: ignore[assignment]
        out = bytearray()
        self.assertTrue(self._run(lean_formal._drain_stdout(proc, out)))
        self.assertEqual(len(out), lean_formal.MAX_CHECK_OUTPUT)

    def test_cancel_kills_tree_and_reraises(self):
        proc = _StubProcess(wait_forever=True)

        async def go() -> None:
            task = asyncio.ensure_future(lean_formal._guarded_wait(proc, bytearray(), 30))
            await asyncio.sleep(0.1)
            task.cancel()
            await task

        with self.assertRaises(asyncio.CancelledError):
            self._run(go())
        self.assertTrue(proc.killed, "cancelled check left the process tree alive")

    def test_normal_completion_no_kill(self):
        proc = _StubProcess()

        class _Small:
            done = False

            async def read(self, n: int) -> bytes:
                if self.done:
                    return b""
                self.done = True
                return b"hello"

        proc.stdout = _Small()  # type: ignore[assignment]
        timed_out, limited = self._run(lean_formal._guarded_wait(proc, bytearray(), 30))
        self.assertFalse(timed_out)
        self.assertFalse(limited)
        self.assertEqual(proc.kill_calls, 0, "healthy process must not be killed")


class EvalOrderTests(unittest.TestCase):
    def test_problem_order_restored(self):
        report = {"problemIds": ["b", "a", "c"],
                  "results": [{"id": "c"}, {"id": "a"}, {"id": "b"}]}
        research_eval._order_results(report)
        self.assertEqual([item["id"] for item in report["results"]], ["b", "a", "c"])

    def test_unknown_ids_sort_last(self):
        report = {"problemIds": ["b", "a"],
                  "results": [{"id": "zzz"}, {"id": "a"}, {"id": "b"}]}
        research_eval._order_results(report)
        self.assertEqual([item["id"] for item in report["results"]], ["b", "a", "zzz"])


if __name__ == "__main__":
    unittest.main()
