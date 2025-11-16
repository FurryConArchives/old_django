"""Versioned JSON API endpoints for Android and other mobile clients."""

import json
import random
import re
import uuid

import requests

from django.conf import settings
from django.db.models import Count, F, Q, Value
from django.db.models.functions import Concat, MD5
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_GET

from archive.models import AppKey, Category, OurFriend, PDFDocument, Schedule, SiteBanner, Tag
from archive.models_physical import PhysicalInventoryItem
from archive.utils import get_pdf_view_stats, get_total_unique_visitors, sort_documents_by_pdf_views

from .auth import api_error, api_success, enforce_api_access
from .serializers import (
    paginate_queryset,
    serialize_category_summary,
    serialize_consurf_payload,
    serialize_consurf_stats,
    serialize_document_detail,
    serialize_document_summary,
    serialize_friend,
    serialize_schedule_summary,
    serialize_site_banner,
    serialize_tag_detail,
    serialize_vault_item,
    summarize_schedule_events,
)
from archive.site_pages import (
    published_contact_payloads,
    published_faq_payloads,
    published_page_section_payloads,
)
from archive.hosts import api_url, frontend_url
from .site_content import (
    FOOTER_LINKS,
    NAV_LINKS,
    PAGE_CATALOG,
    STAFF_MEMBERS,
    page_catalog_entry,
    serialize_staff_member,
)


API_VERSION = '1.3.0'


def _rate_limit(request, scope=None):
    return enforce_api_access(request, scope=scope, namespace='api_v1')


def _active_banner():
    try:
        return SiteBanner.objects.filter(is_enabled=True).exclude(text='').order_by('-updated_at').first()
    except Exception:
        return None


def _absolute_paths(request, items, path_key='path', api_key='api_path'):
    out = []
    for item in items:
        row = dict(item)
        if row.get(path_key):
            row['url'] = frontend_url(request, row[path_key])
        if row.get(api_key):
            row['api_url'] = api_url(request, row[api_key])
        out.append(row)
    return out


def _public_stats():
    total_documents = _published_documents().count()
    total_conventions = (
        _published_documents()
        .exclude(convention_name='')
        .values('convention_name')
        .distinct()
        .count()
    )
    total_categories = Category.objects.annotate(
        document_count=Count('documents', filter=Q(documents__is_published=True)),
    ).filter(document_count__gt=0).count()
    total_schedules = Schedule.objects.filter(deleted=False).count()
    total_vault_items = PhysicalInventoryItem.objects.count()
    total_tags = Tag.objects.annotate(
        document_count=Count(
            'documents',
            filter=Q(documents__is_published=True, documents__takedown_by_request=False),
        ),
    ).filter(document_count__gt=0).count()
    view_stats = get_pdf_view_stats() or {'total': 0}
    years = list(
        _published_documents()
        .filter(year__isnull=False)
        .values_list('year', flat=True)
        .distinct()
        .order_by('year')
    )
    return {
        'documents': total_documents,
        'conventions': total_conventions,
        'categories': total_categories,
        'schedules': total_schedules,
        'vault_items': total_vault_items,
        'tags': total_tags,
        'document_views': view_stats.get('total', 0),
        'unique_visitors': _safe_unique_visitors(),
        'year_min': years[0] if years else None,
        'year_max': years[-1] if years else None,
        'total_documents': total_documents,
        'total_conventions': total_conventions,
        'total_pdf_views': view_stats.get('total', 0),
    }


def _safe_unique_visitors():
    try:
        return get_total_unique_visitors()
    except Exception:
        return 0


def _published_documents():
    return PDFDocument.objects.filter(is_published=True, takedown_by_request=False).select_related(
        'category', 'tags'
    )


