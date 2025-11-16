from django.contrib import admin
from django.utils.html import format_html
from django.urls import reverse
from django.db.models import Count
from django import forms

import secrets
from .models import Category, Tag, PDFDocument, DownloadLog, Schedule, AppKey, DiscordProfile, KoFiDonation, SiteBanner, OurFriend, FaqEntry, ContactChannel, SitePageSection
from .admin_physical import *
from django.conf import settings
from django.utils.text import slugify
from pathlib import Path
import os, json

def _write_schedule_file(sched_obj, events_list):
    try:
        base = Path(getattr(settings, 'BASE_DIR', '.'))
        out_dir = base / 'schedules'
        out_dir.mkdir(parents=True, exist_ok=True)

        slug_val = str(sched_obj.slug or sched_obj.convention_name or '')
        if getattr(sched_obj, 'type', '') == 'pretalx':
            from .pretalx_client import schedule_route_slug
            slug_val = schedule_route_slug(sched_obj)
        safe = slugify(slug_val)
        if not safe:
            safe = slugify(str(sched_obj.convention_name or 'schedule'))

        year = str(getattr(sched_obj, 'year', '') or '')
        fname = f"{sched_obj.type}_{safe}_{year}.json"
        path = out_dir / fname
        with open(path, 'w', encoding='utf-8') as fh:
            json.dump({
                'slug': slug_val,
                'type': sched_obj.type,
                'year': year,
                'convention_name': getattr(sched_obj, 'convention_name', ''),
                'events': events_list,
            }, fh, ensure_ascii=False, indent=2)
        return str(path)
    except Exception:
        return None
# Customize admin site header
admin.site.site_header = 'Furry Con Archives Administration'
admin.site.site_title = 'Furry Archives Admin'
admin.site.index_title = 'Archive Management'

# Register Tag model
admin.site.register(Tag)

