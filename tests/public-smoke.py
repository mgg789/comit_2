"""Regression tests for public CD acceptance without production writes."""
import importlib.util
import io
import json
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('public_smoke', Path(__file__).parents[1] / 'infra/public-smoke.py')
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)


class PublicSmokeTests(unittest.TestCase):
    """Reject HTTP-success responses whose bodies violate public route contracts."""

    def validate(self, kind, payload, status=200, mime='application/json'):
        smoke.validate_response(status, mime, json.dumps(payload).encode(), ('/api/probe', status, kind, None))

    def test_health_requires_ready_service(self):
        self.validate('health', {'service': 'api-core', 'status': 'ok'})
        for payload in ({'service': 'other', 'status': 'ok'}, {'service': 'api-core', 'status': 'failed'}, []):
            with self.subTest(payload=payload), self.assertRaises((ValueError, TypeError)):
                self.validate('health', payload)

    def test_empty_showcase_valid_but_bad_pagination_fails(self):
        good = {'items': [], 'has_more': False, 'limit': 24, 'next_cursor': None}
        self.validate('showcase', good)
        for payload in ({**good, 'items': 'not-list'}, {**good, 'items': [42]},
                        {**good, 'limit': True}, {**good, 'has_more': 0},
                        {**good, 'next_cursor': {}}, {'items': []}):
            with self.subTest(payload=payload), self.assertRaises((ValueError, TypeError)):
                self.validate('showcase', payload)

    def test_api_rejects_spa_fallback_status_and_invalid_json(self):
        for status, mime, body in [(200, 'text/html', b'<html>login</html>'),
                                   (200, 'application/json', b'not json'),
                                   (302, 'application/json', b'{}')]:
            with self.subTest(status=status, mime=mime), self.assertRaises((ValueError, TypeError)):
                smoke.validate_response(status, mime, body, ('/api/ready', 200, 'health', None))

    def test_auth_requires_unauthenticated_contract(self):
        self.validate('unauthenticated', {'detail': 'unauthenticated'}, status=401)
        with self.assertRaises((ValueError, TypeError)):
            self.validate('unauthenticated', {'detail': 'other'}, status=401)

    def test_html_svg_and_response_limits(self):
        probe = ('/testing/', 200, 'html', 'сервис тестирования')
        shell = '<title>сервис тестирования</title><div id="root"></div>'.encode()
        smoke.validate_response(200, 'text/html; charset=utf-8', shell, probe)
        with self.assertRaises((ValueError, TypeError)):
            smoke.validate_response(200, 'text/html', b'<div id="root"></div>', probe)
        smoke.validate_response(200, 'image/svg+xml', b'<svg></svg>', ('/icon.svg', 200, 'svg', None))
        with self.assertRaises((ValueError, TypeError)):
            smoke.validate_response(200, 'text/html', b'<svg></svg>', ('/icon.svg', 200, 'svg', None))
        with self.assertRaises((ValueError, TypeError)):
            smoke.validate_response(200, 'text/html', b'a' * (smoke.MAX_BODY + 1), probe)

    def test_https_root_and_no_redirects(self):
        self.assertEqual(smoke.public_base('https://comit.droidje.com/'), 'https://comit.droidje.com')
        for url in ('http://comit.droidje.com', 'https://user:example@host', 'https://host/path',
                    'https://host?x=y', 'https://host/#fragment'):
            with self.subTest(url=url), self.assertRaises((ValueError, TypeError)):
                smoke.public_base(url)
        self.assertIsNone(smoke.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://other'))

    def test_expected_http_error_body_checked(self):
        class Opener:
            def open(self, request, timeout):
                raise urllib.error.HTTPError(request.full_url, 401, '', {'Content-Type': 'application/json', 'Strict-Transport-Security': 'max-age=31536000'},
                                             io.BytesIO(b'{"detail":"unauthenticated"}'))
        smoke.probe_once(Opener(), 'https://comit.droidje.com', ('/me', 401, 'unauthenticated', None))

    def test_help_does_not_require_react_root(self):
        smoke.validate_response(200, 'text/html', 'Справка Comit'.encode(), ('/help/', 200, 'help', 'Справка Comit'))

    def test_public_response_requires_hsts(self):
        class Opener:
            def open(self, request, timeout):
                raise urllib.error.HTTPError(request.full_url, 401, '', {'Content-Type': 'application/json'},
                                             io.BytesIO(b'{"detail":"unauthenticated"}'))
        with self.assertRaisesRegex(ValueError, 'HSTS'):
            smoke.probe_once(Opener(), 'https://comit.droidje.com', ('/me', 401, 'unauthenticated', None))

    def test_main_bounded_retries_no_body_logging(self):
        with patch('sys.argv', ['public-smoke', 'https://comit.droidje.com']), \
             patch.object(smoke, 'PROBES', (('/api/ready', 200, 'health', None),)), \
             patch.object(smoke, 'probe_once', side_effect=ValueError('external body')) as probe, \
             patch.object(smoke.time, 'sleep') as sleep, patch('builtins.print') as output, \
             patch('sys.stderr', new_callable=io.StringIO), \
             self.assertRaises(SystemExit) as failure:
            smoke.main()
        self.assertEqual(failure.exception.code, 1)
        self.assertEqual(probe.call_count, 3)
        self.assertEqual(sleep.call_count, 2)
        self.assertNotIn('external body', str(output.call_args_list))


if __name__ == '__main__':
    unittest.main()
