import json
import threading
import unittest
import os
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from unittest.mock import patch, MagicMock
import server
from server import Handler


class BootstrapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def request(self, method, path, body=None):
        conn = HTTPConnection('127.0.0.1', self.httpd.server_port, timeout=3)
        conn.request(method, path, body=body)
        response = conn.getresponse()
        payload = json.loads(response.read())
        conn.close()
        return response.status, payload

    def test_health(self):
        status, body = self.request('GET', '/health')
        self.assertEqual(status, 200)
        self.assertEqual(body['execution'], 'disabled')

    def test_execution_is_closed(self):
        status, body = self.request('POST', '/api/run', b'{"code":"print(1)"}')
        self.assertEqual(status, 503)
        self.assertIn('尚未开放', body['error'])

    def test_live_gate_fails_closed(self):
        sandbox = {'state':'complete','checks':{name:True for name in server.SANDBOX_REQUIRED}}
        rate = {'state':'complete','checks':{name:True for name in server.RATE_REQUIRED}}
        model = {'state':'complete','checks':{name:True for name in server.MODEL_REQUIRED}}
        with patch.object(server,'LIVE_ENABLED',True), patch.object(server,'PROXY_HEADER_VERIFIED',True), \
             patch.object(server,'SANDBOX_TESTS',sandbox), patch.object(server,'RATE_TESTS',rate), patch.object(server,'MODEL_TESTS',model), \
             patch.dict(os.environ, {'DEEPSEEK_API_KEY':'test-secret'}, clear=True):
            self.assertTrue(server.live_ready())
            sandbox['checks']['network'] = False
            self.assertFalse(server.live_ready())
            sandbox['checks'] = {}
            self.assertFalse(server.live_ready())
            sandbox['checks'] = {name:True for name in server.SANDBOX_REQUIRED}
            del os.environ['DEEPSEEK_API_KEY']
            self.assertFalse(server.live_ready())

    def test_rate_limit_rejection(self):
        limiter = MagicMock()
        limiter.acquire.return_value = (None, 'rate')
        conn = HTTPConnection('127.0.0.1', self.httpd.server_port, timeout=3)
        with patch.object(server,'live_ready',return_value=True), \
             patch.dict(os.environ, {'DATABASE_URL':'test'}), \
             patch('rate_limit.RateLimiter',return_value=limiter):
            conn.request('POST','/api/run',json.dumps({'question':'2+2'}),
                         {'Origin':server.FRONTEND_ORIGIN,'Content-Type':'application/json','CF-Connecting-IP':'198.51.100.1','X-Forwarded-For':'203.0.113.55'})
            response=conn.getresponse()
            self.assertEqual(response.status,429)
            self.assertIn('最多体验 3 次',json.loads(response.read())['error'])
            limiter.acquire.assert_called_once_with('198.51.100.1')
        conn.close()

    def test_sse_never_echoes_model_key(self):
        secret='fake-secret-for-test-only'
        limiter = MagicMock()
        limiter.acquire.return_value = ('test-ticket', None)
        conn = HTTPConnection('127.0.0.1', self.httpd.server_port, timeout=3)
        with patch.object(server,'live_ready',return_value=True), \
             patch.dict(os.environ, {'DATABASE_URL':'test','DEEPSEEK_API_KEY':secret}), \
             patch('rate_limit.RateLimiter',return_value=limiter), \
             patch('supervisor.supervise',return_value=(x for x in [{'type':'answer','text':'4'}])) as mock_supervisor:
            conn.request('POST','/api/run',json.dumps({'question':'2+2'}),
                         {'Origin':server.FRONTEND_ORIGIN,'Content-Type':'application/json','CF-Connecting-IP':'198.51.100.1'})
            response=conn.getresponse()
            self.assertEqual(response.status,200)
            body=response.read().decode('utf-8')
            self.assertIn('"answer"',body)
            self.assertNotIn(secret,body)
            self.assertGreater(mock_supervisor.call_args.kwargs['seconds'], 0)
            self.assertLessEqual(mock_supervisor.call_args.kwargs['seconds'], 180)
            limiter.release.assert_called_once_with('test-ticket')
        conn.close()

    def test_missing_trusted_client_address_fails_closed(self):
        limiter = MagicMock()
        conn = HTTPConnection('127.0.0.1', self.httpd.server_port, timeout=3)
        with patch.object(server,'live_ready',return_value=True), \
             patch.dict(os.environ, {'DATABASE_URL':'test'}), \
             patch('rate_limit.RateLimiter',return_value=limiter):
            conn.request('POST','/api/run',json.dumps({'question':'2+2'}),
                         {'Origin':server.FRONTEND_ORIGIN,'Content-Type':'application/json','X-Forwarded-For':'203.0.113.55'})
            response=conn.getresponse()
            self.assertEqual(response.status,400)
            response.read()
            limiter.acquire.assert_not_called()
        conn.close()


if __name__ == '__main__':
    unittest.main()
