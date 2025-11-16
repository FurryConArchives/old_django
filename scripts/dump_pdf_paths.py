#!/usr/bin/env python3
"""dump_pdf_paths.py

Print the filesystem path of every published PDFDocument file in the
database.  Useful for auditing, migration, or feeding into other tools.

Usage:
    python scripts/dump_pdf_paths.py          # all published documents
    python scripts/dump_pdf_paths.py --all    # include unpublished too
    python scripts/dump_pdf_paths.py --csv    # output CSV with id,slug,path
"""

import os
import sys
import argparse

# make sure project root is importable; script may be run from anywhere
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'furry_archive.settings')
django.setup()

from archive.models import PDFDocument


def main():
    parser = argparse.ArgumentParser(description='Dump PDFDocument file paths from DB')
    parser.add_argument('--all', action='store_true', help='include non-published documents')
    parser.add_argument('--csv', action='store_true', help='output CSV id,slug,path')
    parser.add_argument('--settings', help='Django settings module to use')
    args = parser.parse_args()

    # allow overriding settings module
    if args.settings:
        os.environ.setdefault('DJANGO_SETTINGS_MODULE', args.settings)

    # print connection info for debugging if things go wrong
    from django.db import connection
    print('using DB settings:', connection.settings_dict)

    qs = PDFDocument.objects.all() if args.all else PDFDocument.objects.filter(is_published=True)
    for doc in qs.order_by('id'):
        try:
            path = doc.file.path
        except Exception:
            path = ''
        if args.csv:
            print(f'{doc.id},{doc.slug},{path}')
        else:
            print(path)


if __name__ == '__main__':
    main()
