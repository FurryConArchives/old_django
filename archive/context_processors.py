"""
Context processors for adding global template variables
"""
from .models import PDFDocument, Category, SiteBanner, OurFriend
from .utils import get_total_pdf_views, get_total_unique_visitors
from django.conf import settings
from django.core.cache import cache
from django.db.models import Count
from django.db.utils import OperationalError, ProgrammingError
import os
import subprocess

_FOOTER_STATS_CACHE_KEY = 'archive:footer_stats:v2'
_FOOTER_STATS_TTL = 300


def footer_stats(request):
    """Add footer statistics to all templates (cached; skipped for AJAX partials)."""
    if request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.GET.get('ajax') == '1':
        return {}

    cached = cache.get(_FOOTER_STATS_CACHE_KEY)
    if cached is not None:
        return cached

    stats = {
        'total_documents': PDFDocument.objects.filter(is_published=True, takedown_by_request=False).count(),
        'total_pdf_views': get_total_pdf_views(),
        'total_conventions': PDFDocument.objects.filter(
            is_published=True,
            takedown_by_request=False,
            convention_name__isnull=False,
        ).exclude(convention_name='').values('convention_name').distinct().count(),
        'unique_visits': get_total_unique_visitors(),
    }
    cache.set(_FOOTER_STATS_CACHE_KEY, stats, _FOOTER_STATS_TTL)
    return stats


def git_commit(request):
    """Provide current git short hash to templates (best-effort)."""
    # Try environment override first (useful in deployments)
    env_hash = os.environ.get('GIT_COMMIT') or os.environ.get('GIT_COMMIT_SHORT')
    if env_hash:
        return {'git_commit': env_hash}

    try:
        out = subprocess.check_output(['git', 'rev-parse', '--short', 'HEAD'], cwd=os.path.dirname(__file__) + '/..')
        sha = out.decode('utf-8').strip()
        return {'git_commit': sha}
    except Exception:
        return {'git_commit': ''}


def site_banner(request):
    """Expose current active site banner."""
    try:
        banner = SiteBanner.objects.filter(is_enabled=True).exclude(text='').order_by('-updated_at').first()
    except (OperationalError, ProgrammingError):
        banner = None
    return {'site_banner': banner}


def site_hosts(request):
    """Expose split frontend/API origins to templates and the JS client."""
    from .hosts import api_base_url, frontend_base_url

    return {
        'API_BASE_URL': api_base_url(request),
        'FRONTEND_BASE_URL': frontend_base_url(request),
        'SITE_API_KEY': getattr(settings, 'SITE_API_KEY', '') or '',
    }


def our_friends(request):
    """Expose enabled Our Friends entries for footer and staff pages."""
    if request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.GET.get('ajax') == '1':
        return {}

    try:
        friends = list(
            OurFriend.objects.filter(is_enabled=True).order_by('order', 'name')
        )
    except (OperationalError, ProgrammingError):
        friends = []

    return {'our_friends': friends}

