"""Real local HTTP keep-alive reuse, concurrent calls and per-request isolation."""
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Barrier, Thread

import httpx
import pytest

from app.adapters import openai_text, transport
from app.adapters.llm import RequestLimits, TextError


def test_pool_reuses_sockets_without_sharing_credentials_or_cookies(monkeypatch):
    backend = transport.CheckedBackend
    monkeypatch.setattr(transport, 'CheckedBackend', lambda: backend(allow_private=True))
    requests = []
    concurrent = Barrier(2)
    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'
        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append((self.client_address, self.headers['Authorization'], self.headers.get('Cookie'), payload))
            if payload['model'] == 'parallel':
                concurrent.wait(timeout=5)
            reply = json.dumps({'choices': [{'finish_reason': 'stop',
                'message': {'content': self.headers['Authorization']}}]}).encode()
            self.send_response(200)
            self.send_header('Content-Length', str(len(reply)))
            self.send_header('Set-Cookie', 'provider_session=private; Path=/')
            self.end_headers()
            self.wfile.write(reply)
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    profile = {'base_url': f'http://127.0.0.1:{server.server_port}', 'model': 'sequential',
               'protocol': 'chat_completions', 'user_agent': 'isolated', 'timeout_seconds': 10, 'max_output_tokens': 256}
    try:
        with ThreadPoolExecutor(8) as executor:
            pools = list(executor.map(lambda _: transport.connection_pool(), range(16)))
        assert all(pool is pools[0] for pool in pools)
        for key in ('first-key', 'second-key'):
            assert openai_text.call_messages([], profile, key).content == 'Bearer ' + key
        assert requests[0][0] == requests[1][0]  # Same TCP connection across isolated HTTPX clients.
        assert [request[1] for request in requests] == ['Bearer first-key', 'Bearer second-key']
        assert all(request[2] is None for request in requests)
        with ThreadPoolExecutor(2) as executor:
            replies = list(executor.map(lambda key: openai_text.call_messages([], {**profile, 'model': 'parallel'}, key),
                                        ('parallel-a', 'parallel-b')))
        assert [reply.content for reply in replies] == ['Bearer parallel-a', 'Bearer parallel-b']
        assert all(request[2] is None for request in requests)
        transport.close_pooled_connections()
        assert not pools[0].connections
        openai_text.call_messages([], profile, 'new-key')
        assert requests[-1][0] != requests[0][0]
    finally:
        transport.close_pooled_connections()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize('error,unknown,retryable', [
    (httpx.ReadTimeout, True, False), (httpx.WriteError, True, False),
    (httpx.ConnectTimeout, False, True), (httpx.PoolTimeout, False, True),
])
def test_transport_failures_distinguish_unsent_from_unknown(monkeypatch, error, unknown, retryable):
    calls = []
    def request(req):
        calls.append(req)
        raise error('private transport diagnostic')
    monkeypatch.setattr(openai_text, 'CheckedTransport', lambda **_: httpx.MockTransport(request))
    profile = {'base_url': 'https://text.example', 'model': 'fixture', 'protocol': 'chat_completions',
               'user_agent': 'isolated', 'timeout_seconds': 10, 'max_output_tokens': 256}
    with pytest.raises(TextError) as failure:
        openai_text.call_messages([], profile, 'isolated-key')
    assert len(calls) == 1
    assert failure.value.unknown is unknown and failure.value.retryable is retryable
    assert 'private' not in failure.value.message


def test_shared_pool_preserves_per_request_timeout_and_output_limits(monkeypatch):
    requests = []
    def handler(request):
        requests.append((request.extensions['timeout'], json.loads(request.content)))
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {'content': 'ok'}}]})
    monkeypatch.setattr(openai_text, 'CheckedTransport', lambda **_: httpx.MockTransport(handler))
    profile = {'base_url': 'https://text.example', 'model': 'fixture', 'protocol': 'chat_completions',
               'user_agent': 'isolated', 'timeout_seconds': 10, 'max_output_tokens': 256}
    openai_text.call_messages([], profile, 'first', limits=RequestLimits(600, 32000, 4096))
    openai_text.call_messages([], profile, 'second')
    assert [request[0]['read'] for request in requests] == [600, 10]
    assert [request[1]['max_completion_tokens'] for request in requests] == [32000, 256]
