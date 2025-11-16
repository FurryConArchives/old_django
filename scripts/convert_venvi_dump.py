"""Convert a Venvi Firestore IndexedDB JSON dump to schedule import format."""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'furry_archive.settings')
django.setup()

from archive.venvi_schedule import venvi_dump_to_schedule_payload
from archive.views import _normalize_event_times


def main():
    if len(sys.argv) < 2:
        print('Usage: python scripts/convert_venvi_dump.py <dump.json> [org_id] [app_id] [out.json]')
        sys.exit(1)

    path = sys.argv[1]
    org_id = sys.argv[2] if len(sys.argv) > 2 else None
    app_id = sys.argv[3] if len(sys.argv) > 3 else None
    out_path = sys.argv[4] if len(sys.argv) > 4 else None

    with open(path, 'r', encoding='utf-8') as fh:
        dump = json.load(fh)

    payload = venvi_dump_to_schedule_payload(
        dump,
        org_id,
        app_id,
        normalize_times=_normalize_event_times,
    )

    if not out_path:
        base, _ = os.path.splitext(path)
        out_path = f'{base}_schedule.json'

    with open(out_path, 'w', encoding='utf-8') as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)

    print(f'Wrote {payload["count"]} events to {out_path} (slug={payload["slug"]}, year={payload["year"]})')


if __name__ == '__main__':
    main()
