"""Local execution prototype; callers must keep public execution disabled."""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

MAX_TOOL_SECONDS = 30
MAX_OUTPUT_BYTES = 65536
WORKER = Path(__file__).with_name('sandbox_worker.py').resolve()


def execute_python(code, timeout=MAX_TOOL_SECONDS):
    if sys.platform != 'linux':
        raise RuntimeError('Linux sandbox required')
    if not isinstance(code, str) or len(code) > 16000 or not (0 < timeout <= MAX_TOOL_SECONDS):
        raise ValueError('invalid calculation')
    session_root = os.environ.get('ALEGGO_SESSION_ROOT')
    with tempfile.TemporaryDirectory(prefix='aleggo-', dir=session_root) as workspace:
        out_path = Path(workspace) / 'stdout.txt'
        err_path = Path(workspace) / 'stderr.txt'
        env = {'PATH': '/usr/bin:/bin', 'HOME': workspace, 'TMPDIR': workspace, 'LANG': 'C.UTF-8'}
        request = json.dumps({'code': code, 'workspace': workspace}, ensure_ascii=False)
        try:
            with out_path.open('wb') as out, err_path.open('wb') as err:
                proc = subprocess.run([sys.executable, '-I', str(WORKER)], input=request + '\n',
                                      text=True, stdout=out, stderr=err, cwd=workspace,
                                      env=env, close_fds=True, timeout=timeout)
            result = {'returncode': proc.returncode, 'timeout': False}
        except subprocess.TimeoutExpired:
            result = {'returncode': None, 'timeout': True}
        for label, path in [('stdout', out_path), ('stderr', err_path)]:
            try:
                with path.open('rb') as file:
                    result[label] = file.read(MAX_OUTPUT_BYTES).decode('utf-8', errors='replace')
                    result[label] = result[label].replace(workspace, '[临时目录]').replace(str(WORKER), '[计算器]')
                    if session_root:
                        result[label] = result[label].replace(session_root, '[会话目录]')
            except OSError:
                result[label] = ''
        last_workspace = workspace
    result['cleaned'] = not os.path.exists(last_workspace)
    return result