def _build_document_queryset(request):
    documents = _published_documents()
    q = (request.GET.get('q') or request.GET.get('search') or '').strip()
    if q:
        documents = documents.filter(
            Q(title__icontains=q)
            | Q(description__icontains=q)
            | Q(author__icontains=q)
            | Q(convention_name__icontains=q)
            | Q(ocr_text__icontains=q)
        )

    category_slug = (request.GET.get('category') or '').strip()
    if category_slug:
        documents = documents.filter(category__slug=category_slug)

    year = (request.GET.get('year') or '').strip()
    if year:
        documents = documents.filter(year=year)

    convention = (request.GET.get('convention') or '').strip()
    if convention:
        documents = documents.filter(convention_name__icontains=convention)

    tag_slug = (request.GET.get('tag') or '').strip()
    if tag_slug:
        documents = documents.filter(tags__slug=tag_slug)

    shuffle = (request.GET.get('shuffle') or request.GET.get('featured') or '').strip().lower()
    sort = (request.GET.get('sort') or '').strip()
    if shuffle in {'1', 'true', 'yes'} and not sort:
        sort = 'random'
    if not sort:
        sort = '-uploaded_at'

    seed = (request.GET.get('seed') or '').strip()
    if not seed and sort == 'random':
        seed = request.session.get('document_order_seed')
        if not seed:
            seed = uuid.uuid4().hex
            request.session['document_order_seed'] = seed
            request.session.modified = True

    valid_sorts = {
        'random': 'random',
        'title': ('title', 'pk'),
        '-title': ('-title', 'pk'),
        'year': ('year', 'pk'),
        '-year': ('-year', 'pk'),
        'uploaded_at': ('uploaded_at', 'pk'),
        '-uploaded_at': ('-uploaded_at', 'pk'),
        'views': 'views',
        '-views': '-views',
    }

    if sort == 'random':
        documents = documents.annotate(
            _shuffle=MD5(Concat(F('slug'), Value(str(seed))))
        ).order_by('_shuffle', 'pk')
    elif sort in {'views', '-views'}:
        documents = documents.order_by('pk')
    elif sort in valid_sorts and sort not in {'random', 'views', '-views'}:
        documents = documents.order_by(*valid_sorts[sort])
    else:
        documents = documents.order_by('-uploaded_at', 'pk')

    if sort in {'views', '-views'}:
        documents = sort_documents_by_pdf_views(documents, descending=(sort == '-views'))

    if shuffle in {'1', 'true', 'yes'}:
        doc_list = list(documents)
        random.shuffle(doc_list)
        return doc_list, {
            'q': q,
            'category': category_slug,
            'year': year,
            'convention': convention,
            'tag': tag_slug,
            'sort': sort or 'random',
            'seed': seed,
        }

    return documents, {
        'q': q,
        'category': category_slug,
        'year': year,
        'convention': convention,
        'tag': tag_slug,
        'sort': sort,
        'seed': seed,
    }


def _year_event_description(event):
    if not isinstance(event, dict):
        return ''
    return (event.get('event_description') or '').strip()


def _document_consurf_fields(document):
    """Year-specific Consurf payload for a document detail response."""
    empty = {
        'consurf_event': None,
        'event_description': '',
        'has_year_event_description': False,
        'display_location': None,
    }
    try:
        from archive.document_descriptions import slug_hint_for_document
        from archive.views import (
            _enrich_consurf_event_for_year,
            _get_consurf_event,
            _location_from_event_payload,
        )
    except Exception:
        return empty

    conv_name = (document.convention_name or '').strip()
    if not conv_name and document.category:
        try:
            conv_name = document.category.get_primary_convention_name() or document.category.name
        except Exception:
            conv_name = document.category.name
    if not conv_name:
        return empty

    slug_hint = None
    try:
        slug_hint = slug_hint_for_document(document)
    except Exception:
        slug_hint = document.category.slug if document.category else None

    try:
        event = _get_consurf_event(conv_name, document.year, slug_hint=slug_hint, allow_network=True)
    except Exception:
        event = None

    if document.category_id and document.year:
        try:
            event = _enrich_consurf_event_for_year(document.category, document.year, event)
        except Exception:
            pass

    description = _year_event_description(event)
    location = None
    try:
        location = _location_from_event_payload(event)
    except Exception:
        location = None

    return {
        'consurf_event': serialize_consurf_payload(event),
        'event_description': description,
        'has_year_event_description': bool(description),
        'display_location': location,
    }


def _attach_view_counts(request, documents):
    slugs = [doc.slug for doc in documents]
    stats = get_pdf_view_stats(slugs) or {'counts': {}}
    counts = stats.get('counts', {}) or {}
    return [serialize_document_summary(request, doc, view_count=counts.get(doc.slug, 0)) for doc in documents]


