"""Read-only production acceptance over verified public HTTPS, without credentials."""
import argparse
import json
import ssl
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

# These routes are public; never send session cookies or print response bodies.
PROBES = (
    ('/', 200, 'html', 'работа настигает сама'),
    ('/candidate/', 200, 'html', 'кабинет кандидата'),
    ('/employer/', 200, 'html', 'кабинет работодателя'),
    ('/testing/', 200, 'html', 'сервис тестирования'),
    ('/support/', 200, 'html', 'служба поддержки'),
    ('/design/comit-mark.svg', 200, 'svg', None),
    ('/design/showcase-icon.svg', 200, 'svg', None),
    ('/api/healthz', 200, 'health', None),
    ('/api/ready', 200, 'health', None),
    ('/api/public/showcase/candidates', 200, 'showcase', None),
    ('/api/public/showcase/vacancies', 200, 'showcase', None),
    ('/api/auth/candidate/me', 401, 'unauthenticated', None),
    ('/api/auth/employer/me', 401, 'unauthenticated', None),
    ('/api/candidate/testing/preflight?specialization=sql', 401, 'unauthenticated', None),
    ('/api/internal/strict-schema', 404, 'status', None),
)
MAX_BODY = 2 * 1024 * 1024


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Reject redirects so login pages or another host cannot satisfy an API probe."""

    def redirect_request(self, req: urllib.request.Request, fp: object, code: int,
                         msg: str, headers: object, newurl: str) -> None:
        """Keep the original route's response; no redirect target is requested."""
        return


def public_base(value: str) -> str:
    """Validate a root HTTPS URL without credentials, queries or fragments."""
    parsed = urlparse(value)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or parsed.path not in ('', '/')
            or parsed.query or parsed.fragment):
        raise ValueError('Expected root public HTTPS URL')
    return value.rstrip('/')


def validate_response(status: int, content_type: str, body: bytes, probe: tuple[str, int, str, str | None]) -> None:
    """Check exact status, MIME and minimal contracts; an empty showcase is valid."""
    _, expected, kind, marker = probe
    if status != expected:
        raise ValueError(f'Expected HTTP {expected}, received {status}')
    if len(body) > MAX_BODY:
        raise ValueError('Response exceeds smoke size limit')
    if kind == 'status':
        return
    mime = content_type.split(';', 1)[0].strip().lower()
    if kind == 'html':
        if mime != 'text/html' or marker not in body.decode('utf-8') or b'id="root"' not in body:
            raise ValueError('Expected application HTML shell')
        return
    if kind == 'svg':
        if mime != 'image/svg+xml' or b'<svg' not in body:
            raise ValueError('Expected SVG asset, not HTML fallback')
        return
    if mime != 'application/json':
        raise ValueError('Expected JSON MIME, not HTML fallback')
    payload = json.loads(body)
    if not isinstance(payload, dict):
        raise TypeError('Expected JSON object')
    if kind == 'health':
        if payload.get('service') != 'api-core' or payload.get('status') != 'ok':
            raise ValueError('API health/readiness contract failed')
    elif kind == 'unauthenticated':
        if payload.get('detail') != 'unauthenticated':
            raise ValueError('Authentication boundary contract failed')
    elif kind == 'showcase':
        if (not isinstance(payload.get('items'), list)
                or not isinstance(payload.get('has_more'), bool)
                or type(payload.get('limit')) is not int or payload['limit'] <= 0
                or 'next_cursor' not in payload
                or payload['next_cursor'] is not None and not isinstance(payload['next_cursor'], str)):
            raise ValueError('Showcase pagination contract failed')
        if not all(isinstance(item, dict) for item in payload['items']):
            raise ValueError('Showcase items must be objects')
    else:
        raise ValueError('Unknown smoke contract')


def probe_once(opener: urllib.request.OpenerDirector, base: str, probe: tuple[str, int, str, str | None]) -> None:
    """Fetch one public response with a 15-second timeout and bounded body size."""
    request = urllib.request.Request(base + probe[0], headers={'User-Agent': 'Comit-CD-public-smoke/1'})
    try:
        response = opener.open(request, timeout=15)
    except urllib.error.HTTPError as error:
        response = error  # 401/404 are expected authentication/privacy boundaries.
    with response:
        validate_response(response.status, response.headers.get('Content-Type', ''),
                          response.read(MAX_BODY + 1), probe)


def main() -> None:
    """Probe all routes, retry transient failures three times, fail the CD job on error."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('url')
    args = parser.parse_args()
    base = public_base(args.url)
    # System trust roots and hostname verification are mandatory; no -k fallback.
    opener = urllib.request.build_opener(NoRedirect(), urllib.request.HTTPSHandler(context=ssl.create_default_context()))
    failures = 0
    for probe in PROBES:
        for attempt in range(3):
            try:
                probe_once(opener, base, probe)
                print(f'PASS {probe[0]} HTTP {probe[1]} / {probe[2]}', flush=True)
                break
            except (OSError, ValueError, TypeError, urllib.error.URLError):
                if attempt < 2:
                    time.sleep(2)
                else:
                    # Do not print external bodies, credentials or arbitrary provider text.
                    print(f'FAIL {probe[0]} expected HTTP {probe[1]} / {probe[2]}', flush=True)
                    failures += 1
    if failures:
        parser.exit(1, f'Public HTTPS smoke failed: {failures} probe(s)\n')
    print('Public HTTPS smoke: all contracts passed')


if __name__ == '__main__':
    main()
