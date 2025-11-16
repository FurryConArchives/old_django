"""Serve the legacy frontend and public API on separate hostnames."""

from django.http import HttpResponse

from .hosts import (
    PermanentRedirectPreserveMethod,
    api_base_url,
    canonical_api_path,
    frontend_base_url,
    is_api_host,
    is_apex_host,
    is_frontend_api_proxy,
    is_frontend_host,
    is_legacy_api_v1_path,
    is_public_api_path,
    redirect_to_base,
    uses_request_host,
)

CORS_ALLOW_HEADERS = (
    'Accept',
    'Authorization',
    'Content-Type',
    'X-API-Key',
    'X-App-Key',
    'X-Requested-With',
)


class HostSplitMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        redirect = self._redirect(request)
        if redirect is not None:
            return self._with_cors(request, redirect)

        if is_api_host(request):
            request.urlconf = 'furry_archive.api_urls'
        elif is_frontend_host(request):
            request.urlconf = 'furry_archive.frontend_urls'

        path = getattr(request, 'path', '') or ''
        if request.method == 'OPTIONS' and (is_api_host(request) or is_public_api_path(path)):
            return self._with_cors(request, HttpResponse(status=204))

        return self._with_cors(request, self.get_response(request))

    def _redirect(self, request):
        if uses_request_host(request):
            return None

        path = getattr(request, 'path', '') or ''
        if is_api_host(request):
            if is_legacy_api_v1_path(path):
                return self._redirect_to_api(request, path)
            return None

        if is_frontend_host(request):
            if is_public_api_path(path):
                return self._redirect_to_api(request, path)
            return None

        if is_apex_host(request):
            if is_frontend_api_proxy(path):
                return redirect_to_base(frontend_base_url(request), request)
            if is_public_api_path(path):
                return self._redirect_to_api(request, path)
            return redirect_to_base(frontend_base_url(request), request)

        return None

    def _redirect_to_api(self, request, path):
        query = request.META.get('QUERY_STRING', '')
        target = f'{api_base_url(request).rstrip("/")}{canonical_api_path(path)}'
        if query:
            target = f'{target}?{query}'
        return PermanentRedirectPreserveMethod(target)

    def _with_cors(self, request, response):
        path = getattr(request, 'path', '') or ''
        if not (is_api_host(request) or is_public_api_path(path)):
            return response
        response['Access-Control-Allow-Origin'] = '*'
        response['Access-Control-Allow-Methods'] = 'GET, HEAD, OPTIONS, POST'
        response['Access-Control-Allow-Headers'] = ', '.join(CORS_ALLOW_HEADERS)
        response['Access-Control-Max-Age'] = '86400'
        return response