def _find_schedule(slug, year=None):
    qs = Schedule.objects.filter(deleted=False)
    exact = qs.filter(slug__iexact=slug)
    if year:
        try:
            exact = exact.filter(year=int(year))
        except (TypeError, ValueError):
            pass
    sched = exact.order_by('-year').first()
    if sched:
        return sched

    base_slug = slug
    slug_year = year
    match = re.match(r'(?P<base>.+)-(?P<y>\d{4})$', slug)
    if match and not year:
        base_slug = match.group('base')
        slug_year = match.group('y')

    cand = qs.filter(slug__icontains=base_slug)
    if slug_year:
        try:
            cand = cand.filter(year=int(slug_year))
        except (TypeError, ValueError):
            pass
    sched = cand.order_by('-year').first()
    if sched:
        return sched

    name_like = base_slug.replace('-', ' ')
    cand = qs.filter(convention_name__icontains=name_like)
    if slug_year:
        try:
            cand = cand.filter(year=int(slug_year))
        except (TypeError, ValueError):
            pass
    return cand.order_by('-year').first()


@require_GET
def api_v1_root(request):
    limited = _rate_limit(request)
    if limited:
        return limited
    return api_success({
        'name': 'Furry Con Archives API',
        'version': API_VERSION,
        'documentation': api_url(request, '/v1/'),
        'endpoints': {
            'site': api_url(request, '/v1/site/'),
            'home': api_url(request, '/v1/home/'),
            'pages': api_url(request, '/v1/pages/'),
            'page_detail': api_url(request, '/v1/pages/{slug}/'),
            'documents': api_url(request, '/v1/documents/'),
            'documents_meta': api_url(request, '/v1/documents/meta/'),
            'document_detail': api_url(request, '/v1/documents/{slug}/'),
            'document_mirrors': api_url(request, '/v1/documents/{slug}/mirrors/'),
            'categories': api_url(request, '/v1/categories/'),
            'category_detail': api_url(request, '/v1/categories/{slug}/'),
            'schedules': api_url(request, '/v1/schedules/'),
            'schedule_detail': api_url(request, '/v1/schedules/{slug}/'),
            'schedule_events': api_url(request, '/v1/schedules/{slug}/events/'),
            'schedule_years': api_url(request, '/v1/schedules/conventions/{category_slug}/'),
            'search': api_url(request, '/v1/search/'),
            'vault': api_url(request, '/v1/vault/'),
            'vault_meta': api_url(request, '/v1/vault/meta/'),
            'vault_detail': api_url(request, '/v1/vault/{id}/'),
            'tags': api_url(request, '/v1/tags/'),
            'tag_detail': api_url(request, '/v1/tags/{slug}/'),
            'stats': api_url(request, '/v1/stats/'),
            'telegram': api_url(request, '/v1/telegram/'),
        },
        'authentication': {
            'optional': True,
            'headers': ['X-App-Key', 'X-API-Key'],
            'query_params': ['app_key', 'api_key'],
            'scopes': [choice[0] for choice in AppKey.SCOPE_CHOICES if choice[0] != AppKey.SCOPE_CON_DASHBOARD],
            'note': (
                'Without a key, requests are limited to 1 per minute per IP. '
                'A key must include the route scope (or api for all routes). '
                'Each key has its own requests-per-minute limit. '
                'The con-dashboard scope is not rate limited and is not used here.'
            ),
        },
        'rate_limit': {
            'anonymous': getattr(settings, 'PUBLIC_API_RATE_PER_MIN', 1),
            'authenticated': 'per_key',
            'unit': 'requests_per_minute',
        },
    })


@require_GET
def api_v1_documents(request):
    limited = _rate_limit(request)
    if limited:
        return limited

    documents, filters = _build_document_queryset(request)
    page = request.GET.get('page', 1)
    page_size = request.GET.get('page_size', 20)
    items, pagination = paginate_queryset(documents, page, page_size)

    start_index = ((pagination['page'] - 1) * pagination['page_size'] + 1) if pagination['total'] else 0
    end_index = min(pagination['page'] * pagination['page_size'], pagination['total'])

    return api_success({
        'results': _attach_view_counts(request, items),
        'pagination': pagination,
        'start_index': start_index,
        'end_index': end_index,
        'filters': filters,
    })


