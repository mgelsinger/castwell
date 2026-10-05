"""Bound request bodies while they are received, including chunked uploads."""

import ipaddress
import os
import re
from urllib.parse import urlsplit

from starlette.responses import JSONResponse
from starlette.formparsers import MultiPartException


def _hostname(value):
    """Parse Host, including bracketed IPv6, without trusting forwarded headers."""
    if not value or re.search(r'[\x00-\x20\x7f/@?#\\%]', value):
        raise ValueError('Invalid hostname')
    try:
        # Bare IPv6 is useful in configuration; requests normally use brackets.
        return str(ipaddress.ip_address(value)).lower()
    except ValueError:
        pass
    parsed = urlsplit('//' + value)
    parsed.port  # Reject malformed and out-of-range ports.
    hostname = (parsed.hostname or '').lower().rstrip('.')
    try:
        return str(ipaddress.ip_address(hostname)).lower()
    except ValueError:
        if not hostname or not re.fullmatch(r'[a-z0-9._-]+', hostname):
            raise ValueError('Invalid hostname') from None
    return hostname


class AllowedHosts:
    """Reject DNS rebinding; deployments opt in to their exact public hosts."""

    def __init__(self, app, allowed_hosts=None):
        self.app = app
        additional = os.getenv('CASTWELL_ALLOWED_HOSTS', '').split(',') if allowed_hosts is None else allowed_hosts
        try:
            self.hosts = {'localhost', '127.0.0.1', '::1'} | {
                _hostname(host.strip()) for host in additional if host.strip()
            }
        except (ValueError, TypeError, AttributeError):
            raise ValueError('CASTWELL_ALLOWED_HOSTS must contain comma-separated exact hostnames or IP addresses, without schemes, paths, credentials, or wildcards') from None

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            await self.app(scope, receive, send)
            return
        hosts = [value for name, value in scope.get('headers', []) if name.lower() == b'host']
        try:
            trusted = len(hosts) == 1 and _hostname(hosts[0].decode('ascii')) in self.hosts
        except (ValueError, UnicodeError):
            trusted = False
        if not trusted:
            await JSONResponse({'detail': 'Untrusted Host header. Configure CASTWELL_ALLOWED_HOSTS for this address.'}, status_code=400)(scope, receive, send)
            return
        await self.app(scope, receive, send)


class _RequestBodyTooLarge(MultiPartException):
    def __init__(self):
        # Older supported Starlette parsers close temporary upload files only
        # for MultiPartException, so cancellation uses their cleanup contract.
        super().__init__('Request body exceeds the allowed size')


class RequestSizeLimit:
    """Apply limits before JSON parsing or multipart files can exhaust storage.

    Content-Length provides an early rejection, but the received byte count is
    authoritative. FastAPI's parsers may translate receive errors into their own
    responses; suppress those responses and report one consistent 413 instead.
    """

    def __init__(self, app, max_body_size=25 * 1024**2, max_audio_size=2 * 1024**3 + 1024**2):
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0
               for value in (max_body_size, max_audio_size)):
            raise ValueError('Request size limits must be nonnegative integers')
        self.app = app
        self.max_body_size = max_body_size
        self.max_audio_size = max_audio_size

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            await self.app(scope, receive, send)
            return
        limit = self.max_audio_size if scope.get('path') == '/api/audio' else self.max_body_size
        lengths = [value for name, value in scope.get('headers', []) if name.lower() == b'content-length']
        if lengths:
            try:
                if any(not value.isdigit() for value in lengths):
                    raise ValueError
                parsed = [int(value) for value in lengths]
                if len(set(parsed)) != 1:
                    raise ValueError
            except ValueError:
                await JSONResponse({'detail': 'Invalid content length'}, status_code=400)(scope, receive, send)
                return
            if parsed[0] > limit:
                await self._too_large(scope, receive, send)
                return

        received = 0
        exceeded = False
        response_started = False

        async def limited_receive():
            nonlocal received, exceeded
            if exceeded:
                raise _RequestBodyTooLarge
            message = await receive()
            if message['type'] == 'http.request':
                received += len(message.get('body', b''))
                if received > limit:
                    exceeded = True
                    raise _RequestBodyTooLarge
            return message

        async def guarded_send(message):
            nonlocal response_started
            if exceeded:
                return
            if message['type'] == 'http.response.start':
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, guarded_send)
        except Exception:
            if not exceeded or response_started:
                # A streaming application that sends headers before consuming its
                # request must abort the connection; HTTP cannot send a new status.
                raise
        if exceeded:
            if response_started:
                raise _RequestBodyTooLarge
            await self._too_large(scope, receive, send)

    @staticmethod
    async def _too_large(scope, receive, send):
        await JSONResponse({'detail': 'The upload exceeds the allowed size'}, status_code=413)(scope, receive, send)
