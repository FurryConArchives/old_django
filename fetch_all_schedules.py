import os
import django

def main():
    # Setup Django environment
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'furry_archive.settings')
    django.setup()

    from archive.models import Schedule
    import json
    # Import views only after Django setup to avoid import errors
    from archive import views

    updated = 0
    all_schedules = Schedule.objects.filter(deleted=False)
    # Ensure output directory for final schedule JSON files
    schedules_dir = os.path.join(os.path.dirname(__file__), 'schedules')
    os.makedirs(schedules_dir, exist_ok=True)
    print(f"Found {all_schedules.count()} schedules (deleted=False)")
    for sched in all_schedules:
        print(f"Schedule: id={sched.id}, slug={sched.slug}, type={sched.type}, error={sched.error}")
    for sched in all_schedules:
        try:
            if sched.type == 'guidebook' and sched.slug:
                from django.http import HttpRequest
                req = HttpRequest()
                # Provide minimal META to avoid KeyError in Django utilities
                req.META = {
                    'SERVER_NAME': 'localhost',
                    'HTTP_HOST': 'localhost',
                    'wsgi.url_scheme': 'http',
                }
                req.method = 'GET'
                req.GET = {'slug': sched.slug}
                from django.contrib.auth import get_user_model
                User = get_user_model()
                staff_user = User.objects.filter(is_staff=True).first()
                req.user = staff_user
                resp = views.api_guidebook(req, sched.slug)
                print(f"Guidebook resp.content: {resp.content}")
                data = json.loads(resp.content)
                print(f"Guidebook data keys: {list(data.keys())}")

                # Helper: try to find an events-like list inside a Guidebook hydration/state blob
                def _extract_events_from_state(state_obj):
                    if not state_obj:
                        return []

                    event_like_keys = {'name', 'title', 'start_date', 'startTime', 'start', 'date', 'id', 'description'}
                    candidates = []

                    def walk(obj):
                        if isinstance(obj, dict):
                            for v in obj.values():
                                walk(v)
                        elif isinstance(obj, list):
                            # If this is a list of dicts, test the first item for event-like keys
                            if obj and isinstance(obj[0], dict):
                                sample = obj[0]
                                if any(k in sample for k in event_like_keys):
                                    candidates.append(obj)
                                else:
                                    # still descend into items
                                    for it in obj:
                                        walk(it)
                            else:
                                for it in obj:
                                    walk(it)

                    walk(state_obj)
                    if not candidates:
                        return []
                    # Prefer the largest candidate (most items)
                    return max(candidates, key=lambda x: len(x))

                # Clear any prior error before trying
                sched.error = ''
                sched.save()

                if data.get('error'):
                    sched.events_json = ''
                    sched.error = data.get('error')
                    sched.save()
                else:
                    # Prefer explicit 'events' when present (bundle path)
                    events = data.get('events')
                    if events is None:
                        # Try source-specific payloads
                        if data.get('state'):
                            events = _extract_events_from_state(data.get('state'))
                        elif data.get('source') == 'hydration' and data.get('state'):
                            events = _extract_events_from_state(data.get('state'))
                        else:
                            events = []

                    try:
                        # For Guidebook schedules we must only use the Guidebook API
                        if not events:
                            sched.events_json = ''
                            sched.error = 'no_events_found_from_guidebook'
                            sched.save()
                        else:
                            sched.events_json = json.dumps(events)
                            sched.error = ''
                            sched.save()
                            updated += 1

                            # Write final schedule JSON to disk
                            filename = f"{sched.type}_{sched.slug}_{sched.year or 'none'}.json"
                            filename = filename.replace(' ', '_')
                            out_path = os.path.join(schedules_dir, filename)
                            payload = {
                                'slug': sched.slug,
                                'type': sched.type,
                                'year': sched.year,
                                'count': len(events),
                                'events': events,
                            }
                            try:
                                with open(out_path, 'w', encoding='utf-8') as fh:
                                    json.dump(payload, fh, ensure_ascii=False, indent=2)
                            except Exception as e:
                                sched.error = f"write_error: {e}"
                                sched.save()
                    except Exception as e:
                        sched.events_json = ''
                        sched.error = f"serialize_error: {e}"
                        sched.save()
            elif sched.type == 'sched' and sched.slug and sched.year:
                events, error = views.fetch_sched_events(sched)
                if not error:
                    sched.events_json = json.dumps(events)
                    sched.error = ''
                    sched.save()
                    updated += 1
                    # Write final schedule JSON to disk
                    filename = f"{sched.type}_{sched.slug}_{sched.year or 'none'}.json"
                    filename = filename.replace(' ', '_')
                    out_path = os.path.join(schedules_dir, filename)
                    payload = {
                        'slug': sched.slug,
                        'type': sched.type,
                        'year': sched.year,
                        'count': len(events),
                        'events': events,
                    }
                    try:
                        with open(out_path, 'w', encoding='utf-8') as fh:
                            json.dump(payload, fh, ensure_ascii=False, indent=2)
                    except Exception as e:
                        sched.error = f"write_error: {e}"
                        sched.save()
                else:
                    sched.events_json = ''
                    sched.error = error
                    sched.save()
            elif sched.type == 'sessionize' and sched.slug:
                try:
                    import requests
                    # Build URL: accept full URL or hostname
                    slug_val = str(sched.slug).strip()
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

                        sched.events_json = json.dumps(events)
                        sched.error = ''
                        sched.save()
                        updated += 1

                        # Write final schedule JSON to disk
                        filename = f"{sched.type}_{sched.slug}_{sched.year or 'none'}.json"
                        filename = filename.replace(' ', '_')
                        out_path = os.path.join(schedules_dir, filename)
                        payload = {
                            'slug': sched.slug,
                            'type': sched.type,
                            'year': sched.year,
                            'count': len(events),
                            'events': events,
                        }
                        try:
                            with open(out_path, 'w', encoding='utf-8') as fh:
                                json.dump(payload, fh, ensure_ascii=False, indent=2)
                        except Exception as e:
                            sched.error = f"write_error: {e}"
                            sched.save()
                    else:
                        sched.events_json = ''
                        sched.error = f'Failed to fetch Sessionize JSON: {resp.status_code} (URL: {url})'
                        sched.save()
                except Exception as e:
                    sched.events_json = ''
                    sched.error = f'Error fetching Sessionize data: {str(e)}'
                    sched.save()
            elif sched.type == 'pretalx' and sched.slug:
                from archive.pretalx_client import fetch_pretalx_events
                events, error = fetch_pretalx_events(sched)
                if not error:
                    sched.events_json = json.dumps(events)
                    sched.error = ''
                    sched.save()
                    updated += 1

                    filename = f"{sched.type}_{sched.slug}_{sched.year or 'none'}.json"
                    filename = filename.replace(' ', '_').replace('/', '_').replace(':', '_')
                    out_path = os.path.join(schedules_dir, filename)
                    payload = {
                        'slug': sched.slug,
                        'type': sched.type,
                        'year': sched.year,
                        'count': len(events),
                        'events': events,
                    }
                    try:
                        with open(out_path, 'w', encoding='utf-8') as fh:
                            json.dump(payload, fh, ensure_ascii=False, indent=2)
                    except Exception as e:
                        sched.error = f"write_error: {e}"
                        sched.save()
                else:
                    sched.events_json = ''
                    sched.error = error
                    sched.save()
            else:
                print(f"Unsupported schedule type or missing slug/year: {sched}")
        except Exception as e:
            print(f"Error fetching schedule for {sched}: {e}")
    print(f"Fetched {updated} schedule(s) now.")

if __name__ == "__main__":
    main()