@require_GET
def api_v1_document_detail(request, slug):
    limited = _rate_limit(request)
    if limited:
        return limited

    document = get_object_or_404(PDFDocument, slug=slug)
    if document.takedown_by_request:
        return api_success({
            'document': serialize_document_detail(request, document),
        })
    if not document.is_published and not (request.user.is_authenticated and request.user.is_staff):
        return api_error('not_found', 'Document not found.', 404)

    stats = get_pdf_view_stats([slug]) or {'counts': {}}
    view_count = stats.get('counts', {}).get(slug, 0)

    related = []
    if document.category_id:
        related_qs = _published_documents().filter(category=document.category).exclude(pk=document.pk)[:5]
        related = _attach_view_counts(request, related_qs)

    payload = serialize_document_detail(
        request,
        document,
        view_count=view_count,
        related=related,
    )
    payload.update(_document_consurf_fields(document))

    return api_success({
        'document': payload,
    })


@require_GET
def api_v1_categories(request):
    limited = _rate_limit(request)
    if limited:
        return limited

    q = (request.GET.get('q') or '').strip()
    categories = Category.objects.annotate(
        document_count=Count('documents', filter=Q(documents__is_published=True, documents__takedown_by_request=False)),
        schedule_count=Count('schedules', filter=Q(schedules__deleted=False)),
    ).order_by('order', 'name')

    if q:
        categories = categories.filter(
            Q(name__icontains=q) | Q(location__icontains=q) | Q(description__icontains=q)
        )

    with_docs = request.GET.get('has_documents', '').strip().lower()
    if with_docs in {'1', 'true', 'yes'}:
        categories = categories.filter(document_count__gt=0)

    with_schedules = request.GET.get('has_schedules', '').strip().lower()
    if with_schedules in {'1', 'true', 'yes'}:
        categories = categories.filter(schedule_count__gt=0)

    page = request.GET.get('page', 1)
    page_size = request.GET.get('page_size', 50)
    items, pagination = paginate_queryset(categories, page, page_size, max_page_size=200)

    start_index = ((pagination['page'] - 1) * pagination['page_size'] + 1) if pagination['total'] else 0
    end_index = min(pagination['page'] * pagination['page_size'], pagination['total'])

    return api_success({
        'results': [
            serialize_category_summary(
                request,
                cat,
                doc_count=cat.document_count,
                schedule_count=cat.schedule_count,
            )
            for cat in items
        ],
        'pagination': pagination,
        'start_index': start_index,
        'end_index': end_index,
    })


@require_GET
def api_v1_category_detail(request, slug):
    limited = _rate_limit(request)
    if limited:
        return limited

    category = get_object_or_404(Category, slug=slug)
    doc_count = _published_documents().filter(category=category).count()
    schedule_count = Schedule.objects.filter(category=category, deleted=False).count()

    year_param = (request.GET.get('year') or '').strip()
    year = int(year_param) if year_param.isdigit() else None

    from archive.views import _build_convention_event_context

    event_ctx = _build_convention_event_context(request, category, year=year, tab='overview')
    schedules = Schedule.objects.filter(category=category, deleted=False).order_by('-year')

    conbook_data = None
    if event_ctx['conbook']:
        conbook_data = serialize_document_detail(request, event_ctx['conbook'])
        conbook_data['view_count'] = event_ctx['conbook_views']

    conbooks = _attach_view_counts(request, event_ctx.get('conbooks') or [])
    other_documents = _attach_view_counts(request, event_ctx['other_documents'])
    year_documents = conbooks + other_documents

    return api_success({
        'category': serialize_category_summary(
            request,
            category,
            doc_count=doc_count,
            schedule_count=schedule_count,
        ),
        'years': event_ctx['years'],
        'selected_year': event_ctx['selected_year'],
        'consurf_event': serialize_consurf_payload(event_ctx['consurf_event']),
        'event_description': event_ctx['event_description'] if event_ctx.get('has_year_event_description') else '',
        'has_year_event_description': event_ctx.get('has_year_event_description', False),
        'display_location': event_ctx.get('display_location'),
        'event_display_title': event_ctx.get('event_display_title') or category.name,
        'event_date_range': event_ctx.get('event_date_range'),
        'event_duration_days': event_ctx.get('event_duration_days'),
        'conbook': conbook_data,
        'conbooks': conbooks,
        'other_documents': other_documents,
        'documents': year_documents,
        'recent_documents': year_documents,
        'schedule': serialize_schedule_summary(event_ctx['schedule'], request) if event_ctx['schedule'] else None,
        'schedules': [serialize_schedule_summary(s, request) for s in schedules],
        'children': [
            serialize_category_summary(request, child)
            for child in category.subcategories.all().order_by('order', 'name')
        ],
        'primary_convention_name': category.get_primary_convention_name(),
        'vault_items': [serialize_vault_item(item, request) for item in event_ctx['vault_items']],
        'archive_stats': event_ctx.get('archive_stats') or {},
        'consurf_event_stats': serialize_consurf_stats(event_ctx.get('consurf_event_stats')),
        'consurf_stats_supported': bool(event_ctx.get('consurf_stats_supported')),
    })


