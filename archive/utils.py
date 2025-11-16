"""
Utility functions for the archive app
"""
try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False

from django.conf import settings
from django.core.cache import cache
from django.core.files.base import File
from django.db.models import Case, When
from django.utils import timezone
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone as dt_timezone
from collections import defaultdict
from contextlib import contextmanager
import hashlib
import json
import logging
import os
import shutil
import threading
import tempfile
from pathlib import Path

from .site_cache import ensure_registry, load_json as load_site_json, store_json as store_site_json

logger = logging.getLogger(__name__)

UMAMI_CACHE_TTL_SECONDS = 60 * 60
UMAMI_CACHE_STALE_TTL_SECONDS = 60 * 60 * 24
UMAMI_REFRESH_LOCK_TTL_SECONDS = 5 * 60
UMAMI_REFRESH_BATCH_SIZE = 20
UMAMI_AUTH_FAILURE_CACHE_KEY = 'umami:auth-failure'
UMAMI_AUTH_FAILURE_TTL_SECONDS = 15 * 60
UMAMI_AUTH_TOKEN_CACHE_KEY = 'umami:auth-token'
UMAMI_AUTH_TOKEN_TTL_SECONDS = 55 * 60


@contextmanager
def local_path_for_field_file(field_file):
    """Yield a usable local filesystem path for a Django FileField or ImageField.

    For local storage backends this returns the existing path. For remote
    storage backends, it downloads the file to a temporary location and cleans
    it up after use.
    """
    if not field_file:
        yield None
        return

    existing_path = None
    try:
        existing_path = field_file.path
    except Exception:
        existing_path = None

    if existing_path and os.path.exists(existing_path):
        yield existing_path
        return

    suffix = Path(getattr(field_file, 'name', '') or '').suffix
    temp_handle = tempfile.NamedTemporaryFile(suffix=suffix or '', delete=False)
    temp_handle.close()
    temp_path = temp_handle.name

    try:
        source_file = getattr(field_file, 'file', None)
        if source_file is not None and not getattr(source_file, 'closed', False):
            current_position = None
            try:
                current_position = source_file.tell()
            except Exception:
                current_position = None

            try:
                source_file.seek(0)
            except Exception:
                pass

            with open(temp_path, 'wb') as destination_file:
                shutil.copyfileobj(source_file, destination_file)

            if current_position is not None:
                try:
                    source_file.seek(current_position)
                except Exception:
                    pass
        else:
            with field_file.open('rb') as source_file, open(temp_path, 'wb') as destination_file:
                shutil.copyfileobj(source_file, destination_file)
        yield temp_path
    finally:
        try:
            os.remove(temp_path)
        except Exception:
            pass


def save_field_file_from_path(field_file, source_path, filename=None):
    """Overwrite a FileField or ImageField with the contents of a local file."""
    if not field_file or not source_path:
        return

    target_name = filename or getattr(field_file, 'name', None)
    if not target_name:
        return

    try:
        field_file.storage.delete(target_name)
    except Exception:
        pass

    with open(source_path, 'rb') as source_file:
        field_file.save(target_name, File(source_file), save=False)


def get_telegram_profile_picture_url(username):
    """
    Get Telegram profile picture URL for a username.
    
    Returns the server-side proxy endpoint URL which handles:
    1. Fetching from Telegram web (og:image from t.me)
    2. Server-side caching
    3. CORS handling
    
    Args:
        username: Telegram username without @
        
    Returns:
        URL to the server-side proxy endpoint
    """
    if not username:
        return None
    
    # Remove @ if present
    username = username.lstrip('@').strip()
    
    if not username:
        return None
    
    # Return the server-side proxy endpoint URL (include leading slash)
    return f"/internal/api/telegram-avatar/{username}"


def _umami_auth_failed():
    return cache.get(UMAMI_AUTH_FAILURE_CACHE_KEY) is not None


