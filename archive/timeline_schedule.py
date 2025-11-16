"""Convert timeline-style schedule JSON to FurryConArchives local schedule format."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta
from typing import Any

import pytz
from dateutil import parser as dateparser


def _format_time_12h(time_24h: str | None) -> str | None:
    if not time_24h:
        return None
    text = str(time_24h).strip()
    if ':' not in text:
        return text
    hour_str, minute_str = text.split(':', 1)
    dt = datetime(2000, 1, 1, int(hour_str), int(minute_str))
    fmt = dt.strftime('%I:%M%p').lower()
    if fmt.startswith('0'):
        fmt = fmt.lstrip('0')
    return fmt


def _make_event_id(name: str, date: str, start_raw: str | None, location: str) -> str:
    key = f'{name}|{date}|{start_raw}|{location}'
    return hashlib.sha256(key.encode()).hexdigest()[:20]


def _normalize_title(title: str) -> str:
    return re.sub(r'\s+', ' ', title.replace('\n', ' ')).strip()


def timeline_schedule_to_events(
    data: dict[str, Any],
    *,
    timezone: str = 'Asia/Kuala_Lumpur',
    normalize_times=None,
) -> list[dict]:
    """Flatten date -> hall -> events timeline JSON into local schedule events."""
    events: list[dict] = []

    for key, halls in data.items():
        if not re.match(r'^\d{4}-\d{2}-\d{2}$', key):
            continue
        if not isinstance(halls, dict):
            continue

        date = key
        for hall, items in halls.items():
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                title = (item.get('title') or '').strip()
                if not title:
                    continue

                name = _normalize_title(title)
                start_raw = _format_time_12h(item.get('start'))
                end_raw = _format_time_12h(item.get('end'))
                ev = {
                    'id': _make_event_id(name, date, start_raw, hall),
                    'name': name,
                    'date': date,
                    'start_raw': start_raw,
                    'end_raw': end_raw,
                    'location': hall,
                    'description': '',
                    'tz_override': timezone,
                }
                events.append(ev)

    events.sort(key=lambda e: (e['date'], e.get('start_raw') or '', e['location'], e['name']))

    if normalize_times:
        events = normalize_times(events)
        for ev in events:
            ev.pop('tz_override', None)

    return events


def timeline_schedule_to_payload(
    data: dict[str, Any],
    *,
    slug: str,
    year: int | None = None,
    schedule_type: str = 'local',
    timezone: str = 'Asia/Kuala_Lumpur',
    normalize_times=None,
) -> dict:
    """Build {slug, type, year, count, events} from timeline schedule JSON."""
    if year is None:
        dates = [k for k in data if re.match(r'^\d{4}-\d{2}-\d{2}$', k)]
        year = int(dates[0][:4]) if dates else None

    events = timeline_schedule_to_events(
        data,
        timezone=timezone,
        normalize_times=normalize_times,
    )

    return {
        'slug': slug,
        'type': schedule_type,
        'year': year,
        'count': len(events),
        'events': events,
    }