@require_GET
def api_v1_schedules(request):
    limited = _rate_limit(request)
    if limited:
        return limited

    q = (request.GET.get('q') or '').strip()
    categories = Category.objects.annotate(
        schedule_count=Count('schedules', filter=Q(schedules__deleted=False)),
    ).filter(schedule_count__gt=0).prefetch_related('schedules').order_by('name')

    if q:
        categories = categories.filter(
            Q(name__icontains=q) | Q(location__icontains=q) | Q(description__icontains=q)
        )

    results = []
    for category in categories:
        schedules = [
            serialize_schedule_summary(s, request)
            for s in category.schedules.filter(deleted=False).order_by('-year')
        ]
        item = serialize_category_summary(request, category, schedule_count=category.schedule_count)
        item['schedules'] = schedules
        results.append(item)

    return api_success({
        'results': results,
        'count': len(results),
    })


@require_GET
def api_v1_schedule_years(request, category_slug):
    limited = _rate_limit(request)
    if limited:
        return limited

    category = get_object_or_404(Category, slug=category_slug)
    schedules = Schedule.objects.filter(category=category, deleted=False).order_by('-year')
    if not schedules.exists():
        return api_error('not_found', 'No schedules found for this convention.', 404)

    return api_success({
        'category': serialize_category_summary(request, category),
        'schedules': [serialize_schedule_summary(s, request) for s in schedules],
    })


@require_GET
def api_v1_schedule_events(request, slug):
    limited = _rate_limit(request, 'schedules')
    if limited:
        return limited

    year = request.GET.get('year')
    sched = _find_schedule(slug, year=year)
    if not sched or not sched.events_json:
        return api_error('not_found', 'Schedule not found or has no events.', 404)

    try:
        events = json.loads(sched.events_json)
    except Exception:
        events = []

    return api_success({
        'schedule': serialize_schedule_summary(sched, request),
        'events': events,
        'events_url': api_url(request, f'/v1/schedules/{slug}/events/'),
    })


