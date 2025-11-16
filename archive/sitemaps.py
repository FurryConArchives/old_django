"""Public /sitemap.xml for crawlers."""

from xml.sax.saxutils import escape

from django.core.cache import cache
from django.db.models import Max, Q
from django.http import HttpResponse
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET

from archive.models import Category, PDFDocument, Schedule

SITEMAP_CACHE_KEY = 'archive:sitemap.xml:v2'
SITEMAP_CACHE_TTL = 60 * 15

STATIC_PAGES = (
    ('index', '1.0', 'daily'),
    ('document_list', '0.9', 'daily'),
    ('category_browse', '0.9', 'daily'),
    ('schedule_index', '0.8', 'weekly'),
    ('physical_inventory_grid', '0.7', 'weekly'),
    ('faq', '0.5', 'monthly'),
    ('contact', '0.5', 'monthly'),
    ('board_of_directors', '0.4', 'monthly'),
    ('rights', '0.4', 'monthly'),
    ('preservation_policy', '0.4', 'monthly'),
    ('preservation_tips', '0.4', 'monthly'),
)


def _abs(request, path):
    from archive.hosts import frontend_url
    return frontend_url(request, path)


def _fmt_date(value):
    if not value:
        return None
    if timezone.is_aware(value):
        value = timezone.localtime(value)
    return value.date().isoformat()


def _iter_urls(request):
    latest_doc = (
        PDFDocument.objects.filter(is_published=True, takedown_by_request=False)
        .aggregate(latest=Max('updated_at'))
        .get('latest')
    )
    for name, priority, changefreq in STATIC_PAGES:
        lastmod = latest_doc if name in {'index', 'document_list', 'category_browse'} else None
        yield reverse(name), lastmod, changefreq, priority

    documents = (
        PDFDocument.objects.filter(is_published=True, takedown_by_request=False)
        .only('slug', 'updated_at')
        .order_by('-updated_at', 'slug')
    )
    for doc in documents.iterator():
        yield reverse('document_detail', kwargs={'slug': doc.slug}), doc.updated_at, 'weekly', '0.8'

    categories = Category.objects.annotate(
        latest_doc=Max(
            'documents__updated_at',
            filter=Q(documents__is_published=True, documents__takedown_by_request=False),
        )
    ).order_by('slug')
    for category in categories:
        yield (
            reverse('convention_detail', kwargs={'slug': category.slug}),
            category.latest_doc or getattr(category, 'created_at', None),
            'weekly',
            '0.7',
        )

    document_years = PDFDocument.objects.filter(
        is_published=True,
        takedown_by_request=False,
        category__isnull=False,
        year__isnull=False,
    ).values_list('category__slug', 'year')
    schedule_years = Schedule.objects.filter(
        deleted=False,
        category__isnull=False,
        year__isnull=False,
    ).values_list('category__slug', 'year')
    for slug, year in sorted({(s, int(y)) for s, y in list(document_years) + list(schedule_years)}):
        yield reverse('convention_detail_year', kwargs={'slug': slug, 'year': year}), None, 'weekly', '0.75'

    schedules = Schedule.objects.filter(deleted=False).select_related('category').order_by('-year', 'slug')
    for schedule in schedules:
        yield reverse('schedule_view', kwargs={'slug': schedule.route_slug}), schedule.last_updated, 'weekly', '0.65'


def _render_sitemap(request):
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    for path, lastmod, changefreq, priority in _iter_urls(request):
        lines.append('<url>')
        lines.append(f'<loc>{escape(_abs(request, path))}</loc>')
        dated = _fmt_date(lastmod)
        if dated:
            lines.append(f'<lastmod>{dated}</lastmod>')
        lines.append(f'<changefreq>{changefreq}</changefreq>')
        lines.append(f'<priority>{priority}</priority>')
        lines.append('</url>')
    lines.append('</urlset>')
    return '\n'.join(lines)


@require_GET
def sitemap_xml(request):
    cache_key = f'{SITEMAP_CACHE_KEY}:{request.get_host()}'
    body = cache.get(cache_key)
    if not body:
        body = _render_sitemap(request)
        cache.set(cache_key, body, SITEMAP_CACHE_TTL)
    return HttpResponse(body, content_type='application/xml; charset=utf-8')


@require_GET
def robots_txt(request):
    sitemap_url = _abs(request, reverse('sitemap'))
    body = '\n'.join([
        'User-agent: *',
        'Allow: /',
        'Disallow: /admin',
        'Disallow: /admin/',
        'Disallow: /django-admin/',
        'Disallow: /con-dashboard',
        'Disallow: /internal/',
        'Disallow: /api/',
        'Disallow: /v1/',
        f'Sitemap: {sitemap_url}',
        '',
    ])
    return HttpResponse(body, content_type='text/plain; charset=utf-8')
