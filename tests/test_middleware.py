"""Enforce upload limits before application and multipart body buffering."""

import json
import os
import tempfile
import unittest
from unittest.mock import patch

from fastapi import FastAPI, File, UploadFile
from fastapi.testclient import TestClient

from castwell.middleware import AllowedHosts, RequestSizeLimit


class RequestSizeLimitTests(unittest.IsolatedAsyncioTestCase):
    async def call(self, chunks, *, path='/api/example', headers=None, limit=8, audio_limit=16, app=None):
        delivered = []
        sent = []
        source = iter(chunks)

        async def receive():
            value = next(source)
            return {'type': 'http.request', 'body': value, 'more_body': True}

        async def default_app(scope, receive, send):
            for _ in chunks:
                delivered.append((await receive())['body'])
            await send({'type': 'http.response.start', 'status': 200, 'headers': []})
            await send({'type': 'http.response.body', 'body': b'ok'})

        async def send(message):
            sent.append(message)

        scope = {'type': 'http', 'method': 'POST', 'path': path, 'headers': headers or []}
        await RequestSizeLimit(app or default_app, max_body_size=limit, max_audio_size=audio_limit)(scope, receive, send)
        return delivered, sent

    async def test_chunked_request_rejected_before_excess_bytes_reach_application(self):
        delivered, messages = await self.call([b'1234', b'5678', b'9', b'not read'])
        self.assertEqual(delivered, [b'1234', b'5678'])
        self.assertEqual([item['status'] for item in messages if item['type'] == 'http.response.start'], [413])
        body = next(item['body'] for item in messages if item['type'] == 'http.response.body')
        self.assertEqual(json.loads(body), {'detail': 'The upload exceeds the allowed size'})

    async def test_content_length_cannot_hide_a_larger_actual_body(self):
        delivered, messages = await self.call([b'1234', b'56789'], headers=[(b'content-length', b'4')])
        self.assertEqual(delivered, [b'1234'])
        self.assertEqual(messages[0]['status'], 413)

    async def test_declared_oversize_body_is_rejected_without_reading_it(self):
        delivered, messages = await self.call([], headers=[(b'content-length', b'9')])
        self.assertEqual(delivered, [])
        self.assertEqual(messages[0]['status'], 413)

    async def test_exact_limit_and_audio_specific_limit_are_accepted(self):
        for path, chunks in [('/api/example', [b'1234', b'5678']), ('/api/audio', [b'12345678', b'12345678'])]:
            with self.subTest(path=path):
                delivered, messages = await self.call(chunks, path=path)
                self.assertEqual(delivered, chunks)
                self.assertEqual(messages[0]['status'], 200)

    async def test_parser_cannot_replace_oversize_response_with_400_or_success(self):
        async def swallowing_parser(scope, receive, send):
            try:
                await receive()
            except Exception:
                pass
            await send({'type': 'http.response.start', 'status': 400, 'headers': []})
            await send({'type': 'http.response.body', 'body': b'parser failure'})

        _, messages = await self.call([b'123456789'], app=swallowing_parser)
        self.assertEqual([item['status'] for item in messages if item['type'] == 'http.response.start'], [413])
        self.assertEqual(len(messages), 2)

    async def test_malformed_or_conflicting_lengths_fail_without_reading(self):
        for lengths in [(b'-1',), (b'bad',), (b'4', b'5')]:
            with self.subTest(lengths=lengths):
                _, messages = await self.call([], headers=[(b'content-length', value) for value in lengths])
                self.assertEqual(messages[0]['status'], 400)

    async def test_unrelated_application_exception_is_not_hidden(self):
        async def broken(scope, receive, send):
            raise RuntimeError('application failed')

        with self.assertRaisesRegex(RuntimeError, 'application failed'):
            await self.call([], app=broken)

    async def test_websocket_lifespan_passthrough(self):
        scopes = []

        async def app(scope, receive, send):
            scopes.append(scope['type'])

        middleware = RequestSizeLimit(app)
        for kind in ('lifespan', 'websocket'):
            await middleware({'type': kind}, None, None)
        self.assertEqual(scopes, ['lifespan', 'websocket'])