@require_GET
def api_v1_search(request):
    limited = _rate_limit(request)
    if limited:
        return limited

    q = (request.GET.get('q') or request.GET.get('query') or '').strip()
    if not q:
        return api_error('missing_query', 'Provide a q or query parameter.', 400)

    documents = list(
        _published_documents()
        .filter(
            Q(title__icontains=q)
            | Q(convention_name__icontains=q)
            | Q(description__icontains=q)
            | Q(author__icontains=q)
            | Q(ocr_text__icontains=q)
        )
        .order_by('-uploaded_at')[:50]
    )
    document_results = _attach_view_counts(request, documents)

    conventions = list(
        Category.objects.annotate(
            document_count=Count(
                'documents',
                filter=Q(documents__is_published=True, documents__takedown_by_request=False),
            ),
        )
        .filter(
            Q(name__icontains=q) | Q(location__icontains=q) | Q(description__icontains=q),
            document_count__gt=0,
        )
        .order_by('name')[:8]
    )
    convention_results = [
        serialize_category_summary(request, cat, doc_count=cat.document_count)
        for cat in conventions
    ]

    schedules = list(
        Schedule.objects.filter(deleted=False)
        .filter(
            Q(convention_name__icontains=q)
            | Q(slug__icontains=q)
            | Q(category__name__icontains=q)
        )
        .select_related('category')
        .order_by('-year')[:8]
    )
    schedule_results = [serialize_schedule_summary(sched, request) for sched in schedules]

    vault_items = list(
        PhysicalInventoryItem.objects.filter(
            Q(con__icontains=q)
            | Q(item_type__icontains=q)
            | Q(donators__icontains=q)
            | Q(notes__icontains=q)
        )
        .order_by('con', 'year')[:8]
    )
    vault_results = [serialize_vault_item(item, request) for item in vault_items]

    sections = []
    if document_results:
        sections.append({
            'type': 'documents',
            'label': 'Documents',
            'count': len(document_results),
            'results': document_results,
        })
    if convention_results:
        sections.append({
            'type': 'conventions',
            'label': 'Conventions',
            'count': len(convention_results),
            'results': convention_results,
        })
    if schedule_results:
        sections.append({
            'type': 'schedules',
            'label': 'Schedules',
            'count': len(schedule_results),
            'results': schedule_results,
        })
    if vault_results:
        sections.append({
            'type': 'vault',
            'label': 'The Vault',
            'count': len(vault_results),
            'results': vault_results,
        })

    return api_success({
        'query': q,
        'count': len(document_results),
        'total': sum(section['count'] for section in sections),
        'results': document_results,
        'sections': sections,
    })


@require_GET
def api_v1_vault(request):
    limited = _rate_limit(request)
    if limited:
        return limited

    q = (request.GET.get('q') or '').strip()
    items = PhysicalInventoryItem.objects.all().order_by('-added_at', 'con')

    if q:
        items = items.filter(
            Q(con__icontains=q)
            | Q(item_type__icontains=q)
            | Q(donators__icontains=q)
            | Q(notes__icontains=q)
        )

    con = (request.GET.get('con') or '').strip()
    if con:
        items = items.filter(con__icontains=con)

    year = (request.GET.get('year') or '').strip()
    if year:
        try:
            items = items.filter(year=int(year))
        except (TypeError, ValueError):
            pass

    item_type = (request.GET.get('item_type') or '').strip()
    if item_type:
        items = items.filter(item_type__icontains=item_type)

    page = request.GET.get('page', 1)
    page_size = request.GET.get('page_size', 50)
    page_items, pagination = paginate_queryset(items, page, page_size, max_page_size=200)

    from archive.utils import (
        get_vault_contributor_avatar_overrides,
        parse_donor_entry_value,
        prefetch_discord_user_data,
    )

    avatar_overrides = get_vault_contributor_avatar_overrides()
    discord_ids = set()
    for item in page_items:
        for entry in (item.donators or '').split(','):
            parsed = parse_donor_entry_value(entry.strip())
            if parsed.get('type') == 'discord' and parsed.get('username'):
                discord_ids.add(parsed['username'])
    discord_users = prefetch_discord_user_data(discord_ids)

    return api_success({
        'results': [
            serialize_vault_item(
                item,
                request,
                avatar_overrides=avatar_overrides,
                discord_users=discord_users,
            )
            for item in page_items
        ],
        'pagination': pagination,
    })


@require_GET
def api_v1_tags(request):
    limited = _rate_limit(request)
    if limited:
        return limited

    tags = Tag.objects.annotate(
        document_count=Count(
            'documents',
            filter=Q(documents__is_published=True, documents__takedown_by_request=False),
        ),
    ).filter(document_count__gt=0).order_by('name')

    return api_success({
        'results': [serialize_tag_detail(request, tag, document_count=tag.document_count) for tag in tags],
        'count': tags.count(),
    })


