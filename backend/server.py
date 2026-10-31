"""Read-only bootstrap while the execution sandbox is being prepared."""
import json
import os
import ctypes
import ctypes.util
import platform
import threading
import ipaddress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LIVE_ENABLED = False
PROXY_HEADER_VERIFIED = False
FRONTEND_ORIGIN = 'https://atomgit.vercel.app'


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
        if self.headers.get('Origin') == FRONTEND_ORIGIN:
            self.send_header('Access-Control-Allow-Origin', FRONTEND_ORIGIN)
            self.send_header('Vary', 'Origin')
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        if self.path == '/health':
            body = {'ok': True, 'execution': 'ready' if live_ready() else 'disabled', 'sandbox_prerequisites': SANDBOX_PREREQUISITES, 'sandbox_selftest': SANDBOX_TESTS, 'rate_selftest': RATE_TESTS}
            if self.headers.get('X-Aleggo-Proxy-Test') == '1':
                forwarded = [v.strip() for v in self.headers.get('X-Forwarded-For', '').split(',') if v.strip()]
                body['proxy_test'] = {'count': len(forwarded), 'spoof_positions': [i for i, v in enumerate(forwarded) if v == '203.0.113.55']}
            self.send_json(200, body)
        else:
            self.send_json(404, {'error': '未找到页面'})

    def do_OPTIONS(self):
        self.send_response(204)
        if self.headers.get('Origin') == FRONTEND_ORIGIN:
            self.send_header('Access-Control-Allow-Origin', FRONTEND_ORIGIN)
            self.send_header('Access-Control-Allow-Methods', 'POST, OPTIONS')
            self.send_header('Access-Control-Allow-Headers', 'Content-Type')
            self.send_header('Vary', 'Origin')
        self.end_headers()

    def do_POST(self):
        self.close_connection = True
        if self.path != '/api/run':
            self.send_json(404, {'error':'未找到页面'})
            return
        if not live_ready():
            self.send_json(503, {'error':'实时计算尚未开放，请稍后再试。'})
            return
        if self.headers.get('Origin') != FRONTEND_ORIGIN or self.headers.get('Content-Type','').split(';')[0].strip().lower() != 'application/json':
            self.send_json(403, {'error':'请求来源无效。'})
            return
        try:
            size = int(self.headers.get('Content-Length','0'))
            if size <= 0 or size > 8192:
                raise ValueError('invalid size')
            self.connection.settimeout(10)
            payload = json.loads(self.rfile.read(size))
            question = payload.get('question')
            if not isinstance(question,str) or not question.strip() or len(question)>500:
                raise ValueError('invalid question')
            forwarded = self.headers.get('X-Forwarded-For','').split(',')[0].strip()
            ip = ipaddress.ip_address(forwarded).compressed
        except (ValueError, TypeError, OSError, json.JSONDecodeError):
            self.send_json(400, {'error':'请填写 500 字以内的计算题。'})
            return
        try:
            from rate_limit import RateLimiter
            limiter = RateLimiter(os.environ['DATABASE_URL'])
            ticket, reason = limiter.acquire(ip)
        except Exception:
            self.send_json(503, {'error':'计算服务暂时不可用，请稍后重试。'})
            return
        if not ticket:
            self.send_json(429, {'error':'请求较多，请稍后再试。' if reason == 'busy' else '每 10 分钟最多体验 3 次。'})
            return
        stream = None
        try:
            from supervisor import supervise
            self.send_response(200)
            self.send_header('Content-Type','text/event-stream; charset=utf-8')
            self.send_header('Cache-Control','no-store')
            self.send_header('Access-Control-Allow-Origin',FRONTEND_ORIGIN)
            self.send_header('Vary','Origin')
            self.send_header('X-Accel-Buffering','no')
            self.end_headers()
            stream = supervise(question.strip(), os.environ['DEEPSEEK_API_KEY'])
            for event in stream:
                data = ': keepalive\n\n' if event.get('type') == 'heartbeat' else 'data: '+json.dumps(event,ensure_ascii=False)+'\n\n'
                self.wfile.write(data.encode('utf-8'))
                self.wfile.flush()
        except Exception:
            pass
        finally:
            if stream is not None:
                stream.close()
            try:
                limiter.release(ticket)
            except Exception:
                pass


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
SANDBOX_TESTS = {'state': 'pending'}
RATE_TESTS = {'state': 'pending' if os.getenv('DATABASE_URL') else 'not_configured'}


def live_ready():
    return (LIVE_ENABLED and PROXY_HEADER_VERIFIED and bool(os.getenv('DEEPSEEK_API_KEY'))
        and SANDBOX_TESTS.get('state') == 'complete' and bool(SANDBOX_TESTS.get('checks')) and all(SANDBOX_TESTS['checks'].values())
        and RATE_TESTS.get('state') == 'complete' and bool(RATE_TESTS.get('checks'))
        and all(RATE_TESTS['checks'].values()))


def run_sandbox_tests():
    try:
        from security_selftest import run_checks
        results = run_checks()
        SANDBOX_TESTS.update({'state': 'complete', 'checks': results})
    except Exception:
        SANDBOX_TESTS.update({'state': 'failed'})


def run_rate_tests():
    try:
        from rate_selftest import run_checks
        from rate_limit import RateLimiter
        dsn = os.environ['DATABASE_URL']
        results = run_checks(dsn)
        if all(results.values()):
            RateLimiter(dsn).setup()
        RATE_TESTS.update({'state': 'complete', 'checks': results})
    except Exception:
        RATE_TESTS.update({'state': 'failed'})


if __name__ == '__main__':
    if SANDBOX_PREREQUISITES['landlock_abi'] >= 3 and SANDBOX_PREREQUISITES['seccomp_library']:
        threading.Thread(target=run_sandbox_tests, daemon=True).start()
    else:
        SANDBOX_TESTS['state'] = 'unavailable'
    if os.getenv('DATABASE_URL'):
        threading.Thread(target=run_rate_tests, daemon=True).start()
    ThreadingHTTPServer(('0.0.0.0', int(os.getenv('PORT', '8000'))), Handler).serve_forever()
