"""Consurf slug normalization when site slugs differ from Consurf canonical slugs."""

from __future__ import annotations

import re

# Site/category slugs that do not match Consurf's canonical slug.
CONSURF_SLUG_ALIASES: dict[str, str] = {
    'furgv': 'furgiv',
}

_DOCUMENT_SUFFIX_RE = re.compile(r'-(?:conbook|program|schedule).*$', re.IGNORECASE)
_YEAR_THEME_RE = re.compile(r'^(.*?)-((?:19|20)\d{2})(?:-(.*))?$')


def _base_slug_alternatives(base: str) -> list[str]:
    base = (base or '').strip('-')
    if not base:
        return []

    candidates = [base]
    alias = CONSURF_SLUG_ALIASES.get(base)
    if alias:
        candidates.append(alias)

    # FurGV / FurGIV-style acronyms: furgv -> furgiv
    if base.endswith('gv') and not base.endswith('giv'):
        candidates.append(f'{base[:-2]}giv')

    seen: set[str] = set()
    ordered: list[str] = []
    for candidate in candidates:
        if candidate and candidate not in seen:
            seen.add(candidate)
            ordered.append(candidate)
    return ordered


def strip_document_slug_suffix(slug: str) -> str:
    """Remove trailing document-type suffixes like ``-conbook``."""
    return _DOCUMENT_SUFFIX_RE.sub('', (slug or '').strip('-')).strip('-')


def parse_document_event_slug(slug: str) -> dict[str, object] | None:
    """Parse ``{convention}-{year}[-{theme...}]`` from archive document slugs."""
    slug = strip_document_slug_suffix(slug)
    if not slug:
        return None

    match = _YEAR_THEME_RE.match(slug)
    if not match:
        return None

    base = match.group(1).strip('-')
    year = int(match.group(2))
    theme = (match.group(3) or '').strip('-') or None
    base_year_slug = f'{base}-{year}'
    themed_event_slug = f'{base_year_slug}-{theme}' if theme else None
    return {
        'base': base,
        'year': year,
        'theme': theme,
        'base_year_slug': base_year_slug,
        'themed_event_slug': themed_event_slug,
    }


def expand_consurf_slug(slug: str) -> list[str]:
    """Return slug candidates to try against Consurf, preferred order first."""
    slug = strip_document_slug_suffix(slug)
    if not slug:
        return []

    parsed = parse_document_event_slug(slug)
    if parsed:
        seen: set[str] = set()
        ordered: list[str] = []
        for candidate in (
            parsed.get('themed_event_slug'),
            parsed['base_year_slug'],
        ):
            if candidate and candidate not in seen:
                seen.add(candidate)
                ordered.append(candidate)
        for base_alt in _base_slug_alternatives(parsed['base']):
            for candidate in (f"{base_alt}-{parsed['year']}", base_alt):
                if candidate not in seen:
                    seen.add(candidate)
                    ordered.append(candidate)
        return ordered

    year_match = re.match(r'^(.*)-((?:19|20)\d{2})$', slug)
    if year_match:
        base, year = year_match.group(1), year_match.group(2)
        seen: set[str] = set()
        ordered: list[str] = []
        for base_alt in _base_slug_alternatives(base):
            for candidate in (f'{base_alt}-{year}', base_alt):
                if candidate not in seen:
                    seen.add(candidate)
                    ordered.append(candidate)
        return ordered

    return _base_slug_alternatives(slug)


def consurf_convention_roots(slug: str) -> list[str]:
    """Convention slug roots when a document slug includes year/theme text."""
    slug = strip_document_slug_suffix(slug)
    if not slug:
        return []

    seen: set[str] = set()
    ordered: list[str] = []

    def add(value: str) -> None:
        value = (value or '').strip('-')
        if not value or value in seen:
            return
        seen.add(value)
        ordered.append(value)

    parsed = parse_document_event_slug(slug)
    if parsed:
        add(parsed['base'])
        for alt in _base_slug_alternatives(parsed['base']):
            add(alt)
        return ordered

    for candidate in expand_consurf_slug(slug):
        add(candidate)

    parts = [part for part in slug.split('-') if part]
    if len(parts) > 1:
        add(parts[0])
        for alt in _base_slug_alternatives(parts[0]):
            add(alt)
    if len(parts) >= 3:
        add(f'{parts[0]}-{parts[1]}')
        for alt in _base_slug_alternatives(f'{parts[0]}-{parts[1]}'):
            add(alt)
    if len(parts) >= 4:
        add(f'{parts[0]}-{parts[1]}-{parts[2]}')

    return ordered


def consurf_year_event_slugs(slug_base: str, year: int | None) -> list[str]:
    """Consurf event slugs like ``furgiv-2024``, including themed archive slugs."""
    if year is None:
        return []

    try:
        year_int = int(year)
    except (TypeError, ValueError):
        return []

    seen: set[str] = set()
    ordered: list[str] = []

    def add(value: str) -> None:
        value = (value or '').strip('-')
        if not value or value in seen:
            return
        seen.add(value)
        ordered.append(value)

    slug_base = strip_document_slug_suffix(slug_base)
    parsed = parse_document_event_slug(slug_base)
    if parsed and parsed['year'] == year_int:
        themed = parsed.get('themed_event_slug')
        if isinstance(themed, str) and themed:
            add(themed)
        add(parsed['base_year_slug'])
        convention_root = parsed['base']
    elif re.search(rf'-{year_int}(?:-|$)', slug_base):
        add(slug_base)
        convention_root = re.sub(rf'-{year_int}(?:-.*)?$', '', slug_base).strip('-') or slug_base
    else:
        convention_root = slug_base

    for root in _base_slug_alternatives(convention_root):
        add(f'{root}-{year_int}')

    return ordered


def primary_consurf_slug(slug: str) -> str:
    """Best single slug to use for direct Consurf convention lookups."""
    expanded = expand_consurf_slug(slug)
    return expanded[0] if expanded else (slug or '')
