"""Read-only bootstrap while the execution sandbox is being prepared."""
import json
import os
import ctypes
import ctypes.util
import platform
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Handler(BaseHTTPRequestHandler):
    def log_message(self, _format, *args):
        # Never log questions, headers, credentials, or request bodies.
        return

    def send_json(self, status, body):
        payload = json.dumps(body, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(payload)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        if self.path == '/health':
            self.send_json(200, {'ok': True, 'execution': 'disabled', 'sandbox_prerequisites': SANDBOX_PREREQUISITES})
        else:
            self.send_json(404, {'error': '未找到页面'})

    def do_POST(self):
        self.close_connection = True
        self.send_json(503, {'error': '实时计算尚未开放，请稍后再试。'})


def sandbox_prerequisites():
    result = {'linux': False, 'landlock_abi': 0, 'seccomp_library': False}
    if platform.system() != 'Linux' or platform.machine() not in ('x86_64', 'aarch64'):
        return result
    result['linux'] = True
    libc = ctypes.CDLL(None, use_errno=True)
    abi = libc.syscall(444, 0, 0, 1)
    result['landlock_abi'] = max(0, abi)
    result['seccomp_library'] = bool(ctypes.util.find_library('seccomp'))
    return result


SANDBOX_PREREQUISITES = sandbox_prerequisites()


if __name__ == '__main__':
    ThreadingHTTPServer(('0.0.0.0', int(os.getenv('PORT', '8000'))), Handler).serve_forever()
