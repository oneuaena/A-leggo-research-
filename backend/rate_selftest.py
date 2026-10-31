"""Exercise real rate-limit transactions in an isolated database namespace."""
from concurrent.futures import ThreadPoolExecutor
from rate_limit import RateLimiter


def run_checks(dsn):
    limiter = RateLimiter(dsn, prefix='test_')
    limiter.setup()
    limiter.clear_test_data()
    checks = {}
    try:
        for _ in range(3):
            ticket, reason = limiter.acquire('198.51.100.1')
            if reason or not ticket:
                return {'test_setup': False}
            limiter.release(ticket)
        ticket, reason = limiter.acquire('198.51.100.1')
        checks['three_per_ten_minutes'] = ticket is None and reason == 'rate'
        with limiter.connect() as db:
            with db.cursor() as cur:
                cur.execute('SELECT ip_hash FROM test_hits')
                rows = cur.fetchall()
                checks['only_hashed_addresses'] = len(rows) == 3 and all(len(bytes(row[0])) == 32 for row in rows)
                cur.execute("UPDATE test_hits SET expires=clock_timestamp()-interval '1 second'")
        ticket, reason = limiter.acquire('198.51.100.1')
        checks['expiry'] = bool(ticket) and reason is None
        if ticket:
            limiter.release(ticket)
        limiter.clear_test_data()
        with ThreadPoolExecutor(max_workers=8) as pool:
            attempts = list(pool.map(limiter.acquire, [f'198.51.100.{i}' for i in range(10, 18)]))
        tickets = [ticket for ticket, reason in attempts if ticket and reason is None]
        checks['two_concurrent_globally'] = len(tickets) == 2 and sum(reason == 'busy' for _, reason in attempts) == 6
        for ticket in tickets:
            limiter.release(ticket)
    finally:
        limiter.clear_test_data()
    return checks
