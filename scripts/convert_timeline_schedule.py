"""Convert a timeline-style schedule JSON file to local schedule import format."""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'furry_archive.settings')
django.setup()

from archive.timeline_schedule import timeline_schedule_to_payload
from archive.views import _normalize_event_times


def main():
    if len(sys.argv) < 2:
        print(
            'Usage: python scripts/convert_timeline_schedule.py '
            '<eventsSchedule.json> [slug] [timezone] [out.json]'
        )
        sys.exit(1)

    path = sys.argv[1]
    slug = sys.argv[2] if len(sys.argv) > 2 else 'bah-2026'
    tz_name = sys.argv[3] if len(sys.argv) > 3 else 'Asia/Kuala_Lumpur'
    out_path = sys.argv[4] if len(sys.argv) > 4 else None

    with open(path, 'r', encoding='utf-8') as fh:
        data = json.load(fh)

    import re

    year = None
    year_match = re.search(r'-(\d{4})$', slug)
    if year_match:
        year = int(year_match.group(1))

    payload = timeline_schedule_to_payload(
        data,
        slug=slug,
        year=year,
        timezone=tz_name,
        normalize_times=_normalize_event_times,
    )

    if not out_path:
        base, _ = os.path.splitext(path)
        out_path = f'{base}_local_schedule.json'
        if out_path == f'{base}.json':
            out_path = f'{base}_schedule.json'

    with open(out_path, 'w', encoding='utf-8') as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)

    print(
        f'Wrote {payload["count"]} events to {out_path} '
        f'(slug={payload["slug"]}, year={payload["year"]})'
    )


if __name__ == '__main__':
    main()
