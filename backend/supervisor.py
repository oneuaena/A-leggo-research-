"""Run a single visitor session with a hard wall-clock deadline."""
import json
import os
import selectors
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

MAX_SESSION_SECONDS = 180
AGENT = Path(__file__).with_name('agent.py').resolve()


def workspace_exceeded(root):
    pending = [root]
    count = size = 0
    while pending:
        directory = pending.pop()
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    count += 1
                    if count > 64:
                        return True
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            pending.append(entry.path)
                        elif entry.is_file(follow_symlinks=False):
                            size += entry.stat(follow_symlinks=False).st_size
                            if size > 8 * 1024 * 1024:
                                return True
                    except FileNotFoundError:
                        continue
        except FileNotFoundError:
            continue
    return False


def supervise(question, key, seconds=MAX_SESSION_SECONDS, runner=AGENT):
    if sys.platform != 'linux' or not (0 < seconds <= MAX_SESSION_SECONDS):
        raise RuntimeError('unsupported supervisor configuration')
    with tempfile.TemporaryDirectory(prefix='aleggo-session-') as workspace:
        env = {'PATH':'/usr/bin:/bin', 'LANG':'C.UTF-8', 'HOME':workspace,
               'TMPDIR':workspace, 'ALEGGO_SESSION_ROOT':workspace, 'DEEPSEEK_API_KEY':key}
        proc = subprocess.Popen([sys.executable, '-I', str(runner)], cwd=workspace, env=env,
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                close_fds=True, start_new_session=True)
        selector = selectors.DefaultSelector()
        selector.register(proc.stdout, selectors.EVENT_READ)
        deadline = time.monotonic() + seconds
        next_heartbeat = time.monotonic() + 5
        buffer = b''
        def terminate_group():
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        watchdog = threading.Timer(seconds, terminate_group)
        watchdog.daemon = True
        watchdog.start()
        try:
            proc.stdin.write((json.dumps({'question':question},ensure_ascii=False)+'\n').encode('utf-8'))
            proc.stdin.close()
            while time.monotonic() < deadline:
                if workspace_exceeded(workspace):
                    yield {'type':'error','text':'本次计算的临时文件超过限制。'}
                    return
                events = selector.select(min(0.25, max(0, deadline-time.monotonic())))
                if not events:
                    if proc.poll() is not None:
                        break
                    if time.monotonic() >= next_heartbeat:
                        yield {'type':'heartbeat'}
                        next_heartbeat = time.monotonic() + 5
                    continue
                chunk = os.read(proc.stdout.fileno(), 8192)
                if not chunk:
                    break
                buffer += chunk
                if len(buffer) > 131072:
                    yield {'type':'error','text':'结果过长，请缩小问题后重试。'}
                    return
                while b'\n' in buffer:
                    line, buffer = buffer.split(b'\n', 1)
                    try:
                        event = json.loads(line)
                        if isinstance(event,dict) and event.get('type') in ('step','tool','result','answer','error'):
                            yield event
                    except (ValueError, UnicodeError):
                        yield {'type':'error','text':'计算结果格式异常。'}
                        return
            if time.monotonic() >= deadline:
                yield {'type':'error','text':'本次计算已达到三分钟上限。'}
        finally:
            watchdog.cancel()
            selector.close()
            # Agent and its sandbox child share this fresh process group.
            terminate_group()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