class MultipartLimitTests(unittest.TestCase):
    def test_chunked_multipart_is_stopped_during_parsing_before_route_execution(self):
        app = FastAPI()
        calls = []

        @app.post('/api/audio')
        def upload(file: UploadFile = File(...)):
            calls.append(True)
            return {'bytes': len(file.file.read())}

        app.add_middleware(RequestSizeLimit, max_audio_size=256)
        chunks = [b'--fixture\r\nContent-Disposition: form-data; name="file"; filename="audio.wav"\r\n'
                  b'Content-Type: audio/wav\r\n\r\n', b'x' * 256, b'\r\n--fixture--\r\n']
        with TestClient(app) as client:
            response = client.post('/api/audio', content=iter(chunks), headers={'Content-Type': 'multipart/form-data; boundary=fixture'})
        self.assertEqual(response.status_code, 413, response.text)
        self.assertEqual(calls, [])


class AllowedHostsTests(unittest.TestCase):
    def test_rebinding_origin_and_host_cannot_read_or_mutate_the_library(self):
        from castwell.app import create_app

        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'CASTWELL_ALLOWED_HOSTS': ''}):
            app = create_app(directory)
            with TestClient(app, base_url='http://attacker.example') as client:
                headers = {'Origin': 'http://attacker.example', 'Sec-Fetch-Site': 'same-origin'}
                self.assertEqual(client.get('/api/episodes', headers=headers).status_code, 400)
                response = client.patch('/api/settings', headers=headers, json={'review_only': False})
                self.assertEqual(response.status_code, 400)
                self.assertTrue(app.state.settings.get()['review_only'])

    def test_loopback_and_configured_lan_hosts_work_and_other_hosts_fail(self):
        app = FastAPI()

        @app.get('/health')
        def health():
            return {'ok': True}

        app.add_middleware(AllowedHosts)
        with patch.dict(os.environ, {'CASTWELL_ALLOWED_HOSTS': 'castwell.lan,192.168.1.25,[2001:db8::1]'}), TestClient(app) as client:
            for host in ['localhost:8000', '127.0.0.1:8000', '[::1]:8000', 'CASTWELL.LAN:8000',
                         '192.168.1.25:8000', '[2001:db8::1]:8000']:
                with self.subTest(host=host):
                    self.assertEqual(client.get('/health', headers={'Host': host}).status_code, 200)
            for host in ['attacker.example', 'castwell.lan.attacker.example', 'localhost@attacker.example',
                         'localhost:invalid', 'testserver', '[::2]:8000']:
                with self.subTest(host=host):
                    self.assertEqual(client.get('/health', headers={'Host': host}).status_code, 400)
            self.assertEqual(client.get('/health', headers={'Host': 'attacker.example', 'X-Forwarded-Host': 'localhost'}).status_code, 400)

    def test_missing_duplicate_and_malformed_host_headers_are_rejected(self):
        app = FastAPI()
        app.add_middleware(AllowedHosts, allowed_hosts=[])
        with TestClient(app) as client:
            for headers in [[], [('host', 'localhost'), ('host', 'attacker.example')], [('host', '[broken')]]:
                request = client.build_request('GET', '/')
                request.headers.clear()
                request.headers.update(headers)
                with self.subTest(headers=headers):
                    self.assertEqual(client.send(request).status_code, 400)

    def test_configuration_cannot_accidentally_enable_wildcards_or_urls(self):
        for host in ['*', '*.example.com', 'https://example.com', 'user:secret@example.com', 'example.com/path']:
            with self.subTest(host=host), self.assertRaisesRegex(ValueError, 'CASTWELL_ALLOWED_HOSTS'):
                AllowedHosts(None, allowed_hosts=[host])


if __name__ == '__main__':
    unittest.main()
