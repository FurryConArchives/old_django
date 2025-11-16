"""Serialize archive models into JSON-friendly dicts for mobile clients."""

from archive.document_descriptions import display_description_for_document
from archive.hosts import api_url, frontend_url


def _serialize_category_parent(category):
    if not getattr(category, 'parent_id', None):
        return None
    try:
        parent = category.parent
    except Exception:
        return {'id': category.parent_id}
    if not parent:
        return {'id': category.parent_id}
    return {
        'id': parent.id,
        'name': parent.name,
        'slug': parent.slug,
    }


def absolute_url(request, path, *, site=None):
    if not path:
        return None
    if path.startswith('http://') or path.startswith('https://'):
        return path
    if site is None:
        site = 'api' if path.startswith('/v1/') or path.startswith('/api/') else 'frontend'
    if site == 'api':
        return api_url(request, path)
    return frontend_url(request, path)


def category_document_year_range(category):
    years = list(
        category.documents.filter(
            is_published=True,
            takedown_by_request=False,
            year__isnull=False,
        ).values_list('year', flat=True).distinct().order_by('year')
    )
    if not years:
        return None
    try:
        years = sorted({int(y) for y in years})
    except (TypeError, ValueError):
        years = sorted(years)
    if len(years) == 1:
        return str(years[0])
    return f'{years[0]}-{years[-1]}'


def serialize_category_summary(request, category, *, doc_count=None, schedule_count=None):
    data = {
        'id': category.id,
        'name': category.name,
        'slug': category.slug,
        'description': category.description or '',
        'location': category.location or '',
        'logo': category.logo or None,
        'parent_id': category.parent_id,
        'parent': _serialize_category_parent(category),
        'order': category.order,
        'detail_url': absolute_url(request, f'/v1/categories/{category.slug}/'),
        'api_url': absolute_url(request, f'/v1/categories/{category.slug}/'),
    }
    if doc_count is not None:
        data['document_count'] = doc_count
    if schedule_count is not None:
        data['schedule_count'] = schedule_count
    year_range = category_document_year_range(category)
    if year_range:
        data['year_range'] = year_range
    else:
        try:
            cached_range = category.get_year_range()
            if cached_range:
                data['year_range'] = cached_range
        except Exception:
            pass
    data['documents_url'] = absolute_url(request, f'/documents?category={category.slug}')
    data['schedules_url'] = absolute_url(request, f'/schedules/list?category={category.slug}')
    data['url'] = absolute_url(request, f'/conventions/{category.slug}')
    return data


def serialize_tag(tag, *, document_count=None):
    data = {
        'id': tag.id,
        'name': tag.name,
        'slug': tag.slug,
        'description': tag.description or '',
    }
    if document_count is not None:
        data['document_count'] = document_count
    return data


def serialize_document_summary(request, document, *, view_count=None):
    if document.takedown_by_request:
        return {
            'slug': document.slug,
            'title': document.title,
            'takedown': True,
            'takedown_explanation': document.takedown_explanation or '',
        }

    # Build from a real page URL so Django does not percent-encode `{page}` in the template.
    cover_url = absolute_url(request, f'/documents/{document.slug}/page/1.jpg')
    file_size_mb = None
    if document.file_size:
        file_size_mb = round(document.file_size / (1024 * 1024), 1)
    data = {
        'id': document.id,
        'slug': document.slug,
        'title': document.title,
        'description': display_description_for_document(document, allow_network=False) or '',
        'year': document.year,
        'convention_name': document.convention_name or '',
        'author': document.author or '',
        'page_count': document.page_count,
        'file_size': document.file_size,
        'file_size_mb': file_size_mb,
        'is_scanned': document.is_scanned,
        'text_percentage': document.text_percentage,
        'creative_commons_license': document.creative_commons_license or '',
        'copyright_holder': document.copyright_holder or '',
        'copyright_year': document.copyright_year,
        'uploaded_at': document.uploaded_at.isoformat() if document.uploaded_at else None,
        'updated_at': document.updated_at.isoformat() if getattr(document, 'updated_at', None) else None,
        'download_count': document.download_count,
        'cover_url': cover_url,
        'view_url': absolute_url(request, f'/documents/{document.slug}/view'),
        'download_url': absolute_url(request, f'/documents/{document.slug}/download'),
        'detail_url': absolute_url(request, f'/v1/documents/{document.slug}/'),
        'api_url': absolute_url(request, f'/v1/documents/{document.slug}/'),
        'web_url': absolute_url(request, f'/documents/{document.slug}'),
        'mirrors_url': absolute_url(request, f'/v1/documents/{document.slug}/mirrors/'),
        'search_url': absolute_url(request, f'/documents/{document.slug}/search'),
        'takedown': False,
    }
    if document.category_id:
        data['category'] = {
            'id': document.category_id,
            'name': document.category.name,
            'slug': document.category.slug,
        }
    else:
        data['category'] = None
    if document.tags_id:
        data['tag'] = {
            'id': document.tags_id,
            'name': document.tags.name,
            'slug': document.tags.slug,
        }
    else:
        data['tag'] = None
    if view_count is not None:
        data['view_count'] = view_count
    return data


