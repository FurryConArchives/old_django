import base64
import functools
import hashlib
import io
import json
import logging
import os
import random
import re
import subprocess
import tempfile
import threading
import time
import uuid
from collections import OrderedDict, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from functools import lru_cache

# Third-party imports
import fitz  # PyMuPDF
import geonamescache
import pytesseract
import pytz
import requests
from bs4 import BeautifulSoup
from dateutil import parser as dateparser
from difflib import SequenceMatcher
from PIL import Image
from pypdf import PdfReader
from pdf2image import convert_from_path
from reportlab.lib import colors
from urllib.parse import urlparse
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from timezonefinder import TimezoneFinder

# Django core
from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.core.paginator import Paginator
from django.db.models import Count, F, Max, Q, Sum, Value
import csv
from django.db.models.functions import Concat, MD5, TruncHour, Coalesce
from django.http import FileResponse, Http404, HttpResponse, JsonResponse, QueryDict
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify
from django.views.decorators.cache import cache_page
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET
from django.views.decorators.vary import vary_on_headers

# Local app imports
from .forms import CategoryForm, FaqEntryForm, PDFUploadForm, SiteBannerForm
from .models import Category, Tag, DiscordProfile, DownloadLog, FaqEntry, KoFiDonation, PDFDocument, SiteBanner
from .models_physical import PhysicalInventoryItem
from .utils import (
    _umami_is_configured,
    _umami_request,
    _umami_requests_parallel,
    attach_pdf_view_counts,
    get_pdf_view_stats,
    get_total_pdf_views,
    local_path_for_field_file,
    sort_documents_by_pdf_views,
)

# Country code to full name mapping for Umami analytics
COUNTRY_CODE_MAP = {
    'US': 'United States', 'GB': 'United Kingdom', 'CA': 'Canada', 'AU': 'Australia',
    'DE': 'Germany', 'FR': 'France', 'IT': 'Italy', 'ES': 'Spain', 'NL': 'Netherlands',
    'SE': 'Sweden', 'NO': 'Norway', 'DK': 'Denmark', 'FI': 'Finland', 'PL': 'Poland',
    'CZ': 'Czech Republic', 'RO': 'Romania', 'GR': 'Greece', 'PT': 'Portugal', 'BE': 'Belgium',
    'AT': 'Austria', 'CH': 'Switzerland', 'IE': 'Ireland', 'HU': 'Hungary', 'SK': 'Slovakia',
    'HR': 'Croatia', 'SL': 'Slovenia', 'LT': 'Lithuania', 'LV': 'Latvia', 'EE': 'Estonia',
    'CY': 'Cyprus', 'LU': 'Luxembourg', 'MT': 'Malta', 'BG': 'Bulgaria', 'RS': 'Serbia',
    'BA': 'Bosnia and Herzegovina', 'ME': 'Montenegro', 'MK': 'North Macedonia', 'AL': 'Albania',
    'JP': 'Japan', 'CN': 'China', 'KR': 'South Korea', 'TW': 'Taiwan', 'HK': 'Hong Kong',
    'SG': 'Singapore', 'MY': 'Malaysia', 'TH': 'Thailand', 'VN': 'Vietnam', 'PH': 'Philippines',
    'ID': 'Indonesia', 'IN': 'India', 'BD': 'Bangladesh', 'PK': 'Pakistan', 'IR': 'Iran',
    'SA': 'Saudi Arabia', 'AE': 'United Arab Emirates', 'IL': 'Israel', 'EG': 'Egypt', 'ZA': 'South Africa',
    'MX': 'Mexico', 'BR': 'Brazil', 'AR': 'Argentina', 'CL': 'Chile', 'CO': 'Colombia',
    'PE': 'Peru', 'VE': 'Venezuela', 'RU': 'Russia', 'UA': 'Ukraine', 'TR': 'Turkey',
    'NZ': 'New Zealand', 'SG': 'Singapore', 'TH': 'Thailand', 'MY': 'Malaysia',
    'Sd': 'Sudan', 'ZW': 'Zimbabwe', 'KE': 'Kenya', 'NG': 'Nigeria', 'GH': 'Ghana',
    'NL': 'Netherlands', 'BE': 'Belgium', 'NZ': 'New Zealand'
}
COUNTRY_NAME_TO_CODE = {name.lower(): code for code, name in COUNTRY_CODE_MAP.items()}

US_STATE_CODE_TO_NAME = {
    'AL': 'Alabama', 'AK': 'Alaska', 'AZ': 'Arizona', 'AR': 'Arkansas', 'CA': 'California',
    'CO': 'Colorado', 'CT': 'Connecticut', 'DE': 'Delaware', 'FL': 'Florida', 'GA': 'Georgia',
    'HI': 'Hawaii', 'ID': 'Idaho', 'IL': 'Illinois', 'IN': 'Indiana', 'IA': 'Iowa',
    'KS': 'Kansas', 'KY': 'Kentucky', 'LA': 'Louisiana', 'ME': 'Maine', 'MD': 'Maryland',
    'MA': 'Massachusetts', 'MI': 'Michigan', 'MN': 'Minnesota', 'MS': 'Mississippi', 'MO': 'Missouri',
    'MT': 'Montana', 'NE': 'Nebraska', 'NV': 'Nevada', 'NH': 'New Hampshire', 'NJ': 'New Jersey',
    'NM': 'New Mexico', 'NY': 'New York', 'NC': 'North Carolina', 'ND': 'North Dakota', 'OH': 'Ohio',
    'OK': 'Oklahoma', 'OR': 'Oregon', 'PA': 'Pennsylvania', 'RI': 'Rhode Island', 'SC': 'South Carolina',
    'SD': 'South Dakota', 'TN': 'Tennessee', 'TX': 'Texas', 'UT': 'Utah', 'VT': 'Vermont',
    'VA': 'Virginia', 'WA': 'Washington', 'WV': 'West Virginia', 'WI': 'Wisconsin', 'WY': 'Wyoming',
    'DC': 'District of Columbia'
}

CA_PROVINCE_CODE_TO_NAME = {
    'AB': 'Alberta', 'BC': 'British Columbia', 'MB': 'Manitoba', 'NB': 'New Brunswick',
    'NL': 'Newfoundland and Labrador', 'NS': 'Nova Scotia', 'NT': 'Northwest Territories',
    'NU': 'Nunavut', 'ON': 'Ontario', 'PE': 'Prince Edward Island', 'QC': 'Quebec',
    'SK': 'Saskatchewan', 'YT': 'Yukon'
}

US_STATE_NAME_TO_CODE = {
    name.lower(): code for code, name in US_STATE_CODE_TO_NAME.items()
}

CA_PROVINCE_NAME_TO_CODE = {
    name.lower(): code for code, name in CA_PROVINCE_CODE_TO_NAME.items()
}

REGION_NAME_TO_CODE = {
    'US': US_STATE_NAME_TO_CODE,
    'CA': CA_PROVINCE_NAME_TO_CODE,
}
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, KeepTogether
from reportlab.lib.enums import TA_LEFT
from xml.sax.saxutils import escape
from django.core.cache import cache
from django.conf import settings
from .scheduler import scheduler
from .models_sitevisit import ScheduleAutoFetchSetting
from .site_cache import (
    deserialize_consurf_event,
    ensure_registry,
    get_registry,
    load_binary as load_site_binary,
    load_json as load_site_json,
    serialize_consurf_event,
    store_binary as store_site_binary,
    store_json as store_site_json,
)
import requests
from requests.adapters import HTTPAdapter
from django.utils import timezone
import csv
try:
    import openpyxl
    from openpyxl.utils import get_column_letter
    from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
except Exception:
    openpyxl = None

# Module logger for debugging external event fetches
logger = logging.getLogger(__name__)


def _format_seconds(seconds_value):
    try:
        total_seconds = int(seconds_value or 0)
    except Exception:
        total_seconds = 0

    days, remainder = divmod(total_seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, _ = divmod(remainder, 60)

    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m"
    return f"{total_seconds}s"


def _clear_umami_cache_entries():
    """Best-effort removal of Umami-related cache entries.

    Returns number of removed keys when introspection is available,
    otherwise returns 0.
    """
    removed = 0
    try:
        # Works for locmem backend where keys are introspectable.
        backend = cache._connections[cache._alias]
        store = getattr(backend, '_cache', None)
        if hasattr(store, 'keys'):
            keys = list(store.keys())
            for key in keys:
                key_str = str(key)
                if 'umami:' in key_str:
                    try:
                        store.pop(key, None)
                        removed += 1
                    except Exception:
                        pass
            return removed
    except Exception:
        pass

    # Other backends may not support key iteration/pattern delete safely.
    return removed


def _register_pdf_unicode_font():
    return 'Helvetica'


def _register_fontawesome_free_solid_font():
    """Register Font Awesome Free Solid for PDF icon glyphs when available."""
    try:
        import fontawesomefree
        base_dir = os.path.join(os.path.dirname(fontawesomefree.__file__), 'static', 'fontawesomefree', 'webfonts')
        font_path = os.path.join(base_dir, 'fa-solid-900.ttf')
        if os.path.exists(font_path):
            font_name = 'FontAwesomeFreeSolid'
            pdfmetrics.registerFont(TTFont(font_name, font_path))
            return font_name
    except Exception:
        pass
    return None


PDF_UNICODE_FONT = _register_pdf_unicode_font()
FA_SOLID_FONT = _register_fontawesome_free_solid_font()


def _fa_icon(codepoint, fallback=''):
    """Render a Font Awesome icon as inline paragraph markup."""
    if FA_SOLID_FONT:
        return f'<font name="{FA_SOLID_FONT}">{chr(codepoint)}</font>'
    return fallback

# Reuse a single requests Session to benefit from connection pooling
requests_session = requests.Session()
requests_session.headers.update({'User-Agent': 'FurryConArchives/1.0'})
try:
    _shared_adapter = HTTPAdapter(pool_connections=50, pool_maxsize=50, max_retries=2)
    requests_session.mount('http://', _shared_adapter)
    requests_session.mount('https://', _shared_adapter)
except Exception:
    pass

GUIDEBOOK_API_TIMEOUT = 10
GUIDEBOOK_PAGE_TIMEOUT = 10
GUIDEBOOK_SCRIPT_TIMEOUT = 5
GUIDEBOOK_GEOCODE_TIMEOUT = 3
GUIDEBOOK_MAX_SCRIPT_FETCH = 3
GUIDEBOOK_STATE_BLOB_MAX_SIZE = 2 * 1024 * 1024


def _normalize_guidebook_slug(value):
    if not value:
        return ''
    slug_value = str(value).strip()
    if not slug_value:
        return ''
    if 'guidebook.com' in slug_value or '/guides/' in slug_value:
        if '/guides/' in slug_value:
            slug_value = slug_value.split('/guides/', 1)[1].split('/', 1)[0]
        else:
            parsed = urlparse(slug_value)
            path = (parsed.path or '').strip('/')
            if path:
                parts = [part for part in path.split('/') if part]
                if parts:
                    slug_value = parts[-1]
                    if slug_value.lower() == 'details' and len(parts) >= 2:
                        slug_value = parts[-2]
    return slug_value.strip()


def _guidebook_missing_response(guide_slug, message='guidebook_slug_missing_or_removed', status=200, api_status=None, api_detail=None, bundle_url=None, bundle_status=None):
    payload = {
        'error': message,
        'guide_slug': guide_slug,
        'message': message,
    }
    if api_status is not None:
        payload['api_status'] = api_status
    if api_detail is not None:
        payload['api_detail'] = api_detail
    if bundle_url is not None:
        payload['bundle_url'] = bundle_url
    if bundle_status is not None:
        payload['bundle_status'] = bundle_status
    return JsonResponse(payload, status=status)


def fetch_schedule():
    try:
        from django.conf import settings as dj_settings
        schedules_dir = os.path.join(getattr(dj_settings, 'BASE_DIR', os.path.dirname(__file__)), 'schedules')
        os.makedirs(schedules_dir, exist_ok=True)
        from archive.models import Schedule
        qs = Schedule.objects.filter(deleted=False)
        refreshed_count = 0
        error_count = 0
        for sched in qs:
            try:
                events = []
                error = None
                def _load_local_events():
                    if sched.events_json:
                        try:
                            parsed = json.loads(sched.events_json)
                            if isinstance(parsed, list):
                                return parsed
                        except Exception:
                            pass

                    filename = f"{sched.type}_{sched.slug}_{sched.year or 'none'}.json".replace(' ', '_')
                    out_path = os.path.join(schedules_dir, filename)
                    if os.path.exists(out_path):
                        try:
                            with open(out_path, 'r', encoding='utf-8') as fh:
                                payload = json.load(fh)
                            loaded_events = payload.get('events') if isinstance(payload, dict) else None
                            if isinstance(loaded_events, list):
                                return loaded_events
                        except Exception:
                            pass

                    try:
                        repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
                        fallback_dir = os.path.join(repo_root, 'schedules')
                        if os.path.isdir(fallback_dir):
                            for fn in os.listdir(fallback_dir):
                                if not fn.lower().endswith('.json'):
                                    continue
                                if sched.slug and sched.slug.lower().replace('-', '') in fn.lower().replace('-', ''):
                                    try:
                                        with open(os.path.join(fallback_dir, fn), 'r', encoding='utf-8') as fh:
                                            payload = json.load(fh)
                                        loaded_events = payload.get('events') if isinstance(payload, dict) else None
                                        if isinstance(loaded_events, list):
                                            return loaded_events
                                    except Exception:
                                        continue
                    except Exception:
                        pass

                    return []

                if sched.type == 'guidebook' and sched.slug:
                    # Build minimal request for api_guidebook
                    from django.http import HttpRequest
                    req = HttpRequest()
                    req.META = {'SERVER_NAME': 'localhost', 'HTTP_HOST': 'localhost', 'wsgi.url_scheme': 'http'}
                    req.method = 'GET'
                    guide_slug = _normalize_guidebook_slug(sched.slug)
                    req.GET = QueryDict('', mutable=True)
                    req.GET['slug'] = guide_slug
                    resp = api_guidebook(req, guide_slug)
                    try:
                        data = json.loads(resp.content)
                    except Exception:
                        data = {}
                    if data.get('error'):
                        error = data.get('error')
                        events = []
                    else:
                        events = data.get('events', []) or []
                elif sched.type == 'sessionize' and sched.slug:
                    try:
                        slug_val = str(sched.slug).strip()
                        if slug_val.startswith('http://') or slug_val.startswith('https://'):
                            url = slug_val
                        else:
                            host = slug_val if '.' in slug_val else f"{slug_val}.sessionize.com"
                            url = f'https://{host}/api/schedule'

                        resp = requests.get(url, timeout=15)
                        if resp.status_code == 200:
                            payload = resp.json()
                            source_events = None
                            for k in ('Events', 'events', 'sessions', 'Sessions'):
                                if isinstance(payload, dict) and k in payload and isinstance(payload[k], list):
                                    source_events = payload[k]
                                    break
                            if source_events is None:
                                if isinstance(payload, dict) and 'schedule' in payload and isinstance(payload['schedule'], list):
                                    source_events = payload['schedule']
                                elif isinstance(payload, list):
                                    source_events = payload

                            if source_events:
                                speakers_map = {}
                                rooms_map = {}
                                if isinstance(payload, dict):
                                    for key in ('Speakers', 'speakers', 'People'):
                                        if key in payload and isinstance(payload[key], list):
                                            for s in payload[key]:
                                                sid = s.get('id')
                                                name = s.get('name') or s.get('fullName')
                                                if sid and name:
                                                    speakers_map[str(sid)] = name
                                            break
                                    for key in ('Rooms', 'rooms'):
                                        if key in payload and isinstance(payload[key], list):
                                            for r in payload[key]:
                                                rid = r.get('id')
                                                name = r.get('name')
                                                if rid and name:
                                                    rooms_map[str(rid)] = name
                                            break

                                for ev in source_events:
                                    title = ev.get('title') or ev.get('name') or ev.get('Title') or ''
                                    desc = ev.get('description') or ev.get('abstract') or ''
                                    start = ev.get('startsAt') or ev.get('start') or ev.get('StartsAt')
                                    end = ev.get('endsAt') or ev.get('end') or ev.get('EndsAt')
                                    room = None
                                    r = ev.get('room') or ev.get('roomId') or ev.get('room_id')
                                    if isinstance(r, dict):
                                        room = r.get('name')
                                    elif r is not None:
                                        room = rooms_map.get(str(r)) or str(r)
                                    spks = []
                                    s = ev.get('speakers') or ev.get('speakerIds') or ev.get('speaker_ids')
                                    if isinstance(s, list):
                                        for sid in s:
                                            spks.append(speakers_map.get(str(sid)) or str(sid))

                                    organizer = None
                                    for ok in ('organizers', 'organizer', 'owners', 'owner', 'presenters', 'presenter', 'host', 'hosts'):
                                        val = ev.get(ok)
                                        if val:
                                            if isinstance(val, list):
                                                mapped = []
                                                for it in val:
                                                    if isinstance(it, (int, str)):
                                                        mapped.append(speakers_map.get(str(it)) or str(it))
                                                    elif isinstance(it, dict):
                                                        mapped.append(it.get('name') or it.get('fullName') or str(it))
                                                if mapped:
                                                    organizer = mapped[0]
                                                    break
                                            elif isinstance(val, dict):
                                                organizer = val.get('name') or val.get('fullName') or None
                                                break
                                            else:
                                                organizer = str(val)
                                                break

                                    if not organizer and spks:
                                        organizer = spks[0]

                                    events.append({
                                        'id': ev.get('id'),
                                        'name': title,
                                        'description': desc,
                                        'start': start,
                                        'end': end,
                                        'speakers': spks,
                                        'location': room,
                                        'organizer': organizer,
                                    })
                        else:
                            error = f'Failed to fetch Sessionize JSON: {resp.status_code} (URL: {url})'
                    except Exception as e:
                        error = f'Error fetching Sessionize data: {str(e)}'
                elif sched.type == 'pretalx' and sched.slug:
                    from archive.pretalx_client import fetch_pretalx_events
                    events, error = fetch_pretalx_events(sched)
                elif sched.type == 'sched' and sched.slug and sched.year:
                    events, error = fetch_sched_events(sched)
                elif sched.type == 'local':
                    events = _load_local_events()
                    if not events:
                        error = 'local_schedule_missing'
                else:
                    events = _load_local_events()
                    if not events:
                        error = 'unsupported_schedule_type'

                if events:
                    try:
                        sched.events_json = json.dumps(events)
                        sched.error = ''
                        sched.save()
                        filename = f"{sched.type}_{sched.slug}_{sched.year or 'none'}.json".replace(' ', '_')
                        out_path = os.path.join(schedules_dir, filename)
                        payload = {'slug': sched.slug, 'type': sched.type, 'year': sched.year, 'count': len(events), 'events': events}
                        if os.path.exists(out_path):
                            logger.info('fetch_schedule: not writing %s because it already exists', out_path)
                        else:
                            with open(out_path, 'w', encoding='utf-8') as fh:
                                json.dump(payload, fh, ensure_ascii=False, indent=2)
                        refreshed_count += 1
                        logger.info('fetch_schedule: refreshed %s %s (%s events)', sched.type, sched.slug, len(events))
                    except Exception as e:
                        sched.error = f'write_error:{e}'
                        sched.save()
                        error_count += 1
                        logger.exception('fetch_schedule: write failed for %s %s', sched.type, sched.slug)
                else:
                    sched.error = error or 'no_events'
                    sched.save()
                    error_count += 1
                    logger.warning('fetch_schedule: no events for %s %s (%s)', sched.type, sched.slug, error or 'no_events')
            except Exception as e:
                error_count += 1
                logger.exception('fetch_schedule: failed for %s: %s', getattr(sched, 'slug', '<unknown>'), e)
                try:
                    sched.error = f'fetch_exception:{e}'
                    sched.save()
                except Exception:
                    pass
        logger.info('fetch_schedule: completed scheduled refresh run (refreshed=%s errors=%s total=%s)', refreshed_count, error_count, qs.count())
    except Exception as e:
        logger.exception('fetch_schedule: top-level failure: %s', e)


_auto_fetch_lock = threading.Lock()


def _run_auto_fetch_cycle(source):
    if not _auto_fetch_lock.acquire(blocking=False):
        logger.info('%s: auto-fetch already running; skipping overlapping refresh', source)
        return

    try:
        logger.info('%s: starting background refresh cycle', source)
        fetch_schedule()
        refresh_consurf_cache()
        logger.info('%s: background refresh cycle finished', source)
    except Exception:
        logger.exception('%s: background refresh cycle failed', source)
    finally:
        _auto_fetch_lock.release()


def queue_auto_fetch_refresh(source='auto_fetch'):
    threading.Thread(target=_run_auto_fetch_cycle, args=(source,), daemon=True).start()

def schedule_auto_fetch_job():
    setting = ScheduleAutoFetchSetting.objects.first()
    if setting and setting.enabled:
        logger.info('schedule_auto_fetch_job: enabled setting found; refreshing schedules and Consurf cache in background')
        queue_auto_fetch_refresh('schedule_auto_fetch_job')
    else:
        logger.info('schedule_auto_fetch_job: auto-fetch disabled or missing setting; skipping run')

def start_auto_fetch():
    if not scheduler.get_job('auto_fetch_job'):
        scheduler.add_job(schedule_auto_fetch_job, 'interval', minutes=30, id='auto_fetch_job', replace_existing=True)
        logger.info('start_auto_fetch: registered auto_fetch_job every 30 minutes')
    else:
        logger.info('start_auto_fetch: auto_fetch_job already registered')

def stop_auto_fetch():
    if scheduler.get_job('auto_fetch_job'):
        scheduler.remove_job('auto_fetch_job')
        logger.info('stop_auto_fetch: removed auto_fetch_job')
    else:
        logger.info('stop_auto_fetch: auto_fetch_job was not registered')
from django.views.decorators.http import require_POST
from .models import PDFDocument, Category, DownloadLog, Schedule
from django.db.models import Q
def schedule_list(request):
    category_slug = request.GET.get('category')
    category = get_object_or_404(Category, slug=category_slug)
    # Get all schedules for this convention/category
    schedules = Schedule.objects.filter(category=category).order_by('-year')
    # Optionally filter for years with schedule data
    # If Schedule has a 'data' or similar field, filter for those with data
    # For now, show all
    context = {
        'convention': category,
        'schedules': schedules,
    }
    return render(request, 'archive/schedule_list.html', context)


@require_GET
def schedule_list_csv(request):
    """
    Return a CSV download of all schedule events for the given category (query param `category` required).
    """
    category_slug = request.GET.get('category')
    category = get_object_or_404(Category, slug=category_slug)
    schedules = Schedule.objects.filter(category=category).order_by('-year')

    # Build CSV in-memory
    output = io.StringIO()
    writer = csv.writer(output)
    # Header
    writer.writerow([
        'schedule_year', 'event_id', 'title', 'start_local', 'end_local', 'start_utc', 'end_utc', 'timezone', 'location', 'room', 'description'
    ])

    for sched in schedules:
        try:
            events = json.loads(sched.events_json) if sched.events_json else []
        except Exception:
            events = []

        # Normalize times if possible
        try:
            events = _normalize_event_times(events)
        except Exception:
            pass

        for ev in events:
            writer.writerow([
                sched.year,
                ev.get('id') or ev.get('event_id') or '',
                ev.get('title') or ev.get('name') or '',
                ev.get('start') or '',
                ev.get('end') or '',
                ev.get('start_utc') or '',
                ev.get('end_utc') or '',
                ev.get('timezone') or '',
                ev.get('location') or '',
                ev.get('room') or ev.get('venue') or '',
                ev.get('description') or ev.get('desc') or ''
            ])

    csv_data = output.getvalue()
    output.close()
    filename = f"{category.slug}-schedules.csv"
    response = HttpResponse(csv_data, content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


@require_GET
def schedule_csv(request, slug):
    """
    Return a CSV download for a single schedule identified by slug (or category-year slug).
    """
    resolved = _resolve_sched_slug(slug, request=request)
    sched_slug = resolved.get('sched_slug')

    # Quick synchronous check: ensure events exist for this slug so we fail fast
    try:
        schedule_obj = None
        events_preview = []
        import re as _re
        m = _re.match(r'([a-z0-9\-]+)-(\d{4})$', sched_slug)
        if m:
            convention_slug, year = m.groups()
            try:
                # Prefer exact slug match with year, then slug-prefix, then convention_name
                schedule_obj = Schedule.objects.filter(slug__iexact=sched_slug, year=year, deleted=False).first()
                if not schedule_obj:
                    schedule_obj = Schedule.objects.filter(slug__istartswith=convention_slug, year=year, deleted=False).first()
                if not schedule_obj:
                    # try matching convention_name heuristically
                    conv_like = convention_slug.replace('-', ' ')
                    schedule_obj = Schedule.objects.filter(convention_name__icontains=conv_like, year=year, deleted=False).first()
                if schedule_obj:
                    events_preview = json.loads(schedule_obj.events_json) if schedule_obj.events_json else []
            except Exception:
                schedule_obj = None

        if not events_preview:
            try:
                schedule_obj = Schedule.objects.filter(slug__iexact=sched_slug, deleted=False).first()
                if schedule_obj:
                    events_preview = json.loads(schedule_obj.events_json) if schedule_obj.events_json else []
            except Exception:
                schedule_obj = None

        if not events_preview:
            # try local schedules dir
            try:
                schedules_dir = os.path.join(settings.BASE_DIR, 'schedules')
                if os.path.isdir(schedules_dir):
                    for fn in os.listdir(schedules_dir):
                        if not fn.lower().endswith('.json'):
                            continue
                        fp = os.path.join(schedules_dir, fn)
                        try:
                            with open(fp, 'r', encoding='utf-8') as fh:
                                payload = json.load(fh)
                        except Exception:
                            continue
                        slug_val = str(payload.get('slug') or '').lower().replace('-', '')
                        if _slug_match((sched_slug or '').lower().replace('-', ''), slug_val):
                            events_preview = payload.get('events', []) or []
                            break
            except Exception:
                pass

        if not events_preview:
            logger.info('schedule_export_start: no events found for slug %s', sched_slug)
            return JsonResponse({'error': 'no_events', 'message': f'no events found for {sched_slug}'}, status=404)
    except Exception as e:
        logger.exception('schedule_export_start quick-check failed for %s: %s', sched_slug, e)
        return JsonResponse({'error': 'lookup_failed', 'message': str(e)}, status=500)

    # Try to parse convention and year from slug
    import re
    m = re.match(r'([a-z0-9\-]+)-(\d{4})$', sched_slug)
    if m:
        convention_slug, year = m.groups()
        try:
            # Prefer exact slug/year, then slug-prefix/year, then convention_name/year
            schedule_obj = Schedule.objects.filter(slug__iexact=sched_slug, year=year, deleted=False).first()
            if not schedule_obj:
                schedule_obj = Schedule.objects.filter(slug__istartswith=convention_slug, year=year, deleted=False).first()
            if not schedule_obj:
                conv_like = convention_slug.replace('-', ' ')
                schedule_obj = Schedule.objects.filter(convention_name__icontains=conv_like, year=year, deleted=False).first()
            if schedule_obj:
                try:
                    events = json.loads(schedule_obj.events_json) if schedule_obj.events_json else []
                except Exception:
                    events = []
        except Exception:
            schedule_obj = None
            events = []
        except Exception:
            schedule_obj = None

    # If not found by year, try by slug field directly
        if not events:
            try:
                schedule_obj = Schedule.objects.filter(slug__iexact=sched_slug, deleted=False).first()
                if schedule_obj:
                    events = json.loads(schedule_obj.events_json) if schedule_obj.events_json else []
            except Exception:
                schedule_obj = None

    # Last resort: search local schedules JSON files
    if not events:
        try:
            schedules_dir = os.path.join(settings.BASE_DIR, 'schedules')
            if os.path.isdir(schedules_dir):
                for fn in os.listdir(schedules_dir):
                    if not fn.lower().endswith('.json'):
                        continue
                    fp = os.path.join(schedules_dir, fn)
                    try:
                        with open(fp, 'r', encoding='utf-8') as fh:
                            payload = json.load(fh)
                    except Exception:
                        continue
                    slug_val = str(payload.get('slug') or '').lower().replace('-', '')
                    if _slug_match((sched_slug or '').lower().replace('-', ''), slug_val):
                        events = payload.get('events', []) or []
                        break
        except Exception:
            pass

    # Normalize times
    try:
        events = _normalize_event_times(events)
    except Exception:
        pass


def _make_xlsx_bytes(events, schedule_obj=None, sched_slug=None):
    """Create an XLSX workbook (bytes) from events. Returns bytes.
    This mirrors the openpyxl branch used by `schedule_csv`.
    """
    from io import BytesIO
    from collections import OrderedDict
    # reuse code from schedule_csv openpyxl branch
    display_name = None
    year_val = None
    try:
        if schedule_obj:
            display_name = getattr(schedule_obj, 'convention_name', None) or getattr(schedule_obj, 'name', None) or sched_slug
            year_val = getattr(schedule_obj, 'year', None)
    except Exception:
        pass
    if not display_name:
        display_name = sched_slug or 'Schedule'
    title_text = f"{display_name} {year_val} Schedule" if year_val else f"{display_name} Schedule"

    events_by_day = OrderedDict()
    for ev in events:
        s = ev.get('start') or ev.get('start_local') or ev.get('start_utc') or ''
        try:
            dt = dateparser.parse(s)
            day_key = dt.date().isoformat()
            display_day = dt.strftime('%a %Y-%m-%d')
        except Exception:
            day_key = 'unknown'
            display_day = 'Unknown'
        if day_key not in events_by_day:
            events_by_day[day_key] = {'display': display_day, 'events': []}
        events_by_day[day_key]['events'].append(ev)

    wb = openpyxl.Workbook()
    index_ws = wb.active
    index_ws.title = 'Index'
    index_ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=4)
    tcell = index_ws.cell(row=1, column=1, value=title_text)
    tcell.font = Font(size=14, bold=True)
    tcell.alignment = Alignment(horizontal='center', vertical='center')
    index_ws.row_dimensions[1].height = 26
    index_ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=4)
    mcell = index_ws.cell(row=2, column=1, value=f"Generated: {timezone.now().isoformat()}")
    mcell.font = Font(size=9, italic=True)
    mcell.alignment = Alignment(horizontal='center')
    hdr_row = 4
    index_ws.cell(row=hdr_row, column=1, value='Day').font = Font(bold=True)
    index_ws.cell(row=hdr_row, column=2, value='Event Count').font = Font(bold=True)

    # compute all rooms across all events so each day shows every room (alphabetical)
    all_rooms = []
    all_room_set = set()
    for ev in events:
        rm = ev.get('room') or ev.get('venue') or ev.get('location') or 'Unspecified'
        if isinstance(rm, str):
            rm = rm.strip()
        if not rm:
            rm = 'Unspecified'
        if rm not in all_room_set:
            all_room_set.add(rm)
            all_rooms.append(rm)
    all_rooms.sort(key=lambda x: x.lower())

    day_sheet_names = []
    row_idx = hdr_row + 1
    for day_key, info in events_by_day.items():
        display_day = info.get('display') or day_key
        sheet_name = display_day
        if len(sheet_name) > 31:
            sheet_name = sheet_name[:28]
        suffix = 1
        base_name = sheet_name
        while sheet_name in wb.sheetnames:
            sheet_name = f"{base_name[:25]}_{suffix}"
            suffix += 1
        day_sheet_names.append(sheet_name)
        c = index_ws.cell(row=row_idx, column=1, value=display_day)
        c.hyperlink = f"#{sheet_name}!A1"
        c.font = Font(color='FF0563C1', underline='single')
        index_ws.cell(row=row_idx, column=2, value=len(info.get('events', [])))
        row_idx += 1

        ws = wb.create_sheet(title=sheet_name)
        ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=8)
        hdr = ws.cell(row=1, column=1, value=f"{title_text} — {display_day}")
        hdr.font = Font(size=12, bold=True)
        hdr.alignment = Alignment(horizontal='center')

        nav_row = 2
        col = 1
        ni = ws.cell(row=nav_row, column=col, value='Index')
        ni.hyperlink = '#Index!A1'
        ni.font = Font(color='FFFFFFFF', bold=True)
        ni.alignment = Alignment(horizontal='center')
        ni.fill = PatternFill(start_color='FF0F62FF', end_color='FF0F62FF', fill_type='solid')
        ws.column_dimensions[get_column_letter(col)].width = 10
        col += 1
        for other in day_sheet_names:
            cell = ws.cell(row=nav_row, column=col, value=other)
            cell.hyperlink = f"#{other}!A1"
            cell.font = Font(color='FFFFFFFF')
            cell.alignment = Alignment(horizontal='center')
            cell.fill = PatternFill(start_color='FF6C757D', end_color='FF6C757D', fill_type='solid')
            ws.column_dimensions[get_column_letter(col)].width = min(20, max(8, len(other) + 2))
            col += 1

        thin = Side(border_style='thin', color='FF444444')
        border = Border(left=thin, right=thin, top=thin, bottom=thin)

        day_events = info.get('events', []) or []
        rooms = []
        room_set = set()
        for ev in day_events:
            rm = (ev.get('room') or ev.get('venue') or ev.get('location') or 'Unspecified')
            if isinstance(rm, str):
                rm = rm.strip()
            if not rm:
                rm = 'Unspecified'
            if rm not in room_set:
                room_set.add(rm)
                rooms.append(rm)
        rooms.sort(key=lambda x: x.lower())

        starts = []
        ends = []
        for ev in day_events:
            try:
                s = ev.get('start')
                e = ev.get('end')
                if s:
                    starts.append(dateparser.parse(s))
                if e:
                    ends.append(dateparser.parse(e))
            except Exception:
                continue

        if not starts or not ends:
            ws.cell(row=4, column=1, value='No timed events for this day')
        else:
            # Use full-day display from 00:00 to 24:00 for the day's date
            earliest = min(starts)
            # start at the day's midnight in the same tz-awareness as the parsed datetimes
            try:
                day_date = earliest.date()
                if getattr(earliest, 'tzinfo', None):
                    start_floor = datetime.combine(day_date, datetime.min.time()).replace(tzinfo=earliest.tzinfo)
                else:
                    start_floor = datetime.combine(day_date, datetime.min.time())
            except Exception:
                # fallback to flooring the earliest to previous 30min
                m = (earliest.minute // 30) * 30
                start_floor = earliest.replace(minute=m, second=0, microsecond=0)

            # end at next day's midnight (full 24-hour span)
            end_ceil = start_floor + timedelta(days=1)

            slot_minutes = 30
            slots = []
            cur = start_floor
            while cur < end_ceil:
                slots.append(cur)
                cur = cur + timedelta(minutes=slot_minutes)

            room_col_width = 28
            ws.column_dimensions[get_column_letter(1)].width = room_col_width
            header_row = 3
            ws.cell(row=header_row, column=1, value='Room').font = Font(bold=True)
            for si, ts in enumerate(slots, start=2):
                label = ts.strftime('%I:%M %p')
                c = ws.cell(row=header_row, column=si, value=label)
                c.alignment = Alignment(horizontal='center', vertical='center')
                c.fill = PatternFill(start_color='FFDDDDDD', end_color='FFDDDDDD', fill_type='solid')
                c.font = Font(bold=True)
                ws.column_dimensions[get_column_letter(si)].width = 12

            start_data_row = header_row + 1
            palette = [
                'FF8DD3C7','FFFFB3BA','FFBDE3FF','FFFBC4FF','FFFFF2AE','FFB8E986','FFFFC9A8',
                'FFD0E1F9','FFE6E6FA','FFC8F7C5','FFF1D6A0'
            ]

            for ri, room in enumerate(rooms, start=0):
                r = start_data_row + ri
                cell = ws.cell(row=r, column=1, value=room)
                cell.alignment = Alignment(vertical='top', wrap_text=True)
                cell.font = Font(bold=True)
                cell.border = border

                room_events = [ev for ev in day_events if ((ev.get('room') or ev.get('venue') or ev.get('location') or 'Unspecified').strip() == room)]
                for ev in room_events:
                    try:
                        sdt = dateparser.parse(ev.get('start'))
                        edt = dateparser.parse(ev.get('end'))
                        offset_start = int((sdt - start_floor).total_seconds() // (60 * slot_minutes))
                        offset_end = int((edt - start_floor).total_seconds() // (60 * slot_minutes))
                        col_start = 2 + offset_start
                        col_end = 2 + max(offset_end, offset_start + 1) - 1
                        ws.merge_cells(start_row=r, start_column=col_start, end_row=r, end_column=col_end)
                        ev_cell = ws.cell(row=r, column=col_start, value=ev.get('title') or ev.get('name') or '')
                        ev_id = str(ev.get('id') or ev.get('event_id') or ev.get('title') or '')
                        try:
                            idx = int(hashlib.md5(ev_id.encode('utf-8')).hexdigest(), 16) % len(palette)
                        except Exception:
                            idx = 0
                        color = palette[idx]
                        ev_cell.fill = PatternFill(start_color=color, end_color=color, fill_type='solid')
                        ev_cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
                        ev_cell.font = Font(bold=False)
                        for col_i in range(col_start, col_end + 1):
                            ctmp = ws.cell(row=r, column=col_i)
                            ctmp.border = border
                    except Exception:
                        continue

            ws.freeze_panes = ws['B4']
            for rr in range(start_data_row, start_data_row + len(rooms)):
                ws.row_dimensions[rr].height = 30

        try:
            ws.sheet_properties.tabColor = 'FF6C757D'
        except Exception:
            pass

    index_ws.column_dimensions[get_column_letter(1)].width = 30
    index_ws.column_dimensions[get_column_letter(2)].width = 14
    wb.active = wb.sheetnames.index('Index')

    # Apply default font to cells to avoid Arial fallback in Excel
    try:
        default_font_name = getattr(settings, 'EXPORT_FONT_NAME', 'Calibri')
        default_font_size = getattr(settings, 'EXPORT_FONT_SIZE', 11)
        for ws in wb.worksheets:
            try:
                if ws.max_row is None or ws.max_column is None:
                    continue
                for row in ws.iter_rows(min_row=1, max_row=ws.max_row or 1, min_col=1, max_col=ws.max_column or 1):
                    for cell in row:
                        try:
                            ef = cell.font or Font()
                            cell.font = Font(
                                name=default_font_name,
                                size=ef.size or default_font_size,
                                bold=ef.bold or False,
                                italic=ef.italic or False,
                                underline=ef.underline or None,
                                color=getattr(ef, 'color', None)
                            )
                        except Exception:
                            continue
            except Exception:
                continue
    except Exception:
        pass

    bio = BytesIO()
    wb.save(bio)
    bio.seek(0)
    return bio.read()

    # If openpyxl is available, produce a styled Excel workbook with an Index sheet; otherwise fall back to CSV
    if openpyxl:
        wb = openpyxl.Workbook()

        # Determine a friendly title for the workbook (use schedule object if available)
        display_name = None
        year_val = None
        try:
            if schedule_obj:
                display_name = getattr(schedule_obj, 'convention_name', None) or getattr(schedule_obj, 'name', None) or sched_slug
                year_val = getattr(schedule_obj, 'year', None)
        except Exception:
            pass
        if not display_name:
            display_name = sched_slug
        title_text = f"{display_name} {year_val} Schedule" if year_val else f"{display_name} Schedule"

        # Group events by local day (YYYY-MM-DD)
        events_by_day = OrderedDict()
        for ev in events:
            s = ev.get('start') or ev.get('start_local') or ev.get('start_utc') or ''
            try:
                dt = dateparser.parse(s)
                day_key = dt.date().isoformat()
                display_day = dt.strftime('%a %Y-%m-%d')
            except Exception:
                # fallback to unknown bucket
                day_key = 'unknown'
                display_day = 'Unknown'
            if day_key not in events_by_day:
                events_by_day[day_key] = {'display': display_day, 'events': []}
            events_by_day[day_key]['events'].append(ev)

        # Create Index sheet first
        index_ws = wb.active
        index_ws.title = 'Index'
        index_ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=4)
        tcell = index_ws.cell(row=1, column=1, value=title_text)
        tcell.font = Font(size=14, bold=True)
        tcell.alignment = Alignment(horizontal='center', vertical='center')
        index_ws.row_dimensions[1].height = 26

        # Add generated timestamp
        index_ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=4)
        mcell = index_ws.cell(row=2, column=1, value=f"Generated: {timezone.now().isoformat()}")
        mcell.font = Font(size=9, italic=True)
        mcell.alignment = Alignment(horizontal='center')

        # Table of contents header
        hdr_row = 4
        index_ws.cell(row=hdr_row, column=1, value='Day').font = Font(bold=True)
        index_ws.cell(row=hdr_row, column=2, value='Event Count').font = Font(bold=True)

        # Compute all rooms across all events so each day shows every room (alphabetical)
        all_rooms = []
        all_room_set = set()
        for ev in events:
            rm = ev.get('room') or ev.get('venue') or ev.get('location') or 'Unspecified'
            if isinstance(rm, str):
                rm = rm.strip()
            if not rm:
                rm = 'Unspecified'
            if rm not in all_room_set:
                all_room_set.add(rm)
                all_rooms.append(rm)
        all_rooms.sort(key=lambda x: x.lower())

        # Build day sheets and fill the index
        day_sheet_names = []
        row_idx = hdr_row + 1
        for day_key, info in events_by_day.items():
            display_day = info.get('display') or day_key
            # Ensure sheet name is Excel-safe (<=31 chars)
            sheet_name = display_day
            if len(sheet_name) > 31:
                sheet_name = sheet_name[:28]
            # Avoid duplicates
            suffix = 1
            base_name = sheet_name
            while sheet_name in wb.sheetnames:
                sheet_name = f"{base_name[:25]}_{suffix}"
                suffix += 1

            day_sheet_names.append(sheet_name)

            # Add index row with hyperlink to sheet
            c = index_ws.cell(row=row_idx, column=1, value=display_day)
            c.hyperlink = f"#{sheet_name}!A1"
            c.font = Font(color='FF0563C1', underline='single')
            index_ws.cell(row=row_idx, column=2, value=len(info.get('events', [])))
            row_idx += 1

            # Create the day sheet
            ws = wb.create_sheet(title=sheet_name)

            # Top title and a small navigation area linking back to Index and other days
            ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=8)
            hdr = ws.cell(row=1, column=1, value=f"{title_text} — {display_day}")
            hdr.font = Font(size=12, bold=True)
            hdr.alignment = Alignment(horizontal='center')

            # Navigation row: create compact links back to Index and to other day sheets
            nav_row = 2
            col = 1
            # Back to Index
            ni = ws.cell(row=nav_row, column=col, value='Index')
            ni.hyperlink = '#Index!A1'
            ni.font = Font(color='FFFFFFFF', bold=True)
            ni.alignment = Alignment(horizontal='center')
            ni.fill = PatternFill(start_color='FF0F62FF', end_color='FF0F62FF', fill_type='solid')
            ws.column_dimensions[get_column_letter(col)].width = 12
            col += 1
            # Other day tabs (small colored cells)
            for other in day_sheet_names:
                cell = ws.cell(row=nav_row, column=col, value=other)
                cell.hyperlink = f"#{other}!A1"
                cell.font = Font(color='FFFFFFFF')
                cell.alignment = Alignment(horizontal='center')
                cell.fill = PatternFill(start_color='FF6C757D', end_color='FF6C757D', fill_type='solid')
                ws.column_dimensions[get_column_letter(col)].width = min(20, max(8, len(other) + 2))
                col += 1

            # Build a grid-style timetable: rows = rooms, columns = 30-min timeslots
            thin = Side(border_style='thin', color='FF444444')
            border = Border(left=thin, right=thin, top=thin, bottom=thin)

            day_events = info.get('events', []) or []
            # Only show rooms used on this day (hide unused rooms)
            rooms = []
            room_set = set()
            for ev in day_events:
                rm = (ev.get('room') or ev.get('venue') or ev.get('location') or 'Unspecified')
                if isinstance(rm, str):
                    rm = rm.strip()
                if not rm:
                    rm = 'Unspecified'
                if rm not in room_set:
                    room_set.add(rm)
                    rooms.append(rm)
            rooms.sort(key=lambda x: x.lower())

            # Determine time range for the day
            starts = []
            ends = []
            for ev in day_events:
                try:
                    s = ev.get('start')
                    e = ev.get('end')
                    if s:
                        starts.append(dateparser.parse(s))
                    if e:
                        ends.append(dateparser.parse(e))
                except Exception:
                    continue

            if not starts or not ends:
                # Fallback: show simple list if no times
                ws.cell(row=4, column=1, value='No timed events for this day')
            else:
                earliest = min(starts)
                latest = max(ends)
                # Floor earliest to previous 30min and ceil latest to next 30min
                def floor_half(dt):
                    m = (dt.minute // 30) * 30
                    return dt.replace(minute=m, second=0, microsecond=0)

                def ceil_half(dt):
                    if dt.minute % 30 == 0 and dt.second == 0 and dt.microsecond == 0:
                        return dt.replace(second=0, microsecond=0)
                    add = 30 - (dt.minute % 30)
                    base = dt + timedelta(minutes=add)
                    return base.replace(second=0, microsecond=0)

                start_floor = floor_half(earliest)
                end_ceil = ceil_half(latest)

                slot_minutes = 30
                slots = []
                cur = start_floor
                while cur < end_ceil:
                    slots.append(cur)
                    cur = cur + timedelta(minutes=slot_minutes)

                # Write header: first column is 'Room', following columns are timeslots
                room_col_width = 30
                ws.column_dimensions[get_column_letter(1)].width = room_col_width
                header_row = 3
                # Header styling: blue strip with white bold text
                hdr_cell = ws.cell(row=header_row, column=1, value='Room')
                hdr_cell.font = Font(bold=True, size=10, color='FFFFFFFF')
                hdr_cell.fill = PatternFill(start_color='FF0F62FF', end_color='FF0F62FF', fill_type='solid')
                for si, ts in enumerate(slots, start=2):
                    label = ts.strftime('%I:%M %p')
                    c = ws.cell(row=header_row, column=si, value=label)
                    c.alignment = Alignment(horizontal='center', vertical='center')
                    c.fill = PatternFill(start_color='FF0F62FF', end_color='FF0F62FF', fill_type='solid')
                    c.font = Font(bold=True, size=9, color='FFFFFFFF')
                    # make time columns slightly wider for readability
                    ws.column_dimensions[get_column_letter(si)].width = 11

                # Create rows per room and place events
                start_data_row = header_row + 1
                palette = [
                    'FFB3E5FC','FFFFF9C4','FFC8E6C9','FFFFCCBC','FFD1C4E9','FFFFF59D','FFEF9A9A',
                    'FFB2EBF2','FFF8BBD0','FFE1BEE7','FFDCEDC8'
                ]

                for ri, room in enumerate(rooms, start=0):
                    r = start_data_row + ri
                    cell = ws.cell(row=r, column=1, value=room)
                    cell.alignment = Alignment(vertical='center', wrap_text=True)
                    cell.font = Font(bold=True, size=10)
                    cell.border = border

                    # Place events for this room
                    room_events = [ev for ev in day_events if ((ev.get('room') or ev.get('venue') or ev.get('location') or 'Unspecified').strip() == room)]
                    for ev in room_events:
                        try:
                            sdt = dateparser.parse(ev.get('start'))
                            edt = dateparser.parse(ev.get('end'))
                            # compute slot indices
                            offset_start = int((sdt - start_floor).total_seconds() // (60 * slot_minutes))
                            offset_end = int((edt - start_floor).total_seconds() // (60 * slot_minutes))
                            # Excel columns
                            col_start = 2 + offset_start
                            col_end = 2 + max(offset_end, offset_start + 1) - 1
                            # Merge across the appropriate columns for this row
                            ws.merge_cells(start_row=r, start_column=col_start, end_row=r, end_column=col_end)
                            # Compose a nicer cell value: title on first line, time range on second
                            title = (ev.get('title') or ev.get('name') or '').strip()
                            try:
                                time_label = f"{sdt.strftime('%I:%M %p').lstrip('0')} - {edt.strftime('%I:%M %p').lstrip('0')}"
                            except Exception:
                                time_label = ''
                            ev_text = title
                            if time_label:
                                ev_text = f"{title}\n{time_label}"
                            ev_cell = ws.cell(row=r, column=col_start, value=ev_text)
                            # Color selection based on event id
                            ev_id = str(ev.get('id') or ev.get('event_id') or ev.get('title') or '')
                            try:
                                idx = int(hashlib.md5(ev_id.encode('utf-8')).hexdigest(), 16) % len(palette)
                            except Exception:
                                idx = 0
                            color = palette[idx]
                            ev_cell.fill = PatternFill(start_color=color, end_color=color, fill_type='solid')
                            ev_cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
                            ev_cell.font = Font(bold=True, size=9)
                            # Put thin border around merged area
                            for col_i in range(col_start, col_end + 1):
                                ctmp = ws.cell(row=r, column=col_i)
                                ctmp.border = border
                        except Exception:
                            # skip problematic events
                            continue

                # Ensure a thin grid border across the timetable area for clearer blocks
                try:
                    max_col = 1 + len(slots)
                    max_row = start_data_row + len(rooms) - 1
                    for rr in range(header_row, max_row + 1):
                        for cc in range(1, max_col + 1):
                            try:
                                cell = ws.cell(row=rr, column=cc)
                                cell.border = border
                            except Exception:
                                continue
                except Exception:
                    pass

                # Freeze header and room column
                ws.freeze_panes = ws['B4']

                # Auto-adjust row heights for readability
                for rr in range(start_data_row, start_data_row + len(rooms)):
                    ws.row_dimensions[rr].height = 40

            # Set a tab color for the sheet to visually separate days
            try:
                ws.sheet_properties.tabColor = 'FF6C757D'
            except Exception:
                pass

        # Adjust column widths on Index
        index_ws.column_dimensions[get_column_letter(1)].width = 30
        index_ws.column_dimensions[get_column_letter(2)].width = 14

        # Make Index the front sheet
        wb.active = wb.sheetnames.index('Index')

        # Save to bytes
        bio = io.BytesIO()
        wb.save(bio)
        bio.seek(0)
        # Use a friendly filename
        safe_name = display_name.replace(' ', '_')
        filename = f"{safe_name}-{year_val or 'schedule'}.xlsx"
        response = HttpResponse(bio.read(), content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response
    else:
        # fallback to CSV if openpyxl not available
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(['event_id', 'title', 'start_local', 'end_local', 'start_utc', 'end_utc', 'timezone', 'location', 'room', 'description'])
        for ev in events:
            writer.writerow([
                ev.get('id') or ev.get('event_id') or '',
                ev.get('title') or ev.get('name') or '',
                ev.get('start') or '',
                ev.get('end') or '',
                ev.get('start_utc') or '',
                ev.get('end_utc') or '',
                ev.get('timezone') or '',
                ev.get('location') or '',
                ev.get('room') or ev.get('venue') or '',
                ev.get('description') or ev.get('desc') or ''
            ])

        csv_data = output.getvalue()
        output.close()
        filename = f"{sched_slug}-schedule.csv"
        response = HttpResponse(csv_data, content_type='text/csv')
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response


@require_GET
def schedule_export_start(request, slug):
    """Start an asynchronous CSV export for the given schedule slug.
    Returns JSON with a `task_id` the client can poll.
    """
    fmt = request.GET.get('format', 'xlsx').lower()
    if fmt != 'xlsx':
        return JsonResponse({'error': 'unsupported_format', 'message': 'only xlsx supported'}, status=400)

    # Ensure XLSX support present
    if openpyxl is None:
        return JsonResponse({'error': 'xlsx_unavailable', 'message': 'openpyxl is not installed on the server'}, status=503)

    resolved = _resolve_sched_slug(slug, request=request)
    sched_slug = resolved.get('sched_slug')

    task_id = uuid.uuid4().hex
    cache_key = f'export:{task_id}'
    cache.set(cache_key, {'status': 'queued', 'progress': 5, 'message': 'queued'}, timeout=24*3600)

    logger.info('schedule_export_start: starting export for %s (task %s)', sched_slug, task_id)
    # Spawn background thread to perform CSV generation
    try:
        t = threading.Thread(target=_export_worker, args=(task_id, sched_slug, fmt), daemon=True)
        t.start()
    except Exception as e:
        logger.exception('schedule_export_start: failed to start worker for %s: %s', sched_slug, e)
        return JsonResponse({'error': 'failed_to_start', 'message': str(e)}, status=500)

    return JsonResponse({
        'task_id': task_id,
        'status': 'queued',
        'events_count': 0,
        'status_url': request.build_absolute_uri(reverse('schedule_export_status', args=[task_id])),
        'download_url': request.build_absolute_uri(reverse('schedule_export_download', args=[task_id])),
    }, status=202)


def _export_worker(task_id, sched_slug, fmt='csv'):
    cache_key = f'export:{task_id}'
    try:
        cache.set(cache_key, {'status': 'started', 'progress': 0, 'message': 'Initializing...'}, timeout=24*3600)

        # Resolve and load events (reuse same logic as schedule_csv)
        schedule_obj = None
        events = []
        # try year match
        import re
        match = re.match(r'([a-z0-9\-]+)-(\d{4})$', sched_slug)
        if match:
            convention_slug, year = match.groups()
            try:
                schedule_obj = Schedule.objects.filter(year=year, deleted=False).first()
                if schedule_obj:
                    events = json.loads(schedule_obj.events_json) if schedule_obj.events_json else []
            except Exception:
                schedule_obj = None

        if not events:
            try:
                schedule_obj = Schedule.objects.filter(slug__iexact=sched_slug, deleted=False).first()
                if schedule_obj:
                    events = json.loads(schedule_obj.events_json) if schedule_obj.events_json else []
            except Exception:
                schedule_obj = None

        if not events:
            # try local schedules dir
            try:
                schedules_dir = os.path.join(settings.BASE_DIR, 'schedules')
                if os.path.isdir(schedules_dir):
                    for fn in os.listdir(schedules_dir):
                        if not fn.lower().endswith('.json'):
                            continue
                        fp = os.path.join(schedules_dir, fn)
                        try:
                            with open(fp, 'r', encoding='utf-8') as fh:
                                payload = json.load(fh)
                        except Exception:
                            continue
                        slug_val = str(payload.get('slug') or '').lower().replace('-', '')
                        if _slug_match((sched_slug or '').lower().replace('-', ''), slug_val):
                            events = payload.get('events', []) or []
                            break
            except Exception:
                pass

        # Normalize
        try:
            events = _normalize_event_times(events)
        except Exception:
            pass

        total = max(1, len(events))

        # Create temp file path; write to a .part file and atomically move to final name when complete
        tmpdir = os.path.join(tempfile.gettempdir(), 'ofa_exports')
        os.makedirs(tmpdir, exist_ok=True)
        safe_name = (getattr(schedule_obj, 'convention_name', None) or sched_slug).replace(' ', '_')
        ext = 'xlsx' if fmt == 'xlsx' else 'csv'
        filename = f"{safe_name}-{getattr(schedule_obj, 'year', 'schedule')}.{ext}"
        out_path = os.path.join(tmpdir, f"{task_id}-{filename}")
        out_path_tmp = out_path + '.part'

        if fmt == 'xlsx':
            cache.set(cache_key, {'status': 'running', 'progress': 5, 'message': 'building xlsx'}, timeout=24*3600)
            try:
                xbytes = _make_xlsx_bytes(events, schedule_obj=schedule_obj, sched_slug=sched_slug)
                with open(out_path_tmp, 'wb') as fh:
                    fh.write(xbytes)
                cache.set(cache_key, {'status': 'running', 'progress': 95, 'message': f'written {len(events)} events'}, timeout=24*3600)
                state = cache.get(cache_key) or {}
                if state.get('status') == 'cancelled':
                    try:
                        if os.path.exists(out_path_tmp):
                            os.remove(out_path_tmp)
                    except Exception:
                        pass
                    cache.set(cache_key, {'status': 'cancelled', 'progress': 0, 'message': 'cancelled by user'}, timeout=24*3600)
                    return
            except Exception as e:
                logger.exception('export_worker xlsx build failed for %s: %s', sched_slug, e)
                cache.set(cache_key, {'status': 'error', 'progress': 0, 'message': str(e)}, timeout=24*3600)
                return
        else:
            cache.set(cache_key, {'status': 'running', 'progress': 0, 'message': 'writing csv'}, timeout=24*3600)

        # Write CSV row-by-row to temporary path and update progress
        with open(out_path_tmp, 'w', encoding='utf-8', newline='') as fh:
            writer = csv.writer(fh)
            writer.writerow(['schedule_year', 'event_id', 'title', 'start_local', 'end_local', 'start_utc', 'end_utc', 'timezone', 'location', 'room', 'description'])
            # compute smoothing parameters so very-small exports still show gradual progress
            smoothing_offset = 5  # keep a small headroom at start
            smoothing_scale = 90   # scale writes into 5..95 range; final 100 set after move
            for i, ev in enumerate(events):
                writer.writerow([
                    getattr(schedule_obj, 'year', ''),
                    ev.get('id') or ev.get('event_id') or '',
                    ev.get('title') or ev.get('name') or '',
                    ev.get('start') or '',
                    ev.get('end') or '',
                    ev.get('start_utc') or '',
                    ev.get('end_utc') or '',
                    ev.get('timezone') or '',
                    ev.get('location') or '',
                    ev.get('room') or ev.get('venue') or '',
                    ev.get('description') or ev.get('desc') or ''
                ])
                # update progress with smoothing to avoid instant jump for tiny payloads
                try:
                    progress = smoothing_offset + int((smoothing_scale * (i+1)) / float(total))
                except Exception:
                    progress = int(100.0 * (i+1) / total)
                # clamp to 99 to leave final 100 for completion
                if progress >= 100:
                    progress = 99
                cache.set(cache_key, {'status': 'running', 'progress': progress, 'message': f'processed {i+1}/{total}'}, timeout=24*3600)
                # small sleep so UI has a chance to observe intermediate updates
                time.sleep(0.03)

                # Check for cancellation request and abort cleanly
                state = cache.get(cache_key) or {}
                if state.get('status') == 'cancelled':
                    # remove temp file if present
                    try:
                        if os.path.exists(out_path_tmp):
                            os.remove(out_path_tmp)
                    except Exception:
                        pass
                    cache.set(cache_key, {'status': 'cancelled', 'progress': progress, 'message': 'cancelled by user'}, timeout=24*3600)
                    return

        # Atomically move the .part file to final location
        try:
            os.replace(out_path_tmp, out_path)
        except Exception:
            # fallback: if replace fails, try rename
            try:
                os.rename(out_path_tmp, out_path)
            except Exception:
                pass

        cache.set(cache_key, {'status': 'done', 'progress': 100, 'message': 'complete', 'file_path': out_path, 'filename': filename}, timeout=24*3600)
    except Exception as e:
        logger.exception('export_worker failed for %s: %s', sched_slug, e)
        cache.set(cache_key, {'status': 'error', 'progress': 0, 'message': str(e)}, timeout=24*3600)


@require_GET
def schedule_export_status(request, task_id):
    cache_key = f'export:{task_id}'
    data = cache.get(cache_key)
    if not data:
        return JsonResponse({'error': 'not_found'}, status=404)
    return JsonResponse(data)


@require_GET
def schedule_export_download(request, task_id):
    cache_key = f'export:{task_id}'
    data = cache.get(cache_key)
    if not data:
        raise Http404('task not found')
    if data.get('status') != 'done':
        return JsonResponse({'error': 'not_ready', 'status': data.get('status'), 'message': data.get('message')}, status=409)
    path = data.get('file_path')
    filename = data.get('filename') or os.path.basename(path)
    if not path or not os.path.exists(path):
        raise Http404('file missing')
    return FileResponse(open(path, 'rb'), as_attachment=True, filename=filename)


@require_GET
def schedule_export_cancel(request, task_id):
    """Request cancellation of an in-progress export task."""
    cache_key = f'export:{task_id}'
    data = cache.get(cache_key)
    if not data:
        return JsonResponse({'error': 'not_found'}, status=404)
    # Mark as cancelled; worker will observe and stop
    try:
        # preserve progress if present
        progress = data.get('progress', 0)
        cache.set(cache_key, {'status': 'cancelled', 'progress': progress, 'message': 'cancelling'}, timeout=24*3600)
        # Best-effort remove temp file
        tmpdir = os.path.join(tempfile.gettempdir(), 'ofa_exports')
        # search for partial file by prefix
        for fn in os.listdir(tmpdir) if os.path.isdir(tmpdir) else []:
            if fn.startswith(task_id) and fn.endswith('.part'):
                try:
                    os.remove(os.path.join(tmpdir, fn))
                except Exception:
                    pass
        return JsonResponse({'status': 'cancelled'})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)

def _normalize_event_times(events):
    """Given a list of event dicts (with 'date', 'start_raw', 'end_raw', 'location'),
    attempt to assign a timezone and produce ISO datetimes and UTC equivalents.
    """
    out = []
    for ev in events:
        ev['start_utc'] = None
        ev['end_utc'] = None

        date_id = ev.get('date')
        start_raw = ev.get('start_raw')
        end_raw = ev.get('end_raw')
        loc = ev.get('location') or ''

        # Honor a per-event tz_override if provided (set earlier per-slug),
        # fall back to geocoding the location otherwise.
        tz_override = ev.get('tz_override')
        tz_name = None
        if tz_override:
            tz_name = TZ_NAME_MAP.get(tz_override.lower()) if isinstance(tz_override, str) else None
            if not tz_name:
                # If the override isn't a canonical IANA name, try resolving it
                tz_name = _guess_timezone_from_location(tz_override)
        else:
            tz_name = _guess_timezone_from_location(loc)

        ev['timezone'] = tz_name

        if date_id and start_raw and tz_name:
            try:
                # parse combined date+time
                st = dateparser.parse(f"{date_id} {start_raw}")
                local_tz = pytz.timezone(tz_name)
                if st.tzinfo is None:
                    st_local = local_tz.localize(st)
                else:
                    st_local = st.astimezone(local_tz)
                ev['start'] = st_local.isoformat()
                ev['start_utc'] = st_local.astimezone(pytz.utc).isoformat()
            except Exception:
                ev['start'] = None
                ev['start_utc'] = None

        if date_id and end_raw and tz_name:
            try:
                en = dateparser.parse(f"{date_id} {end_raw}")
                local_tz = pytz.timezone(tz_name)
                if en.tzinfo is None:
                    en_local = local_tz.localize(en)
                else:
                    en_local = en.astimezone(local_tz)
                ev['end'] = en_local.isoformat()
                ev['end_utc'] = en_local.astimezone(pytz.utc).isoformat()
            except Exception:
                ev['end'] = None
                ev['end_utc'] = None

        # If both start and end are present but end is not after start,
        # assume the end is on the following day (common when end is '12:00 AM').
        try:
            if ev.get('start') and ev.get('end'):
                st_dt = dateparser.parse(ev['start'])
                en_dt = dateparser.parse(ev['end'])
                if en_dt <= st_dt:
                    en_dt = en_dt + timedelta(days=1)
                    # re-localize to tz aware strings
                    ev['end'] = en_dt.isoformat()
                    ev['end_utc'] = en_dt.astimezone(pytz.utc).isoformat()
        except Exception:
            pass

        out.append(ev)
    return out


def _format_city_label(city_label, country_code):
    """
    Standardizes Umami city strings (e.g., 'San Francisco, California, US')
    to a cleaner 'City, Region' format for US/CA or returns the raw value.
    """
    if not city_label:
        return 'Unknown'
    value = str(city_label).strip()
    if not value:
        return 'Unknown'
    
    parts = [part.strip() for part in value.split(',') if part.strip()]

    def _expand_region(region_value):
        region_value = str(region_value or '').strip()
        if not region_value:
            return ''
        if country_code == 'US':
            return US_STATE_CODE_TO_NAME.get(region_value.upper(), region_value)
        if country_code == 'CA':
            return CA_PROVINCE_CODE_TO_NAME.get(region_value.upper(), region_value)
        return region_value

    if country_code in {'US', 'CA'}:
        if len(parts) == 2:
            return f"{parts[0]}, {_expand_region(parts[1])}"
        if len(parts) >= 3:
            city = ', '.join(parts[:-2])
            region = _expand_region(parts[-2])
            country = parts[-1]
            if country.upper() in {'US', 'USA', 'UNITED STATES', 'U.S.', 'CA', 'CANADA'}:
                return f"{city}, {region}"
    return value

@require_GET
def schedule_schema_download(request, slug):
    """Generate a simple Furry Schedule Schema JSON file for `slug` and return as download.
    Best-effort server-side assembly: prefer DB `Schedule`, fall back to local `schedules/` JSON files.
    """
    resolved = _resolve_sched_slug(slug, request=request)
    sched_slug = resolved.get('sched_slug')

    events = []
    schedule_obj = None
    try:
        import re
        m = re.match(r'([a-z0-9\-]+)-(\d{4})$', sched_slug or '')
        if m:
            convention_slug, year = m.groups()
            try:
                schedule_obj = Schedule.objects.filter(slug__iexact=sched_slug, year=year, deleted=False).first()
                if not schedule_obj:
                    schedule_obj = Schedule.objects.filter(slug__istartswith=convention_slug, year=year, deleted=False).first()
                if not schedule_obj:
                    conv_like = convention_slug.replace('-', ' ')
                    schedule_obj = Schedule.objects.filter(convention_name__icontains=conv_like, year=year, deleted=False).first()
                if schedule_obj and schedule_obj.events_json:
                    events = json.loads(schedule_obj.events_json) if schedule_obj.events_json else []
            except Exception:
                schedule_obj = None

        if not events:
            try:
                schedule_obj = Schedule.objects.filter(slug__iexact=sched_slug, deleted=False).first()
                if schedule_obj and schedule_obj.events_json:
                    events = json.loads(schedule_obj.events_json) if schedule_obj.events_json else []
            except Exception:
                schedule_obj = None

        if not events:
            # try local schedules dir
            try:
                schedules_dir = os.path.join(settings.BASE_DIR, 'schedules')
                if os.path.isdir(schedules_dir):
                    for fn in os.listdir(schedules_dir):
                        if not fn.lower().endswith('.json'):
                            continue
                        fp = os.path.join(schedules_dir, fn)
                        try:
                            with open(fp, 'r', encoding='utf-8') as fh:
                                payload = json.load(fh)
                        except Exception:
                            continue
                        slug_val = str(payload.get('slug') or '').lower().replace('-', '')
                        if _slug_match((sched_slug or '').lower().replace('-', ''), slug_val):
                            events = payload.get('events', []) or []
                            break
            except Exception:
                pass
    except Exception:
        events = []

    try:
        events = _normalize_event_times(events)
    except Exception:
        pass

    # convention id/name
    conv_id = sched_slug or ''
    conv_name = (getattr(schedule_obj, 'convention_name', None) or (sched_slug or '').replace('-', ' ').title())
    # try to pull consurf-provided description
    consurf_desc = ''
    try:
        if conv_name:
            ce = _get_consurf_event(conv_name, None, slug_hint=sched_slug)
            if ce and isinstance(ce, dict):
                consurf_desc = ce.get('description') or ce.get('summary') or ''
    except Exception:
        consurf_desc = ''

    git_sha = os.environ.get('GIT_COMMIT') or os.environ.get('GIT_COMMIT_SHORT') or ''
    if not git_sha:
        try:
            out = subprocess.check_output(['git', 'rev-parse', '--short', 'HEAD'], cwd=settings.BASE_DIR)
            git_sha = out.decode('utf-8').strip()
        except Exception:
            git_sha = ''

    from django.utils import timezone as dj_timezone
    schema = {
        'schemaVersion': '1.0.0',
        'updatedAt': dj_timezone.now().isoformat(),
        'source': {'name': 'FurryConArchives', 'vendorId': 'FCA-001', 'appVersion': git_sha or ''},
        'convention': {'id': conv_id, 'name': {'en-US': conv_name or ''}, 'description': {'en-US': consurf_desc or ''}, 'location': {'en-US': ''}, 'timezone': ''},
        'events': []
    }

    for idx, ev in enumerate(events or []):
        ev_id = ev.get('id') or ev.get('event_id') or f'evt-{idx+1}'
        title = ev.get('title') or ev.get('name') or ''
        desc = ev.get('description') or ev.get('desc') or ''
        start = ev.get('start') or ev.get('start_local') or ev.get('start_utc') or None
        end = ev.get('end') or ev.get('end_local') or ev.get('end_utc') or None
        location = ev.get('location') or ev.get('room') or ev.get('venue') or ''
        item = {
            'id': str(ev_id),
            'title': {'en-US': title},
            'description': {'en-US': desc},
            'timeSlots': [{'startTime': start, 'endTime': end, 'venueId': '', 'roomId': location}],
            'typeId': ev.get('type') or '',
            'trackId': None,
            'labelIds': [],
            'hostIds': [],
            'allowedMemberships': [],
            'minAge': 0,
            'ticketed': False,
            'buttons': [{'name': 'Source', 'url': ev.get('sched_url') or ev.get('url') or ''}] if (ev.get('sched_url') or ev.get('url')) else []
        }
        schema['events'].append(item)

    safe_name = (conv_id or sched_slug or 'schedule').replace(' ', '_')
    filename = f"{safe_name}-schema.json"
    body = json.dumps(schema, indent=2, ensure_ascii=False)
    resp = HttpResponse(body, content_type='application/json; charset=utf-8')
    resp['Content-Disposition'] = f'attachment; filename="{filename}"'
    return resp

@staff_member_required
def admin_statistics(request):
    context = _compute_statistics_context(request)
    return render(request, 'archive/admin/statistics.html', context)


def con_dashboard(request, key=None):
    """Public convention dashboard secured by an app key.

    The app key must be active, include the con-dashboard scope, and be linked
    to a convention/category. Dashboard access is not rate limited. The key is
    used to scope analytics to that convention, then the existing analytics
    renderer is reused without requiring staff access.
    """
    from .models import AppKey

    app_key = (key or request.GET.get('app_key') or request.POST.get('app_key') or '').strip()
    if request.method == 'POST' and app_key and not key:
        # Preserve a friendly URL after validation.
        return redirect(f'/con-dashboard/{app_key}')

    if not app_key:
        return render(request, 'archive/con_dashboard_access.html', {
            'page_title': 'Convention Dashboard',
            'error': None,
        })

    key_obj = AppKey.objects.select_related('convention').filter(key=app_key, active=True).first()
    if not key_obj or not key_obj.has_scope(AppKey.SCOPE_CON_DASHBOARD) or not key_obj.convention:
        return render(request, 'archive/con_dashboard_access.html', {
            'page_title': 'Convention Dashboard',
            'error': 'Invalid app key.',
        }, status=403)

    # Build the same analytics context as admin but scoped to this convention
    convention = key_obj.convention
    mutable_get = request.GET.copy()
    # Provide the convention selector value so helper will scope Umami queries
    mutable_get['convention'] = convention.name or ''
    mutable_get['app_key'] = app_key
    # Force convention-scoped view so `_compute_statistics_context` returns per-convention stats
    mutable_get['view'] = 'convention'
    request.GET = mutable_get

    context = _compute_statistics_context(request)
    context['page_title'] = f"{convention.name} Dashboard"
    context['convention'] = convention
    context['app_key'] = app_key
    
    response = render(request, 'archive/con_dashboard_full.html', context)
    # Disable caching so fresh data is always shown
    response['Cache-Control'] = 'no-cache, no-store, must-revalidate, max-age=0'
    response['Pragma'] = 'no-cache'
    response['Expires'] = '0'
    return response


def _compute_statistics_context(request):
    """Build and return the analytics context used by admin_statistics and public con_dashboard.
    Falls back to local aggregates when Umami is not configured or requests fail.
    """
    force_refresh_cache = str(request.GET.get('refresh_cache', '')).strip().lower() in {'1', 'true', 'yes', 'y'}
    if force_refresh_cache:
        removed_umami = _clear_umami_cache_entries()

    documents = PDFDocument.objects.all().order_by('title')
    conventions = sorted(set(PDFDocument.objects.exclude(convention_name='').values_list('convention_name', flat=True)))

    # Get filter parameter
    doc_id = request.GET.get('doc')
    convention = request.GET.get('convention', '')
    view_type = request.GET.get('view', 'global')
    selected_file_year = (request.GET.get('file_year') or '').strip()

    def _extract_year_for_scope(doc_obj):
        """Extract convention/document year for dashboard scoping."""
        year_val = None
        try:
            slug_val = getattr(doc_obj, 'slug', '') or ''
            m = re.search(r'-([0-9]{4})-', slug_val)
            if not m:
                m = re.search(r'-([0-9]{4})(?:[^0-9]|$)', slug_val)
            if not m:
                m = re.search(r'(?:^|[^0-9])([0-9]{4})(?:[^0-9]|$)', slug_val)
            if m:
                year_val = m.group(1)
        except Exception:
            year_val = None

        if not year_val:
            try:
                model_year = getattr(doc_obj, 'year', None) or getattr(doc_obj, 'copyright_year', None)
                year_val = str(int(model_year)) if model_year else None
            except Exception:
                year_val = None

        if not year_val:
            try:
                year_val = str(doc_obj.uploaded_at.year) if getattr(doc_obj, 'uploaded_at', None) else None
            except Exception:
                year_val = None

        return year_val

    # Parse date range
    start_date_str = request.GET.get('start_date', '')
    end_date_str = request.GET.get('end_date', '')
    preset = request.GET.get('preset', '30d')
    now = timezone.now()
    try:
        if start_date_str and end_date_str:
            start_date = dateparser.parse(start_date_str)
            end_date = dateparser.parse(end_date_str)
        else:
            if preset == '24h':
                start_date = now - timedelta(hours=24)
            elif preset == '7d':
                start_date = now - timedelta(days=7)
            else:
                start_date = now - timedelta(days=30)
            end_date = now
    except Exception:
        start_date = now - timedelta(days=30)
        end_date = now

    selected_doc = None
    selected_convention = convention if convention else None
    selected_slugs = []
    convention_scope_slugs = []

    if view_type == 'convention' and selected_convention:
        con_param = str(selected_convention).strip()
        con_qs = PDFDocument.objects.filter(convention_name=con_param)
        if not con_qs.exists():
            con_qs = PDFDocument.objects.filter(convention_name__icontains=con_param)
        if not con_qs.exists():
            slug_fragment = slugify(con_param)
            if slug_fragment:
                con_qs = PDFDocument.objects.filter(slug__icontains=slug_fragment)

        convention_scope_slugs = [d.slug for d in con_qs]
        if selected_file_year:
            selected_slugs = [d.slug for d in con_qs if _extract_year_for_scope(d) == selected_file_year]
        else:
            selected_slugs = list(convention_scope_slugs)
    elif view_type == 'document' and doc_id:
        try:
            selected_doc = PDFDocument.objects.get(id=doc_id)
            selected_slugs = [selected_doc.slug]
        except PDFDocument.DoesNotExist:
            selected_doc = None

    start_ms = int(start_date.timestamp() * 1000)
    end_ms = int(end_date.timestamp() * 1000)

    chart_data = {
        'labels': [],
        'counts': [],
        'views': [],
        'visits': [],
        'bounces': [],
    }
    referer_stats = []
    top_pages_stats = []
    country_stats = []
    country_cities_map = {}
    summary_cards = []
    analytics_source = 'Local logs'

    umami_configured = _umami_is_configured()
    if umami_configured:
        # Fetch overall path metrics to compute top pages
        try:
            # Note: skipping detailed path metrics; we focus on referrers and geography instead
            relevant_metrics = []
            top_pages_stats = []

            metric_maps = {
                'visitors': defaultdict(int),
                'views': defaultdict(int),
                'visits': defaultdict(int),
                'bounces': defaultdict(int),
            }

            def _to_day_key(raw_x):
                try:
                    if isinstance(raw_x, (int, float)):
                        return datetime.utcfromtimestamp(float(raw_x) / 1000.0).date().isoformat()
                    if isinstance(raw_x, str) and len(raw_x) >= 10:
                        parsed = dateparser.parse(raw_x)
                        if parsed:
                            return parsed.date().isoformat()
                except Exception:
                    return None
                return None

            def _add_series_points(target, series_rows):
                for point in series_rows or []:
                    if not isinstance(point, dict):
                        continue
                    day_key = _to_day_key(point.get('x'))
                    if not day_key:
                        continue
                    target[day_key] += int(point.get('y', 0) or 0)

            # Time series: include visitors/views/visits/bounces where available.
            if view_type == 'global':
                pv_params = {'startAt': start_ms, 'endAt': end_ms, 'unit': 'day'}
                pv = _umami_request('pageviews', pv_params) or {}
                _add_series_points(metric_maps['visitors'], pv.get('visitors') or [])
                _add_series_points(metric_maps['views'], pv.get('pageviews') or pv.get('views') or [])
                _add_series_points(metric_maps['visits'], pv.get('sessions') or pv.get('visits') or [])

                if not metric_maps['visitors']:
                    _add_series_points(metric_maps['visitors'], pv.get('sessions') or pv.get('pageviews') or [])
                if not metric_maps['visits']:
                    metric_maps['visits'].update(metric_maps['visitors'])

                # Build daily bounce counts from session rows when available.
                sess_params = {'startAt': start_ms, 'endAt': end_ms, 'pageSize': 99999}
                sess_resp = _umami_request('sessions', sess_params) or {}
                sess_rows = sess_resp.get('data', []) if isinstance(sess_resp, dict) else []
                for s in sess_rows:
                    if not isinstance(s, dict):
                        continue
                    ts = s.get('createdAt') or s.get('firstAt') or s.get('lastAt')
                    day_key = _to_day_key(ts)
                    if not day_key:
                        continue
                    if bool(s.get('bounce') or s.get('isBounce') or s.get('bounced')):
                        metric_maps['bounces'][day_key] += 1
            else:
                seen_session_days = set()
                session_requests = [
                    ('sessions', {
                        'startAt': start_ms,
                        'endAt': end_ms,
                        'pageSize': 99999,
                        'path': f'/documents/{slug}',
                    })
                    for slug in selected_slugs
                ]
                for sess in _umami_requests_parallel(session_requests):
                    sess_list = sess.get('data', []) if isinstance(sess, dict) else []
                    for s in sess_list:
                        if not isinstance(s, dict):
                            continue
                        sid = s.get('id') or s.get('sessionId') or ''
                        ts = s.get('createdAt') or s.get('firstAt') or s.get('lastAt') or ''
                        day_key = _to_day_key(ts)
                        if not sid or not day_key:
                            continue
                        dedupe_key = (day_key, sid)
                        if dedupe_key in seen_session_days:
                            continue
                        seen_session_days.add(dedupe_key)

                        metric_maps['visitors'][day_key] += 1
                        metric_maps['visits'][day_key] += 1
                        views_for_session = int(s.get('pageviews') or s.get('views') or 1)
                        metric_maps['views'][day_key] += max(1, views_for_session)
                        if bool(s.get('bounce') or s.get('isBounce') or s.get('bounced')):
                            metric_maps['bounces'][day_key] += 1

            # Normalize to a complete daily timeline so the graph always spans
            # the full selected range, even on days with zero activity.
            cur_day = start_date.date()
            last_day = end_date.date()
            labels = []
            visitors_series = []
            views_series = []
            visits_series = []
            bounces_series = []
            while cur_day <= last_day:
                day_key = cur_day.isoformat()
                labels.append(f'{day_key}T00:00:00Z')
                visitors_series.append(int(metric_maps['visitors'].get(day_key, 0) or 0))
                views_series.append(int(metric_maps['views'].get(day_key, 0) or 0))
                visits_series.append(int(metric_maps['visits'].get(day_key, 0) or 0))
                bounces_series.append(int(metric_maps['bounces'].get(day_key, 0) or 0))
                cur_day = cur_day + timedelta(days=1)

            chart_data['labels'] = labels
            chart_data['counts'] = visitors_series
            chart_data['views'] = views_series
            chart_data['visits'] = visits_series
            chart_data['bounces'] = bounces_series

            # Summary totals - use metrics/expanded endpoint with proper filter syntax
            total_views = 0
            total_visits = 0
            total_visitors = 0
            total_bounces = 0
            total_time_ms = 0
            
            try:
                if view_type == 'global':
                    # Global: fetch all paths and sum
                    metrics_resp = _umami_request('metrics/expanded', {'startAt': start_ms, 'endAt': end_ms, 'type': 'path', 'limit': 99999}) or []
                    # Sum all paths
                    for metric in metrics_resp:
                        if isinstance(metric, dict):
                            total_views += int(metric.get('pageviews', 0) or 0)
                            total_visits += int(metric.get('visits', 0) or 0)
                            total_visitors += int(metric.get('visitors', 0) or 0)
                            total_bounces += int(metric.get('bounces', 0) or 0)
                            total_time_ms += int(metric.get('totaltime', 0) or 0)
                else:
                    # Scoped view: query each selected path directly.
                    # This avoids data loss when Umami caps global path rows.
                    metric_requests = [
                        ('metrics/expanded', {
                            'startAt': start_ms,
                            'endAt': end_ms,
                            'type': 'path',
                            'path': f'/documents/{slug}',
                            'limit': 99999,
                        })
                        for slug in selected_slugs
                    ]
                    for slug, metric_rows in zip(selected_slugs, _umami_requests_parallel(metric_requests)):
                        metric_rows = metric_rows or []

                        if isinstance(metric_rows, dict):
                            metric_rows = [metric_rows]

                        for metric in metric_rows:
                            if not isinstance(metric, dict):
                                continue
                            metric_path = metric.get('name', '')
                            if metric_path and not metric_path.startswith(f'/documents/{slug}'):
                                continue
                            total_views += int(metric.get('pageviews', 0) or 0)
                            total_visits += int(metric.get('visits', 0) or 0)
                            total_visitors += int(metric.get('visitors', 0) or 0)
                            total_bounces += int(metric.get('bounces', 0) or 0)
                            total_time_ms += int(metric.get('totaltime', 0) or 0)
            except Exception as e:
                logger.exception(f'Error fetching metrics/expanded: {e}')
            
            total_time_str = f'{total_time_ms // 1000}s' if total_time_ms > 0 else '0s'
            
            summary_cards = [
                {'label': 'Views', 'value': total_views},
                {'label': 'Visitors', 'value': total_visitors},
                {'label': 'Visits', 'value': total_visits},
                {'label': 'Bounces', 'value': total_bounces},
            ]

            # Top pages
            pages_acc = defaultdict(int)
            slug_map = {d.slug: d.title for d in documents}
            for m in relevant_metrics:
                path = m.get('x', '')
                cnt = int(m.get('y', 0) or 0)
                matched = None
                for slug, title in slug_map.items():
                    if f"/{slug}" in path:
                        matched = title
                        break
                pages_acc[matched or path] += cnt
            top_pages_stats = sorted(pages_acc.items(), key=lambda x: x[1], reverse=True)[:15]

            # Referrers - fetch sessions and aggregate by referrer field
            try:
                sess_params = {'startAt': start_ms, 'endAt': end_ms, 'pageSize': 99999}
                if view_type != 'global' and selected_slugs:
                    if len(selected_slugs) == 1:
                        sess_params['path'] = f"/documents/{selected_slugs[0]}"
                sess_resp = _umami_request('sessions', sess_params) or {}
                sess_list = sess_resp.get('data', []) if isinstance(sess_resp, dict) else sess_resp
                ref_acc = defaultdict(int)
                for s in sess_list:
                    if not isinstance(s, dict):
                        logger.warning(f'Session item not dict: {type(s)}')
                        continue
                    ref = s.get('referrer') or s.get('referrerName') or 'Direct / Unknown'
                    ref_acc[ref] += 1
                referer_stats = sorted(ref_acc.items(), key=lambda x: x[1], reverse=True)[:15]
            except Exception as e:
                logger.exception(f'Error fetching referrers: {e}')
                referer_stats = []

            # Countries - fetch scoped metrics and aggregate by country field
            try:
                if view_type == 'global':
                    country_rows = _umami_request('metrics', {
                        'startAt': start_ms,
                        'endAt': end_ms,
                        'type': 'country',
                        'limit': 99999,
                    }) or []
                else:
                    agg = defaultdict(int)
                    country_requests = [
                        ('metrics', {
                            'startAt': start_ms,
                            'endAt': end_ms,
                            'type': 'country',
                            'path': f'/documents/{slug}',
                            'limit': 99999,
                        })
                        for slug in selected_slugs
                    ]
                    for rows in _umami_requests_parallel(country_requests):
                        rows = rows or []
                        for row in rows:
                            if not isinstance(row, dict):
                                continue
                            code = (row.get('x') or '').strip().upper()
                            if not code:
                                continue
                            agg[code] += int(row.get('y', 0) or 0)
                    country_rows = [{'x': code, 'y': count} for code, count in agg.items()]

                country_list = []
                for row in (country_rows or []):
                    code = (row.get('x') or '').upper()
                    count = int(row.get('y') or 0)
                    if not code:
                        continue
                    name = COUNTRY_CODE_MAP.get(code, code)
                    country_list.append((name, count))
                country_stats = sorted(country_list, key=lambda x: x[1], reverse=True)
                analytics_source = 'Umami'
            except Exception as e:
                logger.exception(f'Error fetching countries: {e}')
                country_stats = []
                analytics_source = 'Umami'
            # Cities: build a country -> [{city, count}, ...] map
            try:
                country_cities_map = {}

                def _add_city(country_name, city_label, cnt):
                    if not country_name:
                        country_name = 'Unknown'
                    lst = country_cities_map.setdefault(country_name, [])
                    lst.append({'city': city_label, 'count': cnt})

                def _normalize_region(region_value):
                    value = str(region_value or '').strip()
                    if not value:
                        return ''
                    # Umami often returns ISO-3166-2 style values like US-OK / CA-ON.
                    if '-' in value:
                        tail = value.split('-')[-1].strip()
                        if tail:
                            return tail
                    return value

                # Fallback lookup from sessions for US/CA city -> most common region.
                region_counts = defaultdict(lambda: defaultdict(int))

                def _collect_region_lookup(sess_rows):
                    for s in sess_rows or []:
                        if not isinstance(s, dict):
                            continue
                        country_code = (s.get('country') or s.get('countryCode') or '').strip().upper()
                        if country_code not in {'US', 'CA'}:
                            continue
                        city_name = (s.get('city') or '').strip()
                        region_name = _normalize_region(s.get('region') or s.get('subdivision') or '')
                        if city_name and region_name:
                            region_counts[(country_code, city_name.lower())][region_name] += 1

                sess_params = {'startAt': start_ms, 'endAt': end_ms, 'pageSize': 99999}
                if view_type == 'global':
                    sess_resp = _umami_request('sessions', sess_params) or {}
                    sess_rows = sess_resp.get('data', []) if isinstance(sess_resp, dict) else []
                    _collect_region_lookup(sess_rows)
                else:
                    if len(selected_slugs) == 1:
                        sess_params['path'] = f"/documents/{selected_slugs[0]}"
                        sess_resp = _umami_request('sessions', sess_params) or {}
                        sess_rows = sess_resp.get('data', []) if isinstance(sess_resp, dict) else []
                        _collect_region_lookup(sess_rows)
                    else:
                        region_requests = [
                            ('sessions', {
                                'startAt': start_ms,
                                'endAt': end_ms,
                                'pageSize': 99999,
                                'path': f'/documents/{slug}',
                            })
                            for slug in selected_slugs
                        ]
                        for sess_resp in _umami_requests_parallel(region_requests):
                            sess_resp = sess_resp or {}
                            sess_rows = sess_resp.get('data', []) if isinstance(sess_resp, dict) else []
                            _collect_region_lookup(sess_rows)

                city_agg = defaultdict(lambda: defaultdict(int))
                if view_type == 'global':
                    city_rows = _umami_request('metrics', {
                        'startAt': start_ms,
                        'endAt': end_ms,
                        'type': 'city',
                        'limit': 99999,
                    }) or []
                    for row in city_rows:
                        if not isinstance(row, dict):
                            continue
                        city = row.get('x') or row.get('city') or ''
                        country_code = (row.get('country') or row.get('countryCode') or row.get('country_code') or '').strip().upper()
                        if not city or not country_code:
                            continue
                        country_name = COUNTRY_CODE_MAP.get(country_code, country_code)
                        city_agg[country_name][city] += int(row.get('y', 0) or 0)
                else:
                    city_requests = [
                        ('metrics', {
                            'startAt': start_ms,
                            'endAt': end_ms,
                            'type': 'city',
                            'path': f'/documents/{slug}',
                            'limit': 99999,
                        })
                        for slug in selected_slugs
                    ]
                    for rows in _umami_requests_parallel(city_requests):
                        rows = rows or []
                        for row in rows:
                            if not isinstance(row, dict):
                                continue
                            city = row.get('x') or row.get('city') or ''
                            country_code = (row.get('country') or row.get('countryCode') or row.get('country_code') or '').strip().upper()
                            if not city or not country_code:
                                continue
                            country_name = COUNTRY_CODE_MAP.get(country_code, country_code)
                            city_agg[country_name][city] += int(row.get('y', 0) or 0)

                for country_name, cities in city_agg.items():
                    country_code = (COUNTRY_NAME_TO_CODE.get(str(country_name).lower()) or str(country_name)).upper()
                    for city_name, cnt in cities.items():
                        label = _format_city_label(city_name, country_code)
                        if country_code in {'US', 'CA'}:
                            region = ''
                            region_from_metric = _normalize_region('')
                            if region_from_metric:
                                region = region_from_metric
                            if not region:
                                key = (country_code, str(city_name).strip().lower())
                                reg_map = region_counts.get(key) or {}
                                if reg_map:
                                    region = max(reg_map.items(), key=lambda kv: kv[1])[0]
                            if region:
                                if country_code == 'US':
                                    region = US_STATE_CODE_TO_NAME.get(str(region).upper(), region)
                                elif country_code == 'CA':
                                    region = CA_PROVINCE_CODE_TO_NAME.get(str(region).upper(), region)
                                label = f"{city_name}, {region}"
                        _add_city(country_name, label, cnt)

                # sort and trim lists
                for cn, lst in list(country_cities_map.items()):
                    lst.sort(key=lambda x: int(x.get('count', 0) or 0), reverse=True)
                    country_cities_map[cn] = lst[:100]
            except Exception:
                country_cities_map = {}
        except Exception:
            logger.exception('Failed to fetch Umami data; falling back to local aggregates')

    # Local fallback for summary if nothing from Umami
    if not summary_cards:
        docs_qs = PDFDocument.objects.filter(is_published=True)
        summary_cards = [
            {'label': 'Documents', 'value': docs_qs.count()},
            {'label': 'Document Views', 'value': DownloadLog.objects.count()},
        ]

    # Helper to extract year from PDFDocument instance (tries multiple sources)
    def _extract_year_from_doc(d):
        year = None

        # Prefer slug year (conbook year) over upload-path year.
        if not year:
            try:
                slug = getattr(d, 'slug', '') or ''
                # Try pattern: dash-year-dash (most common)
                m = re.search(r'-([0-9]{4})-', slug)
                if not m:
                    # Try pattern: dash-year at start or end
                    m = re.search(r'-([0-9]{4})(?:[^0-9]|$)', slug)
                if not m:
                    # Try pattern: any year surrounded by non-digits
                    m = re.search(r'(?:^|[^0-9])([0-9]{4})(?:[^0-9]|$)', slug)
                if m:
                    year = m.group(1)
            except Exception:
                year = None

        # Try model year fields
        if not year:
            year_val = getattr(d, 'year', None) or getattr(d, 'copyright_year', None)
            try:
                year = str(int(year_val)) if year_val else None
            except Exception:
                year = None

        # Then try file path year (often upload year, less accurate for conbook year)
        if not year:
            try:
                fname = (d.file.name or '') if getattr(d, 'file', None) else ''
                m = re.search(r'/([0-9]{4})/', fname)
                if not m:
                    m = re.search(r'^pdfs/([0-9]{4})/', fname)
                if m:
                    year = m.group(1)
            except Exception:
                year = None

        # Fall back to upload date
        if not year:
            try:
                year = str(d.uploaded_at.year) if getattr(d, 'uploaded_at', None) else 'Unknown'
            except Exception:
                year = 'Unknown'
        
        return year

    # File statistics grouped by year (extract year from file path when available)
    file_stats_by_year = []
    try:
        # Build base queryset scoped to convention/view_type so list of years is relevant
        if view_type == 'convention' and convention_scope_slugs:
            # Keep all convention years visible even when one year is selected.
            base_qs = PDFDocument.objects.filter(slug__in=convention_scope_slugs)
        elif selected_convention:
            base_qs = PDFDocument.objects.filter(convention_name__icontains=selected_convention)
        else:
            base_qs = PDFDocument.objects.all()

        # Compute aggregates for all years (for the UI selector)
        agg_all = defaultdict(lambda: {'count': 0, 'size': 0})
        for d in base_qs:
            year = _extract_year_from_doc(d)

            try:
                size = int(getattr(d, 'file_size', 0) or 0)
            except Exception:
                size = 0

            agg_all[year]['count'] += 1
            agg_all[year]['size'] += size

        file_stats_by_year = [
            {'year': y, 'count': v['count'], 'size': v['size']} for y, v in agg_all.items()
        ]
        # Sort numeric years descending, keep 'Unknown' last
        def _sort_key(item):
            y = item.get('year')
            try:
                return (0, -int(y))
            except Exception:
                return (1, 0)

        file_stats_by_year.sort(key=_sort_key)

        # If a specific year is selected, compute filtered totals for that year
        file_stats_selected = None
        if selected_file_year:
            sel = selected_file_year
            cnt = 0
            sz = 0
            # restrict to base_qs and match by extracted year
            for d in base_qs:
                year = _extract_year_from_doc(d)
                if year == sel:
                    cnt += 1
                    try:
                        sz += int(getattr(d, 'file_size', 0) or 0)
                    except Exception:
                        pass

            file_stats_selected = {'year': sel, 'count': cnt, 'size': sz}
        else:
            file_stats_selected = None
    except Exception:
        file_stats_by_year = []
        file_stats_selected = None

    # Optionally build a file list for the selected year or for all files when requested
    show_files = bool(request.GET.get('show_files'))
    file_list_for_year = []
    try:
        if show_files:
            # build list from base_qs (defined above when computing agg_all)
            # fall back to PDFDocument.objects if base_qs isn't in scope
            try:
                base_qs  # noqa: F821
            except Exception:
                if view_type == 'convention' and selected_slugs:
                    base_qs = PDFDocument.objects.filter(slug__in=selected_slugs)
                elif selected_convention:
                    base_qs = PDFDocument.objects.filter(convention_name__icontains=selected_convention)
                else:
                    base_qs = PDFDocument.objects.all()

            max_rows = 2000
            added = 0
            for d in base_qs.order_by('-uploaded_at'):
                if added >= max_rows:
                    break
                year = _extract_year_from_doc(d)

                if selected_file_year and selected_file_year != '' and year != selected_file_year:
                    continue

                file_list_for_year.append({
                    'title': d.title,
                    'slug': d.slug,
                    'filename': getattr(d, 'filename', getattr(d, 'file', {}).name if getattr(d, 'file', None) else ''),
                    'size': int(getattr(d, 'file_size', 0) or 0),
                    'uploaded_at': getattr(d, 'uploaded_at', None),
                })
                added += 1
    except Exception:
        file_list_for_year = []

    context = {
        'documents': documents,
        'conventions': conventions,
        'selected_doc': selected_doc,
        'selected_convention': selected_convention,
        'chart_data': json.dumps(chart_data),
        'referer_stats': referer_stats,
        'top_pages_stats': top_pages_stats,
        'country_stats': country_stats,
        'country_cities_map': json.dumps(country_cities_map),
        'country_name_to_code': json.dumps(COUNTRY_NAME_TO_CODE),
        'region_name_to_code': json.dumps(REGION_NAME_TO_CODE),
        'summary_cards': summary_cards,
        'analytics_source': analytics_source,
        'chart_title': f'Analytics ({start_date.strftime("%Y-%m-%d")} to {end_date.strftime("%Y-%m-%d")})',
        'start_date': start_date.strftime('%Y-%m-%d'),
        'end_date': end_date.strftime('%Y-%m-%d'),
        'preset': preset,
        'view_type': view_type,
        'file_stats_by_year': file_stats_by_year,
        'file_stats_by_year_json': json.dumps(file_stats_by_year),
        'selected_file_year': selected_file_year or None,
        'file_stats_selected': file_stats_selected,
        'show_files': show_files,
        'file_list_for_year': file_list_for_year,
        'app_key': request.GET.get('app_key', '').strip(),
    }
    
    # Log the JSON being sent to template
    try:
        json_len = len(json.dumps(country_cities_map))
        json_sample = json.dumps(country_cities_map)[:100]
        logger.info(f'Context being sent to template: country_stats={len(country_stats)}, country_cities_map_json_len={json_len}, first_chars={json_sample}')
    except Exception as e:
        logger.error(f'Failed to JSON dump country_cities_map: {e}, type={type(country_cities_map)}')
    try:
        logger.debug('compute_statistics_context: view_type=%s chart_labels=%s country_stats_len=%d country_cities_map_keys=%d',
                     view_type, len(chart_data.get('labels', [])), len(country_stats), len(country_cities_map) if isinstance(country_cities_map, dict) else 0)
    except Exception:
        logger.exception('compute_statistics_context: failed to log debug info')
    return context

def _published_documents_queryset():
    """Base queryset for public document listings — never loads OCR blobs."""
    return PDFDocument.objects.filter(
        is_published=True,
        takedown_by_request=False,
    ).select_related('category').defer('ocr_text')


def _published_documents_filter_q(*, relation_prefix=''):
    if relation_prefix:
        return Q(**{
            f'{relation_prefix}__is_published': True,
            f'{relation_prefix}__takedown_by_request': False,
        })
    return Q(is_published=True, takedown_by_request=False)


def _published_document_count_cached():
    """Cached total for unfiltered document listings (avoids slow paginator COUNT)."""
    cache_key = 'archive:published_doc_count:v2'
    count = cache.get(cache_key)
    if count is None:
        count = PDFDocument.objects.filter(is_published=True, takedown_by_request=False).count()
        cache.set(cache_key, count, 300)
    return count


def _document_list_has_filters(filter_state):
    return bool(
        filter_state.get('search_query')
        or filter_state.get('selected_category')
        or filter_state.get('current_year')
        or filter_state.get('current_convention')
        or filter_state.get('current_sort') != 'random'
    )


def _document_list_total_count(count_queryset, filter_state):
    if _document_list_has_filters(filter_state):
        return count_queryset.count()
    return _published_document_count_cached()


def _paginate_with_count(queryset, page_number, total_count, per_page=21):
    paginator = Paginator(queryset, per_page)
    paginator.__dict__['count'] = total_count
    return paginator.get_page(page_number)


def _apply_document_search(queryset, search_query, *, include_ocr=False):
    """Filter documents by search. Metadata fields are fast; OCR is optional and capped."""
    term = (search_query or '').strip()
    if not term:
        return queryset

    meta_q = (
        Q(title__icontains=term)
        | Q(description__icontains=term)
        | Q(author__icontains=term)
        | Q(convention_name__icontains=term)
    )

    if not include_ocr or len(term) < 3:
        return queryset.filter(meta_q)

    meta_ids = list(queryset.filter(meta_q).values_list('pk', flat=True)[:400])
    ocr_ids = list(
        PDFDocument.objects.filter(is_published=True, takedown_by_request=False, ocr_text__icontains=term)
        .values_list('pk', flat=True)[:200]
    )
    combined_ids = list(dict.fromkeys(meta_ids + [pk for pk in ocr_ids if pk not in meta_ids]))
    if not combined_ids:
        return queryset.none()
    return queryset.filter(pk__in=combined_ids)


def _build_document_queryset(request):
    documents = _published_documents_queryset()

    # Keep a per-session seed so the main document ordering feels random
    # while staying stable for the same visitor until their session changes.
    session_seed = request.session.get('document_order_seed')
    if not session_seed:
        session_seed = uuid.uuid4().hex
        request.session['document_order_seed'] = session_seed
        request.session.modified = True

    search_query = request.GET.get('search', '')
    if search_query:
        documents = _apply_document_search(documents, search_query, include_ocr=True)

    # Filter by category
    category_slug = request.GET.get('category', '')
    selected_category = None
    if category_slug:
        selected_category = get_object_or_404(Category, slug=category_slug)
        documents = documents.filter(category=selected_category)

    # Filter by year
    year = request.GET.get('year', '')
    if year:
        documents = documents.filter(year=year)

    # Filter by convention
    convention = request.GET.get('convention', '')
    if convention:
        documents = documents.filter(convention_name__icontains=convention)

    # Sorting - default to random order, stable within a session.
    sort_by = request.GET.get('sort', 'random')
    valid_sorts = ['random', 'title', '-title', 'year', '-year', 'uploaded_at', '-uploaded_at', 'views', '-views']
    if sort_by not in valid_sorts:
        sort_by = 'random'

    count_queryset = documents

    if sort_by == 'random':
        documents = documents.annotate(
            _shuffle=MD5(Concat(F('slug'), Value(str(session_seed))))
        ).order_by('_shuffle', 'pk')
    elif sort_by == 'title':
        documents = documents.order_by('title', 'pk')
    elif sort_by == '-title':
        documents = documents.order_by('-title', 'pk')
    elif sort_by in {'year', '-year', 'uploaded_at', '-uploaded_at'}:
        documents = documents.order_by(sort_by, 'pk')
    elif sort_by in {'views', '-views'}:
        # Umami view ordering is applied before pagination.
        documents = documents.order_by('pk')
    else:
        documents = documents.order_by(sort_by, 'pk')

    return documents, {
        'search_query': search_query,
        'selected_category': selected_category,
        'current_year': year,
        'current_convention': convention,
        'current_sort': sort_by,
        'count_queryset': count_queryset,
    }


def _paginate_document_list(documents, filter_state, page_number):
    sort_by = filter_state['current_sort']
    if sort_by in {'views', '-views'}:
        documents = sort_documents_by_pdf_views(
            documents,
            descending=(sort_by == '-views'),
        )
    total_count = _document_list_total_count(filter_state['count_queryset'], filter_state)
    page_obj = _paginate_with_count(documents, page_number, total_count)
    attach_pdf_view_counts(page_obj, warm_missing=True)
    return page_obj, total_count


def document_list(request):
    """Document browse shell; results load via /v1/documents/."""
    search_query = request.GET.get('search', '')
    category_slug = request.GET.get('category', '')
    selected_category = None
    if category_slug:
        selected_category = get_object_or_404(Category, slug=category_slug)

    all_categories = Category.objects.annotate(
        doc_count=Count('documents', filter=_published_documents_filter_q(relation_prefix='documents')),
    ).filter(doc_count__gt=0).order_by('name')

    available_years = PDFDocument.objects.filter(
        is_published=True,
        takedown_by_request=False,
        year__isnull=False,
    ).values_list('year', flat=True).distinct().order_by('-year')

    available_conventions = PDFDocument.objects.filter(
        is_published=True,
        takedown_by_request=False,
        convention_name__isnull=False,
    ).exclude(convention_name='').values_list('convention_name', flat=True).distinct().order_by('convention_name')

    total_document_count = _published_document_count_cached()
    available_years_list = list(available_years)
    year = request.GET.get('year', '')
    convention = request.GET.get('convention', '')
    sort_by = request.GET.get('sort', 'random')

    context = {
        'page_obj': None,
        'all_categories': all_categories,
        'selected_category': selected_category,
        'search_query': search_query,
        'available_years': available_years_list,
        'recent_years': available_years_list[:6],
        'available_conventions': available_conventions,
        'current_year': year,
        'current_convention': convention,
        'current_sort': sort_by,
        'total_document_count': total_document_count,
        'has_filters': bool(search_query or selected_category or year or convention),
    }

    return render(request, 'archive/document_list.html', context)


@require_GET
def document_list_data(request):
    documents, filter_state = _build_document_queryset(request)

    page_obj, total_count = _paginate_document_list(documents, filter_state, request.GET.get('page'))

    from archive.document_descriptions import attach_display_descriptions
    attach_display_descriptions(page_obj, allow_network=False)

    html = render_to_string('archive/partials/document_cards.html', {'page_obj': page_obj})

    return JsonResponse({
        'html': html,
        'has_next': page_obj.has_next(),
        'next_page': page_obj.next_page_number() if page_obj.has_next() else None,
        'total_count': total_count,
        'start_index': page_obj.start_index() if page_obj.paginator.count else 0,
        'end_index': page_obj.end_index() if page_obj.paginator.count else 0,
        'page_number': page_obj.number,
    })



MLPCON_API_URL = 'https://mlpcon.info/api/api.json'
MLPCON_API_CACHE_NAMESPACE = 'mlpcon_api'
MLPCON_API_CACHE_VERSION = 'v1'
MLPCON_API_REFRESH_HOUR = 2
_mlpcon_api_lock = threading.Lock()


def _mlpcon_api_cache_keys(now=None):
    try:
        local_now = timezone.localtime(now or timezone.now())
    except Exception:
        local_now = timezone.now()

    try:
        local_hour = local_now.hour
    except Exception:
        local_hour = 0

    try:
        bucket_day = local_now.date()
    except Exception:
        bucket_day = timezone.now().date()

    if local_hour < MLPCON_API_REFRESH_HOUR:
        bucket_day = (local_now - timedelta(days=1)).date()

    bucket_key = f'{MLPCON_API_CACHE_VERSION}:{bucket_day.isoformat()}'
    fallback_key = f'{MLPCON_API_CACHE_VERSION}:{(bucket_day - timedelta(days=1)).isoformat()}'
    return bucket_key, fallback_key


def _load_mlpcon_api_dataset(refresh=False):
    bucket_key, fallback_key = _mlpcon_api_cache_keys()

    def _read_cached_payload(cache_key):
        cached_payload = cache.get(cache_key)
        if cached_payload is not None:
            return cached_payload

        cached_local = load_site_json(MLPCON_API_CACHE_NAMESPACE, cache_key, None)
        if cached_local is not None:
            try:
                cache.set(cache_key, cached_local, 60 * 60 * 25)
            except Exception:
                pass
        return cached_local

    if not refresh:
        cached_payload = _read_cached_payload(bucket_key)
        if isinstance(cached_payload, dict) and isinstance(cached_payload.get('data'), list):
            return cached_payload

    lock_key = f'mlpcon:api:lock:{bucket_key}'
    acquired_lock = False
    try:
        acquired_lock = cache.add(lock_key, '1', 120)
    except Exception:
        acquired_lock = False

    if not acquired_lock and not refresh:
        cached_payload = _read_cached_payload(bucket_key)
        if isinstance(cached_payload, dict) and isinstance(cached_payload.get('data'), list):
            return cached_payload
        fallback_payload = _read_cached_payload(fallback_key)
        if isinstance(fallback_payload, dict) and isinstance(fallback_payload.get('data'), list):
            return fallback_payload

    try:
        with _mlpcon_api_lock:
            try:
                response = requests_session.get(MLPCON_API_URL, timeout=8)
                response.raise_for_status()
                payload = response.json()
            except Exception:
                payload = None
    finally:
        try:
            if acquired_lock:
                cache.delete(lock_key)
        except Exception:
            pass

    if isinstance(payload, list):
        payload = {
            'success': True,
            'count': len(payload),
            'totalCount': len(payload),
            'data': payload,
        }
    elif not isinstance(payload, dict):
        payload = None

    if isinstance(payload, dict):
        rows = payload.get('data')
        if not isinstance(rows, list):
            rows = []
        payload = dict(payload)
        payload['data'] = rows
        payload['_cache_bucket'] = bucket_key
        payload['_source_url'] = MLPCON_API_URL

        try:
            cache.set(bucket_key, payload, 60 * 60 * 25)
        except Exception:
            pass
        try:
            store_site_json(MLPCON_API_CACHE_NAMESPACE, bucket_key, payload)
            ensure_registry(MLPCON_API_CACHE_NAMESPACE, bucket_key)
        except Exception:
            pass
        return payload

    fallback_payload = _read_cached_payload(fallback_key)
    if isinstance(fallback_payload, dict) and isinstance(fallback_payload.get('data'), list):
        return fallback_payload

    return None


def _consurf_api_cache_bucket(now=None):
    try:
        local_now = timezone.localtime(now or timezone.now())
    except Exception:
        local_now = timezone.now()

    try:
        bucket_day = local_now.date()
        allow_legacy_fallback = local_now.hour < 2
        if allow_legacy_fallback:
            bucket_day = (local_now - timedelta(days=1)).date()
    except Exception:
        bucket_day = timezone.now().date()
        allow_legacy_fallback = False

    return bucket_day.isoformat(), allow_legacy_fallback


def _location_text(value):
    """Turn a Consurf location field into a display string, ignoring nested junk."""
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, dict):
        for key in ('formatted', 'address', 'latestAddress', 'latest_address', 'city'):
            text = _location_text(value.get(key))
            if text:
                return text
    return None


def _get_consurf_event(convention_name, year=None, slug_hint=None, refresh=False, allow_network=True):
    """Fetch event data from consurf API by convention name"""
    if not convention_name:
        return None
    
    try:
        from datetime import datetime
        import re
        from django.utils.text import slugify
        import html
        from archive.consurf_client import consurf_get, is_consurf_rate_limited

        rate_limited = False

        def _consurf_request(url, timeout=2):
            nonlocal rate_limited
            response = consurf_get(url, timeout=timeout, logger=logger)
            if is_consurf_rate_limited(response):
                rate_limited = True
            return response
        
        # Create a cleaned slug using Django's slugify for robustness
        base_slug = slugify(convention_name)
        if slug_hint:
            try:
                hinted = slugify(str(slug_hint))
                if hinted:
                    base_slug = hinted
            except Exception:
                pass

        # Remove common suffixes like '-conbook' (case-insensitive)
        if base_slug.endswith('-conbook'):
            base_slug = base_slug[:-8]

        # Remove trailing hyphens
        base_slug = base_slug.strip('-')
        
        # Infer year from slug hint or convention name when not provided
        inferred_year = None
        if year is None and slug_hint:
            hint_match = re.search(r'-((?:19|20)\d{2})(?:-|$)', str(slug_hint))
            if hint_match:
                inferred_year = int(hint_match.group(1))
        if year is None and inferred_year is None:
            match = re.search(r"\b(19|20)\d{2}\b", convention_name)
            if match:
                inferred_year = int(match.group(0))
        effective_year = year if year is not None else inferred_year
        logger.debug("_get_consurf_event: initial slug for '%s' -> %s (year=%s)", convention_name, base_slug, effective_year)

        def _extract_payload_year(payload):
            """Best-effort year extraction from a Consurf/MLP payload."""
            if not isinstance(payload, dict):
                return None

            candidate_dates = []

            for key in ('startDate', 'endDate', 'firstEventDate', 'lastEventDate', 'start_date', 'end_date'):
                raw = payload.get(key)
                if raw:
                    candidate_dates.append(raw)

            events_list = payload.get('events') or payload.get('result') or []
            if isinstance(events_list, list):
                for ev in events_list:
                    if not isinstance(ev, dict):
                        continue
                    for key in ('startDate', 'endDate', 'start_date', 'end_date'):
                        raw = ev.get(key)
                        if raw:
                            candidate_dates.append(raw)

            for raw in candidate_dates:
                try:
                    parsed = dateparser.parse(str(raw))
                except Exception:
                    continue
                if parsed:
                    return int(parsed.year)
            return None

        def _year_score(item_year, target_year):
            if item_year is None or target_year is None:
                return 0.0
            diff = abs(int(item_year) - int(target_year))
            if diff == 0:
                return 0.5
            if diff == 1:
                return 0.25
            if diff == 2:
                return 0.12
            if diff > 5:
                return -1.0
            return -0.05 * (diff - 2)

        cache_version = 'v5'
        bucket_key, allow_legacy_fallback = _consurf_api_cache_bucket()
        legacy_cache_key = f"consurf:event:{cache_version}:{base_slug}:{effective_year if effective_year is not None else 'none'}"
        cache_key = f"consurf:event:{cache_version}:{bucket_key}:{base_slug}:{effective_year if effective_year is not None else 'none'}"
        cached_result = cache.get(cache_key, None)
        if cached_result is None and allow_legacy_fallback:
            cached_result = cache.get(legacy_cache_key, None)
        if cached_result == "__none__":
            logger.debug("_get_consurf_event: cached negative hit for %s (%s)", base_slug, cache_key)
            return None
        if cached_result is not None:
            logger.debug("_get_consurf_event: cache hit for %s (%s)", base_slug, cache_key)
            try:
                cache.set(cache_key, cached_result, 60 * 60 * 24)
            except Exception:
                pass
            return cached_result
        logger.debug("_get_consurf_event: cache miss for %s (%s)", base_slug, cache_key)

        local_cache_key = f"{cache_version}:{bucket_key}:{base_slug}:{effective_year if effective_year is not None else 'none'}"
        legacy_local_cache_key = f"{cache_version}:{base_slug}:{effective_year if effective_year is not None else 'none'}"
        if not refresh:
            cached_local = load_site_json('consurf_events', local_cache_key, 60 * 60 * 24)
            if not isinstance(cached_local, dict) and allow_legacy_fallback:
                cached_local = load_site_json('consurf_events', legacy_local_cache_key, 60 * 60 * 24)
            if isinstance(cached_local, dict):
                if cached_local.get('none'):
                    logger.debug("_get_consurf_event: local cache negative hit for %s (%s)", base_slug, local_cache_key)
                    cache.set(cache_key, "__none__", 60 * 60 * 24)
                    return None
                cached_local = deserialize_consurf_event(cached_local)
                logger.debug("_get_consurf_event: local cache hit for %s (%s)", base_slug, local_cache_key)
                cache.set(cache_key, cached_local, 60 * 60 * 24)
                return cached_local

        if not allow_network:
            logger.debug("_get_consurf_event: network disabled for %s; returning miss", base_slug)
            cache.set(cache_key, "__none__", 60 * 60 * 24)
            return None

        event_slug_source = base_slug

        # If the base slug includes the year, remove it and everything after it
        # This handles cases like "anthro-new-england-2024-its-a-noreastah" -> "anthro-new-england"
        if effective_year:
            # Remove -YYYY and anything that comes after it (like -its-a-noreastah)
            year_pattern = rf"-{effective_year}(?:-.*)?$"
            base_slug = re.sub(year_pattern, "", base_slug)
            base_slug = base_slug.strip('-')

        def _build_slug_variations(include_nearby_years=False):
            from archive.consurf_slugs import consurf_convention_roots, consurf_year_event_slugs, expand_consurf_slug

            # Prefer themed/year-specific slugs from the original document slug first.
            slug_variations = []

            if effective_year:
                slug_variations.extend(consurf_year_event_slugs(event_slug_source, effective_year))

            slug_variations.append(base_slug)

            # Only try nearby years if the primary lookup fails.
            if effective_year and include_nearby_years:
                slug_variations.extend([
                    f"{base_slug}-{effective_year + 1}",
                    f"{base_slug}-{effective_year - 1}",
                ])

            for root in consurf_convention_roots(event_slug_source):
                slug_variations.append(root)
                if effective_year:
                    slug_variations.extend(consurf_year_event_slugs(root, effective_year))

            seen_slugs = set()
            unique_slugs = []
            for slug_value in slug_variations:
                for candidate in expand_consurf_slug(slug_value):
                    if candidate and candidate not in seen_slugs:
                        unique_slugs.append(candidate)
                        seen_slugs.add(candidate)
            return unique_slugs

        api_base = getattr(settings, 'EXTERNAL_EVENTS_API_BASE', '').rstrip('/') or 'https://consurf.net'

        def _extract_text(value):
            if isinstance(value, dict):
                for key in ('en-US', 'en-us', 'en', 'default'):
                    text = value.get(key)
                    if isinstance(text, str) and text.strip():
                        return text.strip()
                for text in value.values():
                    if isinstance(text, str) and text.strip():
                        return text.strip()
                return None
            if isinstance(value, str):
                text = value.strip()
                return text or None
            return None

        def _clean_text(value):
            if not value:
                return None
            text = html.unescape(str(value))
            text = re.sub(r'<[^>]+>', '', text).strip()
            return text or None

        def _fetch_convention_description(slug_value):
            if not slug_value:
                return None
            convention_url = f"{api_base}/api/external/conventions/{slug_value}"
            logger.debug("_get_consurf_event: requesting convention description for %s", convention_url)
            try:
                resp = _consurf_request(convention_url, timeout=2)
            except Exception as e:
                logger.exception("_get_consurf_event: conventions request failed for %s: %s", convention_url, e)
                return None
            if resp is not None and getattr(resp, 'status_code', None) == 200:
                try:
                    payload = resp.json()
                except Exception:
                    payload = None
                if isinstance(payload, dict):
                    return _clean_text(_extract_text(payload.get('description') or payload.get('summary')))
                if isinstance(payload, list) and payload:
                    first = payload[0]
                    if isinstance(first, dict):
                        return _clean_text(_extract_text(first.get('description') or first.get('summary')))
            return None

        def _description_from_payload(payload):
            if not isinstance(payload, dict):
                return None
            return _clean_text(_extract_text(payload.get('description') or payload.get('summary')))

        def _fetch_event_description(slug_value):
            if not slug_value:
                return None
            endpoints = (
                f"{api_base}/api/external/events/{slug_value}",
                f"{api_base}/api/events/{slug_value}",
            )
            for event_url in endpoints:
                logger.debug("_get_consurf_event: requesting event description for %s", event_url)
                try:
                    resp = _consurf_request(event_url, timeout=2)
                except Exception as e:
                    logger.exception("_get_consurf_event: events request failed for %s: %s", event_url, e)
                    continue
                if resp is not None and getattr(resp, 'status_code', None) == 200:
                    try:
                        payload = resp.json()
                    except Exception:
                        payload = None
                    desc = _description_from_payload(payload)
                    if desc:
                        return desc
                    if isinstance(payload, list) and payload:
                        desc = _description_from_payload(payload[0])
                        if desc:
                            return desc
            return None

        def _parse_consurf_datetime(raw):
            if not raw:
                return None
            try:
                return datetime.fromisoformat(str(raw).replace('Z', '+00:00'))
            except Exception:
                return None

        def _result_from_event_payload(event_payload, event_slug):
            if not isinstance(event_payload, dict):
                return None

            start_date = None
            end_date = None
            for key in ('startDate', 'start_date', 'start'):
                start_date = _parse_consurf_datetime(event_payload.get(key))
                if start_date:
                    break
            for key in ('endDate', 'end_date', 'end'):
                end_date = _parse_consurf_datetime(event_payload.get(key))
                if end_date:
                    break
            if not end_date:
                end_date = start_date

            location = _extract_location(event_payload)
            event_description = _description_from_payload(event_payload)

            conv_root = str(event_slug or '').strip('-')
            year_match = re.match(r'^(.*?)-((?:19|20)\d{2})$', conv_root)
            if year_match:
                conv_root = year_match.group(1).strip('-')

            convention_description = _fetch_convention_description(conv_root)
            text_desc = event_description or convention_description

            return {
                'name': event_payload.get('name') or event_payload.get('title') or conv_root,
                'start_date': start_date,
                'end_date': end_date,
                'location': location,
                'description': text_desc,
                'event_description': event_description,
                'convention_description': convention_description,
                'url': f'https://consurf.net/{event_slug}',
                'slug': event_slug,
                'source': 'consurf',
                'theme': event_payload.get('theme') or '',
                'country': event_payload.get('country') or '',
                'timezone': event_payload.get('timezone') or '',
            }

        def _cache_consurf_result(result):
            for cached_slug in batch_slugs:
                cache.set(f"consurf:event:slug:{bucket_key}:{cached_slug}", result, 60 * 60 * 24)
                cache.set(f"consurf:event:slug:{cached_slug}", result, 60 * 60 * 24)
                ensure_registry('consurf_events', f"slug:{bucket_key}:{cached_slug}")
            cache.set(cache_key, result, 60 * 60 * 24)
            store_site_json('consurf_events', local_cache_key, serialize_consurf_event(result))
            ensure_registry('consurf_events', local_cache_key)

        batch_slugs = _build_slug_variations(True)

        def _fetch_year_payload(slug_value):
            if not slug_value:
                return None
            event_url = f"{api_base}/api/external/events/{slug_value}"
            logger.debug("_get_consurf_event: requesting year payload %s", event_url)
            try:
                ev_resp = _consurf_request(event_url, timeout=2)
            except Exception as e:
                logger.exception("_get_consurf_event: year request failed for %s: %s", event_url, e)
                return None
            if ev_resp is not None and getattr(ev_resp, 'status_code', None) == 200:
                try:
                    ev_data = ev_resp.json()
                except Exception:
                    ev_data = None
                if isinstance(ev_data, dict):
                    return ev_data
                if isinstance(ev_data, list) and ev_data:
                    first = ev_data[0]
                    if isinstance(first, dict):
                        return first
            return None

        def _extract_location(payload):
            if not isinstance(payload, dict):
                return None
            for key in ('latestAddress', 'latest_address', 'address', 'location'):
                text = _location_text(payload.get(key))
                if text:
                    return text
            nested = _location_text(payload.get('eventLocation') or payload.get('event_location'))
            if nested:
                return nested
            events_list = payload.get('events') or payload.get('result') or []
            if isinstance(events_list, list):
                for ev in events_list:
                    if not isinstance(ev, dict):
                        continue
                    for key in ('latestAddress', 'latest_address', 'address', 'location'):
                        text = _location_text(ev.get(key))
                        if text:
                            return text
            return None

        def _normalize_mlpcon_row(row):
            if not isinstance(row, dict):
                return None

            def _get_any(*keys):
                for k in keys:
                    v = row.get(k)
                    if v:
                        return v
                return None

            def _parse_mlpcon_date(raw):
                if not raw:
                    return None
                raw_str = str(raw).strip()
                if not raw_str:
                    return None

                fixed_raw = raw_str
                try:
                    m = re.match(r'^\s*(\d{1,2})\/(\d{1,2})\/(\d{2,4})\s*$', raw_str)
                    if m:
                        mm = int(m.group(1))
                        dd = int(m.group(2))
                        yyyy = int(m.group(3))
                        if effective_year and (yyyy < 1900 or yyyy > 2100):
                            fixed_raw = f"{mm}/{dd}/{int(effective_year)}"
                except Exception:
                    pass

                try:
                    return dateparser.parse(fixed_raw)
                except Exception:
                    return None

            def _reverse_geocode_address(lat_long_raw):
                if not lat_long_raw:
                    return None
                try:
                    parts = [p.strip() for p in str(lat_long_raw).split(',')]
                    if len(parts) != 2:
                        return None
                    lat = float(parts[0])
                    lon = float(parts[1])
                except Exception:
                    return None

                cache_key = f"mlpcon:reverse:v3:{round(lat, 5)}:{round(lon, 5)}"
                if not refresh:
                    try:
                        cached_geo = cache.get(cache_key)
                        if cached_geo is not None:
                            return cached_geo or None
                    except Exception:
                        pass
                    try:
                        cached_local_geo = load_site_json('mlpcon_geocode', cache_key, 60 * 60 * 24 * 30)
                        if isinstance(cached_local_geo, str) and cached_local_geo.strip():
                            cache.set(cache_key, cached_local_geo, 60 * 60 * 24 * 30)
                            return cached_local_geo
                    except Exception:
                        pass

                try:
                    headers = {
                        'User-Agent': getattr(settings, 'MLPCON_GEOCODER_USER_AGENT', 'FurryConArchives/1.0 (+https://furryconarchives.org)')
                    }
                    geo_resp = requests.get(
                        'https://nominatim.openstreetmap.org/reverse',
                        params={
                            'format': 'jsonv2',
                            'lat': lat,
                            'lon': lon,
                            'addressdetails': 1,
                            'zoom': 18,
                        },
                        headers=headers,
                        timeout=3,
                    )
                except Exception:
                    geo_resp = None

                if not geo_resp or getattr(geo_resp, 'status_code', None) != 200:
                    return None

                try:
                    geo_data = geo_resp.json()
                except Exception:
                    geo_data = None
                if not isinstance(geo_data, dict):
                    return None

                addr = geo_data.get('address') or {}
                if not isinstance(addr, dict):
                    addr = {}
                display_name = str(geo_data.get('display_name') or '').strip()

                road = addr.get('road') or addr.get('pedestrian') or addr.get('footway') or addr.get('path') or ''
                house_no = addr.get('house_number') or ''
                city = addr.get('city') or addr.get('town') or addr.get('village') or addr.get('municipality') or addr.get('hamlet') or ''
                state = addr.get('state') or addr.get('state_district') or addr.get('region') or addr.get('province') or ''
                country = addr.get('country') or ''

                if not country and addr.get('country_code'):
                    try:
                        country = str(addr.get('country_code')).upper().strip()
                    except Exception:
                        country = ''

                # Fallback parse from display_name when explicit road/house values are missing.
                if (not road or not house_no) and display_name:
                    parts = [p.strip() for p in display_name.split(',') if p and p.strip()]
                    if not city:
                        for key in ('city', 'town', 'village', 'municipality', 'hamlet', 'county'):
                            value = addr.get(key)
                            if value:
                                city = str(value).strip()
                                break
                    if not road:
                        for comp in parts[:5]:
                            low = comp.lower()
                            if re.search(r'\d', comp) or any(tok in low for tok in (
                                'street', ' st', ' avenue', ' ave', ' road', ' rd', ' boulevard', ' blvd',
                                ' drive', ' dr', ' lane', ' ln', ' court', ' ct', ' place', ' pl', ' way'
                            )):
                                road = comp
                                break

                street = f"{house_no} {road}".strip() if (house_no or road) else ''
                locality_parts = [part for part in (city, state, country) if part]

                # Remove duplicates while preserving order (e.g. "Milwaukee, Milwaukee County")
                deduped_locality_parts = []
                seen_parts = set()
                for part in locality_parts:
                    part_key = str(part).strip().lower()
                    if not part_key or part_key in seen_parts:
                        continue
                    seen_parts.add(part_key)
                    deduped_locality_parts.append(str(part).strip())

                components = []
                if street:
                    components.append(street)
                components.extend(deduped_locality_parts)
                parsed_address = ', '.join(components) if components else None

                if parsed_address:
                    try:
                        cache.set(cache_key, parsed_address, 60 * 60 * 24 * 30)
                        store_site_json('mlpcon_geocode', cache_key, parsed_address)
                        ensure_registry('mlpcon_geocode', cache_key)
                    except Exception:
                        pass
                return parsed_address

            name_val = _get_any('Name', 'cname', 'name', 'ConventionName', 'conventionName')
            if not name_val:
                return None

            desc_val = _get_any('Description', 'description', 'Summary', 'summary') or ''
            if isinstance(desc_val, str):
                desc_val = html.unescape(desc_val).strip()
            else:
                desc_val = ''

            venue_val = (_get_any('Venue', 'venue') or '').strip() if isinstance(_get_any('Venue', 'venue'), str) else (_get_any('Venue', 'venue') or '')
            location_val = (_get_any('Location', 'location') or '').strip() if isinstance(_get_any('Location', 'location'), str) else (_get_any('Location', 'location') or '')
            venue_text = re.sub(r'\s+', ' ', str(venue_val)).strip() if venue_val else ''
            location_text = re.sub(r'\s+', ' ', str(location_val)).strip() if location_val else ''
            lat_long_val = _get_any('lat/long', 'lat_long', 'latlong', 'coordinates')
            address_from_coords = _reverse_geocode_address(lat_long_val)

            if venue_text and address_from_coords:
                full_location = f"{venue_text}, {address_from_coords}"
            elif venue_text and location_text:
                full_location = f"{venue_text}, {location_text}"
            else:
                full_location = venue_text or location_text or None

            start_dt = _parse_mlpcon_date(_get_any('Start', 'start', 'startDate', 'start_date'))
            end_dt = _parse_mlpcon_date(_get_any('End', 'end', 'endDate', 'end_date'))
            if not end_dt and start_dt:
                end_dt = start_dt

            website = _get_any('Website', 'website', 'URL', 'url')
            website = str(website).strip() if website else None

            def _parse_int_stat(*keys):
                for key in keys:
                    raw_val = row.get(key)
                    if raw_val is None:
                        continue
                    text = str(raw_val).strip()
                    if not text:
                        continue
                    try:
                        return int(text.replace(',', '').replace('$', ''))
                    except (TypeError, ValueError):
                        continue
                return None

            attendance = _parse_int_stat('Attendance', 'attendance', 'attendeeCount', 'attendees')
            charity = _parse_int_stat('Charity', 'charity', 'charityRaised', 'charity_raised')
            charity_partner = _get_any('Charity Partner', 'charityPartner', 'charity_partner')
            charity_partner = str(charity_partner).strip() if charity_partner else None

            return {
                'name': str(name_val).strip(),
                'start_date': start_dt,
                'end_date': end_dt,
                'location': full_location,
                'description': desc_val or None,
                'url': website,
                'slug': re.sub(r'[^a-z0-9]+', '', str(name_val).lower()),
                'source': 'mlpcon',
                'attendance': attendance,
                'charity': charity,
                'charity_partner': charity_partner,
            }

        def _fetch_mlpcon_event():
            if not base_slug:
                return None

            mlp_payload = _load_mlpcon_api_dataset(refresh=refresh)
            if not isinstance(mlp_payload, dict):
                return None

            rows = mlp_payload.get('data') or []
            if not rows:
                return None

            target_name = str(convention_name or '').strip().lower()
            target_slug = slugify(target_name) if target_name else base_slug
            target_slug_compact = re.sub(r'[^a-z0-9]+', '', target_slug)
            base_slug_compact = re.sub(r'[^a-z0-9]+', '', base_slug)
            first_word = str(base_slug).split('-', 1)[0].strip().lower()

            best = None
            best_score = -1.0
            for row in rows:
                normalized = _normalize_mlpcon_row(row)
                if not normalized:
                    continue

                item_name = str(normalized.get('name') or '').strip()
                item_slug = slugify(item_name)
                item_slug_compact = re.sub(r'[^a-z0-9]+', '', item_slug)

                score_basis = target_slug_compact or target_slug or base_slug_compact
                score_source = item_slug_compact or item_slug or base_slug_compact
                score = SequenceMatcher(None, score_source, score_basis).ratio()

                if item_slug == target_slug or item_slug_compact == target_slug_compact:
                    score += 0.35

                if base_slug and (base_slug in item_slug or item_slug in base_slug):
                    score += 0.15

                if first_word and first_word in item_slug:
                    score += 0.05

                if target_name and item_name and target_name in item_name.lower():
                    score += 0.2

                try:
                    item_year = None
                    if normalized.get('start_date'):
                        item_year = int(normalized['start_date'].year)
                    elif normalized.get('end_date'):
                        item_year = int(normalized['end_date'].year)
                    score += _year_score(item_year, effective_year)
                except Exception:
                    pass

                if score > best_score:
                    best_score = score
                    best = normalized

            # Avoid weak fuzzy matches when Consurf simply missed due to slug drift.
            if best_score < 0.55:
                return None

            return best

        def _fetch_candidates(batch_slugs):
            found = {}

            if not batch_slugs:
                logger.debug("_get_consurf_event: no candidate slugs for %s", base_slug)
                return found

            logger.debug("_get_consurf_event: candidate slugs for %s -> %s", base_slug, batch_slugs)

            # Build query string with multiple slug params.
            # If only one slug, prefer the per-slug endpoint to avoid API 400 on query-form.
            if len(batch_slugs) == 1:
                single = batch_slugs[0]
                url = f"{api_base}/api/external/conventions/{single}"
                logger.debug("_get_consurf_event: requesting %s", url)
                try:
                    response = _consurf_request(url, timeout=2)
                except Exception as e:
                    logger.exception("_get_consurf_event: single request failed for %s: %s", url, e)
                    response = None

                if is_consurf_rate_limited(response):
                    return found

                if response is not None and getattr(response, 'status_code', None) == 200:
                    try:
                        data = response.json()
                    except Exception:
                        data = None
                    if isinstance(data, dict):
                        item_slug = data.get('slug') or data.get('id') or slugify(str(data.get('name') or data.get('title') or single))
                        if item_slug:
                            found[str(item_slug)] = data
                return found

            query = '&'.join([f"slug={s}" for s in batch_slugs])
            url = f"{api_base}/api/external/conventions?{query}"
            logger.debug("_get_consurf_event: requesting %s", url)

            # Page through paginated responses but cap at 18 pages to avoid excessive calls.
            page_count = 0
            next_url = url
            while next_url and page_count < 18:
                try:
                    logger.debug("_get_consurf_event: requesting %s", next_url)
                    response = _consurf_request(next_url, timeout=2)
                except Exception as e:
                    logger.exception("_get_consurf_event: batch request failed for %s: %s", next_url, e)
                    response = None

                if response is None:
                    break

                if is_consurf_rate_limited(response):
                    break

                if getattr(response, 'status_code', None) in (400, 404):
                    logger.debug("_get_consurf_event: batch query returned status %s", response.status_code)
                    # Fallback: try per-slug endpoints when batch fails.
                    for s in batch_slugs:
                        try:
                            single_url = f"{api_base}/api/external/conventions/{s}"
                            logger.debug("_get_consurf_event: requesting %s", single_url)
                            r2 = _consurf_request(single_url, timeout=2)
                        except Exception as e:
                            logger.exception("_get_consurf_event: fallback single request failed for %s: %s", single_url, e)
                            r2 = None
                        if is_consurf_rate_limited(r2):
                            break
                        if r2 is not None and getattr(r2, 'status_code', None) == 200:
                            try:
                                d2 = r2.json()
                            except Exception:
                                d2 = None
                            if isinstance(d2, dict):
                                item_slug = d2.get('slug') or d2.get('id') or slugify(str(d2.get('name') or d2.get('title') or s))
                                if item_slug:
                                    found[str(item_slug)] = d2
                    break

                if getattr(response, 'status_code', None) == 200:
                    try:
                        data = response.json()
                    except Exception:
                        data = None

                    items = []
                    # data may be a paginated dict with 'results', a list, or a single dict.
                    if isinstance(data, dict) and data.get('results') is not None:
                        items = data.get('results') or []
                        # determine next page url if provided.
                        next_url = data.get('next')
                    elif isinstance(data, list):
                        items = data
                        next_url = None
                    elif isinstance(data, dict) and data.get('id'):
                        items = [data]
                        next_url = None
                    else:
                        items = []
                        next_url = None

                    # Process returned items into found by slug.
                    for item in items:
                        if not isinstance(item, dict):
                            continue
                        item_slug = item.get('slug') or item.get('id') or None
                        if not item_slug:
                            item_slug = slugify(str(item.get('name') or item.get('title') or ''))
                        if item_slug:
                            found[str(item_slug)] = item

                    page_count += 1
                    if not next_url:
                        break
                    if page_count >= 18:
                        logger.debug("_get_consurf_event: reached page cap (%s)", page_count)
                        break
                else:
                    break

            return found

        initial_slugs = _build_slug_variations(False)
        fallback_slugs = batch_slugs if effective_year else initial_slugs

        found_items = _fetch_candidates(initial_slugs)
        if not found_items and effective_year:
            logger.debug("_get_consurf_event: retrying with fallback year slugs for %s", base_slug)
            found_items = _fetch_candidates(fallback_slugs)

        # If we found any items, pick the best match from batch_slugs
        if found_items:
            # Prefer an exact slug match in order of the primary slug list.
            selected = None
            selected_score = -1.0
            for slug_key, item in found_items.items():
                slug_bonus = 0.2 if slug_key in initial_slugs else 0.0
                item_year = _extract_payload_year(item)
                year_bonus = _year_score(item_year, effective_year) if effective_year is not None else 0.0

                # Skip obviously unrelated years when a target year is known.
                if effective_year is not None and year_bonus < 0:
                    continue

                score = slug_bonus + year_bonus

                # If a year was provided, give a strong boost to items whose nested
                # events actually contain the requested year.
                if effective_year is not None:
                    events_list = item.get('events') or item.get('result') or []
                    if isinstance(events_list, list):
                        for ev in events_list:
                            try:
                                sd = ev.get('startDate')
                                if sd and int(sd[:4]) == int(effective_year):
                                    score += 0.5
                                    break
                            except Exception:
                                continue

                if score > selected_score:
                    selected = item
                    selected_slug = slug_key
                    selected_score = score

            if not selected:
                # fallback to the first found item
                selected_slug, selected = next(iter(found_items.items()))

            if selected and isinstance(selected, dict):
                data = selected
                slug = selected_slug
                # If only one convention matched in the batch, prefer the base convention URL
                try:
                    if len(found_items) == 1:
                        import re as _re
                        m = _re.match(r"^(.*?)-((?:19|20)\d{2})$", str(slug))
                        slug = m.group(1) if m else str(slug)
                        slug = slug.strip('-')
                except Exception:
                    pass
                # Derive date range from nested events/iterations
                start_date = None
                end_date = None
                events_list = data.get('events') or data.get('result') or []
                parsed_dates = []
                if isinstance(events_list, list) and events_list:
                    for ev in events_list:
                        if isinstance(ev, dict):
                            sd = ev.get('startDate')
                            ed = ev.get('endDate')
                            try:
                                if sd:
                                    parsed_dates.append(datetime.fromisoformat(sd.replace('Z', '+00:00')))
                            except:
                                pass
                            try:
                                if ed:
                                    parsed_dates.append(datetime.fromisoformat(ed.replace('Z', '+00:00')))
                            except:
                                pass
                if parsed_dates:
                    parsed_dates_sorted = sorted(parsed_dates)
                    start_date = parsed_dates_sorted[0]
                    end_date = parsed_dates_sorted[-1]

                if (not start_date) and data.get('firstEventDate'):
                    try:
                        start_date = datetime.fromisoformat(data.get('firstEventDate').replace('Z', '+00:00'))
                    except:
                        start_date = None
                if (not end_date) and data.get('lastEventDate'):
                    try:
                        end_date = datetime.fromisoformat(data.get('lastEventDate').replace('Z', '+00:00'))
                    except:
                        end_date = None

                location = _extract_location(data)
                year_payload = None
                event_slug_used = None
                if effective_year is not None:
                    from archive.consurf_slugs import consurf_year_event_slugs
                    try:
                        for event_slug in consurf_year_event_slugs(event_slug_source, effective_year):
                            year_payload = _fetch_year_payload(event_slug)
                            if year_payload:
                                event_slug_used = event_slug
                                year_location = _extract_location(year_payload)
                                if year_location:
                                    location = year_location
                                break
                    except Exception:
                        pass
                raw_description = data.get('description') or data.get('summary') or None
                if raw_description and isinstance(raw_description, str):
                    text_desc = _clean_text(raw_description)
                else:
                    text_desc = None

                convention_description = _fetch_convention_description(base_slug)
                if not convention_description and isinstance(data.get('convention'), dict):
                    convention_description = _clean_text(_extract_text(data['convention'].get('description')))

                event_description = _fetch_event_description(slug)
                if not event_description:
                    event_description = _fetch_event_description(selected_slug)
                if not event_description and effective_year:
                    from archive.consurf_slugs import consurf_year_event_slugs
                    for event_slug in consurf_year_event_slugs(event_slug_source, effective_year):
                        event_description = _fetch_event_description(event_slug)
                        if event_description:
                            if not event_slug_used:
                                event_slug_used = event_slug
                            break
                if not event_description and year_payload:
                    event_description = _description_from_payload(year_payload)

                if (not start_date or not end_date) and year_payload:
                    event_start = _parse_consurf_datetime(year_payload.get('startDate') or year_payload.get('start_date'))
                    event_end = _parse_consurf_datetime(year_payload.get('endDate') or year_payload.get('end_date'))
                    if event_start:
                        start_date = event_start
                    if event_end:
                        end_date = event_end
                    elif event_start:
                        end_date = event_start

                public_slug = event_slug_used or slug

                result = {
                    'name': data.get('name') or data.get('title') or slug,
                    'start_date': start_date,
                    'end_date': end_date,
                    'location': location,
                    'description': text_desc,
                    'event_description': event_description,
                    'convention_description': convention_description,
                    # Link to the public Consurf event page when a year-specific slug is known.
                    'url': f'https://consurf.net/{public_slug}',
                    'slug': public_slug,
                    'source': 'consurf',
                }

                # Cache results per-slug and return
                _cache_consurf_result(result)
                return result

        if not found_items and effective_year is not None:
            from archive.consurf_slugs import consurf_year_event_slugs
            for event_slug in consurf_year_event_slugs(event_slug_source, effective_year):
                year_payload = _fetch_year_payload(event_slug)
                if not year_payload:
                    continue
                result = _result_from_event_payload(year_payload, event_slug)
                if result:
                    logger.debug(
                        "_get_consurf_event: resolved %s via events API slug %s",
                        base_slug,
                        event_slug,
                    )
                    _cache_consurf_result(result)
                    return result

        # If Consurf did not return a match, search the cached MLPCON dataset as a fallback for MLP conventions.
        mlp_result = _fetch_mlpcon_event()
        if mlp_result:
            if mlp_result.get('description') and not mlp_result.get('event_description'):
                mlp_result = dict(mlp_result)
                mlp_result['event_description'] = mlp_result['description']
            for cached_slug in batch_slugs:
                cache.set(f"consurf:event:slug:{bucket_key}:{cached_slug}", mlp_result, 60 * 60 * 24)
                cache.set(f"consurf:event:slug:{cached_slug}", mlp_result, 60 * 60 * 24)
                ensure_registry('consurf_events', f"slug:{bucket_key}:{cached_slug}")
            cache.set(cache_key, mlp_result, 60 * 60 * 24)
            store_site_json('consurf_events', local_cache_key, serialize_consurf_event(mlp_result))
            ensure_registry('consurf_events', local_cache_key)
            return mlp_result
        
        
        if rate_limited:
            logger.warning(
                "_get_consurf_event: Consurf rate limited for %s; skipping negative cache",
                base_slug,
            )
            return None

        logger.debug("_get_consurf_event: no event found for base_slug %s (cache_key=%s)", base_slug, cache_key)
        cache.set(cache_key, "__none__", 60 * 60 * 24)
        cache.set(legacy_cache_key, "__none__", 60 * 60 * 24)
        store_site_json('consurf_events', local_cache_key, {'none': True})
        store_site_json('consurf_events', legacy_local_cache_key, {'none': True})
        ensure_registry('consurf_events', local_cache_key)
        ensure_registry('consurf_events', legacy_local_cache_key)

    except Exception as e:
        import traceback
        traceback.print_exc()
    
    return None


def _get_consurf_active_years(convention_name, refresh=False):
    """Fetch all active years for a convention from Consurf"""
    if not convention_name:
        return []
    
    try:
        # Convert convention name to slug format
        base_slug = convention_name.lower().replace(' ', '-').replace('_', '-')
        if base_slug.endswith('-conbook'):
            base_slug = base_slug[:-8]
        while '--' in base_slug:
            base_slug = base_slug.replace('--', '-')
        base_slug = base_slug.strip('-')
        if not base_slug:
            return []
        
        bucket_key, allow_legacy_fallback = _consurf_api_cache_bucket()
        cache_key = f"consurf:years:{bucket_key}:{base_slug}"
        legacy_cache_key = f"consurf:years:{base_slug}"
        cached_years = cache.get(cache_key, None)
        if cached_years is None and allow_legacy_fallback:
            cached_years = cache.get(legacy_cache_key, None)
        if cached_years is not None:
            logger.debug("_get_consurf_active_years: cache hit for %s (%s)", base_slug, cache_key)
            try:
                cache.set(cache_key, cached_years, 60 * 60 * 24)
            except Exception:
                pass
            return cached_years
        logger.debug("_get_consurf_active_years: cache miss for %s (%s)", base_slug, cache_key)

        if not refresh:
            cached_local_years = load_site_json('consurf_years', f"{bucket_key}:{base_slug}", 60 * 60 * 24)
            if not isinstance(cached_local_years, list) and allow_legacy_fallback:
                cached_local_years = load_site_json('consurf_years', base_slug, 60 * 60 * 24)
            if isinstance(cached_local_years, list):
                logger.debug("_get_consurf_active_years: local cache hit for %s", base_slug)
                cache.set(cache_key, cached_local_years, 60 * 60 * 24)
                return cached_local_years

        api_base = getattr(settings, 'EXTERNAL_EVENTS_API_BASE', '').rstrip('/') or 'https://consurf.net'
        from archive.consurf_slugs import expand_consurf_slug

        response = None
        for slug_candidate in expand_consurf_slug(base_slug):
            url = f'{api_base}/api/external/conventions/{slug_candidate}'
            logger.debug("_get_consurf_active_years: requesting %s", url)
            try:
                response = requests.get(url, timeout=3)
            except Exception as e:
                logger.exception("_get_consurf_active_years: request failed for %s: %s", url, e)
                response = None
            if response is not None and getattr(response, 'status_code', None) == 200:
                data = response.json()
                if data and isinstance(data, dict) and data.get('id'):
                    def _year_from_iso(date_str):
                        if not date_str or len(date_str) < 4:
                            return None
                        year_part = date_str[:4]
                        return int(year_part) if year_part.isdigit() else None

                    first_year = _year_from_iso(data.get('firstEventDate'))
                    last_year = _year_from_iso(data.get('lastEventDate'))

                    if first_year and last_year and first_year <= last_year:
                        years = list(range(last_year, first_year - 1, -1))
                    elif first_year:
                        years = [first_year]
                    elif last_year:
                        years = [last_year]
                    else:
                        years = []

                    logger.debug("_get_consurf_active_years: parsed years for %s -> %s", base_slug, years)
                    cache.set(cache_key, years, 60 * 60 * 24)
                    cache.set(legacy_cache_key, years, 60 * 60 * 24)
                    store_site_json('consurf_years', f"{bucket_key}:{base_slug}", years)
                    store_site_json('consurf_years', base_slug, years)
                    ensure_registry('consurf_years', f"{bucket_key}:{base_slug}")
                    ensure_registry('consurf_years', base_slug)
                    return years
                break

        if response is not None and getattr(response, 'status_code', None) in (400, 404):
            logger.debug("_get_consurf_active_years: %s returned status %s", base_slug, response.status_code)
            cache.set(cache_key, [], 60 * 60 * 24)
            cache.set(legacy_cache_key, [], 60 * 60 * 24)
            store_site_json('consurf_years', f"{bucket_key}:{base_slug}", [])
            store_site_json('consurf_years', base_slug, [])
            ensure_registry('consurf_years', f"{bucket_key}:{base_slug}")
            ensure_registry('consurf_years', base_slug)
            return []
        
        logger.debug("_get_consurf_active_years: no years found for %s", base_slug)
        cache.set(cache_key, [], 60 * 60 * 24)
        cache.set(legacy_cache_key, [], 60 * 60 * 24)
        store_site_json('consurf_years', f"{bucket_key}:{base_slug}", [])
        store_site_json('consurf_years', base_slug, [])
        ensure_registry('consurf_years', f"{bucket_key}:{base_slug}")
        ensure_registry('consurf_years', base_slug)

    except Exception as e:
        pass

    return []


def refresh_consurf_cache():
    """Force-refresh tracked Consurf event and year lookups."""
    from .site_cache import get_registry as get_site_registry

    event_keys = get_site_registry('consurf_events')
    year_keys = get_site_registry('consurf_years')

    for entry in event_keys:
        try:
            key = entry.replace('slug:', '', 1) if entry.startswith('slug:') else entry
            if ':' in key:
                base_slug, year_value = key.split(':', 1)
                year = int(year_value) if year_value.isdigit() else None
            else:
                base_slug = key
                year = None
            if not base_slug:
                continue
            _get_consurf_event(base_slug.replace('-', ' '), year=year, slug_hint=base_slug, refresh=True)
        except Exception:
            logger.exception('refresh_consurf_cache: failed to refresh event cache for %s', entry)

    for base_slug in year_keys:
        try:
            if base_slug:
                _get_consurf_active_years(base_slug.replace('-', ' '), refresh=True)
        except Exception:
            logger.exception('refresh_consurf_cache: failed to refresh year cache for %s', base_slug)


def document_detail(request, slug):
    """Display details of a specific PDF document"""
    document = get_object_or_404(PDFDocument, slug=slug)
    # Show DMCA page for all users if takedown is active
    if document.takedown_by_request:
        context = {
            'document': document,
            'takedown': True,
            'takedown_explanation': document.takedown_explanation,
        }
        return render(request, 'archive/document_takedown.html', context)
    if not document.is_published and not (request.user.is_authenticated and request.user.is_staff):
        raise Http404("Document not found")
    
    # Extract year from slug (e.g., "aquatifur-2017-conbook" -> 2017)
    slug_year = None
    year_match = re.search(r'-(\d{4})(?:-|$)', slug)
    if year_match:
        try:
            slug_year = int(year_match.group(1))
        except (ValueError, TypeError):
            slug_year = None
    
    # Get related documents (same category)
    related_documents = PDFDocument.objects.filter(
        is_published=True,
        takedown_by_request=False,
        category=document.category,
    ).exclude(id=document.id)[:5] if document.category else []

    pdf_view_stats = get_pdf_view_stats([slug]) or {'counts': {}, 'total': 0}
    document_pdf_views = pdf_view_stats.get('counts', {}).get(slug, 0)
    
    # Attempt to fetch Consurf event data from cache only so page loads stay fast.
    consurf_event = None
    inferred_conv = None
    conv_name = None

    try:
        # Prefer explicit convention name on the document
        conv_name = (document.convention_name or '').strip()
        logger.debug("document_detail: document.convention_name = %s", document.convention_name)
        
        if not conv_name and document.category:
            conv_name = document.category.get_primary_convention_name() or document.category.name
            logger.debug("document_detail: using category convention_name = %s", conv_name)

        # If still missing, try to infer a convention name from the slug
        if not conv_name and slug:
            try:
                # If slug contains a year like 'anthroexpo-2026-...'
                m = re.match(r'([a-z0-9\-]+)-(19|20)\d{2}(?:-|$)', slug)
                if m:
                    inferred_conv = m.group(1).replace('-', ' ').strip()
                else:
                    # fallback: take the first dash-separated token
                    inferred_conv = slug.split('-', 1)[0].replace('-', ' ').strip()
                if inferred_conv:
                    # prefer the document/category explicit name if present, otherwise use inferred
                    conv_name = inferred_conv
                    logger.debug("document_detail: inferred convention_name from slug = %s", conv_name)
            except Exception as e:
                logger.debug("document_detail: failed to infer convention from slug: %s", e)
                inferred_conv = None

        if conv_name:
            logger.debug("document_detail: fetching consurf_event for conv_name=%s, year=%s", conv_name, document.year)
            try:
                slug_hint = None
                if slug:
                    try:
                        from archive.document_descriptions import slug_hint_for_document
                        slug_hint = slug_hint_for_document(document)
                        logger.debug("document_detail: computed slug_hint = %s", slug_hint)
                    except Exception as e:
                        logger.debug("document_detail: failed to compute slug_hint: %s", e)
                        slug_hint = None
                consurf_event = _get_consurf_event(conv_name, document.year, slug_hint=slug_hint)
                if consurf_event:
                    logger.debug("document_detail: successfully fetched consurf_event")
                else:
                    logger.debug("document_detail: consurf_event returned None")
            except Exception:
                # _get_consurf_event is best-effort and already logs; swallow
                logger.exception("document_detail: _get_consurf_event failed for %s (year=%s)", conv_name, document.year)
                consurf_event = None
        else:
            logger.debug("document_detail: no conv_name available after all attempts")
    except Exception:
        logger.exception("document_detail: outer exception during consurf event fetch")
        consurf_event = None
    
    from archive.document_descriptions import resolve_event_display_description

    event_display_description = resolve_event_display_description(
        document,
        consurf_event=consurf_event,
        allow_network=False,
    )
    if not event_display_description:
        event_display_description = resolve_event_display_description(
            document,
            allow_network=True,
        )

    context = {
        'document': document,
        'related_documents': related_documents,
        'document_pdf_views': document_pdf_views,
        'consurf_event': consurf_event,
        'event_display_description': event_display_description,
        'consurf_active_years': [],
        'consurf_years': None,
        'consurf_event_year': None,
        'consurf_event_address': None,
        'inferred_convention_name': inferred_conv if 'inferred_conv' in locals() else None,
    }
    # Build a displayable year range for the template
    try:
        if consurf_event and getattr(consurf_event, 'start_date', None):
            sd = consurf_event.start_date
            ed = consurf_event.end_date or sd
            try:
                syear = sd.year
                eyear = ed.year
                if syear == eyear:
                    context['consurf_years'] = str(syear)
                else:
                    context['consurf_years'] = f"{syear}-{eyear}"
            except Exception:
                context['consurf_years'] = None
        elif document.year:
            context['consurf_years'] = str(document.year)
    except Exception:
        context['consurf_years'] = None

    # Also compute a single event year and an address for the sidebar, and
    # build a Consurf convention URL that includes the year when appropriate.
    try:
        # Helper to read attr or dict key
        def _get_field(obj, *keys):
            for k in keys:
                if not k:
                    continue
                if isinstance(obj, dict):
                    v = obj.get(k)
                else:
                    v = getattr(obj, k, None)
                if v:
                    return v
            return None

        chosen_year = None
        candidate_years = []

        if consurf_event:
            sd = _get_field(consurf_event, 'start_date', 'startDate')
            ed = _get_field(consurf_event, 'end_date', 'endDate') or sd
            # Priority: document.year -> document start_date year -> latest candidate year
            if document.year:
                try:
                    chosen_year = int(document.year)
                except Exception:
                    chosen_year = None

            if chosen_year is None and sd:
                try:
                    chosen_year = sd.year
                except Exception:
                    chosen_year = None

            if chosen_year is None and candidate_years:
                # pick most recent as fallback
                chosen_year = max(candidate_years)

            addr = _get_field(consurf_event, 'location', 'address', 'latestAddress', 'latest_address')
            if addr:
                context['consurf_event_address'] = addr
            # get slug for URL
            slug_val = _get_field(consurf_event, 'slug', 'id')
        else:
            # No consurf_event; fall back to document/year metadata
            slug_val = None
            if document.year:
                try:
                    chosen_year = int(document.year)
                except Exception:
                    chosen_year = None
            if document.category and getattr(document.category, 'location', None):
                context['consurf_event_address'] = document.category.location

        # Build public Consurf convention URL (with year when available)
        try:
            event_source = _get_field(consurf_event, 'source') if consurf_event else None
            if event_source == 'mlpcon':
                context['consurf_convention_url'] = None
            elif not slug_val:
                # Try falling back to convention name or category slug
                slug_val = (document.convention_name or '')
                from django.utils.text import slugify
                slug_val = slugify(slug_val) if slug_val else (getattr(document.category, 'slug', None) or '')
            if event_source != 'mlpcon' and slug_val:
                # Normalize slug: remove any trailing -YYYY so we don't duplicate years
                try:
                    import re as _re
                    m = _re.match(r"^(.*?)-((?:19|20)\d{2})$", str(slug_val))
                    slug_base = m.group(1) if m else str(slug_val)
                    slug_base = slug_base.strip('-')
                except Exception:
                    slug_base = str(slug_val)

                # Add year from document slug to the convention URL
                context['consurf_convention_url'] = f"https://consurf.net/{slug_base}-{slug_year}" if slug_year else f"https://consurf.net/{slug_base}"
            else:
                context['consurf_convention_url'] = None
        except Exception:
            context['consurf_convention_url'] = None

        context['consurf_event_year'] = chosen_year
    except Exception:
        # Preserve any values we managed to compute and fail gracefully
        context['consurf_event_year'] = context.get('consurf_event_year')
        context['consurf_event_address'] = context.get('consurf_event_address')
        context['consurf_convention_url'] = context.get('consurf_convention_url')

    from .ia_mirrors import get_document_ia_mirrors
    context['ia_mirrors'] = get_document_ia_mirrors(document)

    return render(request, 'archive/document_detail.html', context)

def document_view(request, slug):
    """View PDF with server-rendered pages"""
    document = get_object_or_404(PDFDocument, slug=slug)
    # Show DMCA page if flagged, regardless of login
    if document.takedown_by_request:
        context = {
            'document': document,
            'takedown': True,
            'takedown_explanation': document.takedown_explanation,
        }
        return render(request, 'archive/document_takedown.html', context)
    if (not document.is_published or document.takedown_by_request) and not (request.user.is_authenticated and request.user.is_staff):
        if document.takedown_by_request:
            context = {
                'document': document,
                'takedown': True,
                'takedown_explanation': document.takedown_explanation,
            }
            return render(request, 'archive/document_takedown.html', context)
        raise Http404("Document not found")
    
    # Get PDF info
    try:
        with local_path_for_field_file(document.file) as pdf_path:
            pdf = PdfReader(pdf_path)
            page_count = len(pdf.pages)
    except:
        page_count = document.page_count or 1
    
    # Check if OCR is available
    has_ocr = bool(document.ocr_text and document.ocr_text.strip())
    
    context = {
        'document': document,
        'page_count': page_count,
        'has_ocr': has_ocr,
    }
    
    return render(request, 'archive/pdf_viewer.html', context)


def document_download(request, slug):
    """Handle PDF download."""
    document = get_object_or_404(PDFDocument, slug=slug)
    # Show DMCA page if flagged, regardless of login
    if document.takedown_by_request:
        context = {
            'document': document,
            'takedown': True,
            'takedown_explanation': document.takedown_explanation,
        }
        return render(request, 'archive/document_takedown.html', context)
    if (not document.is_published or document.takedown_by_request) and not (request.user.is_authenticated and request.user.is_staff):
        if document.takedown_by_request:
            context = {
                'document': document,
                'takedown': True,
                'takedown_explanation': document.takedown_explanation,
            }
            return render(request, 'archive/document_takedown.html', context)
        raise Http404("Document not found")

    # Serve the file
    try:
        response = FileResponse(document.file.open('rb'), content_type='application/octet-stream')
        response['Content-Disposition'] = f'attachment; filename="{getattr(document, "filename", document.slug)}"'
        response['Content-Type'] = 'application/octet-stream'
        response['X-Content-Type-Options'] = 'nosniff'
        return response
    except Exception:
        raise Http404("File not found")


@require_GET
def api_document_mirrors(request, slug):
    """Return cached Internet Archive mirror and torrent links for a document."""
    document = get_object_or_404(PDFDocument, slug=slug)
    if document.takedown_by_request:
        return JsonResponse({'available': False, 'identifier': None, 'options': []})
    if not document.is_published and not (request.user.is_authenticated and request.user.is_staff):
        raise Http404("Document not found")

    from .ia_mirrors import get_document_ia_mirrors

    force_refresh = request.GET.get('refresh') == '1' and request.user.is_authenticated and request.user.is_staff
    payload = get_document_ia_mirrors(document, force_refresh=force_refresh)
    return JsonResponse(payload)


def _placeholder_jpeg_path():
    from django.contrib.staticfiles import finders

    found = finders.find('archive/placeholder.jpg')
    if found:
        return found
    return os.path.join(settings.BASE_DIR, 'archive', 'static', 'archive', 'placeholder.jpg')


def _placeholder_jpeg_response():
    try:
        with open(_placeholder_jpeg_path(), 'rb') as ph:
            ph_data = ph.read()
    except Exception:
        ph_data = b''
    resp = HttpResponse(ph_data, content_type='image/jpeg')
    resp['X-FCA-Placeholder'] = '1'
    resp['Cache-Control'] = 'no-store, no-cache, must-revalidate'
    return resp


def pdf_page_image(request, slug, page_num):
    """Render a specific PDF page as an image with locked cache writes."""
    from archive.pdf_cache import (
        delete_cached_page_image,
        generate_page_image,
        pdf_page_cache_path,
        read_cached_page_image,
    )

    document = get_object_or_404(PDFDocument, slug=slug)
    if document.takedown_by_request:
        context = {
            'document': document,
            'takedown': True,
            'takedown_explanation': document.takedown_explanation,
        }
        response = render(request, 'archive/document_takedown.html', context)
        response.status_code = 403
        return response
    if not document.is_published and not (request.user.is_authenticated and request.user.is_staff):
        raise Http404("Document not found")

    try:
        page_num = int(page_num)
        if page_num < 1:
            raise Http404("Invalid page number")
        regenerate = request.GET.get('regenerate', '0') == '1'

        _, output_path = pdf_page_cache_path(document.slug, page_num)

        if regenerate:
            delete_cached_page_image(output_path, slug=slug, page_num=page_num)
            logger.info("regenerate: removed existing image for %s page %s", slug, page_num)

        if not regenerate:
            cached = read_cached_page_image(output_path, slug=slug, page_num=page_num)
            if cached:
                resp = HttpResponse(cached, content_type='image/jpeg')
                resp['Cache-Control'] = 'public, max-age=31536000'
                return resp

        with local_path_for_field_file(document.file) as pdf_path:
            if not pdf_path or not os.path.exists(pdf_path):
                logger.warning("pdf_page_image: missing pdf file for %s page %s", slug, page_num)
                return _placeholder_jpeg_response()

            img_data = generate_page_image(
                pdf_path,
                page_num,
                output_path,
                force_regenerate=regenerate,
                slug=slug,
            )
            if img_data:
                resp = HttpResponse(img_data, content_type='image/jpeg')
                if regenerate:
                    resp['Cache-Control'] = 'no-store, no-cache, must-revalidate'
                else:
                    resp['Cache-Control'] = 'public, max-age=31536000'
                return resp

            def async_generate():
                try:
                    generate_page_image(
                        pdf_path,
                        page_num,
                        output_path,
                        force_regenerate=regenerate,
                        slug=slug,
                    )
                except Exception as exc:
                    logger.exception("async pdf_page_image generation failed for %s page %s: %s", slug, page_num, exc)

            import threading
            threading.Thread(target=async_generate, daemon=True).start()
            return _placeholder_jpeg_response()
    except Http404:
        raise
    except Exception as e:
        raise Http404(f"Error rendering page: {str(e)}")

def pdf_page_text(request, slug, page_num):
    """Get text content from a specific PDF page"""
    document = get_object_or_404(PDFDocument, slug=slug)
    # Block text endpoint if DMCA and not logged in
    if document.takedown_by_request and not request.user.is_authenticated:
        return JsonResponse({'error': 'Authentication required'}, status=403)
    if not document.is_published and not (request.user.is_authenticated and request.user.is_staff):
        raise Http404("Document not found")
    
    import tempfile
    tmp_path = None
    try:
        page_num = int(page_num)
        if page_num < 1:
            raise Http404("Invalid page number")
        # Open the PDF file from storage (works with both local and remote storage)
        with document.file.open('rb') as pdf_file:
            # Create a temporary file to work with PdfReader
            with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp:
                tmp.write(pdf_file.read())
                tmp_path = tmp.name
        pdf = PdfReader(tmp_path)
        if page_num > len(pdf.pages):
            raise Http404("Page not found")
        page = pdf.pages[page_num - 1]
        text = page.extract_text()
        return JsonResponse({
            'page': page_num,
            'text': text,
            'has_text': bool(text and text.strip())
        })
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)
    finally:
        if tmp_path:
            import os
            try:
                os.unlink(tmp_path)
            except Exception:
                pass


def pdf_search(request, slug):
        import threading
        from queue import Queue
        import re
        from difflib import SequenceMatcher

        def normalize_ocr_text(text):
            return re.sub(r'[^a-z0-9]+', ' ', (text or '').lower()).strip()

        def build_ocr_tokens(boxes):
            tokens = []
            text_values = boxes.get('text') or []
            left_values = boxes.get('left') or []
            top_values = boxes.get('top') or []
            width_values = boxes.get('width') or []
            height_values = boxes.get('height') or []
            block_values = boxes.get('block_num') or []
            par_values = boxes.get('par_num') or []
            line_values = boxes.get('line_num') or []
            word_values = boxes.get('word_num') or []

            for i, raw_text in enumerate(text_values):
                cleaned = (raw_text or '').strip()
                normalized = normalize_ocr_text(cleaned)
                if not normalized:
                    continue
                tokens.append({
                    'text': cleaned,
                    'normalized': normalized,
                    'x': left_values[i] if i < len(left_values) else 0,
                    'y': top_values[i] if i < len(top_values) else 0,
                    'width': width_values[i] if i < len(width_values) else 0,
                    'height': height_values[i] if i < len(height_values) else 0,
                    'block_num': block_values[i] if i < len(block_values) else 0,
                    'par_num': par_values[i] if i < len(par_values) else 0,
                    'line_num': line_values[i] if i < len(line_values) else 0,
                    'word_num': word_values[i] if i < len(word_values) else 0,
                })

            tokens.sort(key=lambda token: (
                token['block_num'],
                token['par_num'],
                token['line_num'],
                token['word_num'],
                token['y'],
                token['x'],
            ))
            return tokens

        def tokens_to_spans(tokens):
            stream_parts = []
            spans = []
            cursor = 0
            for token in tokens:
                if stream_parts:
                    cursor += 1
                start = cursor
                stream_parts.append(token['normalized'])
                cursor += len(token['normalized'])
                spans.append((start, cursor))
            return ' '.join(stream_parts), spans

        def union_token_boxes(tokens, start_index, end_index):
            selected = tokens[start_index:end_index + 1]
            if not selected:
                return None
            left = min(token['x'] for token in selected)
            top = min(token['y'] for token in selected)
            right = max(token['x'] + token['width'] for token in selected)
            bottom = max(token['y'] + token['height'] for token in selected)
            return {
                'x': left,
                'y': top,
                'width': max(1, right - left),
                'height': max(1, bottom - top),
            }

        def append_match(matches_queue, page_num, start, end, text, box):
            if not box:
                box = {
                    'x': 0,
                    'y': 0,
                    'width': 100,
                    'height': 30,
                }
            matches_queue.put({
                'page': page_num,
                'start': start,
                'end': end,
                'text': text,
                'x': box['x'],
                'y': box['y'],
                'width': box['width'],
                'height': box['height'],
            })

        def ocr_search_worker(page_num, pil_img, page_text, query_lower, matches_queue):
            boxes = pytesseract.image_to_data(pil_img, output_type=pytesseract.Output.DICT)
            tokens = build_ocr_tokens(boxes)
            if tokens:
                normalized_stream, token_spans = tokens_to_spans(tokens)
                query_norm = normalize_ocr_text(query_lower)
                if query_norm:
                    query_terms = query_norm.split()
                    exact_start = 0
                    found_exact = False
                    while True:
                        pos = normalized_stream.find(query_norm, exact_start)
                        if pos == -1:
                            break
                        end_pos = pos + len(query_norm)
                        start_index = None
                        end_index = None
                        for idx, (token_start, token_end) in enumerate(token_spans):
                            if token_end <= pos:
                                continue
                            if token_start >= end_pos:
                                break
                            if start_index is None:
                                start_index = idx
                            end_index = idx
                        if start_index is not None and end_index is not None:
                            box = union_token_boxes(tokens, start_index, end_index)
                            append_match(matches_queue, page_num, pos, end_pos, query_lower, box)
                            found_exact = True
                        exact_start = pos + 1

                    if not found_exact:
                        best_score = 0.0
                        best_window = None
                        query_len = len(query_terms)
                        min_window = max(1, query_len - 1)
                        max_window = min(len(tokens), query_len + 1)
                        for window_size in range(min_window, max_window + 1):
                            for start_index in range(0, len(tokens) - window_size + 1):
                                end_index = start_index + window_size - 1
                                window_text = ' '.join(token['normalized'] for token in tokens[start_index:end_index + 1])
                                score = SequenceMatcher(None, query_norm, window_text).ratio()
                                if score > best_score:
                                    best_score = score
                                    best_window = (start_index, end_index, window_text)
                        if best_window and best_score >= 0.72:
                            start_index, end_index, window_text = best_window
                            box = union_token_boxes(tokens, start_index, end_index)
                            append_match(matches_queue, page_num, 0, len(query_lower), window_text, box)
            else:
                text_lower = page_text.lower()
                start = 0
                while True:
                    pos = text_lower.find(query_lower, start)
                    if pos == -1:
                        break
                    append_match(matches_queue, page_num, pos, pos + len(query_lower), page_text[pos:pos+len(query_lower)], None)
                    start = pos + 1

        """Search OCR text and return pages with matches and match details"""
        document = get_object_or_404(PDFDocument, slug=slug)
        if not document.is_published and not (request.user.is_authenticated and request.user.is_staff):
            raise Http404("Document not found")
        
        query = request.GET.get('q', '').strip()
        if not query:
            return JsonResponse({'error': 'No search query provided'}, status=400)
        
        # Check if OCR text is available
        if not document.ocr_text or not document.ocr_text.strip():
            return JsonResponse({
                'has_ocr': False,
                'message': 'OCR text not available for this document'
            })
        
        try:
            # Get page count
            page_count = document.page_count or 1
            with local_path_for_field_file(document.file) as pdf_path:
                try:
                    pdf = PdfReader(pdf_path)
                    page_count = len(pdf.pages)
                except:
                    pass
            
            # Split OCR text by pages (assuming pages are separated by double newlines)
            ocr_pages = [p.strip() for p in document.ocr_text.split('\n\n') if p.strip()]
            if len(ocr_pages) < page_count and page_count > 1:
                total_chars = len(document.ocr_text)
                chars_per_page = total_chars // page_count
                ocr_pages = []
                for i in range(page_count):
                    start = i * chars_per_page
                    end = start + chars_per_page if i < page_count - 1 else total_chars
                    page_text = document.ocr_text[start:end].strip()
                    ocr_pages.append(page_text if page_text else '')
            
            matches = []
            query_lower = query.lower()
            
            matches_queue = Queue()
            threads = []
            with local_path_for_field_file(document.file) as pdf_path:
                for page_num, page_text in enumerate(ocr_pages, 1):
                    if page_num > page_count:
                        break
                    if page_text:
                        try:
                            images = convert_from_path(pdf_path, first_page=page_num, last_page=page_num)
                            if images:
                                pil_img = images[0]
                                t = threading.Thread(target=ocr_search_worker, args=(page_num, pil_img, page_text, query_lower, matches_queue))
                                t.start()
                                threads.append(t)
                            else:
                                # Fallback: no image
                                text_lower = page_text.lower()
                                start = 0
                                while True:
                                    pos = text_lower.find(query_lower, start)
                                    if pos == -1:
                                        break
                                    append_match(matches_queue, page_num, pos, pos + len(query_lower), page_text[pos:pos+len(query_lower)], None)
                                    start = pos + 1
                        except Exception as e:
                            # Fallback: no image or OCR error
                            text_lower = page_text.lower()
                            start = 0
                            while True:
                                pos = text_lower.find(query_lower, start)
                                if pos == -1:
                                    break
                                append_match(matches_queue, page_num, pos, pos + len(query_lower), page_text[pos:pos+len(query_lower)], None)
                                start = pos + 1
            # Wait for threads to finish
            for t in threads:
                t.join()
            matches = []
            while not matches_queue.empty():
                matches.append(matches_queue.get())
            
            return JsonResponse({
                'has_ocr': True,
                'query': query,
                'matches': matches,
                'total_matches': len(matches)
            })
            
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=500)

def category_list(request):
    """Legacy route — conventions browse moved to /conventions."""
    return redirect('category_browse', permanent=True)


def _location_from_event_payload(event):
    """Extract a displayable location/address from a Consurf or MLPCON event dict."""
    if not event:
        return None
    payload = event if isinstance(event, dict) else None
    if payload is None:
        for key in ('location', 'address', 'latestAddress', 'latest_address'):
            text = _location_text(getattr(event, key, None))
            if text:
                return text
        return None
    for key in ('location', 'address', 'latestAddress', 'latest_address'):
        text = _location_text(payload.get(key))
        if text:
            return text
    return _location_text(payload.get('eventLocation') or payload.get('event_location'))


def _resolve_convention_display_location(category, consurf_event=None, conv_name=None):
    """Prefer Consurf event location over the static Category.location field."""
    location = _location_from_event_payload(consurf_event)
    if location:
        return location

    conv_name = conv_name or category.get_primary_convention_name() or category.name
    try:
        latest = category.get_latest_event()
        location = _location_from_event_payload(latest)
        if location:
            return location
    except Exception:
        pass

    try:
        convention_event = _get_consurf_event(
            conv_name,
            None,
            slug_hint=category.slug,
            allow_network=True,
        )
        location = _location_from_event_payload(convention_event)
        if location:
            return location
    except Exception:
        pass

    fallback = (category.location or '').strip()
    return fallback or None


def _published_category_documents(category):
    return PDFDocument.objects.filter(
        category=category,
        is_published=True,
        takedown_by_request=False,
    ).select_related('category', 'tags')


def _conbooks_tag():
    return Tag.objects.filter(name__iexact='Conbooks').first()


def _local_content_years(category):
    """Years with archived documents or schedules in FCA."""
    years = set()
    for year_val in _published_category_documents(category).filter(
        year__isnull=False,
    ).values_list('year', flat=True):
        try:
            years.add(int(year_val))
        except (TypeError, ValueError):
            continue
    for year_val in Schedule.objects.filter(
        category=category,
        deleted=False,
        year__isnull=False,
    ).values_list('year', flat=True):
        try:
            years.add(int(year_val))
        except (TypeError, ValueError):
            continue
    return years


def _consurf_year_start_dates(category):
    """Map event year -> start date from Consurf's convention events list."""
    from archive.consurf_slugs import expand_consurf_slug

    if not category.slug:
        return {}

    cache_key = f'external:category:year_starts:{category.slug}'
    cached = cache.get(cache_key)
    if cached is not None:
        return {
            int(year): dateparser.parse(str(start)).date()
            for year, start in cached.items()
            if start
        }

    result = {}
    serialized = {}
    try:
        for slug_candidate in expand_consurf_slug(category.slug):
            response = requests.get(
                f'https://consurf.net/api/external/conventions/{slug_candidate}',
                timeout=2,
            )
            if response.status_code != 200:
                continue
            try:
                data = response.json()
            except Exception:
                continue
            if not isinstance(data, dict):
                continue

            events = data.get('events') or data.get('result') or []
            for event in events or []:
                if not isinstance(event, dict):
                    continue
                start_raw = event.get('startDate') or event.get('date') or event.get('start_date')
                if not start_raw:
                    continue
                try:
                    parsed = dateparser.parse(str(start_raw))
                except Exception:
                    parsed = None
                if not parsed:
                    continue
                year_val = int(parsed.year)
                start_date = parsed.date()
                existing = result.get(year_val)
                if existing is None or start_date < existing:
                    result[year_val] = start_date
                    serialized[str(year_val)] = start_date.isoformat()

            if result:
                break
    except Exception:
        pass

    cache.set(cache_key, serialized, 60 * 60 * 12)
    return result


def _filter_started_convention_years(category, years):
    """Hide future Consurf-only years until the event start date has passed."""
    if not years:
        return years

    today = timezone.localdate()
    local_years = _local_content_years(category)
    start_dates = _consurf_year_start_dates(category)
    visible = []

    for year_val in years:
        try:
            year_int = int(year_val)
        except (TypeError, ValueError):
            continue

        if year_int in local_years:
            visible.append(year_int)
            continue

        start_date = start_dates.get(year_int)
        if start_date is not None:
            if start_date <= today:
                visible.append(year_int)
            continue

        if year_int < today.year:
            visible.append(year_int)

    return sorted(set(visible), reverse=True)


def _collect_convention_years(category):
    """Union of document, schedule, and Consurf years for a convention category."""
    years = set()
    years.update(_local_content_years(category))

    try:
        for year_val in category.get_active_years() or []:
            years.add(int(year_val))
    except Exception:
        pass

    return _filter_started_convention_years(category, sorted(years, reverse=True))


def _resolve_convention_year(category, year=None):
    years = _collect_convention_years(category)
    if year is not None:
        try:
            year_int = int(year)
        except (TypeError, ValueError):
            year_int = None
        if year_int is not None and year_int in years:
            return year_int, years
    if years:
        return years[0], years
    return None, years


def _format_event_date_range(start_date, end_date=None):
    if not start_date:
        return None
    try:
        start = start_date.date() if hasattr(start_date, 'date') and callable(start_date.date) else start_date
        end = end_date.date() if end_date and hasattr(end_date, 'date') and callable(end_date.date) else (end_date or start)
        if start == end:
            return start.strftime('%-d %B %Y')
        if start.year == end.year and start.month == end.month:
            return f"{start.day}–{end.day} {start.strftime('%B %Y')}"
        if start.year == end.year:
            return f"{start.strftime('%-d %B')} – {end.strftime('%-d %B %Y')}"
        return f"{start.strftime('%-d %B %Y')} – {end.strftime('%-d %B %Y')}"
    except Exception:
        try:
            return str(start_date)
        except Exception:
            return None


def _event_duration_days(start_date, end_date=None):
    if not start_date:
        return None
    try:
        start = start_date.date() if hasattr(start_date, 'date') and callable(start_date.date) else start_date
        end = end_date.date() if end_date and hasattr(end_date, 'date') and callable(end_date.date) else start
        return max(1, (end - start).days + 1)
    except Exception:
        return None


def _maps_url_for_address(address):
    if not address:
        return None
    from urllib.parse import quote
    return f'https://www.google.com/maps/search/?api=1&query={quote(str(address))}'


def _event_display_title(category, consurf_event=None, selected_year=None):
    base = category.name
    if isinstance(consurf_event, dict):
        name = (consurf_event.get('name') or base).strip()
        theme = (consurf_event.get('theme') or '').strip()
        if theme:
            return f'{name}: {theme}'
        return name
    if selected_year:
        return f'{base} {selected_year}'
    return base


def _fetch_consurf_event_api(category, year):
    """Fetch full per-year event payload from Consurf's public events API."""
    from archive.consurf_slugs import consurf_year_event_slugs

    if not category.slug or year is None:
        return None

    for slug in consurf_year_event_slugs(category.slug, year):
        cache_key = f'consurf:event-api:{slug}'
        cached = cache.get(cache_key)
        if cached is not None:
            if cached != '__miss__':
                return cached
            continue

        try:
            response = requests.get(
                f'https://consurf.net/api/events/{slug}',
                timeout=3,
            )
            if response.status_code != 200:
                cache.set(cache_key, '__miss__', 60 * 30)
                continue
            data = response.json()
            if not isinstance(data, dict):
                cache.set(cache_key, '__miss__', 60 * 30)
                continue
            cache.set(cache_key, data, 60 * 60 * 6)
            return data
        except Exception:
            cache.set(cache_key, '__miss__', 60 * 30)
            continue

    return None


def _enrich_mlpcon_event_stats_fields(event_payload):
    """Fill attendance/charity from the MLPCON API dataset when a cached event lacks them."""
    if not isinstance(event_payload, dict):
        return event_payload

    has_attendance = event_payload.get('attendance') is not None
    has_charity = event_payload.get('charity') is not None
    if has_attendance and has_charity:
        return event_payload

    name = str(event_payload.get('name') or '').strip().lower()
    if not name:
        return event_payload

    event_year = None
    for key in ('start_date', 'end_date'):
        value = event_payload.get(key)
        try:
            if hasattr(value, 'year'):
                event_year = int(value.year)
                break
        except Exception:
            pass

    mlp_payload = _load_mlpcon_api_dataset(refresh=False)
    if not isinstance(mlp_payload, dict):
        return event_payload

    rows = mlp_payload.get('data') or []
    if not rows:
        return event_payload

    target_slug = re.sub(r'[^a-z0-9]+', '', name)

    def _parse_int_stat(row, *keys):
        for key in keys:
            raw_val = row.get(key) if isinstance(row, dict) else None
            if raw_val is None:
                continue
            text = str(raw_val).strip()
            if not text:
                continue
            try:
                return int(text.replace(',', '').replace('$', ''))
            except (TypeError, ValueError):
                continue
        return None

    best = None
    best_score = -1.0
    for row in rows:
        if not isinstance(row, dict):
            continue
        row_name = str(row.get('Name') or row.get('name') or '').strip()
        if not row_name:
            continue
        row_slug = re.sub(r'[^a-z0-9]+', '', row_name.lower())
        score = SequenceMatcher(None, row_slug, target_slug).ratio()
        if row_slug == target_slug:
            score += 0.4

        row_year = None
        for date_key in ('Start', 'start', 'End', 'end'):
            raw_date = row.get(date_key)
            if not raw_date:
                continue
            try:
                parsed = dateparser.parse(str(raw_date))
            except Exception:
                parsed = None
            if parsed:
                row_year = int(parsed.year)
                break
        if event_year and row_year:
            if row_year == event_year:
                score += 0.35
            else:
                score -= 0.2

        if score > best_score:
            best_score = score
            best = row

    if not best or best_score < 0.7:
        return event_payload

    enriched = dict(event_payload)
    if not has_attendance:
        attendance = _parse_int_stat(best, 'Attendance', 'attendance')
        if attendance is not None:
            enriched['attendance'] = attendance
    if not has_charity:
        charity = _parse_int_stat(best, 'Charity', 'charity')
        if charity is not None:
            enriched['charity'] = charity
    if not enriched.get('charity_partner'):
        partner = best.get('Charity Partner') or best.get('charityPartner')
        if partner:
            enriched['charity_partner'] = str(partner).strip()
    return enriched


def _build_mlpcon_event_stats(event_payload):
    """Turn a normalized MLPCON.INFO event into display-ready stat boxes."""
    if not isinstance(event_payload, dict):
        return None

    event_payload = _enrich_mlpcon_event_stats_fields(event_payload)

    slug = (event_payload.get('slug') or '').strip()
    if not slug:
        name = (event_payload.get('name') or '').strip()
        if name:
            slug = re.sub(r'[^a-z0-9]+', '', name.lower())

    raw = {
        'slug': slug,
        'url': f'https://mlpcon.info/{slug}' if slug else 'https://mlpcon.info',
        'source': 'mlpcon',
        'is_cancelled': False,
    }
    boxes = []

    attendance = event_payload.get('attendance')
    if attendance is not None:
        try:
            attendance = int(attendance)
        except (TypeError, ValueError):
            attendance = None
        if attendance is not None:
            raw['attendee_count'] = attendance
            if attendance > 0:
                boxes.append({'label': 'Attendees', 'value': attendance, 'format': 'number'})

    charity = event_payload.get('charity')
    if charity is not None:
        try:
            charity = int(charity)
        except (TypeError, ValueError):
            charity = None
        if charity is not None:
            raw['charity_raised'] = charity
            if charity > 0:
                boxes.append(
                    {
                        'label': 'Charity raised',
                        'value': charity,
                        'format': 'currency',
                        'currency_symbol': '$',
                        'currency_code': 'USD',
                    }
                )

    charity_partner = event_payload.get('charity_partner')
    if isinstance(charity_partner, str) and charity_partner.strip():
        raw['charity_partner'] = charity_partner.strip()

    raw['boxes'] = boxes
    raw['has_stats'] = bool(boxes)
    return raw


def _build_consurf_event_stats(event_payload):
    """Turn a Consurf /api/events payload into display-ready stat boxes."""
    if not isinstance(event_payload, dict):
        return None

    slug = (event_payload.get('slug') or '').strip()
    stat_specs = (
        ('attendee_count', 'attendeeCount', 'Attendees'),
        ('fursuiter_count', 'fursuiterCount', 'Fursuiters'),
        ('parade_count', 'paradeCount', 'Parade'),
        ('volunteer_count', 'volunteerCount', 'Volunteers'),
        ('vendor_count', 'vendorCount', 'Vendors'),
        ('artist_count', 'artistCount', 'Artists'),
        ('total_posts', 'totalPosts', 'Posts'),
    )

    raw = {
        'slug': slug,
        'url': f'https://consurf.net/{slug}' if slug else None,
        'source': 'consurf',
    }
    boxes = []
    for key, api_key, label in stat_specs:
        value = event_payload.get(api_key)
        if value is None:
            continue
        try:
            value = int(value)
        except (TypeError, ValueError):
            continue
        raw[key] = value
        if value > 0:
            boxes.append({'label': label, 'value': value, 'format': 'number'})

    charity_currency_code = (
        event_payload.get('charityCurrencyCode')
        or event_payload.get('charityCurrency')
        or event_payload.get('currencyCode')
        or event_payload.get('currency')
    )
    if isinstance(charity_currency_code, str):
        charity_currency_code = charity_currency_code.strip().upper()
    else:
        charity_currency_code = None
    if not charity_currency_code:
        event_country_code = (
            event_payload.get('countryCode')
            or event_payload.get('country')
            or event_payload.get('locationCountryCode')
            or ((event_payload.get('eventLocation') or {}).get('countryCode') if isinstance(event_payload.get('eventLocation'), dict) else None)
            or ((event_payload.get('convention') or {}).get('hostCountry') if isinstance(event_payload.get('convention'), dict) else None)
        )
        if isinstance(event_country_code, str):
            event_country_code = event_country_code.strip().upper()
        else:
            event_country_code = None
        country_currency_map = {
            'AU': 'AUD',
            'AUS': 'AUD',
            'CA': 'CAD',
            'CAN': 'CAD',
            'NZ': 'NZD',
            'NZL': 'NZD',
            'HK': 'HKD',
            'HKG': 'HKD',
            'US': 'USD',
            'USA': 'USD',
            'GB': 'GBP',
            'GBR': 'GBP',
            'JP': 'JPY',
            'JPN': 'JPY',
            'CN': 'CNY',
            'CHN': 'CNY',
            'DE': 'EUR',
            'DEU': 'EUR',
            'FR': 'EUR',
            'FRA': 'EUR',
            'ES': 'EUR',
            'ESP': 'EUR',
            'IT': 'EUR',
            'ITA': 'EUR',
            'NL': 'EUR',
            'NLD': 'EUR',
            'BE': 'EUR',
            'BEL': 'EUR',
            'IE': 'EUR',
            'IRL': 'EUR',
            'AT': 'EUR',
            'AUT': 'EUR',
            'PT': 'EUR',
            'PRT': 'EUR',
            'FI': 'EUR',
            'FIN': 'EUR',
            'GR': 'EUR',
            'GRC': 'EUR',
            'LU': 'EUR',
            'LUX': 'EUR',
        }
        charity_currency_code = country_currency_map.get(event_country_code)

    charity_currency_symbol = (
        event_payload.get('charityCurrencySymbol')
        or event_payload.get('currencySymbol')
    )
    if isinstance(charity_currency_symbol, str):
        charity_currency_symbol = charity_currency_symbol.strip()
    else:
        charity_currency_symbol = None

    if not charity_currency_symbol and charity_currency_code:
        known_currency_symbols = {
            'USD': '$',
            'EUR': '€',
            'GBP': '£',
            'JPY': '¥',
            'CNY': '¥',
            # Dollar currencies that should not look like USD.
            'CAD': 'C$',
            'AUD': 'A$',
            'NZD': 'NZ$',
            'HKD': 'HK$',
            'BRL': 'R$',
            # Useful fallback display prefixes for common non-symbol currencies.
            'CHF': 'CHF ',
            'SEK': 'SEK ',
            'NOK': 'NOK ',
            'DKK': 'DKK ',
            'PLN': 'PLN ',
            'CZK': 'CZK ',
        }
        charity_currency_symbol = known_currency_symbols.get(charity_currency_code)

    if not charity_currency_symbol:
        charity_currency_symbol = '$'

    charity = event_payload.get('charityRaised')
    if charity is not None:
        try:
            charity = int(charity)
        except (TypeError, ValueError):
            charity = None
        if charity is not None:
            raw['charity_raised'] = charity
            if charity > 0:
                boxes.append(
                    {
                        'label': 'Charity raised',
                        'value': charity,
                        'format': 'currency',
                        'currency_symbol': charity_currency_symbol,
                        'currency_code': charity_currency_code,
                    }
                )

    raw['is_cancelled'] = bool(event_payload.get('isCancelled'))
    raw['boxes'] = boxes
    raw['has_stats'] = bool(boxes)

    return raw


def _fetch_consurf_year_metadata(category, year):
    """Fetch year-accurate dates/theme from Consurf's per-event API."""
    from archive.consurf_slugs import consurf_year_event_slugs

    if not category.slug or year is None:
        return None

    for slug in consurf_year_event_slugs(category.slug, year):
        try:
            response = requests.get(
                f'https://consurf.net/api/external/events/{slug}',
                timeout=2,
            )
            if response.status_code != 200:
                continue
            data = response.json()
            if not isinstance(data, dict):
                continue
            start = dateparser.parse(data['startDate']) if data.get('startDate') else None
            end = dateparser.parse(data['endDate']) if data.get('endDate') else None
            if not start:
                continue
            return {
                'start_date': start,
                'end_date': end or start,
                'theme': data.get('theme') or '',
                'country': data.get('country') or '',
                'timezone': data.get('timezone') or '',
                'name': data.get('name') or category.name,
                'location': data.get('address') or data.get('location') or '',
                'event_description': data.get('description') or '',
            }
        except Exception:
            continue
    return None


def _enrich_consurf_event_for_year(category, selected_year, consurf_event):
    """Prefer per-year Consurf metadata when cached convention dates are wrong."""
    if selected_year is None:
        return consurf_event

    year_meta = _fetch_consurf_year_metadata(category, selected_year)
    if not year_meta:
        return consurf_event

    if not isinstance(consurf_event, dict):
        consurf_event = {'source': 'consurf'}

    merged = dict(consurf_event)
    start = merged.get('start_date')
    try:
        start_year = start.year if start is not None else None
    except Exception:
        start_year = None

    if start_year != selected_year:
        merged['start_date'] = year_meta['start_date']
        merged['end_date'] = year_meta['end_date']

    for key in ('theme', 'country', 'timezone', 'name', 'location', 'event_description'):
        value = year_meta.get(key)
        if value and not merged.get(key):
            merged[key] = value

    if year_meta.get('event_description') and not merged.get('description'):
        merged['description'] = year_meta['event_description']

    return merged


def _build_convention_event_context(request, category, year=None, tab=None):
    """Shared context for convention hub pages and API responses."""
    from archive.consurf_slugs import consurf_year_event_slugs
    from archive.document_descriptions import resolve_event_display_description

    selected_year, years = _resolve_convention_year(category, year)
    valid_tabs = ('overview', 'conbook', 'schedule', 'documents', 'vault', 'stats')
    active_tab = tab if tab in valid_tabs else 'overview'

    conv_name = category.get_primary_convention_name() or category.name
    consurf_event = None
    if selected_year is not None:
        consurf_event = _get_consurf_event(
            conv_name,
            selected_year,
            slug_hint=category.slug,
            allow_network=True,
        )
        consurf_event = _enrich_consurf_event_for_year(category, selected_year, consurf_event)

    conbooks_tag = _conbooks_tag()
    pub_docs = _published_category_documents(category)
    year_docs = pub_docs.filter(year=selected_year) if selected_year is not None else pub_docs

    conbooks = []
    if selected_year is not None and conbooks_tag:
        conbooks = list(year_docs.filter(tags=conbooks_tag).order_by('title'))
    conbook = conbooks[0] if conbooks else None

    other_documents = []
    if selected_year is not None:
        other_qs = year_docs
        if conbooks_tag:
            other_qs = other_qs.exclude(tags=conbooks_tag)
        other_documents = list(other_qs.order_by('title'))

    schedule = None
    if selected_year is not None:
        schedule = Schedule.objects.filter(
            category=category,
            year=selected_year,
            deleted=False,
        ).first()

    vault_items = []
    if selected_year is not None:
        vault_items = list(
            PhysicalInventoryItem.objects.filter(
                con__icontains=category.name,
                year=selected_year,
            ).order_by('item_type', 'id')
        )

    doc_slugs = [doc.slug for doc in year_docs]
    view_stats = get_pdf_view_stats(doc_slugs) or {'counts': {}, 'total': 0}
    view_counts = view_stats.get('counts', {}) or {}
    other_doc_slugs = [doc.slug for doc in other_documents]
    conbook_views = view_counts.get(conbook.slug, 0) if conbook else 0
    other_document_views = sum(view_counts.get(slug, 0) for slug in other_doc_slugs)
    archive_stats = {
        'document_count': year_docs.count(),
        'total_pages': year_docs.aggregate(total=Sum('page_count'))['total'] or 0,
        'has_conbook': conbook is not None,
        'has_schedule': schedule is not None,
        'has_vault': bool(vault_items),
        'conbook_views': conbook_views if conbook else None,
        'other_document_views': other_document_views if other_documents else None,
        'total_views': sum(view_counts.get(slug, 0) for slug in doc_slugs),
        'vault_item_count': len(vault_items),
    }

    event_source = (
        (consurf_event.get('source') or '').lower()
        if isinstance(consurf_event, dict)
        else ''
    )

    consurf_event_api = None
    consurf_event_stats = None
    if event_source == 'mlpcon':
        consurf_stats_supported = True
        consurf_event_stats = _build_mlpcon_event_stats(consurf_event)
    elif selected_year is not None:
        consurf_stats_supported = True
        consurf_event_api = _fetch_consurf_event_api(category, selected_year)
        consurf_event_stats = _build_consurf_event_stats(consurf_event_api)
    else:
        consurf_stats_supported = False

    consurf_url = None
    if isinstance(consurf_event, dict):
        consurf_url = consurf_event.get('url')
    if not consurf_url and selected_year is not None and event_source != 'mlpcon':
        year_slugs = consurf_year_event_slugs(category.slug, selected_year)
        if year_slugs:
            consurf_url = f'https://consurf.net/{year_slugs[0]}'
    if not consurf_url:
        consurf_url = category.get_consurf_public_url()

    event_description = ''
    if isinstance(consurf_event, dict):
        event_description = (consurf_event.get('event_description') or '').strip()
    if not event_description and conbook:
        event_description = (
            resolve_event_display_description(
                conbook,
                consurf_event=consurf_event,
                allow_network=False,
            )
            or ''
        ).strip()
        # Category-level blurbs are not year-specific event descriptions.
        category_desc = (category.description or '').strip()
        if event_description and category_desc and event_description == category_desc:
            event_description = ''

    has_year_event_description = bool(event_description)

    for doc in conbooks + other_documents:
        doc.pdf_view_count = view_counts.get(doc.slug, 0)

    display_location = _resolve_convention_display_location(
        category,
        consurf_event=consurf_event,
        conv_name=conv_name,
    )

    from .ia_mirrors import get_document_ia_mirrors

    conbook_ia_mirrors = (
        get_document_ia_mirrors(conbook)
        if conbook
        else {'available': False, 'options': []}
    )

    event_display_title = _event_display_title(category, consurf_event, selected_year)
    event_date_range = None
    event_duration_days = None
    if isinstance(consurf_event, dict):
        event_date_range = _format_event_date_range(
            consurf_event.get('start_date'),
            consurf_event.get('end_date'),
        )
        event_duration_days = _event_duration_days(
            consurf_event.get('start_date'),
            consurf_event.get('end_date'),
        )
    maps_url = _maps_url_for_address(display_location)

    return {
        'category': category,
        'years': years,
        'selected_year': selected_year,
        'active_tab': active_tab,
        'consurf_event': consurf_event,
        'consurf_url': consurf_url,
        'display_location': display_location,
        'event_description': event_description,
        'has_year_event_description': has_year_event_description,
        'event_display_title': event_display_title,
        'event_date_range': event_date_range,
        'event_duration_days': event_duration_days,
        'maps_url': maps_url,
        'conbook': conbook,
        'conbooks': conbooks,
        'conbook_views': conbook_views,
        'conbook_ia_mirrors': conbook_ia_mirrors,
        'other_documents': other_documents,
        'schedule': schedule,
        'vault_items': vault_items,
        'archive_stats': archive_stats,
        'consurf_event_stats': consurf_event_stats,
        'consurf_stats_supported': consurf_stats_supported,
        'doc_view_counts': view_counts,
    }


def convention_detail(request, slug, year=None):
    """Convention hub with per-year tabs (overview, conbook, schedule, vault, stats)."""
    category = get_object_or_404(Category, slug=slug)
    tab = (request.GET.get('tab') or 'overview').strip().lower()
    context = _build_convention_event_context(request, category, year=year, tab=tab)

    if year is not None and context.get('selected_year') != year:
        selected = context['selected_year']
        if selected is not None:
            url = reverse('convention_detail_year', kwargs={'slug': slug, 'year': selected})
            if tab and tab != 'overview':
                url = f'{url}?tab={tab}'
            return redirect(url)

    return render(request, 'archive/convention_detail.html', context)


def category_browse(request):
    """Convention browse shell; cards load via /v1/categories/."""
    search_query = request.GET.get('q', '')
    context = {
        'categories': [],
        'page_obj': None,
        'search_query': search_query,
    }
    return render(request, 'archive/category_browse.html', context)


def category_display(request):
    """Legacy route — conventions browse moved to /conventions."""
    return redirect('category_browse', permanent=True)



# signal handler to pre-cache first-page thumbnails on upload
from django.db.models.signals import post_save
from django.dispatch import receiver

@receiver(post_save, sender=PDFDocument)
def _generate_first_page_thumbnail(sender, instance, created, **kwargs):
    """Generate page_1.jpg immediately after a document is saved (if missing)."""
    from archive.pdf_cache import generate_page_image, pdf_page_cache_path

    try:
        slug = instance.slug
        if not slug or not instance.file:
            return
        _, output_path = pdf_page_cache_path(slug, 1)
        with local_path_for_field_file(instance.file) as pdf_path:
            if not pdf_path or not os.path.exists(pdf_path):
                logger.warning("Post-save thumbnail skipped for %s: missing pdf file", slug)
                return
            generate_page_image(
                pdf_path,
                1,
                output_path,
                force_regenerate=True,
                slug=slug,
            )
            logger.info("Generated thumbnail for %s", slug)
    except Exception as e:
        logger.exception("Post-save thumbnail gen failed for %s: %s", getattr(instance, 'slug', None), e)

def get_client_ip(request):
    """Get the client IP address from the request"""
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        ip = x_forwarded_for.split(',')[0]
    else:
        ip = request.META.get('REMOTE_ADDR')
    return ip


def _is_private_ip(ip_address):
    if not ip_address:
        return True
    return (
        ip_address.startswith('10.') or
        ip_address.startswith('192.168.') or
        ip_address.startswith('127.') or
        ip_address.startswith('172.16.') or
        ip_address.startswith('172.17.') or
        ip_address.startswith('172.18.') or
        ip_address.startswith('172.19.') or
        ip_address.startswith('172.2') or  # covers 172.20-172.29
        ip_address.startswith('172.3') or  # covers 172.30-172.31
        ip_address in ('::1',) or
        ip_address.startswith('fc') or
        ip_address.startswith('fd') or
        ip_address.startswith('fe80')
    )


def _get_ip_location(ip_address):
    """Best-effort IP geolocation (cached)."""
    if _is_private_ip(ip_address):
        return None

    cache_key = f'geoip:{ip_address}'
    cached_location = cache.get(cache_key)
    if cached_location is not None:
        return cached_location

    try:
        response = requests.get(f'https://ipapi.co/{ip_address}/json/', timeout=2)
        if response.status_code == 200:
            data = response.json()
            city = data.get('city')
            region = data.get('region') or data.get('region_code')
            country = data.get('country_name') or data.get('country')
            parts = [part for part in [city, region, country] if part]
            location = ', '.join(parts) if parts else None
            cache.set(cache_key, location, 60 * 60 * 24)
            return location
    except Exception:
        pass

    cache.set(cache_key, None, 60 * 60 * 2)
    return None


def _get_request_location(request, ip_address=None):
    """Best-effort location from reverse proxy headers or IP geolocation."""
    city = request.META.get('HTTP_GEOIP_CITY')
    region = request.META.get('HTTP_GEOIP_REGION')
    country_name = request.META.get('HTTP_GEOIP_COUNTRY_NAME')
    country_code = request.META.get('HTTP_CF_IPCOUNTRY') or request.META.get('HTTP_X_COUNTRY_CODE')

    parts = [part for part in [city, region, country_name] if part]
    if parts:
        return ', '.join(parts)
    if country_code:
        return country_code

    return _get_ip_location(ip_address)


def index(request):
    """Homepage shell; featured conbooks and stats load via /v1/."""
    return render(request, 'archive/index.html')


def faq(request):
    """FAQ page"""
    from archive.site_pages import published_faq_payloads
    return render(request, 'archive/faq.html', {
        'faq_entries': published_faq_payloads(),
    })


def contact(request):
    """Contact page"""
    from archive.site_pages import published_contact_payloads
    return render(request, 'archive/contact.html', {
        'contact_channels': published_contact_payloads(),
    })


def preservation_policy(request):
    """Preservation Policy page with takedown request information"""
    return render(request, 'archive/preservation_policy.html')


def rights(request):
    """Rights and licensing information page"""
    return render(request, 'archive/rights.html')


def board_of_directors(request):
    """Board of Directors page with team members from Telegram"""
    import requests
    from django.core.cache import cache
    
    from archive.api.site_content import STAFF_MEMBERS
    director_usernames = STAFF_MEMBERS
    from .utils import (
        collect_vault_contributors,
        get_vault_contributor_avatar_overrides,
        resolve_vault_contributor_avatar_url,
        vault_contributor_key,
    )

    avatar_overrides = get_vault_contributor_avatar_overrides()
    directors = []
    for user_info in director_usernames:
        username = user_info['username']
        director_data = {
            'username': username,
            'has_avatar': True,
            'description': user_info['description'],
            'pronouns': user_info.get('pronouns', ''),
            'role': user_info.get('role', ''),
            'bluesky_handle': user_info.get('bluesky_handle', ''),
            'twitter_username': user_info.get('twitter_username', ''),
            'discord_username': user_info.get('discord_username', '')
        }

        discord_id = user_info.get('discord_id')
        telegram_key = vault_contributor_key('telegram', username)
        discord_key = vault_contributor_key('discord', str(discord_id)) if discord_id else None
        if telegram_key in avatar_overrides:
            director_data['avatar_url'] = avatar_overrides[telegram_key]
        elif discord_key and discord_key in avatar_overrides:
            director_data['avatar_url'] = avatar_overrides[discord_key]
        elif username == 'ash_1595' and discord_id:
            director_data['avatar_url'] = resolve_vault_contributor_avatar_url(
                'discord', str(discord_id), overrides=avatar_overrides
            )
        else:
            director_data['avatar_url'] = resolve_vault_contributor_avatar_url(
                'telegram', username, overrides=avatar_overrides
            )
        
        # Add override display name if provided
        if user_info.get('display_name_override'):
            director_data['display_name'] = user_info['display_name_override']
        
        # Try to fetch user info from Telegram API
        cache_key = f'telegram_user_{username.lower()}'
        cached_data = cache.get(cache_key)
        
        if cached_data:
            director_data.update(cached_data)
        else:
            try:
                # Fetch from Telegram API
                resp = requests.get(
                    f'https://tg.tabs.gay/api/getInfo?id=@{username}',
                    timeout=5
                )
                if resp.status_code != 200:
                    resp = requests.get(
                        f'https://tg.tabs.gay/api/getInfo?id={username}',
                        timeout=5
                    )
                
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get('success'):
                        user = data.get('response', {}).get('User', {})
                        if user:
                            display_name = user.get('first_name', '') or ''
                            if user.get('last_name'):
                                display_name = f"{display_name} {user['last_name']}".strip()
                            
                            user_info_data = {
                                'username': username,
                                'display_name': display_name or username,
                                'discord_id': user_info.get('discord_id', ''),
                                'bio': user.get('about', ''),
                                'has_avatar': True,
                            }
                            director_data.update(user_info_data)
                            cache.set(cache_key, user_info_data, 3600)
            except Exception:
                if 'display_name' not in director_data:
                    director_data['display_name'] = username
        
        # Ensure override display name is preserved
        if user_info.get('display_name_override'):
            director_data['display_name'] = user_info['display_name_override']
        
        directors.append(director_data)
    
    # include latest Ko-fi donation if available
    kofi_donator = None
    try:
        from .models import KoFiDonation
        last = KoFiDonation.objects.order_by('-created_at').first()
        if last:
            kofi_donator = {
                'name': last.name,
                'amount': float(last.amount) if last.amount is not None else None,
                'discord_username': last.discord_profile.discord_username if last.discord_profile else None,
                'discord_id': last.discord_profile.discord_userid if last.discord_profile else None,
                'avatar_url': last.avatar_url,
            }
    except Exception:
        kofi_donator = None

    staff_telegram_usernames = {d['username'].lower() for d in director_usernames if d.get('username')}
    staff_discord_ids = {str(d['discord_id']) for d in director_usernames if d.get('discord_id')}
    vault_contributors = collect_vault_contributors()
    for contributor in vault_contributors:
        contributor_type = contributor.get('type')
        contributor_username = (contributor.get('username') or '').strip()
        is_staff = (
            (contributor_type == 'telegram' and contributor_username.lower() in staff_telegram_usernames)
            or (contributor_type == 'discord' and contributor_username in staff_discord_ids)
        )
        contributor['is_staff'] = is_staff

    context = {
        'directors': directors,
        'kofi_donator': kofi_donator,
        'vault_contributors': vault_contributors,
    }
    return render(request, 'archive/staff.html', context)


# Custom Admin Views

def admin_login(request):
    """Custom login page for admin panel"""
    if request.user.is_authenticated and request.user.is_staff:
        return redirect('admin_dashboard')
    
    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')
        user = authenticate(request, username=username, password=password)
        
        if user is not None:
            if user.is_staff:
                login(request, user)
                next_url = request.GET.get('next', 'admin_dashboard')
                return redirect(next_url)
            else:
                messages.error(request, 'You do not have permission to access the admin panel.')
        else:
            messages.error(request, 'Invalid username or password.')
    
    return render(request, 'archive/admin/login.html')


def admin_logout(request):
    """Logout from admin panel"""
    logout(request)
    messages.success(request, 'You have been logged out successfully.')
    return redirect('index')


@staff_member_required
def admin_dashboard(request):
    """Custom admin dashboard with Umami analytics"""
    from datetime import timedelta
    now = timezone.now()
    recent_cutoff = now - timedelta(days=30)
    start_ms = int(recent_cutoff.timestamp() * 1000)
    end_ms = int(now.timestamp() * 1000)
    
    # Fetch Umami stats
    recent_pageviews = 0
    recent_visitors = 0
    top_referer = None
    umami_configured = _umami_is_configured()
    
    context = {
        'total_documents': PDFDocument.objects.count(),
        'published_documents': PDFDocument.objects.filter(is_published=True).count(),
        'total_categories': Category.objects.count(),
        'total_pdf_views': get_total_pdf_views(),
        'recent_pageviews': recent_pageviews,
        'recent_visitors': recent_visitors,
        'top_referer': top_referer,
        'recent_documents': PDFDocument.objects.order_by('-uploaded_at')[:5],
    }
    return render(request, 'archive/admin/dashboard.html', context)


def _prepare_document_form_post(request):
    """Normalize tags in POST data for PDFUploadForm (resolve ID or create by name)."""
    from django.utils.text import slugify

    mutable_post = request.POST.copy()
    tags_vals = [v.strip() for v in request.POST.getlist('tags') if v and v.strip()]
    tags_val = tags_vals[-1] if tags_vals else ''

    if tags_val:
        try:
            tag = Tag.objects.get(pk=int(tags_val))
            mutable_post['tags'] = str(tag.pk)
        except (ValueError, Tag.DoesNotExist):
            tag, _ = Tag.objects.get_or_create(
                name=tags_val,
                defaults={'slug': slugify(tags_val)},
            )
            mutable_post['tags'] = str(tag.pk)
    else:
        mutable_post['tags'] = ''

    return mutable_post


@staff_member_required
def admin_upload(request):
    """Upload a new PDF document"""
    if request.method == 'POST':
        form = PDFUploadForm(_prepare_document_form_post(request), request.FILES)
        if form.is_valid():
            document = form.save(commit=False)
            document.uploaded_by = request.user
            document.save()
            messages.success(request, f'Document "{document.title}" uploaded successfully!')
            return redirect('admin_documents')
    else:
        form = PDFUploadForm()
    
    context = {
        'form': form,
        'page_title': 'Upload PDF Document',
    }
    return render(request, 'archive/admin/upload.html', context)


@staff_member_required
def admin_documents(request):
    """Manage all documents"""
    from django.db.models import Count
    from datetime import timedelta
    from django.utils import timezone
    documents = PDFDocument.objects.all().select_related('category', 'tags').order_by('-uploaded_at')
    # Annotate with like count
    # documents = documents.annotate(like_count=Count('likes'))
    # For peak download times, get last 30 days downloads per document
    peak_times = {}
    now = timezone.now()
    for doc in documents:
        logs = doc.download_logs.filter(downloaded_at__gte=now-timedelta(days=30))
        # Group by hour
        hours = {}
        for log in logs:
            hour = log.downloaded_at.replace(minute=0, second=0, microsecond=0)
            hours[hour] = hours.get(hour, 0) + 1
        if hours:
            peak_hour = max(hours, key=hours.get)
            peak_times[doc.id] = {'hour': peak_hour, 'count': hours[peak_hour]}
        else:
            peak_times[doc.id] = None
    
    # Tag filter
    tag_filter = request.GET.get('tag', '')
    if tag_filter:
        documents = documents.filter(tags__name__iexact=tag_filter)

    # Provide tag list for filter UI
    tag_list = list(Tag.objects.order_by('name').values_list('name', flat=True))

    # Search
    search_query = request.GET.get('search', '')
    if search_query:
        documents = documents.filter(
            Q(title__icontains=search_query) |
            Q(description__icontains=search_query) |
            Q(author__icontains=search_query) |
            Q(tags__name__icontains=search_query)
        ).distinct()
    
    # Filter by published status
    status = request.GET.get('status', '')
    if status == 'published':
        documents = documents.filter(is_published=True)
    elif status == 'unpublished':
        documents = documents.filter(is_published=False)
    
    # Pagination
    paginator = Paginator(documents, 25)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    pdf_view_stats = get_pdf_view_stats([doc.slug for doc in page_obj], warm_missing=False) or {'counts': {}, 'total': 0}
    for doc in page_obj:
        doc.pdf_view_count = pdf_view_stats.get('counts', {}).get(doc.slug, doc.download_count or 0)
    
    context = {
        'page_obj': page_obj,
        'search_query': search_query,
        'current_status': status,
        'page_title': 'Manage Documents',
        'peak_times': peak_times,
        'tag_list': tag_list,
        'current_tag': tag_filter,
    }
    return render(request, 'archive/admin/documents.html', context)


@staff_member_required
def admin_export_documents(request):
    """Export the current filtered document list as CSV (includes tags)."""
    documents = PDFDocument.objects.all().select_related('category', 'tags')

    # apply same filters as admin_documents
    status = request.GET.get('status', '')
    if status == 'published':
        documents = documents.filter(is_published=True)
    elif status == 'unpublished':
        documents = documents.filter(is_published=False)

    tag_filter = request.GET.get('tag', '')
    if tag_filter:
        documents = documents.filter(tags__name__iexact=tag_filter)

    search_query = request.GET.get('search', '')
    if search_query:
        documents = documents.filter(
            Q(title__icontains=search_query) |
            Q(description__icontains=search_query) |
            Q(author__icontains=search_query) |
            Q(tags__name__icontains=search_query)
        ).distinct()

    # Build CSV
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="documents_export.csv"'
    writer = csv.writer(response)
    writer.writerow(['id', 'title', 'slug', 'category', 'year', 'convention_name', 'author', 'tags', 'is_published', 'uploaded_at'])
    for doc in documents.order_by('-uploaded_at'):
        tag_name = doc.tags.name if doc.tags else ''
        writer.writerow([doc.id, doc.title, doc.slug, (doc.category.name if doc.category else ''), doc.year or '', doc.convention_name or '', doc.author or '', tag_name, 'yes' if doc.is_published else 'no', doc.uploaded_at.isoformat()])
    return response


@staff_member_required
def admin_edit_document(request, slug):
    """Edit an existing document"""
    document = get_object_or_404(PDFDocument, slug=slug)
    
    if request.method == 'POST':
        form = PDFUploadForm(_prepare_document_form_post(request), request.FILES, instance=document)
        if form.is_valid():
            doc = form.save()
            messages.success(request, f'Document "{document.title}" updated successfully!')
            return redirect('admin_documents')
    else:
        form = PDFUploadForm(instance=document)
    
    context = {
        'form': form,
        'document': document,
        'page_title': f'Edit: {document.title}'
    }
    return render(request, 'archive/admin/upload.html', context)


@staff_member_required
def admin_delete_document(request, slug):
    """Delete a document"""
    document = get_object_or_404(PDFDocument, slug=slug)
    pdf_view_stats = get_pdf_view_stats([slug]) or {'counts': {}, 'total': 0}
    document_pdf_views = pdf_view_stats.get('counts', {}).get(slug, 0)
    
    if request.method == 'POST':
        title = document.title
        document.file.delete()  # Delete the actual file
        document.delete()
        messages.success(request, f'Document "{title}" deleted successfully!')
        return redirect('admin_documents')
    
    context = {
        'document': document,
        'document_pdf_views': document_pdf_views,
        'page_title': 'Delete Document'
    }
    return render(request, 'archive/admin/delete_confirm.html', context)


@staff_member_required
def admin_categories(request):
    """Manage categories"""
    if request.method == 'POST':
        form = CategoryForm(request.POST, request.FILES)
        if form.is_valid():
            category = form.save()
            messages.success(request, f'Category "{category.name}" created successfully!')
            return redirect('admin_categories')
    else:
        form = CategoryForm()
    
    categories = Category.objects.annotate(doc_count=Count('documents')).order_by('order', 'name')
    
    context = {
        'form': form,
        'categories': categories,
        'page_title': 'Manage Categories'
    }
    return render(request, 'archive/admin/categories.html', context)


@staff_member_required
def admin_edit_category(request, category_id):
    """Edit an existing category"""
    category = get_object_or_404(Category, id=category_id)
    
    if request.method == 'POST':
        form = CategoryForm(request.POST, request.FILES, instance=category)
        if form.is_valid():
            category = form.save()
            messages.success(request, f'Category "{category.name}" updated successfully!')
            return redirect('admin_categories')
    else:
        form = CategoryForm(instance=category)
    
    context = {
        'form': form,
        'category': category,
        'page_title': f'Edit Category: {category.name}'
    }
    return render(request, 'archive/admin/edit_category.html', context)

def preservation_tips(request):
    """Render the preservation tips informational page."""
    return render(request, 'archive/preservation_tips.html')


@staff_member_required
def admin_delete_category(request, category_id):
    """Delete a category"""
    category = get_object_or_404(Category, id=category_id)
    
    if request.method == 'POST':
        name = category.name
        category.delete()
        messages.success(request, f'Category "{name}" deleted successfully!')
        return redirect('admin_categories')
    
    context = {
        'category': category,
        'page_title': 'Delete Category'
    }
    return render(request, 'archive/admin/delete_category_confirm.html', context)


@cache_page(86400)  # Cache for 24 hours
def telegram_avatar(request, username):
    """Stream Telegram profile picture via MadelineProto API"""
    username = username.strip().lstrip('@').lower()
    
    # Get Telegram bot token from settings
    bot_token = getattr(settings, 'TELEGRAM_BOT_TOKEN', None)
    if not bot_token:
        raise Http404("Telegram bot token not configured")
    
    try:
        # Get profile info from MadelineProto wrapper
        api_response = requests.get(
            "https://tg.tabs.gay/api/getPropicInfo",
            params={'id': f'@{username}'},
            timeout=3
        )
        api_response.raise_for_status()
        data = api_response.json()
        
        if not data.get('success') or not data.get('response'):
            raise Http404("Profile not found")
        
        response_data = data['response']
        file_id = response_data.get('botApiFileId')
        mime_type = response_data.get('mimeType', 'image/jpeg')
        
        if not file_id:
            raise Http404("No file ID in response")
        
        # Use official Telegram Bot API to get file path
        file_response = requests.get(
            f"https://api.telegram.org/bot{bot_token}/getFile",
            params={'file_id': file_id},
            timeout=5
        )
        file_response.raise_for_status()
        file_data = file_response.json()
        
        if not file_data.get('ok') or not file_data.get('result'):
            raise Http404("Could not get file info from Telegram")
        
        file_path = file_data['result'].get('file_path')
        if not file_path:
            raise Http404("No file path returned from Telegram")
        
        # Download the actual file from Telegram
        download_url = f"https://api.telegram.org/file/bot{bot_token}/{file_path}"
        download_response = requests.get(download_url, timeout=5, stream=True)
        download_response.raise_for_status()
        
        # Return as streaming response
        response = HttpResponse(
            download_response.content,
            content_type=mime_type
        )
        response['Content-Disposition'] = 'inline; filename="avatar.jpg"'
        return response
        
    except requests.RequestException as e:
        raise Http404(f"Error fetching avatar: {e}")
    except Exception:
        raise Http404("Error processing request")


def internal_telegram_messages_proxy(request):
    """
    Proxy endpoint to fetch Telegram chat messages
    Bypasses CORS issues by fetching server-side
    """
    try:
        limit = int(request.GET.get('limit', 100))  # Increased default to 100 to get more messages
        page = int(request.GET.get('page', 1))
        max_id = request.GET.get('max_id', None)  # Use message ID for pagination
        
        # Limit reasonable values - allow up to 432 to fetch all available messages
        if limit < 1 or limit > 432:
            limit = 100
        if page < 1:
            page = 1
        
        # Fetch from Telegram API
        api_url = 'https://tg.tabs.gay/api/messages.getHistory'
        params = {'peer': 'furryconarchives', 'limit': limit}
        
        # Use max_id for pagination if provided (for getting older messages)
        if max_id:
            try:
                max_id_int = int(max_id)
                params['max_id'] = max_id_int
            except (ValueError, TypeError) as e:
                pass
        else:
            # For first page, use offset approach as fallback
            offset = (page - 1) * limit
            params['offset'] = offset
        
        
        response = requests.get(api_url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        # Unwrap the response - API returns {success: true, response: {...}}
        if data.get('success') and data.get('response'):
            result = data['response']
            return JsonResponse(result)
        
        return JsonResponse(data)
        
    except requests.Timeout:
        return JsonResponse({
            'error': 'API request timed out',
            'messages': []
        }, status=504)
    except requests.ConnectionError:
        return JsonResponse({
            'error': 'Failed to connect to Telegram API',
            'messages': []
        }, status=503)
    except requests.RequestException as e:
        return JsonResponse({
            'error': f'Request failed: {str(e)}',
            'messages': []
        }, status=502)
    except ValueError as e:
        return JsonResponse({
            'error': f'Invalid parameters: {str(e)}',
            'messages': []
        }, status=400)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return JsonResponse({
            'error': f'Server error: {str(e)}',
            'messages': []
        }, status=500)


def internal_telegram_avatar_proxy(request, username):
    """
    Proxy endpoint to fetch Telegram avatar/profile pictures
    Accepts username in the URL path: /telegram-avatar/<username>
    Bypasses CORS issues by fetching server-side with caching
    """
    import logging
    logger = logging.getLogger(__name__)
    try:
        if not username:
            logger.warning('Missing username parameter')
            return HttpResponse('Missing username parameter', status=400)

        username = username.strip().lstrip('@')
        logger.info(f'Fetching Telegram avatar for username: {username}')

        local_cache = load_site_binary('telegram_avatars', username.lower(), 60 * 60 * 24)
        if local_cache:
            logger.info(f'Local avatar cache hit for {username}')
            return HttpResponse(local_cache['content'], content_type=local_cache['content_type'])

        cache_key = f'telegram_avatar_{username}'
        cached_data = cache.get(cache_key)
        if cached_data:
            try:
                logger.info(f'Avatar found in cache for {username}')
                image_data = base64.b64decode(cached_data['data'])
                return HttpResponse(image_data, content_type=cached_data['content_type'])
            except Exception as e:
                logger.error(f'Error decoding cached avatar for {username}: {e}')
                cache.delete(cache_key)

        if not username.isdigit():
            try:
                web_url = f"https://t.me/{username}"
                logger.info(f'Requesting Telegram profile page: {web_url}')
                response = requests.get(web_url, timeout=10)

                if response.status_code == 200:
                    soup = BeautifulSoup(response.text, 'html.parser')
                    og_image = soup.find('meta', attrs={'property': 'og:image'})

                    if og_image and og_image.get('content'):
                        image_url = og_image['content']
                        logger.info(f'Found og:image for {username}: {image_url}')

                        img_response = requests.get(image_url, timeout=10)
                        if img_response.status_code == 200:
                            content_type = img_response.headers.get('content-type', 'image/jpeg')
                            try:
                                store_site_binary('telegram_avatars', username.lower(), img_response.content, content_type, source_url=image_url)
                                ensure_registry('telegram_avatars', username.lower())
                                encoded_data = base64.b64encode(img_response.content).decode('utf-8')
                                cache.set(cache_key, {
                                    'data': encoded_data,
                                    'content_type': content_type
                                }, 86400)
                                cached_usernames = cache.get('telegram_cached_usernames', set())
                                cached_usernames.add(username)
                                cache.set('telegram_cached_usernames', cached_usernames, None)
                            except Exception as e:
                                logger.error(f'Error caching avatar for {username}: {e}')
                            logger.info(f'Successfully fetched and returning avatar for {username}')
                            return HttpResponse(
                                img_response.content,
                                content_type=content_type
                            )
                        else:
                            logger.warning(f'Failed to download avatar image for {username}: status {img_response.status_code}')
                    else:
                        logger.warning(f'og:image not found for {username} at {web_url}')
                else:
                    logger.warning(f'Failed to fetch Telegram profile page for {username}: status {response.status_code}')
            except Exception as e:
                logger.error(f'Exception fetching avatar for {username}: {e}')

        logger.info(f'No avatar found for {username}, returning 404')
        return HttpResponse('Avatar not found', status=404)

    except Exception as e:
        import traceback
        logger.error(f'Server error for {username}: {e}')
        traceback.print_exc()
        return HttpResponse(f'Server error: {str(e)}', status=500)
    
@require_GET
def internal_telegram_media_proxy(request):
    """Proxy to forward media preview requests to tg.tabs.gay/api/getMediaPreview
    Returns binary content (images/thumbnails) with upstream content-type.
    Usage: /internal/api/telegram-media-preview?photo_id=... or ?peer=...&id=...
    """
    import logging
    import requests
    logger = logging.getLogger(__name__)

    base = 'https://tg.tabs.gay/api/getMediaPreview'
    params = {k: request.GET.get(k) for k in request.GET}

    try:
        upstream = requests.get(base, params=params, timeout=10, stream=True)
    except requests.RequestException as e:
        logger.exception('internal_telegram_media_proxy: upstream failed')
        return JsonResponse({'error': 'Upstream media request failed', 'details': str(e)}, status=502)

    content = upstream.content
    content_type = upstream.headers.get('content-type', 'application/octet-stream')
    return HttpResponse(content, content_type=content_type, status=upstream.status_code)

def conbook_spreadsheet(request):
    # Query all PDFDocuments, group by convention_name, aggregate copies, donators, and status
    from django.db.models import Count, F, Value, CharField
    docs = (
        PDFDocument.objects.values('convention_name', 'donated_by', 'is_scanned', 'ocr_processed', 'is_published')
        .annotate(
            copies=Count('id'),
            donators=F('donated_by'),
            status=F('is_scanned'),
        )
        .order_by('convention_name')
    )

    # Build a list of dicts for the template
    conbook_list = []
    for doc in PDFDocument.objects.all().order_by('convention_name', 'title'):
        # Status logic
        if not doc.is_published:
            status = "Not published"
        elif doc.ocr_processed:
            status = "Digital copy available"
        elif doc.is_scanned:
            status = "Crappy scan available"
        else:
            status = "Received, Un-scanned"
        conbook_list.append({
            'con': doc.convention_name or doc.category.name if doc.category else "(Unknown)",
            'copies': 1,  # Each row is a single doc, but could be grouped if needed
            'donators': doc.donated_by or "",
            'status': status,
        })

    # Optionally, group by 'con' and aggregate
    grouped = defaultdict(lambda: {'copies': 0, 'donators': set(), 'status': set()})
    for item in conbook_list:
        g = grouped[item['con']]
        g['copies'] += item['copies']
        if item['donators']:
            g['donators'].add(item['donators'])
        if item['status']:
            g['status'].add(item['status'])
    # Prepare for template
    spreadsheet = []
    for con, vals in grouped.items():
        spreadsheet.append({
            'con': con,
            'copies': vals['copies'],
            'donators': ', '.join(sorted(vals['donators'])),
            'status': '; '.join(sorted(vals['status'])),
        })
    spreadsheet.sort(key=lambda x: x['con'])


    return render(request, 'archive/conbook_spreadsheet.html', {'conbook_list': spreadsheet})


@staff_member_required
def admin_physical_inventory(request):
    """Custom admin panel for managing physical inventory items, with add form and search/filter."""
    from django.db.models import Q
    # Handle add form POST
    if request.method == 'POST':
        con = request.POST.get('con', '').strip()
        year = request.POST.get('year', '').strip()
        item_type = request.POST.get('item_type', '').strip()
        quantity = request.POST.get('quantity', '').strip()
        donators = request.POST.get('donators', '').strip()
        notes = request.POST.get('notes', '').strip()
        if con and quantity.isdigit():
            item = PhysicalInventoryItem.objects.create(
                con=con,
                year=int(year) if year.isdigit() else None,
                item_type=item_type,
                quantity=int(quantity),
                donators=donators,
                notes=notes,
            )
            from django.contrib import messages
            messages.success(request, f'Added inventory for {con} (x{quantity})')
            return redirect('admin_physical_inventory')
        else:
            from django.contrib import messages
            messages.error(request, 'Convention and quantity are required.')
    # Handle search/filter GET
    items = PhysicalInventoryItem.objects.all()
    search = request.GET.get('search', '').strip()
    item_type_filter = request.GET.get('item_type', '').strip()
    if search:
        items = items.filter(
            Q(con__icontains=search) |
            Q(donators__icontains=search) |
            Q(notes__icontains=search)
        )
    if item_type_filter:
        items = items.filter(item_type=item_type_filter)
    items = items.order_by('-added_at')
    context = {
        'items': items,
        'page_title': 'Manage The Vault',
    }
    return render(request, 'archive/admin/physical_inventory.html', context)


@staff_member_required
def admin_vault_contributors(request):
    """Manage custom avatar overrides for vault contributors."""
    from .models_physical import VaultContributorProfile
    from .utils import collect_vault_contributors, vault_contributor_key

    if request.method == 'POST':
        account_type = request.POST.get('account_type', '').strip()
        account_id = request.POST.get('account_id', '').strip()
        remove_override = request.POST.get('remove_override') == '1'

        if account_type in ('telegram', 'discord') and account_id:
            if account_type == 'telegram':
                account_id = account_id.lstrip('@')

            profile, _created = VaultContributorProfile.objects.get_or_create(
                account_type=account_type,
                account_id=account_id,
            )

            if remove_override:
                if profile.avatar:
                    profile.avatar.delete(save=False)
                profile.avatar = None
                profile.save(update_fields=['avatar', 'updated_at'])
                messages.success(request, f'Removed avatar override for {account_id}.')
            elif request.FILES.get('avatar'):
                if profile.avatar:
                    profile.avatar.delete(save=False)
                profile.avatar = request.FILES['avatar']
                profile.save(update_fields=['avatar', 'updated_at'])
                messages.success(request, f'Updated avatar override for {account_id}.')
            else:
                messages.error(request, 'Choose an image file to upload.')

        return redirect('admin_vault_contributors')

    profiles = {
        vault_contributor_key(p.account_type, p.account_id): p
        for p in VaultContributorProfile.objects.exclude(avatar='').exclude(avatar__isnull=True)
    }
    contributors = []
    for contributor in collect_vault_contributors():
        key = vault_contributor_key(contributor['type'], contributor['username'])
        profile = profiles.get(key)
        contributor['has_override'] = profile is not None and bool(profile.avatar)
        contributor['override_avatar_url'] = profile.avatar.url if profile and profile.avatar else None
        contributors.append(contributor)

    context = {
        'page_title': 'Vault Contributor Avatars',
        'contributors': contributors,
    }
    return render(request, 'archive/admin/vault_contributors.html', context)


@require_GET
def telegram_user_info(request, username):
    """API endpoint to fetch Telegram user display name by username."""
    from django.core.cache import cache
    import requests
    
    username = username.strip().lstrip('@').lower()
    cache_key = f'telegram_user_{username}'
    cached_data = cache.get(cache_key)
    if cached_data:
        return JsonResponse(cached_data)
    
    try:
        # Try with @ prefix first
        resp = requests.get(
            f'https://tg.tabs.gay/api/getInfo?id=@{username}',
            timeout=5
        )
        if resp.status_code != 200:
            # Try without @ prefix
            resp = requests.get(
                f'https://tg.tabs.gay/api/getInfo?id={username}',
                timeout=5
            )
        
        if resp.status_code == 200:
            data = resp.json()
            if data.get('success'):
                # The user data is nested under response.User
                user = data.get('response', {}).get('User', {})
                if user:
                    # Combine first_name and last_name for display
                    display_name = user.get('first_name', '') or ''
                    if user.get('last_name'):
                        display_name = f"{display_name} {user['last_name']}".strip()
                    
                    result = {
                        'username': username,
                        'display_name': display_name or username,
                    }
                    cache.set(cache_key, result, 3600)  # Cache for 1 hour
                    return JsonResponse(result)
        
        return JsonResponse({'error': 'User not found'}, status=404)
    except requests.RequestException as e:
        return JsonResponse({'error': f'Failed to fetch user: {str(e)}'}, status=500)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@require_GET
def discord_user_info(request, discord_id):
    """API endpoint to fetch Discord user info (username, display name, avatar) by Discord user ID."""
    from .utils import discord_avatar_proxy_path, fetch_discord_user_data

    user_data = fetch_discord_user_data(discord_id)
    if not user_data:
        return JsonResponse({'error': 'User not found'}, status=404)

    return JsonResponse({
        'id': discord_id,
        'username': user_data.get('username', ''),
        'discriminator': user_data.get('discriminator', ''),
        'display_name': user_data.get('global_name') or user_data.get('username', ''),
        'avatar_url': discord_avatar_proxy_path(discord_id),
    })

@require_GET
def discord_search_user(request, username):
    """Search for Discord user by username and return their info."""
    from .utils import discord_avatar_proxy_path, fetch_discord_user_data

    username = username.strip()
    lookup_id = username.lower()
    user_data = fetch_discord_user_data(lookup_id)
    if user_data:
        return JsonResponse({
            'id': lookup_id,
            'username': user_data.get('username', ''),
            'display_name': user_data.get('global_name') or user_data.get('username', ''),
            'avatar': discord_avatar_proxy_path(lookup_id),
        })

    return JsonResponse({
        'id': username,
        'username': username,
        'display_name': username,
        'avatar': discord_avatar_proxy_path(username) if username.isdigit() else '',
    })
from .models_physical import PhysicalInventoryItem

def physical_inventory_grid(request):
    """The Vault shell; items load via /v1/vault/."""
    return render(request, 'archive/physical_inventory_grid.html', {'conbook_list': []})


@require_GET
def discord_avatar_proxy(request, discord_id):
    """Proxy endpoint to fetch Discord avatar image by user ID, with caching."""
    import requests
    from .utils import discord_avatar_cdn_url, fetch_discord_user_data

    def _proxy_avatar(user_data):
        avatar_url = discord_avatar_cdn_url(discord_id, user_data)
        avatar_cache_key = f"{discord_id}:{user_data.get('avatar') if user_data else 'default'}"
        local_avatar = load_site_binary('discord_avatars', avatar_cache_key, 60 * 60 * 24)
        if local_avatar:
            return HttpResponse(local_avatar['content'], content_type=local_avatar['content_type'])

        img_resp = requests.get(avatar_url, timeout=10)
        if img_resp.status_code == 200:
            content_type = img_resp.headers.get('content-type', 'image/png')
            store_site_binary(
                'discord_avatars',
                avatar_cache_key,
                img_resp.content,
                content_type,
                source_url=avatar_url,
            )
            ensure_registry('discord_avatars', avatar_cache_key)
            return HttpResponse(img_resp.content, content_type=content_type)
        return img_resp

    user_data = fetch_discord_user_data(discord_id)
    if not user_data:
        return HttpResponse('User not found', status=404)

    img_resp = _proxy_avatar(user_data)
    if isinstance(img_resp, HttpResponse):
        return img_resp

    if img_resp.status_code == 404:
        user_data = fetch_discord_user_data(discord_id, force_refresh=True)
        if user_data:
            refreshed = _proxy_avatar(user_data)
            if isinstance(refreshed, HttpResponse):
                return refreshed

    return HttpResponse('Avatar not found', status=404)
    
@staff_member_required
def admin_edit_physical_inventory(request, item_id):
    """Edit a physical inventory item by ID."""
    item = get_object_or_404(PhysicalInventoryItem, id=item_id)
    if request.method == 'POST':
        item.con = request.POST.get('con', item.con)
        year = request.POST.get('year', '').strip()
        item.item_type = request.POST.get('item_type', item.item_type)
        item.quantity = request.POST.get('quantity', item.quantity)
        item.donators = request.POST.get('donators', item.donators)
        item.notes = request.POST.get('notes', item.notes)
        item.year = int(year) if year.isdigit() else None
        item.save()
        messages.success(request, f'Updated inventory for {item.con}')
        return redirect('admin_physical_inventory')
    context = {
        'item': item,
        'page_title': f'Edit The Vault: {item.con}'
    }
    return render(request, 'archive/admin/edit_physical_inventory.html', context)

@require_GET
def fetch_sched_print(slug):
    try:
        url = f'https://{slug}.sched.com/print/all'
        headers = _sched_request_headers()
        r = requests.get(url, headers=headers, timeout=15)
        r.raise_for_status()
    except Exception as exc:
        logger.exception('Failed to fetch sched print page for %s: %s', slug, exc)
        return JsonResponse({'error': 'fetch_failed', 'detail': str(exc)}, status=502)

    soup = BeautifulSoup(r.text, 'html.parser')
    events = []
    anchors = soup.select('a[href^="event/"]')
    for a in anchors:
        href = a.get('href')
        eid = a.get('id') or (href.split('/')[-1] if href else None)
        title = a.get_text(' ', strip=True)

        tr = a.find_parent('tr')
        td = a.find_parent('td')

        # date - previous bar row contains a span with id like 2026-01-15
        date_id = None
        if tr:
            bar = tr.find_previous('tr', class_='bar')
            if bar:
                span = bar.find('span')
                if span:
                    date_id = span.get('id') or span.get_text(strip=True)

        time_text = ''
        if tr:
            time_td = tr.find('td', class_='time')
            if time_td:
                time_text = time_td.get_text(' ', strip=True)
        if not time_text and td:
            prev = td.find_previous_sibling('td')
            if prev and 'time' in (prev.get('class') or []):
                time_text = prev.get_text(' ', strip=True)

        start = end = None
        if time_text:
            parts = __import__('re').split(r'–|—|-| to |\u2013|\u2014', time_text, maxsplit=1)
            if len(parts) == 2:
                start = parts[0].strip()
                end = parts[1].strip()
            else:
                start = time_text.strip()

        location = ''
        if td:
            venue = td.find('div', class_='venue')
            if venue:
                location = venue.get_text(' ', strip=True)

        # Only extract explicit Sched print descriptions (avoid heuristics)
        description = ''
        parent = a.find_parent()
        if parent:
            node = parent.select_one('.sched-description')
            if node:
                description = (node.get_text(' ', strip=True) or '').strip()

        events.append({
            'id': eid,
            'name': title,
            'date': date_id,
            'start_raw': start,
            'end_raw': end,
            'location': location,
            'description': description,
        })

    return JsonResponse({'slug': slug, 'type': 'sched', 'count': len(events), 'events': events})


def _resolve_sched_slug(slug, request=None):

    override = request.GET.get('sched') if request else None
    if override:
        return {
            'sched_slug': override.strip(),
            'display_name': None,
        }

    # Dynamic mapping: category-year -> category_slug+year (no abbrev)
    import re
    match = re.match(r'([a-z0-9\-]+)-(\d{4})$', slug)
    if match:
        category_slug, year = match.groups()
        sched_slug = f"{category_slug}-{year}"
        display_name = f"{category_slug.replace('-', ' ').title()} {year}"
        return {'sched_slug': sched_slug, 'display_name': display_name}

    return {'sched_slug': slug, 'display_name': None}


def _lookup_schedule_for_route_slug(route_slug, year=None, convention_name=None):
    """Find a Schedule row from a public route slug."""
    from archive.pretalx_client import schedule_route_slug

    route_slug = str(route_slug or '').strip()
    qs = Schedule.objects.filter(deleted=False).select_related('category')

    # Canonical public routes use {category.slug}-{year}.
    match = re.match(r'^([a-z0-9\-]+)-(\d{4})$', route_slug, re.I)
    if match:
        cat_slug, route_year = match.groups()
        try:
            route_year_int = int(route_year)
            obj = qs.filter(category__slug__iexact=cat_slug, year=route_year_int).first()
            if obj:
                return obj
            if year is None:
                year = route_year_int
        except (TypeError, ValueError):
            pass

    if year is not None:
        obj = qs.filter(slug__iexact=route_slug, year=year).first()
        if obj:
            return obj

    for sched in qs.filter(type='pretalx'):
        if schedule_route_slug(sched) == route_slug:
            if year is None or sched.year == year:
                return sched
    from archive.furconnect_schedule import furconnect_route_slug
    for sched in qs.filter(type='furconnect'):
        if furconnect_route_slug(sched.slug) == route_slug:
            if year is None or sched.year == year:
                return sched

    obj = qs.filter(slug__iexact=route_slug).order_by('-year').first()
    if obj and year is not None and obj.year != year:
        obj = None
    if obj:
        return obj

    if convention_name and year is not None:
        obj = qs.filter(convention_name__iexact=convention_name, year=year).first()
        if obj:
            return obj
        obj = qs.filter(convention_name__icontains=convention_name, year=year).order_by('-id').first()
        if obj:
            return obj
    return None


def _slug_match(a, b, threshold=0.6):
    """Return True if slugs `a` and `b` are a reasonable match.
    Uses exact match, prefix checks, then a fuzzy ratio threshold.
    """
    if not a or not b:
        return False
    na = str(a).lower().replace('-', '').strip()
    nb = str(b).lower().replace('-', '').strip()
    if not na or not nb:
        return False
    if na == nb:
        return True
    if na.startswith(nb) or nb.startswith(na):
        return True
    try:
        return SequenceMatcher(None, na, nb).ratio() >= float(threshold)
    except Exception:
        return False


def _build_schedule_days(events, sched_slug, source_type=None):
    days_map = OrderedDict()
    timezones = set()

    def _format_time_range(start_dt, end_dt, start_raw, end_raw):
        def _fmt(dt):
            if not dt:
                return ''
            return dt.strftime('%I:%M %p').lstrip('0')

        if start_raw or end_raw:
            if start_raw and end_raw:
                return f"{start_raw} - {end_raw}"
            return start_raw or end_raw

        start_txt = _fmt(start_dt)
        end_txt = _fmt(end_dt)
        if start_txt and end_txt:
            return f"{start_txt} - {end_txt}"
        return start_txt or end_txt

    for ev in events:
        start_dt = None
        end_dt = None
        if ev.get('start'):
            try:
                start_dt = dateparser.parse(ev.get('start'))
            except Exception:
                start_dt = None
        if ev.get('end'):
            try:
                end_dt = dateparser.parse(ev.get('end'))
            except Exception:
                end_dt = None

        day_key = None
        day_label = None
        day_short = None
        if start_dt:
            day_key = start_dt.date().isoformat()
            day_label = start_dt.strftime('%A, %B %d, %Y')
            day_short = start_dt.strftime('%a %b %d')
        elif ev.get('date'):
            try:
                parsed_date = dateparser.parse(ev.get('date'))
                if parsed_date:
                    day_key = parsed_date.date().isoformat()
                    day_label = parsed_date.strftime('%A, %B %d, %Y')
                    day_short = parsed_date.strftime('%a %b %d')
            except Exception:
                day_key = ev.get('date')
                day_label = ev.get('date')
                day_short = ev.get('date')
        if not day_key:
            day_key = 'unknown'
            day_label = 'Unknown date'
            day_short = 'Unknown'

        if day_key not in days_map:
            days_map[day_key] = {
                'label': day_label,
                'short_label': day_short,
                'events': [],
                'sort_key': day_key,
            }

        timezones.add(ev.get('timezone') or '')

        # Build per-event external URL depending on source_type
        event_id = ev.get('id')
        sched_url = ev.get('url') or ev.get('sched_url')
        if not sched_url:
            try:
                if source_type == 'sessionize' and event_id and sched_slug:
                    from urllib.parse import urlparse
                    s = str(sched_slug).strip()
                    if s.startswith('http://') or s.startswith('https://'):
                        host = urlparse(s).netloc
                    else:
                        host = s if '.' in s else f"{s}.sessionize.com"
                    sched_url = f"https://{host}/session/{event_id}"
                elif source_type == 'pretalx' and event_id and sched_slug:
                    from archive.pretalx_client import pretalx_talk_url
                    sched_url = pretalx_talk_url(sched_slug, str(event_id))
                elif sched_slug and event_id:
                    sched_url = f"https://{sched_slug}.sched.com/event/{event_id}"
            except Exception:
                sched_url = None

        days_map[day_key]['events'].append({
            'id': ev.get('id'),
            'name': ev.get('name') or 'Untitled event',
            'location': ev.get('location') or 'TBA',
            'description': (ev.get('description') or '').strip(),
            'time_range': _format_time_range(start_dt, end_dt, ev.get('start_raw'), ev.get('end_raw')),
            'start_dt': start_dt,
            'end_dt': end_dt,
            'date_raw': ev.get('date'),
            'sched_url': sched_url,
            'organizer': ev.get('organizer') or (ev.get('speakers')[0] if ev.get('speakers') and len(ev.get('speakers')) else None),
        })

    days = list(days_map.values())
    for day in days:
        def _event_sort_key(item):
            # Prefer explicit parsed datetimes
            if item.get('start_dt'):
                return (0, item['start_dt'])

            # Try to synthesize a datetime from raw date + raw start text
            parsed_guess = item.get('_parsed_start_guess')
            if parsed_guess is not None:
                return (0, parsed_guess)

            # Heuristics: combine date_raw and time_range/start text
            date_raw = item.get('date_raw') or ''
            # Use time_range first if it contains a leading time
            time_text = ''
            if item.get('time_range'):
                # take content before a newline and before a '-' if present
                time_text = str(item['time_range']).split('\n', 1)[0]
            try:
                # Try date + time_text
                if date_raw and time_text:
                    try:
                        dt = dateparser.parse(f"{date_raw} {time_text}")
                        if dt:
                            item['_parsed_start_guess'] = dt
                            return (0, dt)
                    except Exception:
                        pass

                # Try parsing any apparent time-only token from time_range
                import re
                tm = None
                if time_text:
                    m = re.search(r'([0-9]{1,2}:?[0-9]{0,2}\s*(?:am|pm|AM|PM)?)', time_text)
                    if m:
                        tm = m.group(1)
                if date_raw and tm:
                    try:
                        dt = dateparser.parse(f"{date_raw} {tm}")
                        if dt:
                            item['_parsed_start_guess'] = dt
                            return (0, dt)
                    except Exception:
                        pass
            except Exception:
                pass

            # Finally, untimed events sort after timed events by name
            return (1, (item.get('name') or '').lower())

        day['events'].sort(key=_event_sort_key)

    def _day_sort_key(day):
        raw = day.get('sort_key') or day.get('label') or ''
        try:
            parsed = dateparser.parse(str(raw)) if raw else None
        except Exception:
            parsed = None

        if parsed:
            return (0, parsed)
        return (1, str(raw))

    days.sort(key=_day_sort_key)

    timezone_label = None
    tz_clean = sorted([tz for tz in timezones if tz])
    if len(tz_clean) == 1:
        timezone_label = tz_clean[0]

    return days, timezone_label


@require_GET
def schedule_view(request, slug):
    # Use resolved sched slug (allows overrides and prefix matches)
    resolved = _resolve_sched_slug(slug, request=request)
    sched_slug = resolved.get('sched_slug')
    display_name = resolved.get('display_name')

    # Helper: remove duplicate/embedded year from display names
    def _normalize_display_name(name, year):
        try:
            if not name:
                return name
            if not year:
                return name.strip()
            y = str(year)
            n = name.strip()
            # Remove a trailing " <year>"
            if n.endswith(' ' + y):
                n = n[:-(len(y) + 1)].strip()
            # If still ends with the year digits attached (e.g. 'Vancoufur2019'), strip them
            if n.endswith(y):
                # remove trailing year digits
                n = n[: -len(y)].strip()
            # Insert spaces for common concatenations: camelCase and letter/digit boundaries
            import re
            # split camelCase: between lower->upper
            n = re.sub(r'(?<=[a-z])(?=[A-Z])', ' ', n)
            # split between letters and digits and vice-versa
            n = re.sub(r'(?<=[A-Za-z])(?=\d)', ' ', n)
            n = re.sub(r'(?<=\d)(?=[A-Za-z])', ' ', n)
            # normalize whitespace and title-case for display
            n = ' '.join(n.split()).title()
            return n
        except Exception:
            return name

    # Parse slug for API category and year using resolved sched slug
    import re
    parse_target = sched_slug or slug
    match = re.match(r'([a-z0-9\-]+)-(\d{4})$', parse_target)
    if match:
        convention_slug, year = match.groups()
        if not display_name:
            base = convention_slug.replace('-', ' ').title()
            base = _normalize_display_name(base, year)
            display_name = f"{base} {year}"
    else:
        convention_slug = parse_target
        year = None
        if not display_name:
            display_name = parse_target.replace('-', ' ').title()

    source_type = None

    # Try to load from admin panel Schedule model by convention_name and year
    try:
        # Use convention_name parsed from slug (title-cased, spaces) and year.
        # If `year` is present we expect the display_name to end with the year
        # and strip it; when no year is present keep the full display_name.
        if year:
            convention_name = display_name.rsplit(' ', 1)[0]
        else:
            convention_name = display_name
        schedule_obj = _lookup_schedule_for_route_slug(parse_target, year, convention_name)
        if not schedule_obj:
            raise Schedule.DoesNotExist()
        if schedule_obj.type == 'pretalx':
            from archive.pretalx_client import normalize_pretalx_schedule_record
            if normalize_pretalx_schedule_record(schedule_obj):
                schedule_obj.save(update_fields=['slug'])
        events = json.loads(schedule_obj.events_json) if schedule_obj.events_json else []
        try:
            logger.debug("schedule_view: DB Schedule found id=%s slug=%s convention_name=%s year=%s has_events=%s",
                         getattr(schedule_obj, 'id', None), getattr(schedule_obj, 'slug', None), getattr(schedule_obj, 'convention_name', None), getattr(schedule_obj, 'year', None), bool(getattr(schedule_obj, 'events_json', None)))
        except Exception:
            pass
        error_message = schedule_obj.error if schedule_obj.error else None
        api_slug = schedule_obj.slug
        if schedule_obj.type == 'pretalx':
            from archive.pretalx_client import pretalx_api_input
            api_slug = pretalx_api_input(schedule_obj) or api_slug
        source_type = schedule_obj.type or source_type
        # If DB record exists but has no events, try local schedules/ JSON files
        try:
            schedules_dir = os.path.join(settings.BASE_DIR, 'schedules')
            if os.path.isdir(schedules_dir):
                lookup_key = (convention_slug or '').lower().replace('-', '')
                candidates = []
                for fn in os.listdir(schedules_dir):
                    if not fn.lower().endswith('.json'):
                        continue
                    fp = os.path.join(schedules_dir, fn)
                    try:
                        with open(fp, 'r', encoding='utf-8') as fh:
                            payload = json.load(fh)
                    except Exception:
                        continue
                    slug_val = str(payload.get('slug') or '').lower().replace('-', '')
                    year_val = payload.get('year')
                    if year:
                        # exact year match preferred — require slug match when possible
                        # When a year is provided, don't accept every payload with the same year;
                        # prefer payloads that also match the requested slug to avoid collisions
                        try:
                            if str(year) == str(year_val):
                                if lookup_key:
                                    if _slug_match(lookup_key, slug_val):
                                        candidates.append((payload, year_val))
                                else:
                                    candidates.append((payload, year_val))
                        except Exception:
                            # on any error, be conservative and skip this payload
                            pass
                    else:
                        # when no year provided, collect potential slug matches
                        if lookup_key and _slug_match(lookup_key, slug_val):
                            candidates.append((payload, year_val))

                # Choose best candidate
                if candidates:
                    chosen = None
                    if year:
                        chosen = candidates[0]
                    else:
                        # Prefer highest numeric year when available
                        numeric_candidates = []
                        for p, y in candidates:
                            try:
                                numeric_candidates.append((int(y), p))
                            except Exception:
                                numeric_candidates.append((None, p))
                        numeric_candidates.sort(key=lambda t: (t[0] is None, -(t[0] or 0)))
                        chosen = (numeric_candidates[0][1], numeric_candidates[0][0]) if numeric_candidates else (candidates[0][0], candidates[0][1])

                    if chosen:
                        payload = chosen[0]
                        try:
                            logger.debug("schedule_view: selected local payload (DB branch) slug=%s year=%s display_name=%s",
                                         payload.get('slug'), payload.get('year'), payload.get('display_name'))
                        except Exception:
                            pass
                        events = payload.get('events', [])
                        api_slug = payload.get('slug') or api_slug
                        source_type = payload.get('type') or source_type
                        # Use payload's display/convention name when available so
                        # the rendered title matches the events source.
                        try:
                            if payload.get('display_name'):
                                display_name = payload.get('display_name')
                            elif payload.get('convention_name'):
                                display_name = payload.get('convention_name')
                            else:
                                    # fallback: construct from slug and year
                                    slug_for_title = payload.get('slug') or api_slug or convention_slug
                                    base = slug_for_title.replace('-', ' ').title()
                                    base = _normalize_display_name(base, payload.get('year'))
                                    if payload.get('year'):
                                        display_name = f"{base} {payload.get('year')}"
                                    else:
                                        display_name = base
                        except Exception:
                            pass
                        error_message = None
        except Exception:
            pass
    except Schedule.DoesNotExist:
        # If no DB record, try local schedules/ JSON files before failing
        events = []
        error_message = 'Schedule not found.'
        api_slug = convention_slug
        try:
            schedules_dir = os.path.join(settings.BASE_DIR, 'schedules')
            if os.path.isdir(schedules_dir):
                    lookup_key = (convention_slug or '').lower().replace('-', '')
                    candidates = []
                    for fn in os.listdir(schedules_dir):
                        if not fn.lower().endswith('.json'):
                            continue
                        fp = os.path.join(schedules_dir, fn)
                        try:
                            with open(fp, 'r', encoding='utf-8') as fh:
                                payload = json.load(fh)
                        except Exception:
                            continue
                        slug_val = str(payload.get('slug') or '').lower().replace('-', '')
                        year_val = payload.get('year')
                        if year:
                            # exact year match preferred — require slug match when possible
                            try:
                                if str(year) == str(year_val):
                                    if lookup_key:
                                        if _slug_match(lookup_key, slug_val):
                                            candidates.append((payload, year_val))
                                    else:
                                        candidates.append((payload, year_val))
                            except Exception:
                                pass
                        else:
                            if lookup_key and _slug_match(lookup_key, slug_val):
                                candidates.append((payload, year_val))

                    if candidates:
                        # Pick best candidate: exact year if provided, else newest numeric year
                        chosen = None
                        if year:
                            chosen = candidates[0]
                        else:
                            numeric_candidates = []
                            for p, y in candidates:
                                try:
                                    numeric_candidates.append((int(y), p))
                                except Exception:
                                    numeric_candidates.append((None, p))
                            numeric_candidates.sort(key=lambda t: (t[0] is None, -(t[0] or 0)))
                            chosen = (numeric_candidates[0][1], numeric_candidates[0][0]) if numeric_candidates else (candidates[0][0], candidates[0][1])

                        if chosen:
                            payload = chosen[0]
                            try:
                                logger.debug("schedule_view: selected local payload (no-DB branch) slug=%s year=%s display_name=%s",
                                             payload.get('slug'), payload.get('year'), payload.get('display_name'))
                            except Exception:
                                pass
                            events = payload.get('events', [])
                            api_slug = payload.get('slug') or api_slug
                            source_type = payload.get('type') or source_type
                            try:
                                if payload.get('display_name'):
                                    display_name = payload.get('display_name')
                                elif payload.get('convention_name'):
                                    display_name = payload.get('convention_name')
                                else:
                                    slug_for_title = payload.get('slug') or api_slug or convention_slug
                                    base = slug_for_title.replace('-', ' ').title()
                                    base = _normalize_display_name(base, payload.get('year'))
                                    if payload.get('year'):
                                        display_name = f"{base} {payload.get('year')}"
                                    else:
                                        display_name = base
                            except Exception:
                                pass
                            error_message = None
        except Exception:
            pass
    except Exception as e:
        events = []
        error_message = f'Error loading schedule: {str(e)}'
        api_slug = convention_slug

    if error_message:
        context = {
            'slug': slug,
            'api_slug': api_slug,
            'year': year,
            'display_name': display_name,
            'error_message': error_message,
        }
        return render(request, 'archive/schedule_detail.html', context, status=502)

    # Use the actual slug we used to fetch/choose events when building
    # the schedule and external links. This ensures the rendered page's
    # "View on ..." button matches the source of the events.
    final_api_slug = api_slug or sched_slug or convention_slug

    days, timezone_label = _build_schedule_days(events, final_api_slug, source_type)
    # Ensure display_name ends with year when a year is present
    try:
        ytxt = str(year) if year else ''
        # If display_name contains the year inside, prefer the portion before the first occurrence
        if year and ytxt and ytxt in (display_name or ''):
            base_part = (display_name or '').split(ytxt, 1)[0].strip()
            base_name = _normalize_display_name(base_part, None)
            display_name = f"{base_name} {ytxt}"
        else:
            base_name = _normalize_display_name(display_name, year)
            if year:
                display_name = f"{base_name} {year}"
            else:
                display_name = base_name
    except Exception:
        pass
    title = display_name
    # Determine external source URL for the top 'View on ...' button
    source_url = None
    try:
        if source_type == 'guidebook' and final_api_slug:
            source_url = f"https://builder.guidebook.com/g/#/guides/{final_api_slug}/details"
        elif source_type == 'sessionize' and final_api_slug:
            s = str(final_api_slug).strip()
            if s.startswith('http://') or s.startswith('https://'):
                source_url = s
            else:
                # If the slug looks like a hostname use it, otherwise assume Sessionize subdomain
                host = s if '.' in s else f"{s}.sessionize.com"
                source_url = f"https://{host}"
        elif source_type == 'pretalx' and final_api_slug:
            from archive.pretalx_client import pretalx_schedule_url
            source_url = pretalx_schedule_url(final_api_slug)
        elif source_type == 'furconnect' and final_api_slug:
            from archive.furconnect_schedule import furconnect_schedule_url
            source_url = furconnect_schedule_url(final_api_slug)
        elif final_api_slug:
            source_url = f"https://{final_api_slug}.sched.com"
    except Exception:
        source_url = None

    context = {
        'slug': slug,
        'api_slug': final_api_slug,
        'year': year,
        'title': title,
        'days': days,
        'events_count': len(events),
        'timezone_label': timezone_label,
        'source_url': source_url,
        'source_type': source_type,
    }
    try:
        if getattr(settings, 'DEBUG', False):
            context['debug_schedule_source'] = globals().get('_debug_schedule_source') or locals().get('_debug_schedule_source') if ('_debug_schedule_source' in globals() or '_debug_schedule_source' in locals()) else None
            context['debug_api_slug'] = api_slug
            context['debug_display_name'] = display_name
    except Exception:
        pass
    # If a grid view is requested, build `days_data` compatible with `schedule_grid.html` JS
    view_mode = (request.GET.get('view') or '').lower()
    if view_mode == 'grid':
        days_data = []
        for day in days:
            day_events = day.get('events', []) or []
            # Determine rooms used this day (use same heuristics as XLSX exporter)
            rooms = []
            room_set = set()
            for ev in day_events:
                rm = ev.get('location') or ev.get('room') or ev.get('venue') or 'Unspecified'
                if isinstance(rm, str):
                    rm = rm.strip()
                if not rm:
                    rm = 'Unspecified'
                if rm not in room_set:
                    room_set.add(rm)
                    rooms.append(rm)
            rooms.sort(key=lambda x: x.lower())

            # Compute min/max minutes for timeline
            mins = []
            ev_items = []
            for ev in day_events:
                smin = None
                emin = None
                start_dt = ev.get('start_dt')
                end_dt = ev.get('end_dt')
                if start_dt:
                    try:
                        smin = start_dt.hour * 60 + start_dt.minute
                    except Exception:
                        smin = None
                if end_dt:
                    try:
                        emin = end_dt.hour * 60 + end_dt.minute
                        # If the event crosses midnight, push the end past 24h
                        # so the grid can still render it as extending to midnight.
                        if start_dt and getattr(end_dt, 'date', None) and end_dt.date() > start_dt.date():
                            emin += 1440 * (end_dt.date() - start_dt.date()).days
                    except Exception:
                        emin = None
                if smin is not None:
                    mins.append(smin)
                if emin is not None:
                    mins.append(emin)
                event_url = ''
                try:
                    if source_type == 'guidebook' and ev.get('id') and final_api_slug:
                        from urllib.parse import quote
                        event_url = (
                            f"https://builder.guidebook.com/g/#/guides/{quote(str(final_api_slug))}"
                            f"/schedule/sessions/{quote(str(ev.get('id')))}"
                        )
                    else:
                        event_url = ev.get('sched_url') or ''
                except Exception:
                    event_url = ev.get('sched_url') or ''

                ev_items.append({
                    'name': ev.get('name'),
                    'location': ev.get('location') or ev.get('room') or ev.get('venue') or 'Unspecified',
                    'start_min': smin,
                    'end_min': emin,
                    'start': ev.get('start').isoformat() if ev.get('start') else None,
                    'end': ev.get('end').isoformat() if ev.get('end') else None,
                    'start_dt': ev.get('start_dt').isoformat() if ev.get('start_dt') else None,
                    'end_dt': ev.get('end_dt').isoformat() if ev.get('end_dt') else None,
                    'time_range': ev.get('time_range'),
                    'description': (ev.get('description') or ev.get('desc') or ''),
                    'organizer': ev.get('organizer') or '',
                    'url': event_url,
                })

            # Always show full convention day from 00:00 to 24:00 (midnight-to-midnight)
            start_floor = 0
            end_ceil = 24 * 60

            days_data.append({
                'label': day.get('label'),
                'rooms': rooms,
                'start_min': start_floor,
                'end_min': end_ceil,
                'events': ev_items,
            })

        return render(request, 'archive/schedule_grid.html', {
            'title': title,
            'days_data': json.dumps(days_data),
            'slug': slug,
            'events_count': len(events),
            'timezone_label': timezone_label,
            'source_url': source_url,
            'source_type': source_type,
            'api_slug': final_api_slug,
        })

    return render(request, 'archive/schedule_detail.html', context)


@require_GET
def schedule_print(request, slug):
    resolved = _resolve_sched_slug(slug, request=request)
    sched_slug = resolved['sched_slug']
    display_name = resolved['display_name']
    # Prefer DB/local JSON for schedule data rather than querying Sched.com
    # Use the resolved sched slug (from `_resolve_sched_slug`) when
    # parsing convention/year so print uses the same resolution as
    # the main `schedule_view`.
    import re
    parse_target = sched_slug or slug
    match = re.match(r'([a-z0-9\-]+)-(\d{4})$', parse_target)
    if match:
        convention_slug, year = match.groups()
        if not display_name:
            display_name = f"{convention_slug.replace('-', ' ').title()} {year}"
    else:
        convention_slug = parse_target
        year = None

    events = []
    try:
        convention_name = (display_name.rsplit(' ', 1)[0]) if display_name else None
        schedule_obj = Schedule.objects.filter(convention_name__iexact=convention_name, year=year, deleted=False).first()

        # Prefer DB cached payload when it exists and seems reasonable
        if schedule_obj and schedule_obj.events_json:
            try:
                events = json.loads(schedule_obj.events_json) or []
            except Exception:
                events = []

        # If no events from DB or DB is empty, check local schedules JSON files
        if not events:
            try:
                schedules_dir = os.path.join(settings.BASE_DIR, 'schedules')
                if os.path.isdir(schedules_dir):
                    lookup_key = (convention_slug or '').lower().replace('-', '')
                    candidates = []
                    for fn in os.listdir(schedules_dir):
                        if not fn.lower().endswith('.json'):
                            continue
                        fp = os.path.join(schedules_dir, fn)
                        try:
                            with open(fp, 'r', encoding='utf-8') as fh:
                                payload = json.load(fh)
                        except Exception:
                            continue
                        slug_val = str(payload.get('slug') or '').lower().replace('-', '')
                        year_val = payload.get('year')
                        if year:
                            if str(year) == str(year_val):
                                candidates.append((payload, year_val))
                        else:
                            if lookup_key and _slug_match(lookup_key, slug_val):
                                candidates.append((payload, year_val))

                    if candidates:
                        # Prefer exact-year candidate, else pick newest numeric year
                        chosen = None
                        if year:
                            chosen = candidates[0]
                        else:
                            numeric_candidates = []
                            for p, y in candidates:
                                try:
                                    numeric_candidates.append((int(y), p))
                                except Exception:
                                    numeric_candidates.append((None, p))
                            numeric_candidates.sort(key=lambda t: (t[0] is None, -(t[0] or 0)))
                            chosen = (numeric_candidates[0][1], numeric_candidates[0][0]) if numeric_candidates else (candidates[0][0], candidates[0][1])

                        if chosen:
                            payload = chosen[0]
                            events = payload.get('events', []) or []
            except Exception:
                events = []

        # As a final fallback, optionally try a live fetch for 'sched' types when we have very little data
        if not events and schedule_obj and schedule_obj.type == 'sched' and schedule_obj.slug:
            try:
                live_events, live_error = fetch_sched_events(schedule_obj)
                if live_events:
                    try:
                        logger.debug("schedule_view: live fetch returned %s events for schedule slug=%s", len(live_events), getattr(schedule_obj, 'slug', None))
                    except Exception:
                        pass
                    events = live_events
            except Exception:
                pass
    except Exception:
        events = []
    days, timezone_label = _build_schedule_days(events, sched_slug)

    day_filter = (request.GET.get('day') or '').strip()
    room_filter = (request.GET.get('room') or '').strip().lower()
    query_filter = (request.GET.get('q') or '').strip().lower()

    def _matches_filters(event, day_label):
        if day_filter and day_label != day_filter:
            return False
        if room_filter and (event.get('location') or '').lower() != room_filter:
            return False
        if query_filter:
            haystack = ' '.join([
                event.get('name') or '',
                event.get('location') or '',
                event.get('description') or '',
                event.get('time_range') or '',
                day_label or ''
            ]).lower()
            if query_filter not in haystack:
                return False
        return True

    if day_filter or room_filter or query_filter:
        filtered_days = []
        for day in days:
            day_label = day.get('label') or ''
            filtered_events = [ev for ev in day.get('events', []) if _matches_filters(ev, day_label)]
            if not filtered_events:
                continue
            new_day = day.copy()
            new_day['events'] = filtered_events
            filtered_days.append(new_day)
        days = filtered_days

    title = display_name or slug.replace('-', ' ').title()
    if day_filter:
        title = f"{title} - {day_filter}"
    filename = f"{slug}-schedule.pdf"
    pdf_response = HttpResponse(content_type='application/pdf')
    pdf_response['Content-Disposition'] = f'inline; filename="{filename}"'

    doc = SimpleDocTemplate(
        pdf_response,
        pagesize=letter,
        leftMargin=0.5 * inch,
        rightMargin=0.5 * inch,
        topMargin=0.6 * inch,
        bottomMargin=0.6 * inch,
    )
    styles = getSampleStyleSheet()
    
    # Title style with larger font
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Title'],
        fontSize=28,
        textColor=colors.HexColor('#1a1a1a'),
        spaceAfter=6,
        alignment=TA_LEFT
    )
    
    # Day header with border/background effect
    day_style = ParagraphStyle(
        'DayHeader',
        parent=styles['Heading2'],
        fontSize=14,
        textColor=colors.HexColor('#2c3e50'),
        alignment=TA_LEFT,
        spaceBefore=14,
        spaceAfter=10,
        borderPadding=8
    )
    
    # Time label style (bold, prominent)
    time_style = ParagraphStyle(
        'TimeLabel',
        parent=styles['BodyText'],
        fontSize=11,
        textColor=colors.HexColor('#e74c3c'),
        leading=13,
        spaceAfter=2,
        fontName='Helvetica-Bold',
    )
    
    # Event name style (bold, prominent)
    event_name_style = ParagraphStyle(
        'EventName',
        parent=styles['BodyText'],
        fontSize=12,
        textColor=colors.HexColor('#1a1a1a'),
        leading=14,
        spaceAfter=3,
        fontName='Helvetica-Bold',
    )
    
    # Location/meta info style
    meta_style = ParagraphStyle(
        'MetaInfo',
        parent=styles['BodyText'],
        fontSize=9,
        textColor=colors.HexColor('#555555'),
        leading=11,
        spaceAfter=2,
        fontName=PDF_UNICODE_FONT,
    )
    
    # Description style
    desc_style = ParagraphStyle(
        'Description',
        parent=styles['BodyText'],
        fontSize=9,
        textColor=colors.HexColor('#666666'),
        leading=11,
        spaceAfter=6,
        fontName=PDF_UNICODE_FONT,
    )
    
    # Organizer style
    organizer_style = ParagraphStyle(
        'Organizer',
        parent=styles['BodyText'],
        fontSize=9,
        textColor=colors.HexColor('#7f8c8d'),
        leading=10,
        spaceAfter=4,
        fontName=PDF_UNICODE_FONT,
    )

    story = []
    
    # Title with underline effect
    story.append(Paragraph(escape(f"{title} Schedule"), title_style))
    story.append(Spacer(1, 0.1 * inch))
    
    # Add timezone info if available
    if timezone_label:
        tz_para = Paragraph(f"<font size=8 color='#999999'>All times shown in {escape(timezone_label)}</font>", meta_style)
        story.append(tz_para)
        story.append(Spacer(1, 0.1 * inch))

    for day in days:
        # Day header with visual separator
        day_label = day.get('label') or 'Schedule'
        day_icon = _fa_icon(0xf073, fallback='[Day]')
        story.append(Paragraph(f"{day_icon} {escape(day_label)}", day_style))
        
        for event in day.get('events', []):
            time_label = event.get('time_range') or 'Time TBA'
            name = event.get('name') or 'Untitled event'
            location = event.get('location') or 'Location TBA'
            organizer = event.get('organizer')
            description = event.get('description', '').strip()
            
            event_flowables = [
                Paragraph(
                    f"{_fa_icon(0xf017, fallback='[Time]')} {escape(time_label)}",
                    time_style,
                ),
                Paragraph(escape(name), event_name_style),
                Paragraph(
                    f"{_fa_icon(0xf3c5, fallback='[Location]')} {escape(location)}",
                    meta_style,
                ),
            ]

            if organizer:
                event_flowables.append(
                    Paragraph(
                        f"{_fa_icon(0xf130, fallback='[Host]')} {escape(organizer)}",
                        organizer_style,
                    )
                )

            if description:
                event_flowables.append(Paragraph(escape(description), desc_style))

            story.append(KeepTogether(event_flowables))
            story.append(Spacer(1, 0.08 * inch))
        
        # Spacing between days
        story.append(Spacer(1, 0.12 * inch))

    doc.build(story)
    return pdf_response


@require_GET
@cache_page(60 * 15)
def api_sched(request, slug):
    cache_key = f'sched:print:{slug}:all'
    cached = cache.get(cache_key)
    if cached is not None:
        return JsonResponse(cached)

    try:
        from .models import Schedule
        sched = Schedule.objects.filter(slug__iexact=slug, deleted=False).order_by('-year').first()
    except Exception:
        sched = None

    if not sched:
        class _SchedObj:
            pass
        sched = _SchedObj()
        sched.slug = slug

    events, error = fetch_sched_events(sched)
    if error:
        if error == 'sched_slug_missing_or_removed':
            return _guidebook_missing_response(
                slug,
                message='sched_slug_missing_or_removed',
                status=200,
            )
        return JsonResponse({'error': 'fetch_failed', 'detail': error}, status=502)

    payload = {
        'slug': slug,
        'type': 'sched',
        'year': getattr(sched, 'year', None),
        'count': len(events),
        'events': events,
    }
    try:
        cache.set(cache_key, payload, 60 * 15)
    except Exception:
        pass
    return JsonResponse(payload)


def _is_internal_request(req):
    # Determine whether a request is coming from a trusted internal source.
    #
    # Internal endpoints are intended for use by the local application or
    # other processes running on the same host.  Staff users and localhost
    # addresses are always allowed.  We deliberately **do not** use API keys
    # or any shared secret for these endpoints; the existence of
    # ``INTERNAL_API_KEY``/``X_INTERNAL_SECRET`` has been deprecated and
    # is ignored.  This keeps internal APIs simple and avoids the need to
    # distribute credentials to internal callers.
    try:
        if getattr(req, 'user', None) and getattr(req.user, 'is_staff', False):
            return True
    except Exception:
        pass
    addr = req.META.get('REMOTE_ADDR') or req.META.get('REMOTE_HOST')
    if addr in ('127.0.0.1', '::1'):
        return True
    # No header-based secret check – internal APIs do not use API keys or
    # shared secrets.
    return False


def internal_only(view_func):
    """Decorator for views that should only be callable by internal code.

    Allows calls from localhost or staff users; there is no API key or
    shared-secret requirement.  Do **not** use this decorator for public
    endpoints.
    """
    def _wrapped(request, *args, **kwargs):
        if _is_internal_request(request):
            return view_func(request, *args, **kwargs)
        return JsonResponse({'error': 'internal_only'}, status=403)
    return _wrapped


@staff_member_required
def admin_api_keys(request):
    """Custom admin page to manage app keys (create, activate, deactivate, regenerate)."""
    from .models import AppKey, Category
    import secrets as _secrets

    message = None
    if request.method == 'POST':
        action = request.POST.get('action')
        tid = request.POST.get('id')
        if action == 'create':
            name = (request.POST.get('name') or '').strip()
            convention_id = request.POST.get('convention') or ''
            convention_obj = Category.objects.filter(id=convention_id).first() if convention_id else None
            scopes = [scope for scope in request.POST.getlist('scopes') if scope]
            rate_raw = (request.POST.get('rate_per_minute') or '').strip()
            rate_per_minute = int(rate_raw) if rate_raw.isdigit() and int(rate_raw) > 0 else None
            tok = AppKey.objects.create(
                name=name,
                convention=convention_obj,
                scopes=scopes,
                rate_per_minute=rate_per_minute,
                created_by=request.user,
                active=True,
            )
            message = f"Created app key: {tok.key}"
        elif action == 'update' and tid:
            try:
                tok = AppKey.objects.get(id=tid)
                tok.scopes = [scope for scope in request.POST.getlist('scopes') if scope]
                rate_raw = (request.POST.get('rate_per_minute') or '').strip()
                tok.rate_per_minute = int(rate_raw) if rate_raw.isdigit() and int(rate_raw) > 0 else None
                convention_id = request.POST.get('convention') or ''
                if convention_id:
                    tok.convention = Category.objects.filter(id=convention_id).first()
                tok.save()
                message = 'Updated app key scopes and rate limit.'
            except Exception:
                message = 'App key not found.'
        elif action in ('deactivate', 'activate', 'regenerate', 'delete') and tid:
            try:
                tok = AppKey.objects.get(id=tid)
                if action == 'deactivate':
                    tok.active = False
                    tok.save()
                    message = 'Deactivated app key.'
                elif action == 'activate':
                    tok.active = True
                    tok.save()
                    message = 'Activated app key.'
                elif action == 'regenerate':
                    new = _secrets.token_urlsafe(48)
                    tok.key = new
                    tok.save()
                    message = f'Regenerated key: {tok.key}'
                elif action == 'delete':
                    tok.delete()
                    message = 'Deleted app key.'
            except Exception:
                message = 'App key not found.'

    tokens = AppKey.objects.select_related('convention').all().order_by('-created_at')[:200]
    conventions = Category.objects.all().order_by('name')
    context = {
        'tokens': tokens,
        'conventions': conventions,
        'scope_groups': [
            ('Access', [
                (AppKey.SCOPE_API, 'Full API'),
                (AppKey.SCOPE_CON_DASHBOARD, 'Dashboard'),
            ]),
            ('API routes', [(name, name) for name in AppKey.API_ROUTE_SCOPES]),
        ],
        'message': message,
        'page_title': 'App Keys',
    }
    return render(request, 'archive/admin/api_keys.html', context)


@staff_member_required
def admin_site_banner(request):
    """Custom admin page to edit the site-wide banner."""
    banner = SiteBanner.objects.order_by('-updated_at').first()

    if request.method == 'POST':
        form = SiteBannerForm(request.POST, instance=banner)
        if form.is_valid():
            saved = form.save(commit=False)
            saved.updated_by = request.user
            saved.save()
            messages.success(request, 'Site banner updated successfully.')
            return redirect('admin_site_banner')
    else:
        if banner is None:
            banner = SiteBanner(text='Welcome to Furry Con Archives', color='#0066cc', is_enabled=False)
        form = SiteBannerForm(instance=banner)

    preview = SiteBanner.objects.filter(is_enabled=True).exclude(text='').order_by('-updated_at').first()
    context = {
        'form': form,
        'preview_banner': preview,
        'page_title': 'Site Banner',
    }
    return render(request, 'archive/admin/site_banner.html', context)


@staff_member_required
def admin_faq(request):
    """Create and list FAQ entries used by the website and Android app."""
    from archive.site_pages import ensure_default_site_content
    ensure_default_site_content()

    if request.method == 'POST':
        form = FaqEntryForm(request.POST)
        if form.is_valid():
            entry = form.save()
            messages.success(request, f'Added FAQ: “{entry.question}”')
            return redirect('admin_faq')
    else:
        form = FaqEntryForm(initial={'order': (FaqEntry.objects.aggregate(Max('order')).get('order__max') or 0) + 10})

    context = {
        'form': form,
        'faq_entries': FaqEntry.objects.order_by('order', 'id'),
        'page_title': 'FAQ entries',
    }
    return render(request, 'archive/admin/faq.html', context)


@staff_member_required
def admin_edit_faq(request, entry_id):
    entry = get_object_or_404(FaqEntry, id=entry_id)
    if request.method == 'POST':
        form = FaqEntryForm(request.POST, instance=entry)
        if form.is_valid():
            form.save()
            messages.success(request, f'Updated FAQ: “{entry.question}”')
            return redirect('admin_faq')
    else:
        form = FaqEntryForm(instance=entry)
    return render(request, 'archive/admin/edit_faq.html', {
        'form': form,
        'entry': entry,
        'page_title': f'Edit FAQ: {entry.question}',
    })


@staff_member_required
def admin_delete_faq(request, entry_id):
    entry = get_object_or_404(FaqEntry, id=entry_id)
    if request.method == 'POST':
        question = entry.question
        entry.delete()
        messages.success(request, f'Deleted FAQ: “{question}”')
        return redirect('admin_faq')
    return render(request, 'archive/admin/delete_faq_confirm.html', {
        'entry': entry,
        'page_title': 'Delete FAQ',
    })

    # helper: look backwards up to N siblings/parents for a date string
    def _find_date(a):
        # 1) look for enclosing tr and previous 'bar' row
        tr = a.find_parent('tr')
        if tr:
            bar = tr.find_previous(lambda tag: tag.name == 'tr' and 'bar' in (tag.get('class') or []))
            if bar:
                span = bar.find('span')
                if span:
                    return span.get('id') or span.get_text(strip=True)
                # fallback: raw text
                txt = bar.get_text(' ', strip=True)
                if txt:
                    return txt

        # 2) look for nearby headings that contain month/day/year
        prev = a
        for _ in range(6):
            prev = prev.find_previous()
            if not prev:
                break
            txt = (prev.get_text(' ', strip=True) or '').strip()
            if re.search(r'\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\b', txt, re.I):
                return txt
            if re.search(r'\d{4}-\d{2}-\d{2}', txt):
                return txt
        return None

    def _find_time(a):
        # look inside row for a td.time or any sibling td containing a time-like string
        tr = a.find_parent('tr')
        candidates = []
        if tr:
            candidates.extend(tr.select('td.time, td'))
        td = a.find_parent('td')
        if td:
            candidates.extend(td.find_previous_siblings('td'))
            candidates.extend(td.find_all('div'))

        # also check the anchor's text
        candidates.append(a)

        time_text = ''
        time_re = re.compile(r'\d{1,2}:?\d{0,2}\s*(?:am|pm|AM|PM)?|\d{1,2}\s*(?:am|pm|AM|PM)')
        for c in candidates:
            txt = (c.get_text(' ', strip=True) or '').strip()
            if not txt:
                continue
            # prefer strings containing a range marker
            if any(sep in txt for sep in ['–', '—', '-', ' to ', '\u2013', '\u2014']):
                return txt
            if time_re.search(txt):
                return txt
        return ''

    def _find_location(a):
        # look for common venue classes
        td = a.find_parent('td')
        if td:
            venue = td.find(lambda tag: tag.name in ['div', 'span'] and ('venue' in (tag.get('class') or []) or 'venue' in (tag.get('id') or '').lower()))
            if venue:
                return venue.get_text(' ', strip=True)
            # look for floated venue text
            float_venue = td.find(lambda tag: tag.name in ['div', 'span'] and 'float' in (tag.get('style') or ''))
            if float_venue:
                return float_venue.get_text(' ', strip=True)

        # fallback: look in ancestor .title div
        title_div = a.find_parent(class_='title')
        if title_div:
            v = title_div.find(lambda tag: tag.name in ['div', 'span'] and 'venue' in (tag.get('class') or []))
            if v:
                return v.get_text(' ', strip=True)

        # last resort: look for parentheses in nearby text
        cont = a.find_parent()
        if cont:
            txt = cont.get_text(' ', strip=True)
            m = re.search(r"\(([^)]+Hotel[^)]*)\)", txt)
            if m:
                return m.group(1)
        return ''

    def _find_description(a):
        # many detailed print pages include a description element near the
        # event title. Try common class names first, then fallback to nearby
        # paragraph/div text excluding venue/role lines.
        # 1) look for sibling or parent elements with likely classes
        candidates = []
        parent = a.find_parent()
        if parent:
            candidates.extend(parent.select('.description, .desc, .summary, .synopsis, .panel-description'))
        # 2) look in the row for a p/div that looks like a description
        tr = a.find_parent('tr')
        if tr:
            candidates.extend(tr.select('p, div'))

        for c in candidates:
            if not c:
                continue
            txt = (c.get_text(' ', strip=True) or '').strip()
            if not txt:
                continue
            # skip short tokens and venue lines
            if len(txt) < 20:
                continue
            if 'Hotel' in txt and len(txt) < 200:
                continue
            # skip role/speakers lines
            if txt.lower().startswith('speakers:') or txt.lower().startswith('panelists:'):
                continue
            return txt

        # 3) look at following siblings
        sib = a.find_next_sibling()
        for _ in range(6):
            if not sib:
                break
            txt = (sib.get_text(' ', strip=True) or '').strip()
            if txt and len(txt) > 20 and 'Hotel' not in txt:
                return txt
            sib = sib.find_next_sibling()

        return ''

    for a in anchors:
        # normalize: if the selected node isn't an anchor, try to find one inside
        node = a
        if getattr(node, 'name', '') != 'a':
            inner = node.find('a')
            if inner:
                node = inner

        href = node.get('href')
        eid = node.get('id') or (href.split('/')[-1] if href else None) or node.get('data-event-id')
        title = node.get_text(' ', strip=True)

        date_id = _find_date(a)
        time_text = _find_time(a)

        start = end = None
        if time_text:
            parts = re.split(r'–|—|-| to |\u2013|\u2014', time_text, maxsplit=1)
            if len(parts) == 2 and re.search(r'\d', parts[0]):
                start = parts[0].strip()
                end = parts[1].strip()
            else:
                # try to extract first time-like token
                tm = re.search(r'([0-9]{1,2}:?[0-9]{0,2}\s*(?:am|pm|AM|PM)?)', time_text)
                if tm:
                    start = tm.group(1).strip()

        location = _find_location(a)
        description = _find_description(a)

        events.append({
            'id': eid,
            'name': title,
            'date': date_id,
            'start_raw': start,
            'end_raw': end,
            'location': location,
            'description': description,
        })

    # If events lack a venue/address, try to derive a site-level location
    # (from page header/title) or fall back to the slug so timezone
    # lookup has something to work with.
    site_location = ''
    # common page-level location hints
    hdr = soup.find(lambda tag: tag.name in ['div', 'header', 'section'] and (
        'venue' in (tag.get('class') or []) or 'location' in (tag.get('class') or []) or 'convention' in (tag.get('class') or [])))
    if hdr:
        site_location = (hdr.get_text(' ', strip=True) or '').strip()
    if not site_location:
        title = soup.find('h1') or soup.find('title')
        if title:
            site_location = (title.get_text(' ', strip=True) or '').strip()
    if not site_location:
        # fallback to slug (e.g. midwestfurfest2025 -> "midwestfurfest2025")
        site_location = slug.replace('-', ' ').strip()

    # apply fallback site_location to events missing a location
    # Prefer using the event name as the lookup string (optionally combined
    # with the site location) so geocoders sometimes resolve venue names.
    for ev in events:
        if not ev.get('location'):
            name = (ev.get('name') or '').strip()
            if name:
                # If the name is short/ambiguous, append the site_location to help
                # the geocoder (e.g. "Panel Title Boston MA"). Otherwise use name.
                if len(name.split()) < 4 and site_location:
                    ev['location'] = f"{name} {site_location}"
                else:
                    ev['location'] = name
            else:
                ev['location'] = site_location

    # Per-slug overrides for known conventions with ambiguous venue strings
    SCHED_TZ_OVERRIDES = {
        'anthrocon': 'America/New_York',
        'ane2': 'America/New_York',
        'anthronewengland': 'America/New_York',
        'furfest': 'America/Chicago',
        'furryweekend': 'America/New_York',
    }
    slug_l = (slug or '').lower().strip()
    if slug_l in SCHED_TZ_OVERRIDES:
        for ev in events:
            # attach a tz_override (don't overwrite scraped `location`) so
            # normalization can use the known IANA timezone directly.
            ev['tz_override'] = SCHED_TZ_OVERRIDES[slug_l]

    # Normalize times (heuristic timezone mapping)
    normalized = _normalize_event_times(events)

    # Ensure we don't expose internal tz override hints in the public JSON
    for ev in normalized:
        if 'tz_override' in ev:
            ev.pop('tz_override', None)

    payload = {'slug': slug, 'count': len(normalized), 'events': normalized}
    # Cache for 6 hours
    try:
        cache.set(cache_key, payload, 60 * 60 * 6)
    except Exception:
        logger.exception('api_sched: cache.set failed for %s', cache_key)

    return JsonResponse(payload)


@require_GET
def api_guidebook(request, guide_slug):
    guide_slug = (request.GET.get('slug') or guide_slug).strip()
    if not guide_slug:
        return JsonResponse({'error': 'missing_guide_slug'}, status=400)

    def _extract_state(text):
        if not text:
            return None
        patterns = [
            r'window\.__INITIAL_STATE__\s*=\s*',
            r'window\.__PRELOADED_STATE__\s*=\s*',
        ]
        for pat in patterns:
            import re
            match = re.search(pat, text)
            if not match:
                continue
            start = match.end()
            # Find the first '{' after the assignment.
            brace_start = text.find('{', start)
            if brace_start == -1:
                continue
            depth = 0
            for idx in range(brace_start, len(text)):
                ch = text[idx]
                if ch == '{':
                    depth += 1
                elif ch == '}':
                    depth -= 1
                    if depth == 0:
                        return text[brace_start:idx + 1]
        return None

    def _extract_events_from_state(state_obj):
        if not state_obj:
            return []

        event_like_keys = {
            'name', 'title', 'start_date', 'startTime', 'start', 'date', 'id', 'description', 'location'
        }
        candidates = []

        def walk(obj):
            if isinstance(obj, dict):
                for value in obj.values():
                    walk(value)
            elif isinstance(obj, list):
                if obj and isinstance(obj[0], dict):
                    sample = obj[0]
                    if any(k in sample for k in event_like_keys):
                        candidates.append(obj)
                        return
                for item in obj:
                    walk(item)

        walk(state_obj)
        if not candidates:
            return []
        return max(candidates, key=lambda x: len(x))

    base_headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
        'Referer': f"https://builder.guidebook.com",
    }

    api_url = f"https://builder.guidebook.com/api/guidebook-web/{guide_slug}/"
    api_status = None
    api_detail = None
    try:
        api_headers = dict(base_headers)
        api_headers['Accept'] = 'application/json'
        api_resp = requests_session.get(api_url, headers=api_headers, timeout=GUIDEBOOK_API_TIMEOUT)
        api_status = api_resp.status_code
        if api_resp.status_code == 200:
            try:
                data = api_resp.json()
            except Exception as exc:
                logger.exception('api_guidebook: failed parse api json %s: %s', api_url, exc)
                data = None
            if data is not None:
                # If jsonFilePath is present, fetch the full schedule bundle
                bundle_data = None
                bundle_url = None
                try:
                    json_path = data.get('jsonFilePath')
                    if json_path:
                        bundle_url = f"https://s3.amazonaws.com/media.guidebook.com/{json_path}"
                        bundle_resp = requests_session.get(bundle_url, headers=api_headers, timeout=GUIDEBOOK_API_TIMEOUT)
                        if bundle_resp.status_code == 200:
                            try:
                                bundle_data = bundle_resp.json()
                            except Exception as exc:
                                logger.exception('api_guidebook: failed parse bundle json %s: %s', bundle_url, exc)
                                bundle_data = None
                except Exception as exc:
                    logger.exception('api_guidebook: bundle fetch failed %s: %s', bundle_url, exc)
                if bundle_data is not None:
                    # Parse and normalize Guidebook bundle to Sched-like output with times and timezone
                    events = []
                    sessions = bundle_data.get('guidebook_event', [])
                    seen_ids = set()
                    seen_keys = set()
                    # Build location_id -> name map
                    location_map = {}
                    for loc in bundle_data.get('guidebook_location', []):
                        location_map[loc.get('id')] = loc.get('name')

                    guide_tz = None
                    guide_list = bundle_data.get('guidebook_guide', [])
                    if guide_list and isinstance(guide_list, list):
                        guide_tz = guide_list[0].get('timezone')
                    if not guide_tz:
                        guide_tz = _guess_timezone_from_location(bundle_data.get('venue', {}).get('name', '') or guide_slug)
                    if not guide_tz:
                        guide_tz = 'UTC'

                    import re
                    for session in sessions:
                        name_field = session.get('name')
                        if isinstance(name_field, dict):
                            event_name = name_field.get('en-US') or next(iter(name_field.values()), None) or 'Untitled event'
                        else:
                            event_name = name_field or 'Untitled event'
                        # Parse description: prefer description_html, fallback to description
                        import html
                        desc_raw = ''
                        if session.get('description_html'):
                            desc_raw = session.get('description_html', {}).get('en-US') or ''
                        elif session.get('description'):
                            desc_val = session.get('description')
                            if isinstance(desc_val, dict):
                                desc_raw = desc_val.get('en-US') or next(iter(desc_val.values()), '')
                            else:
                                desc_raw = desc_val
                        desc_unescaped = html.unescape(desc_raw)
                        desc_plain = re.sub(r'<[^>]+>', '', desc_unescaped).strip()
                        # Prefer start_date/end_date, fallback to startTime/endTime
                        start_dt = session.get('start_date') or session.get('startTime')
                        end_dt = session.get('end_date') or session.get('endTime')
                        date_val = None
                        if start_dt:
                            # Try to extract date from datetime string
                            try:
                                from dateutil import parser as dateparser
                                st = dateparser.parse(start_dt)
                                date_val = st.date().isoformat()
                            except Exception:
                                date_val = None
                        # Parse location: get first location id from locations array, map to name
                        location_name = ''
                        locations_val = session.get('locations')
                        loc_id = None
                        if isinstance(locations_val, list) and len(locations_val) > 0:
                            loc_id = locations_val[0]
                        elif isinstance(locations_val, str) and locations_val.strip():
                            loc_id = locations_val.strip()
                        if loc_id is not None:
                            # If loc_id is numeric string, convert to int for mapping
                            try:
                                loc_id_int = int(loc_id)
                            except Exception:
                                loc_id_int = loc_id
                            location_name = location_map.get(loc_id_int, location_map.get(loc_id, ''))
                        ev = {
                            'id': session.get('id') or session.get('uuid'),
                            'name': event_name,
                            'date': date_val or '',
                            'start_raw': None,
                            'end_raw': None,
                            'location': location_name,
                            'description': desc_plain,
                        }
                        import re
                        time_re = re.compile(r'T(\d{1,2}:\d{2})')
                        # Try to extract time from ISO or datetime string
                        if start_dt:
                            try:
                                from dateutil import parser as dateparser
                                st = dateparser.parse(start_dt)
                                start_fmt = st.strftime('%I:%M%p').lower()
                                ev['start_raw'] = start_fmt.lstrip('0') if start_fmt.startswith('0') else start_fmt
                            except Exception:
                                ev['start_raw'] = None
                        if end_dt:
                            try:
                                from dateutil import parser as dateparser
                                en = dateparser.parse(end_dt)
                                end_fmt = en.strftime('%I:%M%p').lower()
                                ev['end_raw'] = end_fmt.lstrip('0') if end_fmt.startswith('0') else end_fmt
                            except Exception:
                                ev['end_raw'] = None
                        start_dt = session.get('start_date')
                        end_dt = session.get('end_date')
                        import re
                        time_re = re.compile(r'T(\d{1,2}:\d{2})')
                        if start_dt:
                            m = time_re.search(start_dt)
                            ev['start_raw'] = m.group(1) if m else ev['start_raw']
                        if end_dt:
                            m = time_re.search(end_dt)
                            ev['end_raw'] = m.group(1) if m else ev['end_raw']
                        if not ev['start_raw'] and start_dt:
                            try:
                                from dateutil import parser as dateparser
                                st = dateparser.parse(start_dt)
                                ev['start_raw'] = st.strftime('%I:%M%p').lower()
                            except Exception:
                                ev['start_raw'] = None
                        if not ev['end_raw'] and end_dt:
                            try:
                                from dateutil import parser as dateparser
                                en = dateparser.parse(end_dt)
                                ev['end_raw'] = en.strftime('%I:%M%p').lower()
                            except Exception:
                                ev['end_raw'] = None

                        tz_name = guide_tz
                        ev['timezone'] = tz_name

                        ev['start'] = None
                        ev['end'] = None
                        ev['start_utc'] = None
                        ev['end_utc'] = None
                        if ev['date'] and ev['start_raw'] and tz_name:
                            try:
                                from dateutil import parser as dateparser
                                import pytz
                                st = dateparser.parse(f"{ev['date']} {ev['start_raw']}")
                                local_tz = pytz.timezone(tz_name)
                                st_local = local_tz.localize(st)
                                ev['start'] = st_local.isoformat()
                                ev['start_utc'] = st_local.astimezone(pytz.utc).isoformat()
                            except Exception:
                                ev['start'] = None
                                ev['start_utc'] = None
                        if ev['date'] and ev['end_raw'] and tz_name:
                            try:
                                from dateutil import parser as dateparser
                                import pytz
                                en = dateparser.parse(f"{ev['date']} {ev['end_raw']}")
                                local_tz = pytz.timezone(tz_name)
                                en_local = local_tz.localize(en)
                                ev['end'] = en_local.isoformat()
                                ev['end_utc'] = en_local.astimezone(pytz.utc).isoformat()
                            except Exception:
                                ev['end'] = None
                                ev['end_utc'] = None
                        try:
                            if ev['start'] and ev['end']:
                                from dateutil import parser as dateparser
                                st_dt = dateparser.parse(ev['start'])
                                en_dt = dateparser.parse(ev['end'])
                                if en_dt <= st_dt:
                                    from datetime import timedelta
                                    en_dt = en_dt + timedelta(days=1)
                                    ev['end'] = en_dt.isoformat()
                                    ev['end_utc'] = en_dt.astimezone(pytz.utc).isoformat()
                        except Exception:
                            pass

                        # Deduplication: by id, or by (name, date, start_raw, location)
                        dedup_key = ev['id'] or f"{ev['name']}|{ev['date']}|{ev['start_raw']}|{ev['location']}"
                        if dedup_key in seen_ids or dedup_key in seen_keys:
                            continue
                        if ev['id']:
                            seen_ids.add(ev['id'])
                        else:
                            seen_keys.add(dedup_key)
                        events.append(ev)

                    payload = {
                        'slug': guide_slug,
                        'type': 'guidebook',
                        'count': len(events),
                        'events': events,
                    }
                    return JsonResponse(payload)
                else:
                    logger.error('api_guidebook: bundle fetch failed: url=%s status=%s text=%s', bundle_url, bundle_resp.status_code if bundle_resp else None, bundle_resp.text[:500] if bundle_resp else None)
                    return JsonResponse({
                        'error': 'Bundle not found',
                        'bundle_url': bundle_url,
                        'bundle_status': bundle_resp.status_code if bundle_resp else None,
                        'bundle_text': bundle_resp.text[:500] if bundle_resp else None
                    }, status=404)
        else:
            logger.warning('api_guidebook: api status %s for %s', api_resp.status_code, api_url)
            api_detail = (api_resp.text or '')[:500]
    except Exception as exc:
        logger.exception('api_guidebook: api fetch failed %s: %s', api_url, exc)
        api_detail = str(exc)

    page_url = f"https://builder.guidebook.com/g/#/guides/{guide_slug}/details"
    try:
        page_headers = dict(base_headers)
        page_headers['Accept'] = 'text/html,application/xhtml+xml'
        resp = requests_session.get(page_url, headers=page_headers, timeout=GUIDEBOOK_PAGE_TIMEOUT)
        if resp.status_code != 200:
            return JsonResponse({
                'error': 'fetch_failed',
                'status': resp.status_code,
                'detail': resp.text[:500] if resp.text else '',
                'page_url': page_url,
                'guide_slug': guide_slug,
                'api_status': api_status,
                'api_detail': api_detail,
            }, status=502)
    except Exception as exc:
        logger.exception('api_guidebook: failed page %s: %s', page_url, exc)
        return JsonResponse({
            'error': 'fetch_failed',
            'detail': str(exc),
            'page_url': page_url,
            'guide_slug': guide_slug,
            'api_status': api_status,
            'api_detail': api_detail,
        }, status=502)

    html = resp.text or ''
    state_blob = _extract_state(html)

    if state_blob and len(state_blob) > GUIDEBOOK_STATE_BLOB_MAX_SIZE:
        logger.warning('api_guidebook: hydration blob too large for %s (%s bytes)', guide_slug, len(state_blob))
        return JsonResponse({
            'error': 'hydration_blob_too_large',
            'guide_slug': guide_slug,
            'blob_size': len(state_blob),
            'api_status': api_status,
            'api_detail': api_detail,
        }, status=502)

    # If the HTML doesn't include the hydration payload, try the JS bundle.
    if not state_blob:
        import re
        script_urls = re.findall(r'<script[^>]+src="([^"]+)"', html, re.I)
        for script_url in script_urls[:GUIDEBOOK_MAX_SCRIPT_FETCH]:
            try:
                js_resp = requests_session.get(script_url, headers=page_headers, timeout=GUIDEBOOK_SCRIPT_TIMEOUT)
                if js_resp.status_code != 200:
                    continue
                state_blob = _extract_state(js_resp.text or '')
                if state_blob:
                    break
            except Exception:
                continue

    if not state_blob:
        return JsonResponse({
            'error': 'hydration_not_found',
            'page_url': page_url,
            'guide_slug': guide_slug,
            'api_status': api_status,
            'api_detail': api_detail,
        }, status=502)

    try:
        state = json.loads(state_blob)
    except Exception as exc:
        logger.exception('api_guidebook: failed parse hydration %s: %s', page_url, exc)
        return JsonResponse({
            'error': 'hydration_parse_failed',
            'detail': str(exc),
            'page_url': page_url,
            'guide_slug': guide_slug,
            'api_status': api_status,
            'api_detail': api_detail,
        }, status=502)

    events = _extract_events_from_state(state)
    if events:
        return JsonResponse({
            'slug': guide_slug,
            'type': 'guidebook',
            'guide_slug': guide_slug,
            'source': 'hydration',
            'count': len(events),
            'events': events,
        })

    return JsonResponse({
        'error': 'hydration_no_events',
        'guide_slug': guide_slug,
        'api_status': api_status,
        'api_detail': api_detail,
    }, status=502)

_tf = TimezoneFinder()

# Map lowercased IANA names to canonical names for case-insensitive matching
TZ_NAME_MAP = {tz.lower(): tz for tz in pytz.all_timezones}

# Build a simple in-memory city -> (lat, lon) map from geonamescache for fast local lookups
_gc = geonamescache.GeonamesCache()
_GC_CITY_MAP = {}
_GC_CITY_NAMES = []
try:
    for city_id, data in _gc.get_cities().items():
        name = (data.get('name') or '').strip()
        if not name:
            continue
        lat = data.get('latitude')
        lon = data.get('longitude')
        try:
            lat = float(lat)
            lon = float(lon)
        except Exception:
            continue
        key = name.lower()
        _GC_CITY_MAP[key] = (lat, lon)
        _GC_CITY_NAMES.append(key)
except Exception:
    _GC_CITY_MAP = {}
    _GC_CITY_NAMES = []


@lru_cache(maxsize=1024)
def _guess_timezone_from_location_uncached(loc_key):
    """In-process cached helper that does the actual lookup.
    `loc_key` should be a normalized (lower/stripped) string.
    """
    # Work on a cleaned copy; callers pass a lower/stripped key but that
    # may include parenthetical noise like "Room 45 (Level 2)" which
    # confuses geocoders and cache keys. Remove parentheticals and
    # common room/level tokens before further processing.
    location = loc_key
    try:
        # remove parenthetical groups
        location = re.sub(r"\([^)]*\)", " ", location)
        # remove tokens like 'room 45', 'rm 45', 'level 2'
        location = re.sub(r"\b(room|rm|level|lvl)\s*\d+\b", " ", location)
        # collapse whitespace
        location = re.sub(r"\s+", " ", location).strip()
    except Exception:
        pass

    # 1) explicit IANA-like token e.g. "America/New_York"
    try:
        iana_candidates = re.findall(r'[A-Za-z_]+\/[A-Za-z_]+', location)
        for cand in iana_candidates:
            tz = TZ_NAME_MAP.get(cand.lower())
            if tz:
                return tz
    except Exception:
        pass

    # 2) Geocode using Nominatim (respect rate limits) using session
    # Try local city lookup via geonamescache first (no network)
    try:
        if _GC_CITY_MAP:
            # split by common separators and try tokens
            tokens = re.split(r'[,@\-()\n\t]+', location)
            tokens = [t.strip().lower() for t in tokens if t and len(t) > 1]
            for t in tokens:
                if t in _GC_CITY_MAP:
                    lat, lon = _GC_CITY_MAP[t]
                    tz = _tf.timezone_at(lat=lat, lng=lon)
                    if tz:
                        return tz
            # fuzzy match against known city names
            if tokens:
                # try matching the longest token
                token = max(tokens, key=len)
                matches = difflib.get_close_matches(token, _GC_CITY_NAMES, n=1, cutoff=0.85)
                if matches:
                    lat, lon = _GC_CITY_MAP[matches[0]]
                    tz = _tf.timezone_at(lat=lat, lng=lon)
                    if tz:
                        return tz
    except Exception:
        # fall through to remote geocoding
        pass

    try:
        # Try a sequence of query variants to handle slugs like midwestfurfest2025
        queries = [location]
        # If slug-like (trailing year), strip the year: midwestfurfest2025 -> midwestfurfest
        m = re.match(r'^([a-z0-9\-\_]+?)(\d{4})$', location)
        if m:
            base = m.group(1)
            queries.append(base)
            # add a human-friendly title-cased form
            queries.append(base.replace('-', ' ').replace('_', ' ').title())
            # try with 'convention' as a hint
            queries.append(f"{base.replace('-', ' ').replace('_', ' ').title()} Convention")

        # also try the bare event name/title if it looks different (contains spaces)
        if ' ' in location:
            queries.append(location.title())

        # de-duplicate while keeping order
        seen = set()
        queries_ordered = []
        for q in queries:
            if not q:
                continue
            qq = q.strip()
            if qq and qq not in seen:
                seen.add(qq)
                queries_ordered.append(qq)

        for q in queries_ordered:
            params = {'q': q, 'format': 'json', 'limit': 1, 'addressdetails': 1}
            try:
                resp = requests_session.get('https://nominatim.openstreetmap.org/search', params=params, timeout=GUIDEBOOK_GEOCODE_TIMEOUT)
                resp.raise_for_status()
                results = resp.json()
                if results:
                    lat = float(results[0]['lat'])
                    lon = float(results[0]['lon'])
                    tz = _tf.timezone_at(lat=lat, lng=lon)
                    if tz:
                        return tz

                    # country fallback
                    addr = results[0].get('address', {})
                    cc = (addr.get('country_code') or '').upper()
                    if cc:
                        tzs = pytz.country_timezones.get(cc)
                        if tzs:
                            return tzs[0]
            except (Exception, SystemExit):
                # try the next query variant
                continue
    except Exception:
        pass

    # 3) small substring fallback (retain a tiny list only)
    small_fallback = {
        'boston': 'America/New_York',
        'new york': 'America/New_York',
        'nyc': 'America/New_York',
        'los angeles': 'America/Los_Angeles',
        'san francisco': 'America/Los_Angeles',
        'seattle': 'America/Los_Angeles',
    }
    for k, v in small_fallback.items():
        if k in location:
            return v

    return None


def _guess_timezone_from_location(location):
    """Return an IANA timezone name for a free-form location string.
    Uses Django `cache` (shared) and an in-process LRU cache to avoid repeated lookups.
    """
    if not location or not location.strip():
        return None

    loc_key = location.strip().lower()
    # Use a hashed cache key to avoid memcached/redis key character restrictions
    digest = hashlib.sha1(loc_key.encode('utf-8')).hexdigest()[:24]
    cache_key = f'tz_lookup:{digest}'
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    result = None
    try:
        # Give the uncached resolver a cleaned key (it will further strip
        # parentheticals). This keeps the in-process LRU effective while the
        # shared cache uses a short, safe key.
        result = _guess_timezone_from_location_uncached(loc_key)
    except BaseException:
        result = None

    try:
        # longer TTL for positive results, shorter for misses
        ttl = 60 * 60 * 24 * 30 if result else 60 * 60 * 6
        cache.set(cache_key, result, ttl)
    except Exception:
        logger.exception('api_sched: cache.set failed for %s', cache_key)

    return result


VENVI_ID_RE = re.compile(r'^[-a-zA-Z0-9_]{8,128}$')

# Same IndexedDB export as the browser console snippet; returns { storeName: [docs...] }.
VENVI_FIRESTORE_EXPORT_JS = """
async () => {
  const dbName = "firestore/[DEFAULT]/venvi-a83ac/main";

  function openDb() {
    return new Promise((resolve, reject) => {
      const request = indexedDB.open(dbName);
      request.onsuccess = (event) => resolve(event.target.result);
      request.onerror = (event) => reject(event.target.error);
    });
  }

  async function waitForStores(maxMs) {
    const deadline = Date.now() + maxMs;
    while (Date.now() < deadline) {
      let db;
      try {
        db = await openDb();
        const names = Array.from(db.objectStoreNames);
        db.close();
        if (names.length > 0) {
          return names.length;
        }
      } catch (e) {
        /* retry */
      }
      await new Promise((r) => setTimeout(r, 1000));
    }
    return 0;
  }

  await waitForStores(90000);

  const db = await openDb();
  const objectStoreNames = Array.from(db.objectStoreNames);
  const dump = {};

  if (objectStoreNames.length === 0) {
    db.close();
    return dump;
  }

  await Promise.all(objectStoreNames.map((storeName) => new Promise((resolve) => {
    try {
      const transaction = db.transaction(storeName, "readonly");
      const store = transaction.objectStore(storeName);
      const getAllRequest = store.getAll();
      getAllRequest.onsuccess = () => {
        dump[storeName] = getAllRequest.result;
        resolve();
      };
      getAllRequest.onerror = () => resolve();
    } catch (e) {
      resolve();
    }
  })));

  db.close();
  return dump;
}
"""


@require_GET
def api_venvi_firestore_dump(request, org_id, app_id):
    """Dump Venvi Firestore local cache (IndexedDB) as JSON for a convention app.

    Loads https://web.venvi.app/<org_id>/<app_id>/home in headless Chromium,
    waits for Firestore to sync into IndexedDB, then exports all object stores.
    """
    if not VENVI_ID_RE.match(org_id) or not VENVI_ID_RE.match(app_id):
        return JsonResponse({'error': 'invalid_venvi_ids'}, status=400)

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return JsonResponse(
            {
                'error': 'playwright_not_installed',
                'hint': 'pip install playwright && playwright install chromium',
            },
            status=503,
        )

    url = f'https://web.venvi.app/{org_id}/{app_id}/home'
    timeout_ms = int(getattr(settings, 'VENVI_EXPORT_TIMEOUT_MS', 120_000))

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.goto(url, wait_until='domcontentloaded', timeout=timeout_ms)
                dump = page.evaluate(VENVI_FIRESTORE_EXPORT_JS)
            finally:
                browser.close()
    except Exception as exc:
        logger.exception('api_venvi_firestore_dump failed for %s/%s', org_id, app_id)
        return JsonResponse({'error': 'venvi_export_failed', 'detail': str(exc)}, status=502)

    if not isinstance(dump, dict):
        dump = {}

    if request.GET.get('raw') in ('1', 'true', 'yes'):
        payload = dump
        filename = f'firestore_venvi_{org_id}_{app_id}_dump.json'
    else:
        from .venvi_schedule import venvi_dump_to_schedule_payload
        payload = venvi_dump_to_schedule_payload(
            dump,
            org_id,
            app_id,
            normalize_times=_normalize_event_times,
        )
        slug = payload.get('slug') or 'venvi'
        filename = f'{slug}.json'

    response = JsonResponse(payload, json_dumps_params={'indent': 2})
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


# The following internal schedule-related endpoints were previously
# restricted to staff users.  In keeping with the desire to treat all
# /internal/api/ URLs as openly callable (e.g. the telegram-messages proxy
# is already open), the staff check has been removed.  These views are now
# publicly accessible; callers are responsible for any misuse.
@require_GET
def api_schedule_list(request):
    """Return all cached schedules as JSON for admin panel."""
    from archive.furconnect_schedule import furconnect_schedule_url
    schedules = Schedule.objects.filter(deleted=False).order_by('-last_updated')
    out = []
    for sched in schedules:
        out.append({
            'id': sched.id,
            'type': sched.type,
            'slug': sched.slug,
            'route_slug': sched.route_slug,
            'furconnect_url': furconnect_schedule_url(sched.slug) if sched.type == 'furconnect' else '',
            'category': sched.category.id if sched.category else None,
            'category_name': sched.category.name if sched.category else '-',
            'year': sched.year,
            'convention_name': sched.convention_name,
            'last_updated': sched.last_updated.strftime('%Y-%m-%d %H:%M:%S'),
            'error': sched.error,
            'events_count': len(json.loads(sched.events_json)) if sched.events_json else 0,
        })
    return JsonResponse({'schedules': out})


@staff_member_required
def admin_schedules(request):
    """Custom admin panel tab for schedule management."""
    return render(request, 'archive/admin_schedule.html')

# API endpoint for schedule creation
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

@csrf_exempt
@require_POST
def api_schedule_create(request):
    """Create a new schedule from admin panel."""
    try:
        data = json.loads(request.body.decode('utf-8'))
        schedule_type = data.get('type')
        slug = data.get('slug')
        events_json = data.get('events_json', '')
        if not schedule_type or not slug:
            return JsonResponse({'error': 'Missing type or slug.'}, status=400)
        # Normalize events_json so it's always stored as a JSON string in the DB
        try:
            if isinstance(events_json, (list, dict)):
                events_json_str = json.dumps(events_json)
            elif isinstance(events_json, str) and events_json.strip():
                # Ensure string contains valid JSON (if it's already a JSON string, keep it normalized).
                # Fallback: accept Python-style literals (single quotes) using ast.literal_eval.
                try:
                    parsed = json.loads(events_json)
                    events_json_str = json.dumps(parsed)
                except Exception:
                    try:
                        import ast
                        parsed = ast.literal_eval(events_json)
                        events_json_str = json.dumps(parsed)
                    except Exception:
                        # Not JSON or Python-literal — treat as empty
                        events_json_str = ''
            else:
                events_json_str = ''
        except Exception:
            events_json_str = ''

        if schedule_type == 'pretalx':
            from archive.pretalx_client import storage_fields_for_pretalx_input, PretalxConfigError
            try:
                slug = storage_fields_for_pretalx_input(slug)
            except PretalxConfigError:
                return JsonResponse({'error': 'Invalid pretalx URL or event slug.'}, status=400)

        schedule_csv = data.get('schedule_csv') or ''
        furconnect_url = data.get('furconnect_url') or data.get('source_url') or ''
        timezone_name = data.get('timezone') or 'America/Los_Angeles'

        if schedule_type == 'furconnect':
            if schedule_csv:
                from archive.furconnect_schedule import furconnect_csv_to_events
                events = furconnect_csv_to_events(
                    schedule_csv,
                    timezone=timezone_name,
                    normalize_times=_normalize_event_times,
                )
                events_json_str = json.dumps(events, ensure_ascii=False)
            if not furconnect_url:
                return JsonResponse({'error': 'Furconnect schedules require a source URL.'}, status=400)
            from archive.furconnect_schedule import storage_fields_for_furconnect_input
            try:
                slug = storage_fields_for_furconnect_input(slug, furconnect_url)
            except ValueError as exc:
                return JsonResponse({'error': str(exc)}, status=400)

        category_id = data.get('category_id')
        year = data.get('year')
        convention_name = data.get('convention_name', '')

        if schedule_type == 'local':
            from archive.local_schedule import local_schedule_meta_from_payload
            schedule_payload = data.get('schedule_payload')
            if isinstance(schedule_payload, dict):
                meta = local_schedule_meta_from_payload(schedule_payload)
                if meta:
                    slug = meta['slug']
                    if meta.get('year') is not None:
                        year = meta['year']
                    if meta.get('convention_name'):
                        convention_name = meta['convention_name']

        # Check for duplicates
        if Schedule.objects.filter(type=schedule_type, slug=slug, deleted=False).exists():
            return JsonResponse({'error': 'Schedule already exists.'}, status=409)
        sched = Schedule.objects.create(
            type=schedule_type,
            slug=slug,
            events_json=events_json_str,
            category_id=category_id if category_id else None,
            year=int(year) if year else None,
            convention_name=convention_name or '',
        )
        return JsonResponse({
            'id': sched.id,
            'type': sched.type,
            'slug': sched.slug,
            'last_updated': sched.last_updated,
            'error': sched.error,
            'events_count': len(json.loads(sched.events_json)) if sched.events_json else 0,
        }, status=201)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)

# API endpoint for categories list
@require_GET
def api_categories_list(request):
    from .models import Category
    from django.db.models import Count
    # Return all categories (include those without documents) so admin dropdowns can show every category
    categories = Category.objects.annotate(doc_count=Count('documents')).order_by('name')
    out = []
    for cat in categories:
        out.append({
            'id': cat.id,
            'name': cat.name,
            'location': cat.location,
            'order': cat.order,
            'parent': cat.parent_id,
            'doc_count': cat.doc_count,
        })
    return JsonResponse({'categories': out})

@csrf_exempt
@require_POST
def api_schedule_edit(request):
    """Edit an existing schedule from admin panel."""
    try:
        data = json.loads(request.body.decode('utf-8'))
        schedule_id = data.get('id')
        category_id = data.get('category_id')
        year = data.get('year')
        slug = data.get('slug')
        # Find schedule
        sched = Schedule.objects.filter(id=schedule_id, deleted=False).first()
        if not sched:
            return JsonResponse({'error': 'Schedule not found.'}, status=404)
        # Update fields
        deleted = data.get('deleted')
        if deleted is not None:
            # allow marking deleted
            sched.deleted = bool(deleted)
        if 'type' in data and data.get('type'):
            sched.type = data.get('type')
        if slug:
            if sched.type == 'pretalx':
                from archive.pretalx_client import storage_fields_for_pretalx_input, PretalxConfigError
                try:
                    slug = storage_fields_for_pretalx_input(slug)
                except PretalxConfigError:
                    return JsonResponse({'error': 'Invalid pretalx URL or event slug.'}, status=400)
            elif sched.type == 'furconnect':
                furconnect_url = data.get('furconnect_url') or data.get('source_url') or ''
                if furconnect_url:
                    from archive.furconnect_schedule import storage_fields_for_furconnect_input
                    try:
                        slug = storage_fields_for_furconnect_input(slug, furconnect_url)
                    except ValueError as exc:
                        return JsonResponse({'error': str(exc)}, status=400)
            sched.slug = slug
        if year is not None and year != '':
            try:
                sched.year = int(year)
            except ValueError:
                sched.year = None
        # convention_name may be provided
        if 'convention_name' in data:
            sched.convention_name = data.get('convention_name') or ''
        if category_id is not None:
            sched.category_id = category_id

        schedule_payload = data.get('schedule_payload')
        if isinstance(schedule_payload, dict):
            is_local = sched.type == 'local' or schedule_payload.get('type') == 'local'
            if is_local:
                from archive.local_schedule import apply_local_schedule_meta
                apply_local_schedule_meta(sched, schedule_payload)

        schedule_csv = data.get('schedule_csv') or ''
        if schedule_csv and sched.type == 'furconnect':
            from archive.furconnect_schedule import furconnect_csv_to_events
            timezone_name = data.get('timezone') or 'America/New_York'
            events = furconnect_csv_to_events(
                schedule_csv,
                timezone=timezone_name,
                normalize_times=_normalize_event_times,
            )
            sched.events_json = json.dumps(events, ensure_ascii=False)
            sched.error = ''

        # allow updating events_json via admin panel (upload from local JSON)
        if 'events_json' in data:
            events_val = data.get('events_json')
            try:
                # accept either JSON string or structured object/array
                if isinstance(events_val, str) and events_val.strip():
                    # try JSON first, then accept Python-style literal (single quotes) as fallback
                    try:
                        parsed = json.loads(events_val)
                    except Exception:
                        import ast
                        parsed = ast.literal_eval(events_val)
                    sched.events_json = json.dumps(parsed)
                elif isinstance(events_val, (list, dict)):
                    sched.events_json = json.dumps(events_val)
                elif events_val == '' or events_val is None:
                    sched.events_json = ''
                else:
                    # fallback: stringify
                    sched.events_json = json.dumps(events_val)
            except Exception as e:
                return JsonResponse({'error': f'invalid events_json: {e}'}, status=400)

        sched.save()
        return JsonResponse({'success': True})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)

@require_GET
def schedule_index(request):
    """Schedule browse shell; cards load via /v1/schedules/."""
    search_query = request.GET.get('q', '').strip()
    return render(request, 'archive/schedule_index.html', {
        'categories': [],
        'search_query': search_query,
    })

@csrf_exempt
@require_POST
def api_schedule_fetch_now(request):
    """Fetch schedule events now for a given schedule ID."""
    try:
        data = json.loads(request.body.decode('utf-8'))
        schedule_id = data.get('id')
        sched = Schedule.objects.get(id=schedule_id)
        # Sched type
        if sched.type == 'sched' and sched.slug and sched.year:
            events, error = fetch_sched_events(sched)
            import json as pyjson
            if not error:
                sched.events_json = pyjson.dumps(events)
                sched.error = ''
            else:
                sched.error = error
            sched.save()
            if error == 'sched_slug_missing_or_removed':
                return _guidebook_missing_response(
                    sched.slug,
                    message='sched_slug_missing_or_removed',
                    status=200,
                )
            return JsonResponse({'success': not error, 'error': error if error else None})
        # Guidebook type
        elif sched.type == 'guidebook' and sched.slug:
            # Use the existing api_guidebook logic to fetch and normalize events
            from django.http import HttpRequest
            req = HttpRequest()
            # Provide minimal META to avoid KeyError in Django utilities
            req.META = {
                'SERVER_NAME': 'localhost',
                'HTTP_HOST': 'localhost',
                'wsgi.url_scheme': 'http',
            }
            req.method = 'GET'
            guide_slug = _normalize_guidebook_slug(sched.slug)
            req.GET = QueryDict('', mutable=True)
            req.GET['slug'] = guide_slug
            resp = api_guidebook(req, guide_slug)
            try:
                data = resp.json()
                if 'error' in data:
                    sched.error = data['error']
                else:
                    sched.events_json = json.dumps(data.get('events', []))
                    sched.error = ''
                sched.save()
                return JsonResponse({'success': True})
            except Exception as e:
                sched.error = f'Guidebook fetch error: {str(e)}'
                sched.save()
                return JsonResponse({'error': str(e)})
        elif sched.type == 'pretalx' and sched.slug:
            from archive.pretalx_client import fetch_pretalx_events
            events, error = fetch_pretalx_events(sched)
            if not error:
                sched.events_json = json.dumps(events)
                sched.error = ''
            else:
                sched.error = error
            sched.save()
            return JsonResponse({'success': not error, 'error': error if error else None})
        return JsonResponse({'error': 'Unknown or unsupported schedule type'})
    except Exception as e:
        return JsonResponse({'error': str(e)})
    

@csrf_exempt
@require_POST
def api_schedule_fetch_all(request):
    """Fetch all schedules now (Sched & Guidebook)."""
    updated = 0
    for sched in Schedule.objects.filter(deleted=False):
        try:
            if sched.type == 'guidebook' and sched.slug:
                from django.http import HttpRequest
                req = HttpRequest()
                req.META = {
                    'SERVER_NAME': 'localhost',
                    'HTTP_HOST': 'localhost',
                    'wsgi.url_scheme': 'http',
                }
                req.method = 'GET'
                guide_slug = _normalize_guidebook_slug(sched.slug)
                req.GET = QueryDict('', mutable=True)
                req.GET['slug'] = guide_slug
                req.user = request.user
                resp = api_guidebook(req, guide_slug)
                data = json.loads(resp.content)
                if 'error' not in data:
                    sched.events_json = json.dumps(data.get('events', []))
                    sched.error = ''
                    sched.save()
                    try:
                        _write_schedule_file(sched, data.get('events', []))
                    except Exception:
                        pass
                    updated += 1
                else:
                    sched.error = data['error']
                    sched.save()
            elif sched.type == 'sched' and sched.slug and sched.year:
                events, error = fetch_sched_events(sched)
                if not error:
                    sched.events_json = json.dumps(events)
                    sched.error = ''
                    sched.save()
                    try:
                        _write_schedule_file(sched, events)
                    except Exception:
                        pass
                    updated += 1
                else:
                    sched.error = error
                    sched.save()
            elif sched.type == 'pretalx' and sched.slug:
                from archive.pretalx_client import fetch_pretalx_events
                events, error = fetch_pretalx_events(sched)
                if not error:
                    sched.events_json = json.dumps(events)
                    sched.error = ''
                    sched.save()
                    try:
                        _write_schedule_file(sched, events)
                    except Exception:
                        pass
                    updated += 1
                else:
                    sched.error = error
                    sched.save()
            else:
                continue
        except Exception:
            pass
    return JsonResponse({'success': True, 'updated': updated})

@staff_member_required
def toggle_auto_fetch(request):
    setting, _ = ScheduleAutoFetchSetting.objects.get_or_create(id=1)
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'start':
            setting.enabled = True
            setting.save()
            start_auto_fetch()
        elif action == 'stop':
            setting.enabled = False
            setting.save()
            stop_auto_fetch()
        return redirect('toggle_auto_fetch')
    is_running = setting.enabled
    return render(request, 'archive/toggle_auto_fetch.html', {'is_running': is_running})

def fetch_sched_events(sched):
    """
    Fetch events from Sched.com for a given Schedule instance (type 'sched').
    Returns (events, error_message). Updates nothing in DB.
    """
    import requests, re
    from bs4 import BeautifulSoup
    html_url = f'https://{sched.slug}.sched.com/print/all'
    try:
        response = requests.get(html_url, headers=_sched_request_headers(), timeout=15)
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            # Support multiple Sched markup variants: event/session links, title links, and print/event paths
            anchors = soup.select(
                'a[href*="/event/"], a[href*="/session/"], a[href*="/print/event/"], .title a, a.title'
            ) or []
            # Deduplicate anchors by href+text to avoid repeats
            seen = set()
            unique_anchors = []
            for a in anchors:
                key = (a.get('href') or '').split('?')[0] + '|' + (a.get_text(' ', strip=True) or '')
                if key in seen:
                    continue
                seen.add(key)
                unique_anchors.append(a)
            anchors = unique_anchors
            events = []
            def _find_date(a):
                tr = a.find_parent('tr')
                if tr:
                    bar = tr.find_previous(lambda tag: tag.name == 'tr' and 'bar' in (tag.get('class') or []))
                    if bar:
                        span = bar.find('span')
                        if span:
                            return span.get('id') or span.get_text(strip=True)
                        txt = bar.get_text(' ', strip=True)
                        if txt:
                            return txt
                prev = a
                for _ in range(6):
                    prev = prev.find_previous()
                    if not prev:
                        break
                    txt = (prev.get_text(' ', strip=True) or '').strip()
                    if re.search(r'\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\b', txt, re.I):
                        return txt
                    if re.search(r'\d{4}-\d{2}-\d{2}', txt):
                        return txt
                return 'Unknown date'
            def _find_time(a):
                tr = a.find_parent('tr')
                candidates = []
                if tr:
                    candidates.extend(tr.select('td.time, td'))
                td = a.find_parent('td')
                if td:
                    candidates.extend(td.find_previous_siblings('td'))
                    candidates.extend(td.find_all('div'))
                candidates.append(a)
                time_text = ''
                time_re = re.compile(r'\d{1,2}:?\d{0,2}\s*(?:am|pm|AM|PM)?|\d{1,2}\s*(?:am|pm|AM|PM)')
                for c in candidates:
                    txt = (c.get_text(' ', strip=True) or '').strip()
                    if not txt:
                        continue
                    if any(sep in txt for sep in ['–', '—', '-', ' to ', '\u2013', '\u2014']):
                        return txt
                    if time_re.search(txt):
                        return txt
                return ''
            def _find_location(a):
                td = a.find_parent('td')
                if td:
                    venue = td.find(lambda tag: tag.name in ['div', 'span'] and ('venue' in (tag.get('class') or []) or 'venue' in (tag.get('id') or '').lower()))
                    if venue:
                        return venue.get_text(' ', strip=True)
                    float_venue = td.find(lambda tag: tag.name in ['div', 'span'] and 'float' in (tag.get('style') or ''))
                    if float_venue:
                        return float_venue.get_text(' ', strip=True)
                title_div = a.find_parent(class_='title')
                if title_div:
                    v = title_div.find(lambda tag: tag.name in ['div', 'span'] and 'venue' in (tag.get('class') or []))
                    if v:
                        return v.get_text(' ', strip=True)
                cont = a.find_parent()
                if cont:
                    txt = cont.get_text(' ', strip=True)
                    m = re.search(r"\(([^)]+Hotel[^)]*)\)", txt)
                    if m:
                        return m.group(1)
                return ''
            def _find_description(a):
                # Only use explicit Sched print description containers to avoid false positives
                parent = a.find_parent()
                if not parent:
                    return ''
                node = parent.select_one('.sched-description')
                if not node:
                    return ''
                txt = (node.get_text(' ', strip=True) or '').strip()
                return txt if txt else ''
            for a in anchors:
                href = a.get('href')
                eid = a.get('id') or (href.split('/')[-1] if href else None)
                title = a.get_text(' ', strip=True)
                date_id = _find_date(a)
                time_text = _find_time(a)
                start = end = None
                if time_text:
                    parts = re.split(r'–|—|-| to |\u2013|\u2014', time_text, maxsplit=1)
                    if len(parts) == 2 and re.search(r'\d', parts[0]):
                        start = parts[0].strip()
                        end = parts[1].strip()
                    else:
                        tm = re.search(r'([0-9]{1,2}:?[0-9]{0,2}\s*(?:am|pm|AM|PM)?)', time_text)
                        if tm:
                            start = tm.group(1).strip()
                location = _find_location(a)
                description = _find_description(a)
                events.append({
                    'id': eid,
                    'name': title,
                    'date': date_id,
                    'start_raw': start,
                    'end_raw': end,
                    'location': location,
                    'description': description,
                })
            return events, ''
        if response.status_code == 429:
            cached_events = []
            try:
                if sched.events_json:
                    cached_events = json.loads(sched.events_json) or []
            except Exception:
                cached_events = []
            if cached_events:
                logger.warning('fetch_sched_events: rate limited for %s; using cached events', html_url)
                return cached_events, 'rate_limited_cached'
            return [], f'Failed to fetch events HTML: {response.status_code} (URL: {html_url})'
        if response.status_code in (404, 410):
            return [], 'sched_slug_missing_or_removed'
        else:
            return [], f'Failed to fetch events HTML: {response.status_code} (URL: {html_url})'
    except Exception as e:
        return [], f'Error fetching events HTML: {str(e)}'


def _sched_request_headers():
    return {
        'User-Agent': (
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
            'AppleWebKit/537.3(KHTML, like Gecko) '
            'Chrome/124.0.0.0 Safari/537.36'
        ),
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
        'Referer': 'https://sched.com/',
        'Upgrade-Insecure-Requests': '1',
    }


@require_GET
def schedule_schema_download(request, slug):
    """Server-side schema JSON download for a schedule slug.
    - Loads events from DB `Schedule` or local `schedules/` files.
    - Queries Consurf event API for convention metadata (description/address/timezone) when available.
    """
    try:
        sched_slug = str(slug or '').strip()
        events = []
        schedule_obj = None

        # Try DB lookups (yeared slug or plain slug)
        try:
            import re
            m = re.match(r'([a-z0-9\-]+)-(\d{4})$', sched_slug)
            if m:
                conv_slug, year = m.groups()
                schedule_obj = Schedule.objects.filter(slug__iexact=sched_slug, year=year, deleted=False).first()
                if not schedule_obj:
                    schedule_obj = Schedule.objects.filter(slug__istartswith=conv_slug, year=year, deleted=False).first()
                if not schedule_obj:
                    conv_like = conv_slug.replace('-', ' ')
                    schedule_obj = Schedule.objects.filter(convention_name__icontains=conv_like, year=year, deleted=False).first()
            else:
                schedule_obj = Schedule.objects.filter(slug__iexact=sched_slug, deleted=False).first()
            if schedule_obj and schedule_obj.events_json:
                events = json.loads(schedule_obj.events_json) if schedule_obj.events_json else []
        except Exception:
            schedule_obj = None

        # Fallback: local schedules directory
        if not events:
            try:
                schedules_dir = os.path.join(settings.BASE_DIR, 'schedules')
                if os.path.isdir(schedules_dir):
                    for fn in os.listdir(schedules_dir):
                        if not fn.lower().endswith('.json'):
                            continue
                        fp = os.path.join(schedules_dir, fn)
                        try:
                            with open(fp, 'r', encoding='utf-8') as fh:
                                payload = json.load(fh)
                        except Exception:
                            continue
                        slug_val = str(payload.get('slug') or '').lower().replace('-', '')
                        if (sched_slug or '').lower().replace('-', '') == slug_val:
                            events = payload.get('events', []) or []
                            break
            except Exception:
                pass

        # Normalize times when possible
        try:
            events = _normalize_event_times(events)
        except Exception:
            pass

        # Query Consurf API for metadata (best-effort)
        consurf_desc = ''
        consurf_address = ''
        consurf_tz = ''
        consurf_start = ''
        consurf_end = ''
        try:
            api_base = getattr(settings, 'EXTERNAL_EVENTS_API_BASE', '').rstrip('/') or 'https://consurf.net'
            consurf_url = f"{api_base}/api/external/events/{sched_slug}"
            r = requests.get(consurf_url, timeout=3)
            if r is not None and getattr(r, 'status_code', None) == 200:
                try:
                    cdata = r.json()
                except Exception:
                    cdata = None
                # If API returns a dict with metadata
                if isinstance(cdata, dict):
                    consurf_desc = cdata.get('description') or cdata.get('summary') or ''
                    consurf_address = cdata.get('address') or cdata.get('location') or ''
                    consurf_tz = cdata.get('timezone') or ''
                    # try common start/end keys
                    consurf_start = cdata.get('startDate') or cdata.get('start_date') or cdata.get('start') or cdata.get('starts_at') or ''
                    consurf_end = cdata.get('endDate') or cdata.get('end_date') or cdata.get('end') or cdata.get('ends_at') or ''
                # If API returns a list, take first dict
                elif isinstance(cdata, list) and cdata:
                    first = cdata[0]
                    if isinstance(first, dict):
                        consurf_desc = first.get('description') or first.get('summary') or ''
                        consurf_address = first.get('address') or first.get('location') or ''
                        consurf_tz = first.get('timezone') or ''
                        consurf_start = first.get('startDate') or first.get('start_date') or first.get('start') or first.get('starts_at') or ''
                        consurf_end = first.get('endDate') or first.get('end_date') or first.get('end') or first.get('ends_at') or ''
        except Exception:
            pass

        # Build schema
        try:
            from django.utils import timezone as dj_timezone
            git_sha = os.environ.get('GIT_COMMIT') or os.environ.get('GIT_COMMIT_SHORT') or ''
            if not git_sha:
                try:
                    out = subprocess.check_output(['git', 'rev-parse', '--short', 'HEAD'], cwd=settings.BASE_DIR)
                    git_sha = out.decode('utf-8').strip()
                except Exception:
                    git_sha = ''

            conv_id = sched_slug or ''
            conv_name = (getattr(schedule_obj, 'convention_name', None) or (sched_slug or '').replace('-', ' ').title())

            # determine schedule start/end dates: prefer Consurf, else infer from event start times
            start_date_val = ''
            end_date_val = ''
            try:
                if consurf_start:
                    # normalize to YYYY-MM-DD when possible
                    try:
                        sd = dateparser.parse(consurf_start)
                        start_date_val = sd.date().isoformat()
                    except Exception:
                        start_date_val = str(consurf_start)
                if consurf_end:
                    try:
                        ed = dateparser.parse(consurf_end)
                        end_date_val = ed.date().isoformat()
                    except Exception:
                        end_date_val = str(consurf_end)
            except Exception:
                start_date_val = ''
                end_date_val = ''

            if not start_date_val or not end_date_val:
                # infer from events
                try:
                    starts = []
                    ends = []
                    for ev in events or []:
                        s = ev.get('start') or ev.get('start_local') or ev.get('start_utc')
                        e = ev.get('end') or ev.get('end_local') or ev.get('end_utc')
                        try:
                            if s:
                                ds = dateparser.parse(s)
                                starts.append(ds.date())
                            if e:
                                de = dateparser.parse(e)
                                ends.append(de.date())
                        except Exception:
                            continue
                    if starts and not start_date_val:
                        start_date_val = min(starts).isoformat()
                    if ends and not end_date_val:
                        end_date_val = max(ends).isoformat()
                    # if ends empty but starts present, set end to start
                    if starts and not end_date_val:
                        end_date_val = max(starts).isoformat()
                except Exception:
                    pass

            schema = {
                'schemaVersion': '1.0.0',
                'updatedAt': dj_timezone.now().isoformat(),
                'source': {'name': 'FurryConArchives', 'vendorId': 'FCA-001', 'appVersion': git_sha or ''},
                'convention': {
                    'id': conv_id,
                    'name': {'en-US': conv_name or ''},
                    'description': {'en-US': consurf_desc or ''},
                    'location': {'en-US': consurf_address or ''},
                    'timezone': consurf_tz or '',
                    'startDate': start_date_val,
                    'endDate': end_date_val
                },
                'events': []
            }

            for idx, ev in enumerate(events or []):
                ev_id = ev.get('id') or ev.get('event_id') or f'evt-{idx+1}'
                title = ev.get('title') or ev.get('name') or ''
                desc = ev.get('description') or ev.get('desc') or ''
                start = ev.get('start') or ev.get('start_local') or ev.get('start_utc') or None
                end = ev.get('end') or ev.get('end_local') or ev.get('end_utc') or None
                location = ev.get('location') or ev.get('room') or ev.get('venue') or ''
                item = {
                    'id': str(ev_id),
                    'title': {'en-US': title},
                    'description': {'en-US': desc},
                    'timeSlots': [{'startTime': start, 'endTime': end, 'venueId': '', 'roomId': location}],
                    'typeId': ev.get('type') or '',
                    'trackId': None,
                    'labelIds': [],
                    'hostIds': [],
                    'allowedMemberships': [],
                    'minAge': 0,
                    'ticketed': False,
                    'buttons': [{'name': 'Source', 'url': ev.get('sched_url') or ev.get('url') or ''}] if (ev.get('sched_url') or ev.get('url')) else []
                }
                schema['events'].append(item)

            filename = f"{(conv_id or 'schedule').replace(' ', '_')}-schema.json"
            body = json.dumps(schema, indent=2, ensure_ascii=False)
            resp = HttpResponse(body, content_type='application/json; charset=utf-8')
            resp['Content-Disposition'] = f'attachment; filename="{filename}"'
            return resp
        except Exception as e:
            logger.exception('schedule_schema_download: build failed for %s: %s', sched_slug, e)
            return JsonResponse({'error': 'build_failed', 'message': str(e)}, status=500)

    except Exception as e:
        logger.exception('schedule_schema_download: unexpected failure for %s: %s', slug, e)
        return JsonResponse({'error': 'unexpected', 'message': str(e)}, status=500)

# Text-based PDF search (not OCR)
@csrf_exempt
def pdf_text_search(request, slug):
    document = get_object_or_404(PDFDocument, slug=slug)
    if not document.is_published and not (request.user.is_authenticated and request.user.is_staff):
        raise Http404("Document not found")
    query = request.GET.get('q', '').strip()
    if not query:
        return JsonResponse({'error': 'No search query provided'}, status=400)
    matches = []
    import fitz
    with local_path_for_field_file(document.file) as pdf_path:
        doc = fitz.open(pdf_path)
        for page_num in range(doc.page_count):
            page = doc.load_page(page_num)
            rects = page.search_for(query)
            pdf_width = float(page.rect.width)
            pdf_height = float(page.rect.height)
            for rect in rects:
                matches.append({
                    'page': page_num + 1,
                    'text': query,
                    'x': rect.x0,
                    'y': rect.y0,
                    'width': rect.x1 - rect.x0,
                    'height': rect.y1 - rect.y0,
                    'pdf_width': pdf_width,
                    'pdf_height': pdf_height,
                    'ocr': False
                })

    return JsonResponse({
        'has_text': True,
        'query': query,
        'matches': matches,
        'total_matches': len(matches)
    })
