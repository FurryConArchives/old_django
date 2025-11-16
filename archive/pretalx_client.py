"""Fetch and normalize pretalx schedule data via the public REST API."""

from __future__ import annotations

import logging
import time
from typing import Any
from urllib.parse import urlparse

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 15
PAGE_SIZE = 50
PAGE_DELAY_S = 0.25
PRETALX_SLUG_SEP = '|'


class PretalxConfigError(ValueError):
    pass


def normalize_pretalx_slug(slug: str) -> str:
    """Normalize a pretalx slug/URL for parsing."""
    s = str(slug or '').strip()
    if not s:
        return s
    if ('/' in s or '.' in s) and not (s.startswith('http://') or s.startswith('https://')):
        s = 'https://' + s
    return s


def parse_pretalx_slug(slug: str) -> tuple[str, str]:
    """Return ``(base_url, event_slug)`` from a pretalx URL or host/path slug."""
    s = normalize_pretalx_slug(slug)
    if not s:
        raise PretalxConfigError('pretalx_slug_missing')

    parsed = urlparse(s)
    if not parsed.netloc:
        raise PretalxConfigError('pretalx_host_missing')

    base_url = f'{parsed.scheme or "https"}://{parsed.netloc}'.rstrip('/')
    parts = [p for p in parsed.path.split('/') if p]
    if not parts:
        raise PretalxConfigError('pretalx_event_slug_missing')

    event_slug = parts[0]
    return base_url, event_slug


def pretalx_schedule_url(slug: str) -> str:
    base_url, event_slug = parse_pretalx_slug(pretalx_api_input_from_value(slug))
    return f'{base_url}/{event_slug}/schedule/'


def pretalx_talk_url(slug: str, talk_code: str) -> str:
    base_url, event_slug = parse_pretalx_slug(pretalx_api_input_from_value(slug))
    return f'{base_url}/{event_slug}/talk/{talk_code}/'


def _looks_like_pretalx_url(value: str) -> bool:
    s = str(value or '').strip()
    return bool(s) and ('://' in s or '/' in s or '.' in s)


def _encode_pretalx_storage(event_slug: str, host: str) -> str:
    return f'{event_slug}{PRETALX_SLUG_SEP}{host}'


def _decode_pretalx_storage(slug: str) -> tuple[str, str] | None:
    if PRETALX_SLUG_SEP not in slug:
        return None
    event_slug, host = slug.split(PRETALX_SLUG_SEP, 1)
    if event_slug and host:
        return event_slug, host
    return None


def pretalx_api_input_from_value(slug: str) -> str:
    """Return pretalx URL/host input from a slug or stored value."""
    s = str(slug or '').strip()
    decoded = _decode_pretalx_storage(s)
    if decoded:
        event_slug, host = decoded
        return f'https://{host}/{event_slug}'
    return s


def storage_fields_for_pretalx_input(slug_input: str) -> str:
    """Return the ``Schedule.slug`` value for pretalx schedules."""
    s = normalize_pretalx_slug(slug_input)
    if not s:
        raise PretalxConfigError('pretalx_slug_missing')
    if _looks_like_pretalx_url(s):
        base_url, event_slug = parse_pretalx_slug(s)
        host = urlparse(base_url).netloc
        return _encode_pretalx_storage(event_slug, host)
    return s


def pretalx_api_input(sched) -> str:
    """Return the pretalx URL/host input used for API requests."""
    return pretalx_api_input_from_value(getattr(sched, 'slug', '') or '')


def schedule_route_slug(sched) -> str:
    """Return a slug safe for ``/schedules/<slug>`` URL routes."""
    slug = str(getattr(sched, 'slug', '') or '').strip()
    decoded = _decode_pretalx_storage(slug)
    if decoded:
        return decoded[0]
    if getattr(sched, 'type', '') == 'pretalx' and _looks_like_pretalx_url(slug):
        try:
            _, event_slug = parse_pretalx_slug(slug)
            return event_slug
        except PretalxConfigError:
            pass
    return slug


