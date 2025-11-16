from django.http import HttpResponseRedirect
from django.urls import Resolver404, get_resolver


class StripTrailingSlashFallbackMiddleware:
    """Redirect '/path/' -> '/path' when slashless route exists.

    This runs only for safe methods after a 404 response, so we avoid changing
    semantics for POST/PUT/PATCH/DELETE.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        return self.process_response(request, response)

    def process_response(self, request, response):
        if response.status_code != 404:
            return response

        if request.method not in {"GET", "HEAD"}:
            return response

        path = request.path_info or ""
        if not path or path == "/" or not path.endswith("/"):
            return response

        candidate_path = path[:-1]
        if not candidate_path:
            return response

        try:
            get_resolver(getattr(request, 'urlconf', None)).resolve(candidate_path)
        except Resolver404:
            return response

        query = request.META.get("QUERY_STRING", "")
        redirect_target = f"{candidate_path}?{query}" if query else candidate_path
        return HttpResponseRedirect(redirect_target)