def _umami_is_configured():
    website_id = getattr(settings, 'UMAMI_SITE_ID', '').strip()
    if not website_id:
        return False
    username = getattr(settings, 'UMAMI_USERNAME', '').strip()
    password = getattr(settings, 'UMAMI_PASSWORD', '').strip()
    if username and password:
        return True
    api_key = getattr(settings, 'UMAMI_API_KEY', '').strip()
    return bool(api_key)


def _umami_uses_bearer_auth():
    return bool(
        getattr(settings, 'UMAMI_USERNAME', '').strip()
        and getattr(settings, 'UMAMI_PASSWORD', '').strip()
    )


def _umami_base_url():
    return getattr(settings, 'UMAMI_API_BASE_URL', '').rstrip('/')


def _mark_umami_auth_failed():
    if cache.add(UMAMI_AUTH_FAILURE_CACHE_KEY, 1, UMAMI_AUTH_FAILURE_TTL_SECONDS):
        if _umami_uses_bearer_auth():
            logger.error(
                'Umami login failed or bearer token was rejected (HTTP 401). '
                'Check UMAMI_USERNAME, UMAMI_PASSWORD, and UMAMI_API_BASE_URL.'
            )
        else:
            logger.error(
                'Umami API key is invalid or revoked (HTTP 401). '
                'Generate a new key at https://cloud.umami.is and set UMAMI_API_KEY.'
            )


def _invalidate_umami_auth_token():
    cache.delete(UMAMI_AUTH_TOKEN_CACHE_KEY)


def _fetch_umami_auth_token():
    username = getattr(settings, 'UMAMI_USERNAME', '').strip()
    password = getattr(settings, 'UMAMI_PASSWORD', '').strip()
    base_url = _umami_base_url()
    if not username or not password or not base_url:
        return None

    login_url = f"{base_url}/auth/login"
    try:
        response = requests.post(
            login_url,
            json={'username': username, 'password': password},
            headers={
                'Accept': 'application/json',
                'Content-Type': 'application/json',
            },
            timeout=8,
        )
        status = response.status_code
        if status == 401:
            _mark_umami_auth_failed()
            return None
        response.raise_for_status()
        token = (response.json() or {}).get('token')
        if not token:
            logger.error('Umami login succeeded but response did not include a token')
            return None
        cache.delete(UMAMI_AUTH_FAILURE_CACHE_KEY)
        cache.set(UMAMI_AUTH_TOKEN_CACHE_KEY, token, UMAMI_AUTH_TOKEN_TTL_SECONDS)
        return token
    except requests.exceptions.HTTPError:
        logger.warning(
            'umami login returned http error for url=%s status=%s body=%s',
            login_url,
            status,
            (response.text or '')[:300],
        )
        return None
    except Exception:
        logger.exception('umami login failed for url=%s', login_url)
        return None


def _get_umami_auth_token(force_refresh=False):
    if not _umami_uses_bearer_auth():
        return None
    if not force_refresh:
        token = cache.get(UMAMI_AUTH_TOKEN_CACHE_KEY)
        if token:
            return token
    return _fetch_umami_auth_token()


def _umami_request_headers(force_refresh=False):
    headers = {'Accept': 'application/json'}
    if _umami_uses_bearer_auth():
        token = _get_umami_auth_token(force_refresh=force_refresh)
        if not token:
            return None
        headers['Authorization'] = f'Bearer {token}'
        return headers

    api_key = getattr(settings, 'UMAMI_API_KEY', '').strip()
    if not api_key:
        return None
    headers['x-umami-api-key'] = api_key
    return headers


