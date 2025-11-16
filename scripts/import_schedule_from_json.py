import os
import sys
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'furry_archive.settings')
django.setup()

import json
from django.utils.text import slugify
from archive.models import Schedule, Category


def import_file(path, make_active=True):
    with open(path, 'r', encoding='utf-8') as fh:
        payload = json.load(fh)

    slug = str(payload.get('slug') or '').strip()
    s_type = str(payload.get('type') or 'sched').strip()
    year = payload.get('year')
    events = payload.get('events') or []

    if not slug:
        # derive slug from filename
        slug = slugify(os.path.splitext(os.path.basename(path))[0])

    # Derive convention_name from slug where possible
    convention_name = slug.replace('-', ' ').replace('_', ' ').title()

    # Try to find existing schedule
    sched = None
    try:
        sched = Schedule.objects.filter(type=s_type, slug=slug).first()
    except Exception:
        sched = None

    if not sched:
        sched = Schedule(type=s_type, slug=slug)

    sched.year = int(year) if year else None
    sched.convention_name = convention_name
    try:
        sched.events_json = json.dumps(events)
    except Exception:
        sched.events_json = ''
    sched.deleted = False
    sched.error = ''
    sched.save()

    print(f"Imported schedule: id={sched.id} slug={sched.slug} type={sched.type} year={sched.year} events={len(events)}")


def main():
    if len(sys.argv) < 2:
        print("Usage: python import_schedule_from_json.py /path/to/file.json")
        sys.exit(1)
    path = sys.argv[1]
    if not os.path.isfile(path):
        print(f"File not found: {path}")
        sys.exit(1)
    import_file(path)


if __name__ == '__main__':
    main()
