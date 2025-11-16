"""Convert Furconnect schedule CSV exports to FurryConArchives schedule format."""

from __future__ import annotations

import csv
import hashlib
import io
import re
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

from dateutil import parser as dateparser

FURCONNECT_SLUG_SEP = '|'


def normalize_furconnect_url(url: str) -> str:
    s = str(url or '').strip()
    if not s:
        return ''
    if not s.startswith('http://') and not s.startswith('https://'):
        s = 'https://' + s
    return s.rstrip('/')


def decode_furconnect_storage(slug: str) -> tuple[str, str] | None:
    if FURCONNECT_SLUG_SEP not in slug:
        return None
    route_slug, host = slug.split(FURCONNECT_SLUG_SEP, 1)
    if route_slug and host:
        return route_slug, host
    return None


def storage_fields_for_furconnect_input(route_slug: str, source_url: str) -> str:
    route_slug = str(route_slug or '').strip()
    source_url = normalize_furconnect_url(source_url)
    if not route_slug:
        raise ValueError('Furconnect schedules require a route slug.')
    if not source_url:
        raise ValueError('Furconnect schedules require a source URL.')
    host = urlparse(source_url).netloc
    if not host:
        raise ValueError('Furconnect source URL must include a hostname.')
    return f'{route_slug}{FURCONNECT_SLUG_SEP}{host}'


def furconnect_route_slug(slug: str) -> str:
    decoded = decode_furconnect_storage(slug)
    if decoded:
        return decoded[0]
    return str(slug or '').strip()


def furconnect_schedule_url(slug: str) -> str:
    decoded = decode_furconnect_storage(slug)
    if decoded:
        _, host = decoded
        if host.startswith('http://') or host.startswith('https://'):
            return host.rstrip('/')
        return f'https://{host}'.rstrip('/')
    s = str(slug or '').strip()
    if '://' in s or '.' in s:
        return normalize_furconnect_url(s)
    return ''


def furconnect_meta_from_filename(filename: str) -> dict[str, Any] | None:
    """Guess route slug, year, and convention name from a Furconnect CSV filename."""
    base = re.sub(r'(?i)[_\s]+schedule\.csv$', '', str(filename or '').strip())
    base = re.sub(r'\.csv$', '', base, flags=re.IGNORECASE).strip()
    if not base:
        return None

    year = None
    year_match = re.search(r'\b(20\d{2})\b', base)
    if year_match:
        year = int(year_match.group(1))

    convention_name = re.sub(r'\b20\d{2}\b', '', base).strip(' -_')
    convention_name = re.sub(r'\s+', ' ', convention_name).strip()
    if convention_name:
        convention_name = convention_name.title()

    route_slug = re.sub(r'[^a-z0-9]+', '-', base.lower()).strip('-')
    if year and not route_slug.endswith(str(year)):
        route_slug = f'{route_slug}-{year}' if route_slug else str(year)

    if not route_slug:
        return None

    meta = {
        'slug': route_slug,
        'year': year,
        'convention_name': convention_name or route_slug.replace('-', ' ').title(),
    }
    if convention_name and year:
        meta['convention_name'] = f'{convention_name} {year}'
    return meta


def _format_time_12h(time_value: str | None) -> str | None:
    if not time_value:
        return None
    text = str(time_value).strip()
    if not text:
        return None

    if re.search(r'[ap]m', text, re.IGNORECASE):
        parsed = dateparser.parse(text)
        if not parsed:
            return text
        fmt = parsed.strftime('%I:%M%p').lower()
        return fmt.lstrip('0') if fmt.startswith('0') else fmt

    if ':' not in text:
        return text

    hour_str, minute_str = text.split(':', 1)
    dt = datetime(2000, 1, 1, int(hour_str), int(minute_str))
    fmt = dt.strftime('%I:%M%p').lower()
    return fmt.lstrip('0') if fmt.startswith('0') else fmt


def _make_event_id(name: str, date: str, start_raw: str | None, location: str) -> str:
    key = f'{name}|{date}|{start_raw}|{location}'
    return hashlib.sha256(key.encode()).hexdigest()[:20]


def _normalize_title(title: str) -> str:
    return re.sub(r'\s+', ' ', title.replace('\n', ' ')).strip()


def _build_description(description: str, tags: str, hosts: str) -> str:
    parts = [description.strip()] if description and description.strip() else []
    if tags and tags.strip():
        parts.append(f'Tags: {tags.strip()}')
    if hosts and hosts.strip():
        parts.append(f'Hosts: {hosts.strip()}')
    return '\n\n'.join(parts)


def _read_csv_rows(csv_text: str) -> list[dict[str, str]]:
    reader = csv.DictReader(io.StringIO(csv_text))
    rows: list[dict[str, str]] = []
    for row in reader:
        normalized = {
            (key or '').strip().lower(): (value or '').strip()
            for key, value in row.items()
        }
        if normalized.get('title'):
            rows.append(normalized)
    return rows


def furconnect_csv_to_events(
    csv_text: str,
    *,
    timezone: str = 'America/Los_Angeles',
    normalize_times=None,
) -> list[dict[str, Any]]:
    """Flatten a Furconnect CSV export into local schedule events."""
    events: list[dict[str, Any]] = []

    for row in _read_csv_rows(csv_text):
        name = _normalize_title(row.get('title', ''))
        if not name:
            continue

        date = row.get('date', '')
        if not re.match(r'^\d{4}-\d{2}-\d{2}$', date):
            continue

        room = row.get('room', '')
        start_raw = _format_time_12h(row.get('start time'))
        end_raw = _format_time_12h(row.get('end time'))
        description = _build_description(
            row.get('description', ''),
            row.get('tags', ''),
            row.get('hosts', ''),
        )

        ev = {
            'id': _make_event_id(name, date, start_raw, room),
            'name': name,
            'date': date,
            'start_raw': start_raw,
            'end_raw': end_raw,
            'location': room,
            'description': description,
            'tz_override': timezone,
        }
        events.append(ev)

    events.sort(key=lambda e: (e['date'], e.get('start_raw') or '', e['location'], e['name']))

    if normalize_times:
        events = normalize_times(events)
        for ev in events:
            ev.pop('tz_override', None)

    return events


def furconnect_csv_to_payload(
    csv_text: str,
    *,
    slug: str,
    year: int | None = None,
    schedule_type: str = 'furconnect',
    timezone: str = 'America/Los_Angeles',
    convention_name: str | None = None,
    normalize_times=None,
) -> dict[str, Any]:
    """Build {slug, type, year, count, events} from a Furconnect CSV export."""
    if year is None:
        match = re.search(r'-(\d{4})$', slug)
        if match:
            year = int(match.group(1))

    events = furconnect_csv_to_events(
        csv_text,
        timezone=timezone,
        normalize_times=normalize_times,
    )

    if year is None and events:
        year = int(events[0]['date'][:4])

    payload: dict[str, Any] = {
        'slug': slug,
        'type': schedule_type,
        'year': year,
        'count': len(events),
        'events': events,
    }
    if convention_name:
        payload['convention_name'] = convention_name
    return payload