# Custom Schedule admin tab
@admin.register(Schedule)
class ScheduleAdmin(admin.ModelAdmin):
    actions = ['fetch_now', 'fetch_all_now']
    
    class ScheduleAdminForm(forms.ModelForm):
        # allow admins to upload a local JSON schedule file from their desktop
        json_file = forms.FileField(required=False, label='Upload schedule JSON',
                                    help_text='Choose a local schedule JSON file to import events (overrides API fetch).')
        csv_file = forms.FileField(required=False, label='Upload Furconnect CSV',
                                   help_text='Choose a Furconnect schedule CSV export to import events.')
        furconnect_url = forms.CharField(
            required=False,
            label='Furconnect source URL',
            help_text='Public Furconnect schedule URL (e.g. https://events.furlingame.com).',
        )

        class Meta:
            model = Schedule
            fields = '__all__'

        def clean(self):
            cleaned = super().clean()
            t = cleaned.get('type')
            slug = cleaned.get('slug')
            if t == 'sessionize' and slug:
                s = str(slug).strip()
                # If user provided a hostname containing sessionize but no scheme, add https://
                if ('sessionize.com' in s) and not (s.startswith('http://') or s.startswith('https://')):
                    s = 'https://' + s
                cleaned['slug'] = s
            if t == 'pretalx' and slug:
                from .pretalx_client import storage_fields_for_pretalx_input, PretalxConfigError
                try:
                    cleaned['slug'] = storage_fields_for_pretalx_input(str(slug))
                except PretalxConfigError:
                    pass
            if t == 'furconnect' and slug:
                from .furconnect_schedule import (
                    decode_furconnect_storage,
                    furconnect_route_slug,
                    storage_fields_for_furconnect_input,
                )
                furconnect_url = cleaned.get('furconnect_url') or ''
                route_slug = furconnect_route_slug(str(slug))
                if furconnect_url:
                    try:
                        cleaned['slug'] = storage_fields_for_furconnect_input(route_slug, furconnect_url)
                    except ValueError:
                        pass
                elif not decode_furconnect_storage(str(slug)):
                    cleaned['slug'] = route_slug
            return cleaned

    form = ScheduleAdminForm

    def fetch_now(self, request, queryset):
        from .views import api_guidebook
        from django.http import HttpRequest

        updated = 0
        for obj in queryset:
            try:
                if obj.type == 'guidebook' and obj.slug:
                    req = HttpRequest()
                    req.META = {
                        'SERVER_NAME': 'localhost',
                        'HTTP_HOST': 'localhost',
                        'wsgi.url_scheme': 'http',
                    }
                    req.method = 'GET'
                    from django.http import QueryDict
                    req.GET = QueryDict('', mutable=True)
                    req.GET['slug'] = obj.slug
                    req.user = request.user
                    resp = api_guidebook(req, obj.slug)
                    import json
                    data = json.loads(resp.content)
                    if 'error' not in data:
                        obj.events_json = json.dumps(data.get('events', []))
                        obj.error = ''
                        obj.save()
                        try:
                            _write_schedule_file(obj, data.get('events', []))
                        except Exception:
                            pass
                        updated += 1
                    else:
                        obj.error = data['error']
                        obj.save()
                else:
                    self.save_model(request, obj, None, True)
                    obj.save()
                    updated += 1
            except Exception as e:
                self.message_user(request, f"Error fetching schedule for {obj}: {e}", level='error')
        self.message_user(request, f"Fetched {updated} schedule(s) now.")
    fetch_now.short_description = "Fetch selected schedules now from Sched.com"

    def fetch_all_now(self, request, queryset):
        from .views import api_schedule_fetch_now, api_guidebook
        from django.http import HttpRequest
        all_schedules = Schedule.objects.filter(deleted=False)
        updated = 0
        for sched in all_schedules:
            try:
                if sched.type == 'guidebook' and sched.slug:
                    req = HttpRequest()
                    req.META = {
                        'SERVER_NAME': 'localhost',
                        'HTTP_HOST': 'localhost',
                        'wsgi.url_scheme': 'http',
                    }
                    req.method = 'GET'
                    from django.http import QueryDict
                    req.GET = QueryDict('', mutable=True)
                    req.GET['slug'] = sched.slug
                    req.user = request.user
                    resp = api_guidebook(req, sched.slug)
                    import json
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
                elif sched.type == 'sessionize' and sched.slug:
                    try:
                        import requests, json
                        # Build URL: accept full URL or hostname
                        slug_val = str(sched.slug).strip()
                        if slug_val.startswith('http://') or slug_val.startswith('https://'):
                            url = slug_val
                        else:
                            # If slug is a bare short name (no dot), assume sessionize subdomain
                            if '.' in slug_val:
                                host = slug_val
                            else:
                                host = f"{slug_val}.sessionize.com"
                            url = f'https://{host}/api/schedule'

                        resp = requests.get(url, timeout=15)
                        if resp.status_code == 200:
                            payload = resp.json()
                            # normalize to list of sessions/events
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

                            events = []
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

                                    # Try to detect an explicit organizer/owner field
                                    organizer = None
                                    for ok in ('organizers', 'organizer', 'owners', 'owner', 'presenters', 'presenter', 'host', 'hosts'):
                                        val = ev.get(ok)
                                        if val:
                                            if isinstance(val, list):
                                                # Map ids if present
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

                                    # Fallback: use first speaker if present
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
                            sched.events_json = json.dumps(events)
                            sched.error = ''
                            sched.save()
                            try:
                                _write_schedule_file(sched, events)
                            except Exception:
                                pass
                            updated += 1
                        else:
                            sched.events_json = ''
                            sched.error = f'Failed to fetch Sessionize JSON: {resp.status_code} (URL: {url})'
                            sched.save()
                    except Exception as e:
                        sched.events_json = ''
                        sched.error = f'Error fetching Sessionize data: {str(e)}'
                        sched.save()
                elif sched.type == 'pretalx' and sched.slug:
                    from .pretalx_client import fetch_pretalx_events
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
                        sched.events_json = ''
                        sched.error = error
                        sched.save()
                else:
                    from .views import api_schedule_fetch_now
                    req = HttpRequest()
                    req.method = 'POST'
                    req._dont_enforce_csrf_checks = True
                    req.POST = {'id': str(sched.id)}
                    req.user = request.user
                    resp = api_schedule_fetch_now(req)
                    import json
                    data = json.loads(resp.content)
                    if data.get('success'):
                        updated += 1
            except Exception as e:
                self.message_user(request, f"Error fetching schedule for {sched}: {e}", level='error')
        self.message_user(request, f"Fetched {updated} schedule(s) now.")
    fetch_all_now.short_description = "Fetch ALL schedules now (Sched & Guidebook)"

    def save_model(self, request, obj, form, change):
        # Allow admins to upload a local JSON file to import events (overrides API fetch)
        import requests, json

        # If the admin supplied a JSON file in the form, use that and skip remote fetch
        if form and hasattr(form, 'cleaned_data'):
            uploaded = form.cleaned_data.get('json_file')
            if uploaded:
                try:
                    raw = uploaded.read()
                    if isinstance(raw, bytes):
                        raw = raw.decode('utf-8')
                    payload = json.loads(raw)

                    # Accept either an array of events or a dict containing 'events'
                    if isinstance(payload, list):
                        events = payload
                    elif isinstance(payload, dict):
                        # support full schedule file shape { 'events': [...] }
                        events = payload.get('events') or payload.get('schedule')
                        if events is None:
                            # maybe the dict itself is a single event
                            events = [payload]
                    else:
                        raise ValueError('Unsupported JSON structure')

                    if obj.type == 'local' or (isinstance(payload, dict) and payload.get('type') == 'local'):
                        from archive.local_schedule import apply_local_schedule_meta
                        if isinstance(payload, dict):
                            apply_local_schedule_meta(obj, payload)

                    # Write into the Schedule object and persist
                    obj.events_json = json.dumps(events, ensure_ascii=False)
                    obj.error = ''
                    obj.deleted = False
                    obj.save()
                    try:
                        _write_schedule_file(obj, events)
                    except Exception:
                        pass
                    self.message_user(request, f"Imported {len(events)} events from uploaded JSON.")
                    return
                except Exception as e:
                    self.message_user(request, f"Failed to import JSON file: {e}", level='error')
                    # fall through to normal behavior (attempt remote fetch) if upload fails

            uploaded_csv = form.cleaned_data.get('csv_file')
            if uploaded_csv and obj.type == 'furconnect':
                try:
                    from archive.furconnect_schedule import furconnect_csv_to_events
                    from archive.views import _normalize_event_times
                    raw = uploaded_csv.read()
                    if isinstance(raw, bytes):
                        raw = raw.decode('utf-8-sig')
                    events = furconnect_csv_to_events(
                        raw,
                        timezone='America/Los_Angeles',
                        normalize_times=_normalize_event_times,
                    )
                    obj.events_json = json.dumps(events, ensure_ascii=False)
                    obj.error = ''
                    obj.deleted = False
                    obj.save()
                    try:
                        _write_schedule_file(obj, events)
                    except Exception:
                        pass
                    self.message_user(request, f"Imported {len(events)} events from uploaded Furconnect CSV.")
                    return
                except Exception as e:
                    self.message_user(request, f"Failed to import Furconnect CSV: {e}", level='error')

        # Fetch events from Sched API and store in events_json
        if obj.type == 'sched' and obj.slug and getattr(obj, 'year', None):
            try:
                # Delegate Sched HTML parsing to the shared helper in views.py
                from .views import fetch_sched_events
                events, err = fetch_sched_events(obj)
                if err:
                    obj.events_json = json.dumps([])
                    obj.error = err
                else:
                    obj.events_json = json.dumps(events, ensure_ascii=False)
                    obj.error = ''
                    try:
                        _write_schedule_file(obj, events)
                    except Exception:
                        pass
            except Exception as e:
                obj.events_json = ''
                obj.error = f'Error fetching Sched data: {str(e)}'
        elif obj.type == 'sessionize' and obj.slug:
            try:
                slug_val = str(obj.slug).strip()
                if slug_val.startswith('http://') or slug_val.startswith('https://'):
                    url = slug_val
                else:
                    if '.' in slug_val:
                        host = slug_val
                    else:
                        host = f"{slug_val}.sessionize.com"
                    url = f'https://{host}/api/schedule'

                resp = requests.get(url, timeout=15)
                if resp.status_code == 200:
                    payload = resp.json()
                    # normalize to list of sessions/events
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

                    events = []
                    if source_events:
                        # optional speaker/room maps
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
                            events.append({
                                'id': ev.get('id'),
                                'name': title,
                                'description': desc,
                                'start': start,
                                'end': end,
                                'speakers': spks,
                                'location': room,
                            })
                    obj.events_json = json.dumps(events)
                    obj.error = ''
                    try:
                        _write_schedule_file(obj, events)
                    except Exception:
                        pass
                else:
                    obj.events_json = ''
                    obj.error = f'Failed to fetch Sessionize JSON: {resp.status_code} (URL: {url})'
            except Exception as e:
                obj.events_json = ''
                obj.error = f'Error fetching Sessionize data: {str(e)}'
        elif obj.type == 'pretalx' and obj.slug:
            try:
                from .pretalx_client import fetch_pretalx_events
                events, error = fetch_pretalx_events(obj)
                if error:
                    obj.events_json = json.dumps([])
                    obj.error = error
                else:
                    obj.events_json = json.dumps(events, ensure_ascii=False)
                    obj.error = ''
                    try:
                        _write_schedule_file(obj, events)
                    except Exception:
                        pass
            except Exception as e:
                obj.events_json = ''
                obj.error = f'Error fetching Pretalx data: {str(e)}'
        elif obj.type == 'guidebook' and obj.slug:
            try:
                from .views import api_guidebook
                from django.http import HttpRequest
                req = HttpRequest()
                req.META = {
                    'SERVER_NAME': 'localhost',
                    'HTTP_HOST': 'localhost',
                    'wsgi.url_scheme': 'http',
                }
                req.method = 'GET'
                req.GET = {'slug': obj.slug}
                req.user = request.user
                resp = api_guidebook(req, obj.slug)
                data = json.loads(resp.content)
                if 'error' not in data:
                    obj.events_json = json.dumps(data.get('events', []))
                    obj.error = ''
                    try:
                        _write_schedule_file(obj, data.get('events', []))
                    except Exception:
                        pass
                else:
                    obj.events_json = ''
                    obj.error = data['error']
            except Exception as e:
                obj.events_json = ''
                obj.error = f'Error fetching Guidebook data: {str(e)}'
        super().save_model(request, obj, form, change)

    list_display = ['type', 'category', 'year', 'convention_name', 'slug', 'last_updated', 'deleted', 'error_short']
    list_filter = ['type', 'category', 'year', 'deleted']
    search_fields = ['slug', 'convention_name', 'error', 'year', 'category__name']
    readonly_fields = ['last_updated', 'events_json', 'error']
    fieldsets = (
        ('Schedule Info', {
            'fields': ('type', 'category', 'year', 'convention_name', 'slug', 'last_updated', 'deleted', 'error', 'events_json'),
            'description': 'Manage cached schedules for conventions. Events are stored as JSON.'
        }),
    )

    def error_short(self, obj):
        if obj.error:
            return obj.error[:80] + ('...' if len(obj.error) > 80 else '')
        return '-'
    error_short.short_description = 'Error (truncated)'

