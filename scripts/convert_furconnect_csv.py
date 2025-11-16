"""Convert a Furconnect schedule CSV export to local schedule import format."""

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'furry_archive.settings')
django.setup()

from archive.furconnect_schedule import furconnect_csv_to_payload
from archive.views import _normalize_event_times


def _slug_from_filename(path: str) -> str:
    base = os.path.splitext(os.path.basename(path))[0]
    base = re.sub(r'(?i)[_\s]+schedule$', '', base).strip()
    return re.sub(r'[^a-z0-9]+', '-', base.lower()).strip('-')


def main():
    if len(sys.argv) < 2:
        print(
            'Usage: python scripts/convert_furconnect_csv.py '
            '<schedule.csv> [slug] [timezone] [out.json]'
        )
        sys.exit(1)

    path = sys.argv[1]
    slug = sys.argv[2] if len(sys.argv) > 2 else _slug_from_filename(path)
    tz_name = sys.argv[3] if len(sys.argv) > 3 else 'America/Los_Angeles'
    out_path = sys.argv[4] if len(sys.argv) > 4 else None

    with open(path, 'r', encoding='utf-8-sig') as fh:
        csv_text = fh.read()

    year = None
    year_match = re.search(r'-(\d{4})$', slug)
    if year_match:
        year = int(year_match.group(1))

    convention_name = slug.replace('-', ' ').replace('_', ' ').title()
    if year:
        convention_name = re.sub(rf'\s*{year}\s*$', '', convention_name).strip() or convention_name

    payload = furconnect_csv_to_payload(
        csv_text,
        slug=slug,
        year=year,
        schedule_type='furconnect',
        timezone=tz_name,
        convention_name=convention_name,
        normalize_times=_normalize_event_times,
    )

    if not out_path:
        out_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            f'local_{slug}_schedule.json',
        )

    with open(out_path, 'w', encoding='utf-8') as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)

    print(
        f'Wrote {payload["count"]} events to {out_path} '
        f'(slug={payload["slug"]}, year={payload["year"]})'
    )


if __name__ == '__main__':
    main()
