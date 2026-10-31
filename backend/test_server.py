import json
import threading
import unittest
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
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


if __name__ == '__main__':
    unittest.main()
