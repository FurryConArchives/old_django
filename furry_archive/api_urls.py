"""URLConf for the public JSON API on api.furryconarchives.org."""

from django.http import HttpResponse
from django.urls import include, path

from archive.api.views import api_v1_root


def api_robots(_request):
    return HttpResponse(
        'User-agent: *\nAllow: /v1/\n',
        content_type='text/plain; charset=utf-8',
    )


urlpatterns = [
    path('', api_v1_root, name='api_v1_host_root'),
    path('robots.txt', api_robots, name='api_robots_txt'),
    path('', include('archive.urls_api')),
]
