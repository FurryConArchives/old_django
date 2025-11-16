"""Authentication, scopes, and rate limiting for the public API."""

from django.conf import settings
from django.core.cache import cache
from django.http import JsonResponse

from archive.models import AppKey


def presented_app_key_value(request):
    raw = (
        request.META.get('HTTP_X_APP_KEY')
        or request.META.get('HTTP_X_API_KEY')
        or request.GET.get('app_key')
        or request.GET.get('api_key')
    )
    if not raw:
        return ''
    key = raw.strip() if isinstance(raw, str) else str(raw).strip()
    if key.lower().startswith('bearer '):
        key = key.split(None, 1)[1].strip()
    return key


def extract_app_key(request):
    """Return a validated AppKey instance or None."""
    key = presented_app_key_value(request)
    if not key:
        return None
    return AppKey.objects.filter(key=key, active=True).select_related('convention').first()


def is_site_api_key(app_key=None, request=None):
    configured = (getattr(settings, 'SITE_API_KEY', '') or '').strip()
    if not configured:
        return False
    if app_key is not None and getattr(app_key, 'key', '') == configured:
        return True
    if request is not None and presented_app_key_value(request) == configured:
        return True
    return False


def client_ip(request):
    forwarded = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if forwarded:
        return forwarded.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', 'anonymous')


def api_scope_for_path(path):
    """Return the API route scope for a request path, or None if none is required."""
    parts = [part for part in (path or '').split('/') if part]
    if parts[:2] == ['api', 'v1']:
        parts = parts[1:]
    if parts and parts[0] == 'v1' and len(parts) >= 2:
        route = parts[1]
        if route in AppKey.API_ROUTE_SCOPES:
            return route
    return None


def enforce_api_access(request, scope=None, namespace='api_v1', app_key=None):
    """Check key scope and rate limits. Return a JsonResponse on failure, else None."""
    if app_key is None:
        app_key = extract_app_key(request)
    needed = scope if scope is not None else api_scope_for_path(getattr(request, 'path', ''))
    if app_key and needed and not app_key.has_scope(needed):
        return api_error(
            'insufficient_scope',
            f'This app key is missing the {needed} scope.',
            403,
            required_scope=needed,
        )
    return enforce_rate_limit(request, namespace=namespace, app_key=app_key)


def enforce_rate_limit(request, namespace='api_v1', limit=None, app_key=None):
    """Return a JsonResponse when rate limited, otherwise None."""
    if is_site_api_key(app_key, request):
        return None
    if app_key is None:
        app_key = extract_app_key(request)
    if is_site_api_key(app_key, request):
        return None

    if app_key:
        per_min = getattr(app_key, 'rate_per_minute', None)
        if not per_min:
            return None
        identity = f'key:{getattr(app_key, "pk", None) or getattr(app_key, "key", "unknown")}'
    else:
        per_min = limit if limit is not None else getattr(settings, 'PUBLIC_API_RATE_PER_MIN', 1)
        identity = f'ip:{client_ip(request)}'

    cache_key = f'rl:{namespace}:{identity}'
    if cache.add(cache_key, 1, timeout=60):
        current = 1
    else:
        try:
            current = cache.incr(cache_key)
        except ValueError:
            cache.set(cache_key, 1, timeout=60)
            current = 1
    if current > per_min:
        if app_key:
            message = 'Too many requests for this app key. Try again shortly.'
        else:
            message = 'Too many requests. Try again shortly or use an app key.'
        return api_error('rate_limited', message, 429)
    return None


def api_error(code, message, status=400, **extra):
    payload = {'error': code, 'message': message}
    payload.update(extra)
    return JsonResponse(payload, status=status)


def api_success(data, status=200):
    return JsonResponse(data, status=status, json_dumps_params={'ensure_ascii': False})
