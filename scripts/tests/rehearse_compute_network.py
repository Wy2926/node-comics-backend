"""Local Docker/TLS/netem acceptance; only synthetic images and text suppliers."""
import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from uuid import uuid4

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from rehearse_deployment import ROOT, OPENRESTY, REDIS, command, until


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--compute-root', type=Path, default=ROOT.parent / 'node-comics-compute')
    args = parser.parse_args()
    compute = args.compute_root.resolve()
    if not (compute / 'services/classic-engine/classic_node/agent.py').is_file():
        parser.error('--compute-root must point to the compute checkout')
    name = 'nc-compute-network-' + uuid4().hex[:8]
    report = ROOT / 'artifacts/compute-network' / name
    report.mkdir(parents=True)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'wire')])
    cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(datetime.now(timezone.utc) - timedelta(minutes=1))
        .not_valid_after(datetime.now(timezone.utc) + timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName('wire')]), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256()))
    (report / 'cert.pem').write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (report / 'key.pem').write_bytes(key.private_bytes(serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    (report / 'nginx.conf').write_text('''worker_processes 1;
error_log /dev/stderr crit;
events { worker_connections 1024; }
http {
    access_log off;
    map $http_upgrade $comics_connection_upgrade { default upgrade; '' ''; }
    upstream comics_api { server runner:18088; keepalive 32; }
    server {
        listen 8443 ssl;
        ssl_certificate /report/cert.pem;
        ssl_certificate_key /report/key.pem;
        client_max_body_size 132m;
        location / { include /production-api.inc; }
    }
}
''', encoding='utf-8')
    containers = [name + suffix for suffix in ('-tests', '-wire', '-postgres', '-redis')]
    tests, wire, postgres, redis = containers
    try:
        command('docker', 'build', '-f', ROOT / 'deploy/Dockerfile.network-tests',
                '-t', 'node-comics-network:test', ROOT, timeout=900)
        command('docker', 'network', 'create', '--internal', name)
        command('docker', 'run', '-d', '--name', postgres, '--network', name, '--network-alias', 'postgres',
            '-e', 'POSTGRES_USER=nodecomics', '-e', 'POSTGRES_PASSWORD=isolated-test-only',
            '-e', 'POSTGRES_DB=nodecomics_concurrency_test', '--tmpfs', '/var/lib/postgresql/data', 'postgres:17.6-alpine')
        command('docker', 'run', '-d', '--name', redis, '--network', name, '--network-alias', 'redis',
            REDIS, 'redis-server', '--save', '', '--appendonly', 'no', '--maxmemory-policy', 'noeviction')
        until(lambda: 'accepting connections' in command('docker', 'exec', postgres, 'pg_isready', '-U', 'nodecomics'))
        until(lambda: command('docker', 'exec', redis, 'redis-cli', 'ping') == 'PONG')
        command('docker', 'run', '-d', '--name', tests, '--network', name, '--network-alias', 'runner',
            '--cap-add', 'NET_ADMIN', '-v', f'{ROOT / "backend"}:/workspace/backend:ro',
            '-v', f'{compute}:/compute:ro', '-v', f'{report}:/report',
            '-e', 'COMICS_COMPUTE_ROOT=/compute', '-e', 'PYTHONPATH=/compute/packages/document-files',
            '-e', 'RUN_COMPUTE_NETWORK=1', '-e', 'TEST_PG_HOST=postgres',
            '-e', 'TEST_PG_PASSWORD=isolated-test-only', '-e', 'TEST_REDIS_URL=redis://redis:6379/0',
            'node-comics-network:test')
        command('docker', 'run', '-d', '--name', wire, '--network', name, '--network-alias', 'wire',
            '-v', f'{report}:/report:ro', '-v', f'{report / "nginx.conf"}:/usr/local/openresty/nginx/conf/nginx.conf:ro',
            '-v', f'{ROOT / "deploy/openresty.api.inc"}:/production-api.inc:ro', OPENRESTY)
        exit_code = command('docker', 'wait', tests, timeout=1800)
        output = command('docker', 'logs', tests)
        (report / 'pytest.txt').write_text(output, encoding='utf-8')
        print(output, flush=True)
        assert exit_code == '0', f'Network acceptance failed; see {report}'
        print(json.dumps({'report': str(report), 'status': 'passed'}), flush=True)
    finally:
        for container in containers:
            command('docker', 'rm', '-f', container, check=False)
        command('docker', 'network', 'rm', name, check=False)
        (report / 'key.pem').unlink(missing_ok=True)


if __name__ == '__main__':
    main()
