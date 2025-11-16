"""Helpers for local schedule JSON imports."""

from __future__ import annotations

import re

from django.utils.text import slugify


def local_schedule_meta_from_payload(payload: dict | None) -> dict | None:
    """Extract slug, year, and convention_name from a local schedule JSON file."""
    if not isinstance(payload, dict):
        return None

    events = payload.get('events') or payload.get('schedule')
    if not isinstance(events, list):
        return None

    slug = str(payload.get('slug') or '').strip()
    convention_name = str(
        payload.get('convention_name') or payload.get('display_name') or ''
    ).strip()

    year = payload.get('year')
    if year is not None and year != '':
        try:
            year = int(year)
        except (TypeError, ValueError):
            year = None
    else:
        year = None

    if not slug and convention_name and year:
        slug = f'{slugify(convention_name)}-{year}'

    if slug and year is None:
        match = re.search(r'-(\d{4})$', slug)
        if match:
            year = int(match.group(1))

    if not convention_name and slug:
        base = re.sub(r'-\d{4}$', '', slug)
        title = base.replace('-', ' ').title()
        convention_name = f'{title} {year}' if year else title

    if not slug:
        return None

    return {
        'slug': slug,
        'year': year,
        'convention_name': convention_name,
    }


def apply_local_schedule_meta(sched, payload: dict | None) -> bool:
    """Apply slug/year/convention_name from a local schedule payload onto a Schedule."""
    meta = local_schedule_meta_from_payload(payload)
    if not meta:
        return False

    sched.slug = meta['slug']
    if meta.get('year') is not None:
        sched.year = meta['year']
    if meta.get('convention_name'):
        sched.convention_name = meta['convention_name']
    return True