def _umami_request(endpoint, params=None):
    """Best-effort Umami API request with short caching."""
    if not REQUESTS_AVAILABLE:
        return None

    base_url = _umami_base_url()
    website_id = getattr(settings, 'UMAMI_SITE_ID', '').strip()

    if not _umami_is_configured() or not base_url:
        return None

    if _umami_auth_failed():
        return None

    cache_key = f"umami:{website_id}:{endpoint}:{hashlib.sha1(json.dumps(params or {}, sort_keys=True).encode('utf-8')).hexdigest()}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    url = f"{base_url}/websites/{website_id}/{endpoint.lstrip('/')}"
    headers = _umami_request_headers()
    if headers is None:
        return None

    try:
        response = requests.get(url, headers=headers, params=params, timeout=8)
        status = response.status_code
        if status == 401 and _umami_uses_bearer_auth():
            _invalidate_umami_auth_token()
            headers = _umami_request_headers(force_refresh=True)
            if headers is not None:
                response = requests.get(url, headers=headers, params=params, timeout=8)
                status = response.status_code
        if status == 401:
            _mark_umami_auth_failed()
            return None
        response.raise_for_status()
        data = response.json()
        cache.delete(UMAMI_AUTH_FAILURE_CACHE_KEY)
        cache.set(cache_key, data, UMAMI_CACHE_TTL_SECONDS)
        return data
    except requests.exceptions.HTTPError:
        logger.warning(
            'umami request returned http error for url=%s params=%s status=%s body=%s',
            url,
            params,
            status,
            (response.text or '')[:300],
        )
        return None
    except Exception:
        logger.exception('umami request failed for url=%s params=%s', url, params)
        return None


def _umami_requests_parallel(requests_list):
    """Make multiple Umami API requests in parallel."""
    if not requests_list:
        return []

    results = [None] * len(requests_list)
    with ThreadPoolExecutor(max_workers=min(8, len(requests_list))) as executor:
        futures = {
            executor.submit(_umami_request, endpoint, params): index
            for index, (endpoint, params) in enumerate(requests_list)
        }
        for future, index in futures.items():
            try:
                results[index] = future.result()
            except Exception:
                results[index] = None
    return results


