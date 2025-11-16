"""Convert Venvi Firestore IndexedDB dumps to FurryConArchives schedule JSON."""

from __future__ import annotations

from typing import Any

import pytz
from dateutil import parser as dateparser
from django.utils.text import slugify


def decode_firestore_value(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    if 'stringValue' in value:
        return value['stringValue']
    if 'booleanValue' in value:
        return value['booleanValue']
    if 'integerValue' in value:
        return int(value['integerValue'])
    if 'doubleValue' in value:
        return float(value['doubleValue'])
    if 'nullValue' in value:
        return None
    if 'timestampValue' in value:
        return value['timestampValue']
    if 'geoPointValue' in value:
        gp = value['geoPointValue']
        return {'latitude': gp.get('latitude'), 'longitude': gp.get('longitude')}
    if 'arrayValue' in value:
        values = value['arrayValue'].get('values') or []
        return [decode_firestore_value(v) for v in values]
    if 'mapValue' in value:
        fields = value['mapValue'].get('fields') or {}
        return {k: decode_firestore_value(v) for k, v in fields.items()}
    return value


def _remote_documents(dump: dict) -> list:
    docs = dump.get('remoteDocumentsV14')
    if isinstance(docs, list):
        return docs
    return []


def _matches_con(entry: dict, org_id: str | None, app_id: str | None) -> bool:
    if not org_id or not app_id:
        return True
    path = entry.get('prefixPath') or []
    return path == ['orgs', org_id, 'cons', app_id]


def _decode_entry_fields(entry: dict) -> dict | None:
    if entry.get('noDocument'):
        return None
    doc = entry.get('document') or {}
    fields = doc.get('fields')
    if not fields:
        return None
    return {k: decode_firestore_value(v) for k, v in fields.items()}


def detect_venvi_con_ids(dump: dict) -> tuple[str | None, str | None]:
    paths: set[tuple[str, str]] = set()
    for entry in _remote_documents(dump):
        if entry.get('collectionGroup') != 'events':
            continue
        path = entry.get('prefixPath') or []
        if len(path) == 4 and path[0] == 'orgs' and path[2] == 'cons':
            paths.add((path[1], path[3]))
    if len(paths) == 1:
        return paths.pop()
    return None, None


def _find_con_meta(dump: dict, org_id: str | None, app_id: str | None) -> dict:
    for entry in _remote_documents(dump):
        if entry.get('collectionGroup') != 'cons':
            continue
        if org_id and entry.get('documentId') != app_id:
            continue
        path = entry.get('prefixPath') or []
        if org_id and path != ['orgs', org_id]:
            continue
        fields = _decode_entry_fields(entry)
        if fields:
            return fields
    return {}


def _format_time_local(ts: str | None, tz_name: str) -> tuple[str | None, str | None]:
    if not ts or not tz_name:
        return None, None
    try:
        dt = dateparser.parse(ts)
        if dt.tzinfo is None:
            dt = pytz.UTC.localize(dt)
        local = dt.astimezone(pytz.timezone(tz_name))
        date_val = local.strftime('%Y-%m-%d')
        time_val = local.strftime('%I:%M%p').lower()
        if time_val.startswith('0'):
            time_val = time_val.lstrip('0')
        return date_val, time_val
    except Exception:
        return None, None


def _year_from_con(con: dict) -> int | None:
    for key in ('startDate', 'endDate'):
        raw = con.get(key)
        if not raw:
            continue
        try:
            return dateparser.parse(raw).year
        except Exception:
            continue
    return None


def venvi_dump_to_schedule_payload(
    dump: dict,
    org_id: str | None = None,
    app_id: str | None = None,
    *,
    normalize_times=None,
) -> dict:
    """Build {slug, type, year, count, events} from a Venvi IndexedDB export."""
    if not org_id or not app_id:
        detected_org, detected_app = detect_venvi_con_ids(dump)
        org_id = org_id or detected_org
        app_id = app_id or detected_app

    con = _find_con_meta(dump, org_id, app_id)
    con_name = (con.get('name') or 'venvi').strip()
    year = _year_from_con(con)
    slug_base = slugify(con_name) or 'venvi'
    slug = f'{slug_base}-{year}' if year else slug_base

    location = con.get('location') or {}
    tz_name = location.get('timeZoneId') or 'UTC'
    site_location = location.get('address') or location.get('name') or ''

    events = []
    for entry in _remote_documents(dump):
        if entry.get('collectionGroup') != 'events':
            continue
        if not _matches_con(entry, org_id, app_id):
            continue
        fields = _decode_entry_fields(entry)
        if not fields:
            continue
        if fields.get('hidden') is True:
            continue

        title = (fields.get('title') or '').strip()
        if not title:
            continue

        start_date, start_raw = _format_time_local(fields.get('startTime'), tz_name)
        end_date, end_raw = _format_time_local(fields.get('endTime'), tz_name)
        date_val = start_date or end_date or ''

        venue = fields.get('venue') or {}
        room = ''
        if isinstance(venue, dict):
            room = (venue.get('name') or '').strip()

        desc = (fields.get('desc') or '').strip()
        tags = fields.get('tags') or []
        if tags and isinstance(tags, list):
            tag_line = ', '.join(str(t) for t in tags if t)
            if tag_line:
                desc = f'{desc}\n\nTags: {tag_line}'.strip() if desc else f'Tags: {tag_line}'

        ev = {
            'id': fields.get('id') or entry.get('documentId'),
            'name': title,
            'date': date_val,
            'start_raw': start_raw,
            'end_raw': end_raw,
            'location': room or site_location,
            'description': desc,
            'tz_override': tz_name,
            'venvi_url': (
                f'https://web.venvi.app/{org_id}/{app_id}/home'
                if org_id and app_id
                else None
            ),
        }
        if ev['venvi_url'] is None:
            ev.pop('venvi_url', None)
        events.append(ev)

    if normalize_times:
        events = normalize_times(events)
        for ev in events:
            ev.pop('tz_override', None)
            ev.pop('venvi_url', None)

    return {
        'slug': slug,
        'type': 'venvi',
        'year': year,
        'count': len(events),
        'events': events,
    }
