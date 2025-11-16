"""Internet Archive / Wayback mirror links for published documents."""

from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
from typing import Any

from django.core.cache import cache

from .site_cache import (
    delete_json as delete_site_json,
    ensure_registry,
    load_json as load_site_json,
    store_json as store_site_json,
)

logger = logging.getLogger(__name__)

try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False

IA_METADATA_URL = 'https://archive.org/metadata/{identifier}'
IA_CACHE_NAMESPACE = 'ia_mirrors'
IA_CACHE_TTL_AVAILABLE = 60 * 60 * 24 * 7
IA_CACHE_TTL_MISSING = 60 * 60 * 24
DJANGO_CACHE_PREFIX = 'ia_mirrors:v2:'
NEGATIVE_CACHE_RETRY_SECONDS = 15 * 60

TORRENT_FORMATS = {'archive bittorrent', 'bittorrent'}
SKIP_FORMATS = {'metadata', 'collection container', 'item tile image', 'thumb', 'json', 'item tile'}
PDF_FORMAT_HINTS = ('pdf', 'text pdf', 'image pdf')


def safe_ia_identifier(base: str) -> str:
    s = re.sub(r'[^A-Za-z0-9._-]', '-', base or '')
    s = re.sub(r'-+', '-', s).strip('-')
    return s.lower()[:200]


def _django_cache_key(slug: str) -> str:
    return f'{DJANGO_CACHE_PREFIX}{slug}'


def _cache_ttl_for_result(result: dict[str, Any]) -> int:
    return IA_CACHE_TTL_AVAILABLE if result.get('available') else IA_CACHE_TTL_MISSING


def _load_file_cache(slug: str) -> dict[str, Any] | None:
    """Load a persisted mirror payload. Only positive hits are stored on disk."""
    positive = load_site_json(IA_CACHE_NAMESPACE, slug, IA_CACHE_TTL_AVAILABLE)
    if isinstance(positive, dict) and positive.get('available'):
        return positive
    return None


def _cache_entry_age_seconds(entry: dict[str, Any]) -> float | None:
    fetched_at = entry.get('_fetched_at')
    if fetched_at is None:
        return None
    try:
        return max(0.0, time.time() - float(fetched_at))
    except (TypeError, ValueError):
        return None


def _with_fetched_at(payload: dict[str, Any]) -> dict[str, Any]:
    stamped = dict(payload)
    stamped['_fetched_at'] = time.time()
    return stamped


def _public_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if not str(key).startswith('_')}


def _should_use_cached_negative(entry: dict[str, Any]) -> bool:
    age = _cache_entry_age_seconds(entry)
    if age is None:
        return True
    return age < NEGATIVE_CACHE_RETRY_SECONDS


def invalidate_document_ia_mirrors(slug: str) -> None:
    """Drop cached IA mirror data so the next lookup re-fetches archive.org."""
    if not slug:
        return
    cache.delete(_django_cache_key(slug))
    delete_site_json(IA_CACHE_NAMESPACE, slug)


def _parse_ia_files(identifier: str, files: list, *, preferred_filename: str = '') -> dict[str, Any]:
    preferred_base = os.path.basename(preferred_filename or '').lower()
    torrent_url = None
    pdf_candidates: list[tuple[int, str, str]] = []

    for entry in files or []:
        if not isinstance(entry, dict):
            continue
        name = (entry.get('name') or '').strip()
        if not name or name.endswith('_files.xml') or name.endswith('_meta.xml'):
            continue
        fmt = (entry.get('format') or '').strip()
        fmt_lower = fmt.lower()
        url = f'https://archive.org/download/{identifier}/{name}'

        if fmt_lower in TORRENT_FORMATS or name.endswith('_archive.torrent'):
            torrent_url = url
            continue

        if fmt_lower in SKIP_FORMATS:
            continue

        if 'pdf' in fmt_lower or name.lower().endswith('.pdf'):
            size = 0
            try:
                size = int(entry.get('size') or 0)
            except (TypeError, ValueError):
                size = 0
            priority = 0
            if preferred_base and name.lower() == preferred_base:
                priority = 3
            elif preferred_base and preferred_base in name.lower():
                priority = 2
            elif any(hint in fmt_lower for hint in PDF_FORMAT_HINTS):
                priority = 1
            pdf_candidates.append((priority, size, name, url))

    options: list[dict[str, str]] = []
    if pdf_candidates:
        pdf_candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
        best_name = pdf_candidates[0][2]
        options.append({
            'type': 'mirror',
            'label': 'Internet Archive mirror',
            'url': f'https://archive.org/download/{identifier}/{best_name}',
        })

    if torrent_url:
        options.append({
            'type': 'torrent',
            'label': 'BitTorrent (.torrent)',
            'url': torrent_url,
        })

    if options:
        options.append({
            'type': 'details',
            'label': 'View on Archive.org',
            'url': f'https://archive.org/details/{identifier}',
        })

    return {
        'available': bool(options),
        'identifier': identifier,
        'options': options,
    }


