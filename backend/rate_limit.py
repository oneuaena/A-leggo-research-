"""Postgres-only limits for the public demo; never opens formal research data."""
import hashlib
import hmac
import ipaddress
import re
import secrets
import uuid


class RateLimiter:
    def __init__(self, dsn, prefix='demo_'):
        if not dsn or not re.fullmatch(r'[a-z_]+', prefix):
            raise ValueError('rate store configuration invalid')
        self.dsn = dsn
        self.prefix = prefix

    def connect(self):
        import psycopg
        return psycopg.connect(self.dsn, connect_timeout=5,
            options='-c statement_timeout=3000 -c lock_timeout=2000',
            keepalives=1, keepalives_idle=5, keepalives_interval=2, keepalives_count=2)

    def setup(self):
        p = self.prefix
        with self.connect() as db:
            with db.cursor() as cur:
                cur.execute('SELECT pg_advisory_xact_lock(716038201)')
                cur.execute(f'CREATE TABLE IF NOT EXISTS {p}settings (id INTEGER PRIMARY KEY, secret BYTEA NOT NULL)')
                cur.execute(f'CREATE TABLE IF NOT EXISTS {p}hits (ip_hash BYTEA NOT NULL, expires TIMESTAMPTZ NOT NULL)')
                cur.execute(f'CREATE TABLE IF NOT EXISTS {p}slots (token UUID PRIMARY KEY, expires TIMESTAMPTZ NOT NULL)')
                cur.execute(f'INSERT INTO {p}settings (id, secret) VALUES (1, %s) ON CONFLICT (id) DO NOTHING', (secrets.token_bytes(32),))

    def acquire(self, ip):
        address = ipaddress.ip_address(ip).compressed.encode('ascii')
        p = self.prefix
        with self.connect() as db:
            with db.cursor() as cur:
                cur.execute('SELECT pg_advisory_xact_lock(716038201)')
                cur.execute(f'DELETE FROM {p}hits WHERE expires <= clock_timestamp()')
                cur.execute(f'DELETE FROM {p}slots WHERE expires <= clock_timestamp()')
                cur.execute(f'SELECT secret FROM {p}settings WHERE id=1')
                secret = bytes(cur.fetchone()[0])
                digest = hmac.new(secret, address, hashlib.sha256).digest()
                cur.execute(f'SELECT count(*) FROM {p}hits WHERE ip_hash=%s', (digest,))
                if cur.fetchone()[0] >= 3:
                    return None, 'rate'
                cur.execute(f'SELECT count(*) FROM {p}slots')
                if cur.fetchone()[0] >= 2:
                    return None, 'busy'
                ticket = str(uuid.uuid4())
                cur.execute(f"INSERT INTO {p}hits (ip_hash, expires) VALUES (%s, clock_timestamp() + interval '10 minutes')", (digest,))
                # Leave a safety margin after the three-minute process deadline.
                cur.execute(f"INSERT INTO {p}slots (token, expires) VALUES (%s, clock_timestamp() + interval '4 minutes')", (ticket,))
                return ticket, None

    def release(self, ticket):
        p = self.prefix
        with self.connect() as db:
            with db.cursor() as cur:
                cur.execute(f'DELETE FROM {p}slots WHERE token=%s', (ticket,))

    def clear_test_data(self):
        if self.prefix != 'test_':
            raise ValueError('test namespace required')
        with self.connect() as db:
            with db.cursor() as cur:
                cur.execute('SELECT pg_advisory_xact_lock(716038201)')
                cur.execute('DELETE FROM test_hits')
                cur.execute('DELETE FROM test_slots')