def serialize_document_detail(request, document, *, view_count=None, related=None):
    summary = serialize_document_summary(request, document, view_count=view_count)
    if summary.get('takedown'):
        return summary
    summary.update({
        'contributor_notes': document.contributor_notes or '',
        'donated_by': document.donated_by or '',
        'donated_by_telegram': document.donated_by_telegram or '',
        'ocr_available': bool(document.ocr_processed and document.ocr_text),
        'related_documents': related or [],
        'resources': {
            'page_image_template': None,
            'page_text_template': None,
            'search_url': absolute_url(request, f'/documents/{document.slug}/search'),
            'mirrors_url': absolute_url(request, f'/v1/documents/{document.slug}/mirrors/'),
            'download_url': absolute_url(request, f'/documents/{document.slug}/download'),
            'view_url': absolute_url(request, f'/documents/{document.slug}/view'),
        },
    })
    page_image = absolute_url(request, f'/documents/{document.slug}/page/1.jpg')
    if page_image:
        template = page_image.replace('/1.jpg', '/{page}.jpg')
        summary['page_image_template'] = template
        summary['resources']['page_image_template'] = template
    page_text = absolute_url(request, f'/documents/{document.slug}/page/1/text')
    if page_text:
        template = page_text.replace('/1/text', '/{page}/text')
        summary['page_text_template'] = template
        summary['resources']['page_text_template'] = template
    return summary


def _consurf_location_text(value):
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, dict):
        for key in ('formatted', 'address', 'latestAddress', 'city'):
            text = _consurf_location_text(value.get(key))
            if text:
                return text
    return None


def serialize_consurf_payload(event):
    if not event:
        return None
    from archive.site_cache import serialize_consurf_event
    from archive.views import (
        _event_duration_days,
        _format_event_date_range,
        _location_from_event_payload,
    )

    data = serialize_consurf_event(event)
    location = _location_from_event_payload(event) or _consurf_location_text(data.get('location'))
    if location:
        data['location'] = location
    elif 'location' in data and not isinstance(data.get('location'), str):
        data.pop('location', None)
    data['formatted_date_range'] = _format_event_date_range(
        event.get('start_date'),
        event.get('end_date'),
    )
    data['duration_days'] = _event_duration_days(
        event.get('start_date'),
        event.get('end_date'),
    )
    return data


def serialize_consurf_stats(stats):
    if not stats:
        return None
    boxes = []
    for box in stats.get('boxes') or []:
        if not isinstance(box, dict):
            continue
        boxes.append({
            'label': box.get('label') or '',
            'value': box.get('value'),
            'format': box.get('format') or 'number',
            'currency_symbol': box.get('currency_symbol'),
            'currency_code': box.get('currency_code'),
        })
    return {
        'source': stats.get('source') or 'consurf',
        'is_cancelled': bool(stats.get('is_cancelled')),
        'has_stats': bool(stats.get('has_stats') or boxes),
        'charity_partner': stats.get('charity_partner') or '',
        'boxes': boxes,
    }


def serialize_schedule_summary(schedule, request=None):
    events_count = 0
    if schedule.events_json:
        try:
            import json
            events_count = len(json.loads(schedule.events_json))
        except Exception:
            events_count = 0
    slug = schedule.route_slug
    data = {
        'id': schedule.id,
        'type': schedule.type,
        'slug': slug,
        'source_slug': schedule.slug,
        'year': schedule.year,
        'convention_name': schedule.convention_name or '',
        'events_count': events_count,
        'last_updated': schedule.last_updated.isoformat() if schedule.last_updated else None,
        'has_error': bool(schedule.error),
        'error': schedule.error or None,
    }
    if schedule.category_id and getattr(schedule, 'category', None):
        data['category'] = {
            'id': schedule.category_id,
            'name': schedule.category.name,
            'slug': schedule.category.slug,
        }
    else:
        data['category'] = None
    if request is not None:
        data['detail_url'] = absolute_url(request, f'/v1/schedules/{slug}/')
        data['events_url'] = absolute_url(request, f'/v1/schedules/{slug}/events/')
        data['web_url'] = absolute_url(request, f'/schedules/{slug}')
        data['csv_url'] = absolute_url(request, f'/schedules/{slug}/csv')
        data['print_url'] = absolute_url(request, f'/schedules/{slug}/print')
    return data