def _pdf_view_stats_end(end_date=None):
    """Hour-aligned end time so PDF view cache keys stay stable within each window."""
    if end_date is not None:
        end = end_date
        if timezone.is_naive(end):
            return timezone.make_aware(end)
        return end

    now = timezone.now()
    ts = int(now.timestamp())
    window_seconds = 60 * 60
    window_end_ts = ((ts // window_seconds) + 1) * window_seconds
    return datetime.fromtimestamp(window_end_ts, tz=dt_timezone.utc)


def _pdf_view_cache_keys(slug, start_at, end_at):
    stamp = f"{int(start_at.timestamp() * 1000)}:{int(end_at.timestamp() * 1000)}"
    fresh_key = f"umami:pdf-view-count:fresh:{slug}:{stamp}"
    stale_key = f"umami:pdf-view-count:stale:{slug}:{stamp}"
    return fresh_key, stale_key


def _normalize_umami_path(path):
    path = str(path or '').strip()
    if not path:
        return ''

    # Strip scheme/host if Umami returns a full URL.
    if '://' in path:
        try:
            from urllib.parse import urlparse
            parsed = urlparse(path)
            path = parsed.path or ''
        except Exception:
            pass

    if not path.startswith('/'):
        path = f'/{path}'

    # Umami path metrics may be returned with or without a trailing slash.
    if len(path) > 1:
        path = path.rstrip('/')

    return path


def _fetch_pdf_view_count(slug, start_at, end_at):
    # Kept for compatibility, but the batch path now fetches one request for many slugs.
    return 0


def _fetch_pdf_view_batch_counts(slugs, start_at, end_at):
    unique_slugs = [str(slug).strip() for slug in (slugs or []) if str(slug).strip()]
    if not unique_slugs:
        return {}

    metrics = _umami_request('metrics/expanded', {
        'startAt': int(start_at.timestamp() * 1000),
        'endAt': int(end_at.timestamp() * 1000),
        'type': 'path',
    }) or []

    if isinstance(metrics, dict):
        metrics = [metrics]

    wanted_paths = {}
    for slug in unique_slugs:
        candidates = {
            f'/documents/{slug}/view',
            f'/documents/{slug}/view/',
            f'/documents/{slug}',
            f'/documents/{slug}/',
        }
        for candidate in candidates:
            wanted_paths[_normalize_umami_path(candidate)] = slug

    counts = {slug: 0 for slug in unique_slugs}

    for row in metrics:
        if not isinstance(row, dict):
            continue
        row_name = _normalize_umami_path(
            row.get('name') or row.get('x') or row.get('path') or row.get('pathname') or row.get('url') or ''
        )
        slug = wanted_paths.get(row_name)
        if not slug:
            continue
        counts[slug] = int(row.get('pageviews', 0) or row.get('views', 0) or 0)

    return counts


def _store_pdf_view_batch_counts(slugs, counts, start_at, end_at):
    for slug in slugs or []:
        slug = str(slug).strip()
        if not slug:
            continue
        views = int((counts or {}).get(slug, 0) or 0)
        fresh_key, stale_key = _pdf_view_cache_keys(slug, start_at, end_at)
        cache.set(fresh_key, views, UMAMI_CACHE_TTL_SECONDS)
        cache.set(stale_key, views, UMAMI_CACHE_STALE_TTL_SECONDS)


def _refresh_pdf_view_stats_async(slugs, start_at, end_at):
    unique_slugs = sorted({str(slug).strip() for slug in (slugs or []) if str(slug).strip()})
    if not unique_slugs:
        return

    lock_seed = json.dumps({
        'startAt': int(start_at.timestamp() * 1000),
        'endAt': int(end_at.timestamp() * 1000),
        'slugs': unique_slugs,
    }, sort_keys=True, separators=(',', ':'))
    lock_key = f"umami:pdf-view-refresh-lock:{hashlib.sha1(lock_seed.encode('utf-8')).hexdigest()}"
    if not cache.add(lock_key, 1, UMAMI_REFRESH_LOCK_TTL_SECONDS):
        return

    def _worker():
        try:
            for index in range(0, len(unique_slugs), UMAMI_REFRESH_BATCH_SIZE):
                batch = unique_slugs[index:index + UMAMI_REFRESH_BATCH_SIZE]
                batch_counts = _fetch_pdf_view_batch_counts(batch, start_at, end_at)
                if not batch_counts:
                    logger.warning('umami batch refresh returned no data for batch=%s start=%s end=%s', batch, start_at, end_at)
                    continue
                _store_pdf_view_batch_counts(batch, batch_counts, start_at, end_at)
        finally:
            cache.delete(lock_key)

    threading.Thread(target=_worker, daemon=True).start()


def get_pdf_view_stats(slugs=None, start_date=None, end_date=None, warm_missing=True):
    """Return Umami PDF view counts for the given slugs and a total."""
    start_at = start_date or datetime(2000, 1, 1, tzinfo=dt_timezone.utc)
    end_at = _pdf_view_stats_end(end_date)
    slug_filter = {str(slug).strip() for slug in (slugs or []) if str(slug).strip()}

    if not slug_filter:
        from .models import PDFDocument

        slug_filter = set(
            PDFDocument.objects.filter(is_published=True).values_list('slug', flat=True)
        )

    counts = {}
    total = 0
    missing_slugs = []
    for slug in sorted(slug_filter):
        fresh_key, stale_key = _pdf_view_cache_keys(slug, start_at, end_at)
        cached = cache.get(fresh_key)
        if cached is not None:
            counts[slug] = int(cached or 0)
            total += int(cached or 0)
            continue

        cached = cache.get(stale_key)
        if cached is not None:
            counts[slug] = int(cached or 0)
            total += int(cached or 0)
            missing_slugs.append(slug)
            continue

        missing_slugs.append(slug)

    if warm_missing and missing_slugs:
        # Fetch immediately so the current page can render real counts instead of
        # waiting for the background warmer to finish.
        try:
            fresh_counts = _fetch_pdf_view_batch_counts(missing_slugs, start_at, end_at)
        except Exception:
            fresh_counts = {}

        if fresh_counts:
            _store_pdf_view_batch_counts(missing_slugs, fresh_counts, start_at, end_at)
            for slug in missing_slugs:
                if slug in fresh_counts:
                    counts[slug] = int(fresh_counts.get(slug, 0) or 0)
                    total += int(fresh_counts.get(slug, 0) or 0)
        else:
            _refresh_pdf_view_stats_async(missing_slugs, start_at, end_at)

    return {
        'counts': counts,
        'total': total,
    }


def get_total_pdf_views(start_date=None, end_date=None):
    """Return the total Umami PDF views across all documents."""
    start_at = start_date or datetime(2000, 1, 1, tzinfo=dt_timezone.utc)
    end_at = _pdf_view_stats_end(end_date)
    total_cache_key = (
        "umami:pdf-view-total:"
        f"{int(start_at.timestamp() * 1000)}:{int(end_at.timestamp() * 1000)}"
    )

    cached_total = cache.get(total_cache_key)
    if cached_total is not None:
        return int(cached_total or 0)

    from .models import PDFDocument

    slugs = list(
        PDFDocument.objects.filter(is_published=True).values_list('slug', flat=True)
    )
    if not slugs:
        cache.set(total_cache_key, 0, UMAMI_CACHE_TTL_SECONDS)
        return 0

    total = 0
    for index in range(0, len(slugs), UMAMI_REFRESH_BATCH_SIZE):
        batch = slugs[index:index + UMAMI_REFRESH_BATCH_SIZE]
        batch_counts = _fetch_pdf_view_batch_counts(batch, start_at, end_at)
        if not batch_counts:
            logger.warning('umami total refresh returned no data for batch=%s start=%s end=%s', batch, start_at, end_at)
            continue
        _store_pdf_view_batch_counts(batch, batch_counts, start_at, end_at)
        total += sum(int(batch_counts.get(slug, 0) or 0) for slug in batch)

    cache.set(total_cache_key, total, UMAMI_CACHE_TTL_SECONDS)
    return total


def get_total_unique_visitors(start_date=None, end_date=None):
    """Return the total Umami unique visitors across all documents."""
    start_at = start_date or datetime(2000, 1, 1, tzinfo=dt_timezone.utc)
    end_at = _pdf_view_stats_end(end_date)
    visitors_cache_key = (
        "umami:unique-visitors:"
        f"{int(start_at.timestamp() * 1000)}:{int(end_at.timestamp() * 1000)}"
    )

    cached_visitors = cache.get(visitors_cache_key)
    if cached_visitors is not None:
        return int(cached_visitors or 0)

    stats = _umami_request('stats', {
        'startAt': int(start_at.timestamp() * 1000),
        'endAt': int(end_at.timestamp() * 1000),
    }) or {}

    visitors = int(stats.get('visitors', 0) or 0) if isinstance(stats, dict) else 0
    cache.set(visitors_cache_key, visitors, UMAMI_CACHE_TTL_SECONDS)
    return visitors


def attach_pdf_view_counts(documents, *, warm_missing=False):
    """Attach Umami PDF view counts to document instances as pdf_view_count."""
    if not documents:
        return documents

    slugs = [doc.slug for doc in documents]
    stats = get_pdf_view_stats(slugs, warm_missing=warm_missing) or {'counts': {}}
    counts = stats.get('counts', {}) or {}
    for doc in documents:
        doc.pdf_view_count = int(counts.get(doc.slug, 0) or 0)
    return documents


def sort_documents_by_pdf_views(queryset, *, descending=True):
    """Order a document queryset by Umami PDF view counts."""
    slug_pks = list(queryset.values_list('slug', 'pk'))
    if not slug_pks:
        return queryset

    slugs = [slug for slug, _ in slug_pks]
    stats = get_pdf_view_stats(slugs, warm_missing=True) or {'counts': {}}
    counts = stats.get('counts', {}) or {}
    ordered_pks = [
        pk
        for _, pk in sorted(
            slug_pks,
            key=lambda item: int(counts.get(item[0], 0) or 0),
            reverse=descending,
        )
    ]
    ordering = Case(
        *[When(pk=pk, then=position) for position, pk in enumerate(ordered_pks)],
        default=len(ordered_pks),
    )
    return queryset.order_by(ordering)


def parse_donor_entry_value(donor_entry):
    """Parse donor entry to extract type, display_name, username, and avatar."""
    import re

    donor_entry = donor_entry.strip()

    if donor_entry.startswith('Telegram:'):
        match = re.search(r'\(([^)]+)\)$', donor_entry)
        if match:
            username = match.group(1)
            display_name = donor_entry.replace('Telegram:', '').replace(f'({username})', '').strip()
            return {
                'type': 'telegram',
                'display_name': display_name,
                'username': username,
                'avatar': None,
            }
        name = donor_entry.replace('Telegram:', '').strip()
        username = re.sub(r'[^a-zA-Z0-9_]', '', name.lstrip('@').replace(' ', ''))
        return {
            'type': 'telegram',
            'display_name': name,
            'username': username,
            'avatar': None,
        }
    if donor_entry.startswith('Discord:'):
        parts = donor_entry.replace('Discord:', '').strip().split('|')
        display_name = parts[0].strip()
        discord_id = parts[2].strip() if len(parts) > 2 else ''
        if not discord_id and display_name.isdigit():
            discord_id = display_name
            display_name = ''
        return {
            'type': 'discord',
            'display_name': display_name,
            'username': discord_id,
            'avatar': None,
        }
    return {
        'type': 'unknown',
        'display_name': donor_entry,
        'username': donor_entry,
        'avatar': None,
    }


def vault_contributor_key(account_type, account_id):
    if account_type == 'telegram':
        return f"telegram:{(account_id or '').lower()}"
    return f"discord:{account_id or ''}"


def get_vault_contributor_avatar_overrides():
    from .models_physical import VaultContributorProfile

    overrides = {}
    for profile in VaultContributorProfile.objects.exclude(avatar='').exclude(avatar__isnull=True):
        if not profile.avatar:
            continue
        try:
            overrides[vault_contributor_key(profile.account_type, profile.account_id)] = profile.avatar.url
        except ValueError:
            continue
    return overrides


def resolve_vault_contributor_avatar_url(account_type, username, *, fallback_avatar=None, overrides=None):
    if overrides is None:
        overrides = get_vault_contributor_avatar_overrides()
    key = vault_contributor_key(account_type, username)
    if key in overrides:
        return overrides[key]
    if account_type == 'telegram' and username:
        return f'/internal/api/telegram-avatar/{username}'
    if account_type == 'discord' and username:
        return f'/internal/api/discord-avatar/{username}'
    if fallback_avatar and account_type != 'discord':
        return fallback_avatar
    return None


def discord_avatar_proxy_path(discord_id):
    if not discord_id:
        return ''
    return f'/internal/api/discord-avatar/{discord_id}'


def discord_avatar_cdn_url(discord_id, user_data):
    if user_data and user_data.get('avatar'):
        return f"https://cdn.discordapp.com/avatars/{discord_id}/{user_data['avatar']}.png?size=128"
    try:
        discriminator = int(user_data.get('discriminator', '0') or '0') if user_data else 0
    except (TypeError, ValueError):
        discriminator = 0
    return f"https://cdn.discordapp.com/embed/avatars/{discriminator % 5}.png"


def fetch_discord_user_data(discord_id, *, force_refresh=False):
    """Fetch Discord user profile data, with optional cache bypass."""
    from django.conf import settings
    from django.core.cache import cache

    cache_key = f'discord_user_{discord_id}'
    if not force_refresh:
        cached_data = cache.get(cache_key)
        if cached_data:
            return cached_data
        local_user_data = load_site_json('discord_users', discord_id, 60 * 60 * 24)
        if local_user_data:
            cache.set(cache_key, local_user_data, 3600)
            return local_user_data

    bot_token = getattr(settings, 'DISCORD_BOT_TOKEN', None)
    if not bot_token or not REQUESTS_AVAILABLE:
        return None

    headers = {
        'Authorization': f'Bot {bot_token}',
        'Content-Type': 'application/json',
    }
    try:
        response = requests.get(
            f'https://discord.com/api/v10/users/{discord_id}',
            headers=headers,
            timeout=5,
        )
        if response.status_code != 200:
            return None
        user_data = response.json()
    except Exception:
        return None

    cache.set(cache_key, user_data, 3600)
    store_site_json('discord_users', discord_id, user_data)
    ensure_registry('discord_users', discord_id)
    return user_data


def discord_profile_display_name(user_data, *, fallback=''):
    if not user_data:
        return fallback
    return (user_data.get('global_name') or user_data.get('username') or fallback).strip()


def prefetch_discord_user_data(discord_ids):
    profiles = {}
    for discord_id in discord_ids:
        if not discord_id:
            continue
        user_data = fetch_discord_user_data(discord_id)
        if user_data:
            profiles[discord_id] = user_data
    return profiles


def resolve_vault_contributor_display_name(account_type, account_id, *, fallback=None, discord_users=None):
    fallback = (fallback or account_id or '').strip()
    if account_type != 'discord' or not account_id:
        return fallback
    user_data = (discord_users or {}).get(account_id)
    if user_data is None:
        user_data = fetch_discord_user_data(account_id)
    return discord_profile_display_name(user_data, fallback=fallback)


def collect_vault_contributors(*, exclude_usernames=None):
    """Return unique contributors parsed from The Vault donator entries."""
    from .models_physical import PhysicalInventoryItem

    excluded = {u.lower() for u in (exclude_usernames or [])}
    contributors = {}
    donator_rows = (
        PhysicalInventoryItem.objects
        .exclude(donators='')
        .values_list('donators', flat=True)
    )
    for donators in donator_rows:
        for entry in donators.split(','):
            entry = entry.strip()
            if not entry:
                continue
            parsed = parse_donor_entry_value(entry)
            donor_type = parsed['type']
            if donor_type not in ('telegram', 'discord'):
                continue
            username = (parsed.get('username') or '').strip()
            display_name = (parsed.get('display_name') or username or entry).strip()
            if donor_type == 'telegram' and username.lower() in excluded:
                continue
            if donor_type == 'telegram':
                key = f"telegram:{username.lower()}"
            else:
                key = f"discord:{username}"
            if key not in contributors:
                contributors[key] = {
                    'type': donor_type,
                    'display_name': display_name,
                    'username': username,
                    'avatar': parsed.get('avatar'),
                    'contributions': 0,
                }
            contributors[key]['contributions'] += 1

    avatar_overrides = get_vault_contributor_avatar_overrides()
    result = list(contributors.values())
    discord_users = prefetch_discord_user_data(
        {contributor['username'] for contributor in result if contributor['type'] == 'discord' and contributor['username']}
    )
    for contributor in result:
        if contributor['type'] == 'discord':
            user_data = discord_users.get(contributor['username'])
            contributor['display_name'] = resolve_vault_contributor_display_name(
                contributor['type'],
                contributor['username'],
                fallback=contributor.get('display_name'),
                discord_users=discord_users,
            )
            contributor['discord_handle'] = (user_data or {}).get('username') or ''
        contributor['avatar_url'] = resolve_vault_contributor_avatar_url(
            contributor['type'],
            contributor['username'],
            fallback_avatar=contributor.get('avatar'),
            overrides=avatar_overrides,
        )
    result.sort(
        key=lambda c: (
            -c['contributions'],
            (c['display_name'] or c['username']).lower(),
        )
    )
    return result