def _download_ia_metadata_payload(identifier: str) -> dict[str, Any] | None:
    if not identifier:
        return None

    url = IA_METADATA_URL.format(identifier=identifier)
    if REQUESTS_AVAILABLE:
        try:
            response = requests.get(url, timeout=8)
            if response.status_code == 404:
                return None
            response.raise_for_status()
            payload = response.json()
            if isinstance(payload, dict):
                return payload
        except Exception:
            logger.warning('IA metadata fetch via requests failed for %s', identifier, exc_info=True)

    try:
        request = urllib.request.Request(
            url,
            headers={'User-Agent': 'FurryConArchives/1.0 (https://furryconarchives.org)'},
        )
        with urllib.request.urlopen(request, timeout=8) as response:
            if response.status == 404:
                return None
            payload = json.loads(response.read().decode('utf-8'))
            if isinstance(payload, dict):
                return payload
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        logger.warning('IA metadata fetch via urllib failed for %s', identifier, exc_info=True)
    except Exception:
        logger.warning('IA metadata fetch via urllib failed for %s', identifier, exc_info=True)
    return None


def _fetch_ia_metadata(identifier: str, *, preferred_filename: str = '') -> dict[str, Any] | None:
    payload = _download_ia_metadata_payload(identifier)
    if not isinstance(payload, dict) or not payload.get('metadata'):
        return None

    files = payload.get('files') or []
    result = _parse_ia_files(identifier, files, preferred_filename=preferred_filename)
    if result.get('available'):
        return result
    return None


def _identifier_candidates(document) -> list[str]:
    slug = getattr(document, 'slug', '') or ''
    doc_id = getattr(document, 'id', None)
    candidates = []
    if slug:
        candidates.append(safe_ia_identifier(slug))
    if doc_id:
        candidates.append(safe_ia_identifier(f'doc-{doc_id}'))
    return list(dict.fromkeys(item for item in candidates if item))


def get_document_ia_mirrors(document, *, force_refresh: bool = False) -> dict[str, Any]:
    """Return cached IA mirror / torrent links for a document."""
    slug = getattr(document, 'slug', '') or ''
    if not slug:
        return _public_payload({'available': False, 'identifier': None, 'options': []})

    cache_key = _django_cache_key(slug)
    if not force_refresh:
        cached = cache.get(cache_key)
        if isinstance(cached, dict):
            if cached.get('available'):
                return _public_payload(cached)
            if _should_use_cached_negative(cached):
                return _public_payload(cached)
        local_cached = _load_file_cache(slug)
        if isinstance(local_cached, dict) and local_cached.get('available'):
            cache.set(cache_key, _with_fetched_at(local_cached), _cache_ttl_for_result(local_cached))
            return _public_payload(local_cached)

    preferred_filename = ''
    try:
        if getattr(document, 'file', None):
            preferred_filename = document.file.name or ''
    except Exception:
        preferred_filename = ''

    result = {'available': False, 'identifier': None, 'options': []}
    for identifier in _identifier_candidates(document):
        fetched = _fetch_ia_metadata(identifier, preferred_filename=preferred_filename)
        if fetched and fetched.get('available'):
            result = fetched
            break

    stamped = _with_fetched_at(result)
    cache.set(cache_key, stamped, _cache_ttl_for_result(result))
    if result.get('available'):
        store_site_json(IA_CACHE_NAMESPACE, slug, result)
        ensure_registry(IA_CACHE_NAMESPACE, slug)
    else:
        delete_site_json(IA_CACHE_NAMESPACE, slug)
    return _public_payload(result)