@require_GET
def api_v1_documents_meta(request):
    limited = _rate_limit(request)
    if limited:
        return limited

    categories = Category.objects.annotate(
        document_count=Count(
            'documents',
            filter=Q(documents__is_published=True, documents__takedown_by_request=False),
        ),
    ).filter(document_count__gt=0).order_by('name')

    years = list(
        _published_documents()
        .filter(year__isnull=False)
        .values_list('year', flat=True)
        .distinct()
        .order_by('-year')
    )
    conventions = list(
        _published_documents()
        .exclude(convention_name='')
        .values_list('convention_name', flat=True)
        .distinct()
        .order_by('convention_name')
    )

    tags = Tag.objects.annotate(
        document_count=Count(
            'documents',
            filter=Q(documents__is_published=True, documents__takedown_by_request=False),
        ),
    ).filter(document_count__gt=0).order_by('name')

    return api_success({
        'total_documents': _published_documents().count(),
        'categories': [
            serialize_category_summary(request, cat, doc_count=cat.document_count)
            for cat in categories
        ],
        'years': years,
        'conventions': conventions,
        'tags': [serialize_tag_detail(request, tag, document_count=tag.document_count) for tag in tags],
        'sorts': [
            'random', 'title', '-title', 'year', '-year',
            'uploaded_at', '-uploaded_at', 'views', '-views',
        ],
    })


@require_GET
def api_v1_stats(request):
    limited = _rate_limit(request)
    if limited:
        return limited
    return api_success(_public_stats())


@require_GET
def api_v1_site(request):
    limited = _rate_limit(request)
    if limited:
        return limited
    friends = OurFriend.objects.filter(is_enabled=True).order_by('order', 'name')
    return api_success({
        'name': 'Furry Con Archives',
        'version': API_VERSION,
        'banner': serialize_site_banner(_active_banner(), request),
        'navigation': _absolute_paths(request, NAV_LINKS),
        'footer_links': _absolute_paths(request, FOOTER_LINKS),
        'pages': _absolute_paths(request, PAGE_CATALOG),
        'friends': [serialize_friend(friend, request) for friend in friends],
        'contact': published_contact_payloads(),
        'stats': _public_stats(),
    })


@require_GET
def api_v1_home(request):
    limited = _rate_limit(request)
    if limited:
        return limited
    featured = list(_published_documents().order_by('-uploaded_at')[:12])
    recent = list(_published_documents().order_by('-uploaded_at')[:8])
    return api_success({
        'banner': serialize_site_banner(_active_banner(), request),
        'stats': _public_stats(),
        'featured_documents': _attach_view_counts(request, featured),
        'recent_documents': _attach_view_counts(request, recent),
        'pages': _absolute_paths(request, PAGE_CATALOG),
    })


@require_GET
def api_v1_pages(request):
    limited = _rate_limit(request)
    if limited:
        return limited
    return api_success({
        'results': _absolute_paths(request, PAGE_CATALOG),
        'count': len(PAGE_CATALOG),
    })


def _page_payload(request, slug):
    meta = page_catalog_entry(slug)
    if not meta:
        return None
    payload = _absolute_paths(request, [meta])[0]
    if slug == 'faq':
        payload['items'] = published_faq_payloads()
    elif slug == 'contact':
        payload['channels'] = published_contact_payloads()
    elif slug == 'staff':
        payload['staff'] = [serialize_staff_member(member) for member in STAFF_MEMBERS]
        payload['friends'] = [
            serialize_friend(friend, request)
            for friend in OurFriend.objects.filter(is_enabled=True).order_by('order', 'name')
        ]
    elif slug == 'rights':
        payload['sections'] = published_page_section_payloads('rights')
    elif slug == 'preservation-policy':
        payload['sections'] = published_page_section_payloads('preservation-policy')
        payload['takedown_email'] = 'dmca@furryconarchives.org'
    elif slug == 'preservation-tips':
        payload['sections'] = published_page_section_payloads('preservation-tips')
    elif slug == 'home':
        payload['stats'] = _public_stats()
        payload['banner'] = serialize_site_banner(_active_banner(), request)
    return payload


@require_GET
def api_v1_page_detail(request, slug):
    limited = _rate_limit(request)
    if limited:
        return limited
    payload = _page_payload(request, slug)
    if not payload:
        return api_error('not_found', 'Page not found.', 404)
    return api_success(payload)