def summarize_schedule_events(events):
    """Pull days/rooms from mixed schedule event payloads without dropping the raw list."""
    if not isinstance(events, list):
        return {'days': [], 'rooms': [], 'events_count': 0}
    days = []
    rooms = []
    seen_days = set()
    seen_rooms = set()
    for event in events:
        if not isinstance(event, dict):
            continue
        day = event.get('day') or event.get('date') or event.get('event_date')
        if day and day not in seen_days:
            seen_days.add(day)
            days.append(day)
        room = event.get('room') or event.get('roomId') or event.get('room_id') or event.get('location')
        if isinstance(room, dict):
            room = room.get('name') or room.get('id')
        if room and room not in seen_rooms:
            seen_rooms.add(room)
            rooms.append(room)
    return {
        'days': days,
        'rooms': rooms,
        'events_count': len(events),
    }


def serialize_vault_donor_entry(parsed, request, *, avatar_overrides=None, discord_users=None):
    from archive.utils import (
        resolve_vault_contributor_avatar_url,
        resolve_vault_contributor_display_name,
    )

    account_type = parsed.get('type') or 'unknown'
    username = (parsed.get('username') or '').strip()
    display_name = resolve_vault_contributor_display_name(
        account_type,
        username,
        fallback=(parsed.get('display_name') or username or '').strip(),
        discord_users=discord_users,
    )
    avatar_path = resolve_vault_contributor_avatar_url(
        account_type,
        username,
        fallback_avatar=parsed.get('avatar'),
        overrides=avatar_overrides,
    )
    avatar_url = absolute_url(request, avatar_path) if avatar_path else None

    profile_url = None
    if account_type == 'telegram' and username:
        profile_url = f'https://t.me/{username}'
    elif account_type == 'discord' and username:
        profile_url = f'https://discord.com/users/{username}'

    return {
        'type': account_type,
        'username': username,
        'display_name': display_name,
        'avatar_url': avatar_url,
        'profile_url': profile_url,
    }


def parse_vault_donor_entries(donators_str, request, *, avatar_overrides=None, discord_users=None):
    from archive.utils import parse_donor_entry_value

    entries = []
    for raw in (donators_str or '').split(','):
        raw = raw.strip()
        if not raw:
            continue
        parsed = parse_donor_entry_value(raw)
        entries.append(
            serialize_vault_donor_entry(
                parsed,
                request,
                avatar_overrides=avatar_overrides,
                discord_users=discord_users,
            )
        )
    return entries


def serialize_vault_item(item, request=None, *, avatar_overrides=None, discord_users=None):
    donor_entries = []
    if request is not None:
        donor_entries = parse_vault_donor_entries(
            item.donators,
            request,
            avatar_overrides=avatar_overrides,
            discord_users=discord_users,
        )

    data = {
        'id': item.id,
        'con': item.con,
        'year': item.year,
        'item_type': item.item_type or '',
        'quantity': item.quantity,
        'copies': item.quantity,
        'donators': item.donators or '',
        'donor_entries': donor_entries,
        'notes': item.notes or '',
        'added_at': item.added_at.isoformat() if item.added_at else None,
    }
    if request is not None:
        data['detail_url'] = absolute_url(request, f'/v1/vault/{item.id}/')
        data['web_url'] = absolute_url(request, f'/the-vault?q={item.con}')
    return data


def serialize_site_banner(banner, request=None):
    if not banner:
        return None
    return {
        'text': banner.text,
        'url': banner.link_url or '',
        'color': banner.color,
        'opens_externally': banner.opens_externally,
        'updated_at': banner.updated_at.isoformat() if banner.updated_at else None,
    }


def serialize_friend(friend, request=None):
    username = friend.avatar_username
    return {
        'id': friend.id,
        'name': friend.name,
        'subtitle': friend.subtitle or '',
        'url': friend.url,
        'telegram_username': username,
        'order': friend.order,
    }


def serialize_tag_detail(request, tag, *, document_count=None):
    data = serialize_tag(tag, document_count=document_count)
    data['detail_url'] = absolute_url(request, f'/v1/tags/{tag.slug}/')
    data['documents_url'] = absolute_url(request, f'/v1/documents/?tag={tag.slug}')
    data['web_url'] = absolute_url(request, f'/documents?tag={tag.slug}')
    return data


def paginate_queryset(queryset, page, page_size, max_page_size=100):
    from django.db.models.query import QuerySet

    page = max(1, int(page or 1))
    page_size = min(max(1, int(page_size or 20)), max_page_size)
    start = (page - 1) * page_size
    end = start + page_size

    if isinstance(queryset, QuerySet):
        total = queryset.count()
        items = list(queryset[start:end])
    else:
        seq = list(queryset)
        total = len(seq)
        items = seq[start:end]

    total_pages = (total + page_size - 1) // page_size if page_size else 1
    return items, {
        'page': page,
        'page_size': page_size,
        'total': total,
        'total_pages': total_pages,
        'has_next': page < total_pages,
        'has_previous': page > 1,
    }