@admin.register(DownloadLog)
class DownloadLogAdmin(admin.ModelAdmin):
    list_display = ['document', 'user', 'downloaded_at', 'ip_address']
    list_filter = ['downloaded_at', 'document']
    search_fields = ['document__title', 'user__username', 'ip_address']
    readonly_fields = ['document', 'user', 'downloaded_at', 'ip_address']
    date_hierarchy = 'downloaded_at'
    
    def has_add_permission(self, request):
        return False
    
    def has_change_permission(self, request, obj=None):
        return False


class AppKeyAdminForm(forms.ModelForm):
    scopes = forms.MultipleChoiceField(
        choices=AppKey.SCOPE_CHOICES,
        widget=forms.CheckboxSelectMultiple,
        required=False,
        help_text='API route scopes plus the convention dashboard. Dashboard access is not rate limited.',
    )
    rate_per_minute = forms.IntegerField(
        required=False,
        min_value=1,
        help_text='API requests per minute for this key. Leave blank for unlimited. Ignored for the convention dashboard.',
    )

    class Meta:
        model = AppKey
        fields = (
            'name',
            'convention',
            'key',
            'created_by',
            'active',
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.initial['scopes'] = self.instance.scopes
            self.initial['rate_per_minute'] = self.instance.rate_per_minute

    def save(self, commit=True):
        obj = super().save(commit=False)
        obj.set_access(self.cleaned_data.get('scopes'), self.cleaned_data.get('rate_per_minute'))
        if commit:
            obj.save()
            self.save_m2m()
        return obj


@admin.register(AppKey)
class AppKeyAdmin(admin.ModelAdmin):
    form = AppKeyAdminForm
    list_display = ('name', 'scope_display', 'rate_display', 'convention', 'key_preview', 'created_by', 'created_at', 'active')
    readonly_fields = ('key', 'created_at')
    fields = ('name', 'convention', 'scopes', 'rate_per_minute', 'key', 'created_by', 'active', 'created_at')
    search_fields = ('name', 'key', 'convention__name')
    list_filter = ('active', 'created_at')

    def key_preview(self, obj):
        return obj.key[:12] + '...' if obj and obj.key else ''
    key_preview.short_description = 'Key'

    def scope_display(self, obj):
        if not obj:
            return '-'
        labels = obj.scope_labels()
        return ', '.join(labels) if labels else '-'
    scope_display.short_description = 'Scopes'

    def rate_display(self, obj):
        if not obj or not obj.rate_per_minute:
            return 'Unlimited'
        return f'{obj.rate_per_minute}/min'
    rate_display.short_description = 'API rate'

    def save_model(self, request, obj, form, change):
        if not obj.key:
            obj.key = secrets.token_urlsafe(48)
        if not obj.created_by:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(DiscordProfile)
class DiscordProfileAdmin(admin.ModelAdmin):
    list_display = ('discord_username', 'discord_userid', 'last_updated', 'kofi_donated_at', 'kofi_amount')
    search_fields = ('discord_username', 'discord_userid')
    readonly_fields = ('last_updated',)
    fields = ('discord_userid', 'discord_username', 'avatar_url', 'kofi_donated_at', 'kofi_amount', 'last_updated')


@admin.register(KoFiDonation)
class KoFiDonationAdmin(admin.ModelAdmin):
    list_display = ('name', 'amount', 'discord_profile', 'created_at')
    list_filter = ('created_at',)
    search_fields = ('name', 'discord_profile__discord_username', 'discord_profile__discord_userid')
    readonly_fields = ('created_at',)
    fields = ('discord_profile', 'name', 'amount', 'avatar_url', 'created_at')


@admin.register(SiteBanner)
class SiteBannerAdmin(admin.ModelAdmin):
    list_display = ('text', 'color_preview', 'is_enabled', 'updated_at', 'updated_by')
    list_filter = ('is_enabled', 'updated_at')
    search_fields = ('text', 'color')
    readonly_fields = ('updated_at',)
    fields = ('text', 'url', 'color', 'is_enabled', 'updated_by', 'updated_at')

    def color_preview(self, obj):
        color = obj.color or '#0066cc'
        return format_html(
            '<span style="display:inline-block;width:16px;height:16px;border-radius:50%;background:{};margin-right:6px;border:1px solid #777;"></span>{}',
            color,
            color,
        )
    color_preview.short_description = 'Color'

    def save_model(self, request, obj, form, change):
        if request.user and request.user.is_authenticated:
            obj.updated_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(FaqEntry)
class FaqEntryAdmin(admin.ModelAdmin):
    list_display = ('question', 'slug', 'order', 'is_published', 'updated_at')
    list_editable = ('order', 'is_published')
    list_filter = ('is_published',)
    search_fields = ('question', 'answer', 'slug')
    prepopulated_fields = {'slug': ('question',)}
    ordering = ('order', 'id')
    readonly_fields = ('created_at', 'updated_at')
    fields = ('question', 'slug', 'answer', 'icon', 'order', 'is_published', 'created_at', 'updated_at')


@admin.register(ContactChannel)
class ContactChannelAdmin(admin.ModelAdmin):
    list_display = ('label', 'value', 'url', 'order', 'is_published', 'updated_at')
    list_editable = ('order', 'is_published')
    list_filter = ('is_published',)
    search_fields = ('label', 'value', 'url', 'slug')
    prepopulated_fields = {'slug': ('label',)}
    ordering = ('order', 'id')
    readonly_fields = ('created_at', 'updated_at')
    fields = ('label', 'slug', 'value', 'url', 'icon', 'style_key', 'order', 'is_published', 'created_at', 'updated_at')


@admin.register(SitePageSection)
class SitePageSectionAdmin(admin.ModelAdmin):
    list_display = ('title', 'page_slug', 'slug', 'order', 'is_published', 'updated_at')
    list_editable = ('order', 'is_published')
    list_filter = ('page_slug', 'is_published')
    search_fields = ('title', 'body', 'slug')
    prepopulated_fields = {'slug': ('title',)}
    ordering = ('page_slug', 'order', 'id')
    readonly_fields = ('created_at', 'updated_at')
    fields = ('page_slug', 'title', 'slug', 'body', 'order', 'is_published', 'created_at', 'updated_at')


@admin.register(OurFriend)
class OurFriendAdmin(admin.ModelAdmin):
    list_display = ('name', 'subtitle', 'url', 'telegram_username', 'order', 'is_enabled', 'updated_at')
    list_editable = ('order', 'is_enabled')
    list_filter = ('is_enabled',)
    search_fields = ('name', 'subtitle', 'url', 'telegram_username')
    ordering = ('order', 'name')
    readonly_fields = ('created_at', 'updated_at')
    fields = ('name', 'subtitle', 'url', 'telegram_username', 'order', 'is_enabled', 'created_at', 'updated_at')

# PDFDocument admin registration with takedown fields
@admin.register(PDFDocument)
class PDFDocumentAdmin(admin.ModelAdmin):
    list_display = [
        'title', 'year', 'convention_name', 'category', 'is_published', 'takedown_by_request'
    ]
    list_filter = ['is_published', 'takedown_by_request', 'category', 'year', 'convention_name']
    search_fields = ['title', 'description', 'convention_name', 'author', 'donated_by']
    readonly_fields = ['download_count', 'uploaded_by', 'uploaded_at', 'updated_at']
    fieldsets = (
        (None, {
            'fields': (
                'title', 'slug', 'file', 'description', 'category', 'year', 'convention_name', 'author',
                'donated_by', 'donated_by_telegram', 'contributor_notes',
                'creative_commons_license', 'copyright_holder', 'copyright_year',
                'is_scanned', 'is_published', 'takedown_by_request', 'takedown_explanation',
                'download_count', 'uploaded_by', 'uploaded_at', 'updated_at'
            )
        }),
    )