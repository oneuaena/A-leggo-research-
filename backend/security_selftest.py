"""Fixed diagnostic programs; no public endpoint accepts code."""
import os
import tempfile
import time
from pathlib import Path
from sandbox import execute_python


def run_checks():
    results = {}
    with tempfile.TemporaryDirectory(prefix='aleggo-canary-') as outer:
        secret = Path(outer) / 'canary.txt'
        secret.write_text('CANARY_VALUE', encoding='utf-8')
        cases = {
            'basic': "print(sum(range(1, 101)))",
            'temp_read_write': "open('ok.txt','w').write('ok'); print(open('ok.txt').read())",
            'outside_read': "\ntry:\n open(%r).read()\n print('BAD')\nexcept PermissionError:\n print('DENIED')" % str(secret),
            'outside_write': "\ntry:\n open(%r,'w').write('bad')\n print('BAD')\nexcept PermissionError:\n print('DENIED')" % str(secret),
            'network': "import ctypes,errno\nc=ctypes.CDLL(None,use_errno=True)\nr=c.socket(2,1,0)\nprint('DENIED' if r==-1 and ctypes.get_errno()==errno.EPERM else 'BAD')",
            'memory': "\ntry:\n bytearray(300*1024*1024)\n print('BAD')\nexcept MemoryError:\n print('DENIED')",
            'environment': "import os\nprint('DENIED' if 'DEEPSEEK_API_KEY' not in os.environ else 'BAD')",
            'process_creation': "import ctypes,errno,os\nc=ctypes.CDLL(None,use_errno=True)\nr=c.fork()\nif r==0: os._exit(0)\nprint('DENIED' if r==-1 and ctypes.get_errno()==errno.EPERM else 'BAD')",
        }
        expected = {'basic':'5050', 'temp_read_write':'ok', 'outside_read':'DENIED',
                    'outside_write':'DENIED', 'network':'DENIED', 'memory':'DENIED', 'environment':'DENIED',
                    'process_creation':'DENIED'}
        for name, code in cases.items():
            try:
                out = execute_python(code, timeout=10)
                results[name] = out['returncode'] == 0 and expected[name] in out['stdout']
                results[name + '_cleanup'] = out['cleaned']
            except Exception:
                results[name] = False
        results['canary_unchanged'] = secret.read_text(encoding='utf-8') == 'CANARY_VALUE'
        started = time.monotonic()
        try:
            out = execute_python('while True: pass')
            results['thirty_second_limit'] = (out['timeout'] or (out['returncode'] is not None and out['returncode'] < 0)) and time.monotonic() - started <= 32
            results['timeout_cleanup'] = out['cleaned']
        except Exception:
            results['thirty_second_limit'] = False
    return results