def normalize_pretalx_schedule_record(sched) -> bool:
    """Convert legacy pretalx URL values stored in ``slug``. Returns True if updated."""
    if getattr(sched, 'type', '') != 'pretalx':
        return False
    slug = str(getattr(sched, 'slug', '') or '').strip()
    if _decode_pretalx_storage(slug):
        return False
    if not _looks_like_pretalx_url(slug):
        return False
    sched.slug = storage_fields_for_pretalx_input(slug)
    return True


def _localized(value: Any) -> str:
    if isinstance(value, dict):
        for key in ('en', 'de', 'fr', 'es'):
            if value.get(key):
                return str(value[key])
        for val in value.values():
            if val:
                return str(val)
        return ''
    if value is None:
        return ''
    return str(value)


def _api_headers() -> dict[str, str]:
    token = getattr(settings, 'PRETALX_API_TOKEN', '') or ''
    if token:
        return {'Authorization': f'Token {token}'}
    return {}


def _paginate_api(url: str, *, session: requests.Session, headers: dict[str, str]) -> tuple[list[dict], str | None]:
    results: list[dict] = []
    next_url: str | None = url

    while next_url:
        try:
            resp = session.get(next_url, headers=headers, timeout=DEFAULT_TIMEOUT)
        except requests.RequestException as exc:
            return [], f'pretalx_request_error:{exc}'

        if resp.status_code == 429:
            retry_after = resp.headers.get('Retry-After')
            sleep_s = PAGE_DELAY_S
            if retry_after:
                try:
                    sleep_s = max(float(retry_after), PAGE_DELAY_S)
                except (TypeError, ValueError):
                    pass
            logger.warning('pretalx rate limited for %s; sleeping %.1fs', next_url, sleep_s)
            time.sleep(sleep_s)
            continue

        if resp.status_code != 200:
            return [], f'pretalx_api_error:{resp.status_code}:{next_url}'

        data = resp.json()
        if isinstance(data, list):
            return data, None

        page_results = data.get('results')
        if isinstance(page_results, list):
            results.extend(page_results)
        next_url = data.get('next')
        if next_url and next_url.startswith('http://'):
            next_url = 'https://' + next_url[len('http://'):]
        if next_url:
            time.sleep(PAGE_DELAY_S)

    return results, None


def fetch_pretalx_events(sched) -> tuple[list[dict], str | None]:
    """Fetch scheduled talks from pretalx and normalize to archive event dicts."""
    try:
        if normalize_pretalx_schedule_record(sched):
            sched.save(update_fields=['slug'])
        base_url, event_slug = parse_pretalx_slug(pretalx_api_input(sched))
    except PretalxConfigError as exc:
        return [], str(exc)

    headers = _api_headers()
    session = requests.Session()
    api_base = f'{base_url}/api/events/{event_slug}'

    speakers, error = _paginate_api(f'{api_base}/speakers/?page_size={PAGE_SIZE}', session=session, headers=headers)
    if error:
        return [], error

    speakers_map: dict[str, str] = {}
    for speaker in speakers:
        code = speaker.get('code')
        if code:
            speakers_map[str(code)] = speaker.get('name') or str(code)

    slots_url = f'{api_base}/slots/?expand=submission,room&page_size={PAGE_SIZE}'
    slots, error = _paginate_api(slots_url, session=session, headers=headers)
    if error:
        return [], error
    if not slots:
        return [], 'no_events_found_from_pretalx'

    api_input = pretalx_api_input(sched)
    events: list[dict] = []
    for slot in slots:
        submission = slot.get('submission')
        if not isinstance(submission, dict):
            continue

        code = submission.get('code') or slot.get('id')
        room = slot.get('room') if isinstance(slot.get('room'), dict) else {}
        room_name = _localized(room.get('name')) if room else ''
        speaker_codes = submission.get('speakers') or []
        speakers_list = [speakers_map.get(str(code), str(code)) for code in speaker_codes]
        organizer = speakers_list[0] if speakers_list else None
        talk_url = pretalx_talk_url(api_input, str(code)) if code else None

        events.append({
            'id': code,
            'name': submission.get('title') or 'Untitled',
            'description': submission.get('description') or '',
            'start': slot.get('start'),
            'end': slot.get('end'),
            'speakers': speakers_list,
            'location': room_name,
            'organizer': organizer,
            'url': talk_url,
        })

    if not events:
        return [], 'no_events_found_from_pretalx'
    return events, None