@require_GET
def api_v1_document_mirrors(request, slug):
    limited = _rate_limit(request)
    if limited:
        return limited
    document = get_object_or_404(PDFDocument, slug=slug)
    if document.takedown_by_request:
        return api_success({'available': False, 'identifier': None, 'options': []})
    if not document.is_published and not (request.user.is_authenticated and request.user.is_staff):
        return api_error('not_found', 'Document not found.', 404)
    from archive.ia_mirrors import get_document_ia_mirrors
    return api_success(get_document_ia_mirrors(document))


@require_GET
def api_v1_tag_detail(request, slug):
    limited = _rate_limit(request)
    if limited:
        return limited
    tag = get_object_or_404(Tag, slug=slug)
    documents = _published_documents().filter(tags=tag).order_by('-uploaded_at')
    page = request.GET.get('page', 1)
    page_size = request.GET.get('page_size', 20)
    items, pagination = paginate_queryset(documents, page, page_size)
    return api_success({
        'tag': serialize_tag_detail(request, tag, document_count=documents.count()),
        'results': _attach_view_counts(request, items),
        'pagination': pagination,
    })


@require_GET
def api_v1_vault_meta(request):
    limited = _rate_limit(request)
    if limited:
        return limited
    cons = list(
        PhysicalInventoryItem.objects.exclude(con='')
        .values_list('con', flat=True)
        .distinct()
        .order_by('con')
    )
    years = list(
        PhysicalInventoryItem.objects.filter(year__isnull=False)
        .values_list('year', flat=True)
        .distinct()
        .order_by('-year')
    )
    item_types = list(
        PhysicalInventoryItem.objects.exclude(item_type='')
        .values_list('item_type', flat=True)
        .distinct()
        .order_by('item_type')
    )
    return api_success({
        'total': PhysicalInventoryItem.objects.count(),
        'cons': cons,
        'years': years,
        'item_types': item_types,
    })


@require_GET
def api_v1_vault_detail(request, item_id):
    limited = _rate_limit(request)
    if limited:
        return limited
    item = get_object_or_404(PhysicalInventoryItem, pk=item_id)
    from archive.utils import (
        get_vault_contributor_avatar_overrides,
        parse_donor_entry_value,
        prefetch_discord_user_data,
    )
    avatar_overrides = get_vault_contributor_avatar_overrides()
    discord_ids = set()
    for entry in (item.donators or '').split(','):
        parsed = parse_donor_entry_value(entry.strip())
        if parsed.get('type') == 'discord' and parsed.get('username'):
            discord_ids.add(parsed['username'])
    return api_success({
        'item': serialize_vault_item(
            item,
            request,
            avatar_overrides=avatar_overrides,
            discord_users=prefetch_discord_user_data(discord_ids),
        ),
    })


@require_GET
def api_v1_schedule_detail(request, slug):
    limited = _rate_limit(request)
    if limited:
        return limited
    year = request.GET.get('year')
    sched = _find_schedule(slug, year=year)
    if not sched:
        return api_error('not_found', 'Schedule not found.', 404)
    events = []
    if sched.events_json:
        try:
            events = json.loads(sched.events_json)
        except Exception:
            events = []
    summary = summarize_schedule_events(events)
    return api_success({
        'schedule': serialize_schedule_summary(sched, request),
        'days': summary['days'],
        'rooms': summary['rooms'],
        'events_count': summary['events_count'],
        'events': events,
    })


@require_GET
def api_v1_telegram(request):
    from archive.telegram_messages import fetch_telegram_history, serialize_telegram_history

    limited = _rate_limit(request)
    if limited:
        return limited

    try:
        payload, limit, page = fetch_telegram_history(
            limit=request.GET.get('limit', 100),
            page=request.GET.get('page', 1),
            max_id=request.GET.get('max_id'),
        )
    except requests.Timeout:
        return api_error('timeout', 'Telegram request timed out.', 504)
    except requests.RequestException:
        return api_error('upstream_error', 'Failed to load Telegram messages.', 502)
    except Exception:
        return api_error('server_error', 'Failed to load Telegram messages.', 500)

    return api_success(serialize_telegram_history(payload, request, limit=limit, page=page))
