"""Canonical hosts for the legacy HTML site and the public JSON API."""

from django.conf import settings
from django.http import HttpResponse

LOCAL_HOSTS = frozenset({
    'localhost',
    '127.0.0.1',
    '0.0.0.0',
    'testserver',
    '::1',
})

APEX_HOSTS = frozenset({
    'furryconarchives.org',
    'www.furryconarchives.org',
})

FRONTEND_PROXY_API_PREFIXES = (
    '/api/telegram-messages',
)


class PermanentRedirectPreserveMethod(HttpResponse):
    """308 so POST webhooks keep their method when the host moves."""

    status_code = 308

    def __init__(self, redirect_to):
        super().__init__()
        self['Location'] = redirect_to


def hostname_of(request):
    host = (request.get_host() if request else '') or ''
    return host.split(':')[0].lower()


def frontend_host():
    return getattr(settings, 'FRONTEND_HOST', 'old.furryconarchives.org')


def api_host():
    return getattr(settings, 'API_HOST', 'api.furryconarchives.org')


def is_local_host(host):
    if not host:
        return True
    return host in LOCAL_HOSTS or host.endswith('.localhost')


def uses_request_host(request):
    """Keep a single origin for local runserver / Django tests."""
    return is_local_host(hostname_of(request))


def is_api_host(request):
    host = hostname_of(request)
    configured = api_host()
    return bool(configured) and host == configured


def is_frontend_host(request):
    host = hostname_of(request)
    configured = frontend_host()
    return bool(configured) and host == configured


def is_apex_host(request):
    return hostname_of(request) in APEX_HOSTS


def is_frontend_api_proxy(path):
    path = path or ''
    return any(path == prefix or path.startswith(prefix + '/') for prefix in FRONTEND_PROXY_API_PREFIXES)


def is_v1_path(path):
    path = path or ''
    return path == '/v1' or path.startswith('/v1/')


def is_legacy_api_v1_path(path):
    path = path or ''
    return path == '/api/v1' or path.startswith('/api/v1/')


def canonical_api_path(path):
    """Map leftover /api/v1/ URLs onto /v1/."""
    path = path or ''
    if is_legacy_api_v1_path(path):
        rest = path[len('/api/v1'):] or '/'
        if not rest.startswith('/'):
            rest = '/' + rest
        return '/v1' + rest
    return path


def is_public_api_path(path):
    path = path or ''
    if is_frontend_api_proxy(path):
        return False
    return is_v1_path(path) or is_legacy_api_v1_path(path)


def is_api_request(request):
    if is_api_host(request):
        return True
    return is_public_api_path(getattr(request, 'path', '') or '')


def join_origin(base, path):
    if not path:
        return None
    if path.startswith('http://') or path.startswith('https://'):
        return path
    base = (base or '').rstrip('/')
    if not path.startswith('/'):
        path = '/' + path
    return f'{base}{path}'


def origin_from_request(request):
    if not request:
        return frontend_base_url()
    scheme = 'https' if request.is_secure() else 'http'
    return f'{scheme}://{request.get_host()}'


def frontend_base_url(request=None):
    if request is not None and uses_request_host(request):
        return origin_from_request(request)
    return getattr(settings, 'FRONTEND_BASE_URL', f'https://{frontend_host()}').rstrip('/')


def api_base_url(request=None):
    if request is not None and uses_request_host(request):
        return origin_from_request(request)
    return getattr(settings, 'API_BASE_URL', f'https://{api_host()}').rstrip('/')


def frontend_url(request, path):
    return join_origin(frontend_base_url(request), path)


def api_url(request, path):
    return join_origin(api_base_url(request), path)


def redirect_to_base(base, request):
    return PermanentRedirectPreserveMethod(f'{base.rstrip("/")}{request.get_full_path()}')
