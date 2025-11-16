from django.urls import include, path

from archive.hosts import PermanentRedirectPreserveMethod


def redirect_legacy_api_v1(request, rest=''):
    target = '/v1/' + (rest or '').lstrip('/')
    query = request.META.get('QUERY_STRING')
    if query:
        target = f'{target}?{query}'
    return PermanentRedirectPreserveMethod(target)


urlpatterns = [
    path('v1/', include('archive.api.urls')),
    path('api/v1/<path:rest>', redirect_legacy_api_v1),
    path('api/v1/', redirect_legacy_api_v1),
]
