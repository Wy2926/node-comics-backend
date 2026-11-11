"""Container-only progress probe: environment, PostgreSQL and Redis; no application imports."""
import argparse
from datetime import datetime, timedelta, timezone
import os
import socket
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit


def instance_id(pid=None):
    return f"{socket.gethostname()}:{os.getpid() if pid is None else pid}"[:200]


def ready(role, pid=1):
    import psycopg
    from redis import Redis
    from redis.backoff import NoBackoff
    from redis.retry import Retry

    # Compose injects the effective environment. Full configuration validation
    # belongs to process startup, not a fresh interpreter every 15 seconds.
    url = urlsplit(os.environ['DATABASE_URL'])
    # SQLAlchemy encodes spaces as '+', while libpq URI parameters require '%20'.
    url = urlunsplit(url._replace(scheme=url.scheme.removesuffix('+psycopg'),
        query=urlencode(parse_qsl(url.query, keep_blank_values=True), quote_via=quote)))
    deadline = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
        seconds=int(os.environ.get('CLUSTER_NODE_TIMEOUT_SECONDS', '120')))
    with psycopg.connect(url, connect_timeout=2, autocommit=True) as connection:
        connection.execute("SET statement_timeout = '1s'")
        row = connection.execute('''SELECT 1 FROM service_heartbeats
            WHERE role = %s AND instance_id = %s AND heartbeat_at > %s
                AND last_success_at > %s AND consecutive_failures = 0''',
            (role, instance_id(pid), deadline, deadline)).fetchone()
    if not row:
        return False
    with Redis.from_url(os.environ['REDIS_URL'], socket_connect_timeout=1, socket_timeout=1,
                        retry=Retry(NoBackoff(), 0)) as client:
        return bool(client.ping())


def main():
    parser = argparse.ArgumentParser(description='Check this container\'s committed progress and Redis')
    parser.add_argument('role', choices=('control-worker', 'maintenance', 'document-worker'))
    parser.add_argument('--pid', type=int, default=1)
    args = parser.parse_args()
    try:
        healthy = ready(args.role, args.pid)
    except Exception:
        # Driver exceptions may include credentials, addresses or SQL values.
        healthy = False
    print('ready' if healthy else 'unavailable')
    raise SystemExit(0 if healthy else 1)


if __name__ == '__main__':
    main()
