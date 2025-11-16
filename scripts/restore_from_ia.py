#!/usr/bin/env python3
"""
restore_from_ia.py

Reverse of ``backup_to_ia.py``  download items previously uploaded to
archive.org and repopulate the local PDFDocument records and media files.

Usage examples:

    # dryrun, just report what would be downloaded
    python scripts/restore_from_ia.py --dry-run

    # download everything in collection "community-texts"
    python scripts/restore_from_ia.py --collection community-texts

    # restore only documents whose IA identifier matches a slug/id in the
    # database; skip files that already exist locally
    python scripts/restore_from_ia.py --from-db --skip-existing

    # force redownload even if file exists
    python scripts/restore_from_ia.py --from-db --force

Requirements:
    * internetarchive installed and IA_ACCESS_KEY / IA_SECRET_KEY
      configured (see ``backup_to_ia.py`` comments).
    * django settings module available (defaults to furry_archive.settings).
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from typing import Dict, List, Optional

try:
    import pymysql
    pymysql.install_as_MySQLdb()
except ImportError:
    # PyMySQL not installed; nothing to do
    pass

LOG = logging.getLogger("restore_from_ia")

STATE_FILE = os.path.join(os.path.dirname(__file__), ".restore_state.json")
DEFAULT_COLLECTION = os.environ.get("IA_COLLECTION", "community-texts")


def ensure_django(settings_module: Optional[str] = None) -> None:
    if settings_module:
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", settings_module)
    else:
        os.environ.setdefault(
            "DJANGO_SETTINGS_MODULE",
            os.environ.get("DJANGO_SETTINGS_MODULE", "furry_archive.settings"),
        )
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    import django  # noqa: E402

    django.setup()


def load_ia_credentials_from_settings() -> None:
    try:
        from django.conf import settings as djsettings  # noqa: E402
    except Exception:
        LOG.debug("Django settings unavailable; skipping IA credential load")
        return

    ia_access = getattr(djsettings, "IA_ACCESS_KEY", None)
    ia_secret = getattr(djsettings, "IA_SECRET_KEY", None)
    ia_collection = getattr(djsettings, "IA_COLLECTION", None)
    if ia_access:
        os.environ.setdefault("IA_ACCESS_KEY", ia_access)
    if ia_secret:
        os.environ.setdefault("IA_SECRET_KEY", ia_secret)
    if ia_collection:
        os.environ.setdefault("IA_COLLECTION", ia_collection)


def download_item(identifier: str, target_dir: str, dry_run: bool = False) -> bool:
    """
    Fetch all files associated with ``identifier`` and write them under target_dir.
    Returns True if any file was downloaded (or would be downloaded in dry_run).
    """
    try:
        from internetarchive import get_item  # noqa: E402
    except ImportError:
        LOG.error("internetarchive library not installed")
        return False

    LOG.info("Fetching IA item %s", identifier)
    try:
        item = get_item(identifier)
    except Exception as exc:
        LOG.error("Could not open IA item %s: %s", identifier, exc)
        return False

    downloaded = False
    for f in item.files:
        name = f.get("name")
        if not name:
            continue
        dest_path = os.path.join(target_dir, name)
        if os.path.exists(dest_path):
            LOG.debug("file already exists: %s", dest_path)
            continue
        if dry_run:
            LOG.info("[dry-run] would download %s -> %s", name, dest_path)
            downloaded = True
            continue

        try:
            LOG.info("downloading %s -> %s", name, dest_path)
            item.download(dest_path)
            downloaded = True
        except Exception as exc:
            LOG.error("failed to download %s: %s", name, exc)
    return downloaded


def identifiers_from_db() -> List[str]:
    """Return a list of IA identifiers computed from slugs/ids on PDFDocument."""
    ensure_django(None)
    from archive.models import PDFDocument  # noqa: E402

    ids: List[str] = []
    qs = PDFDocument.objects.filter(is_published=True)
    for doc in qs:
        slug = getattr(doc, "slug", "") or ""
        base = slug if slug else f"doc-{getattr(doc, 'id', '')}"
        # reuse safe_identifier logic from backup script
        from scripts.backup_to_ia import safe_identifier  # type: ignore  # noqa: E402

        ids.append(safe_identifier(base))
    return ids


def identifiers_from_collection(collection: str) -> List[str]:
    """
    Query archive.org's collection metadata to enumerate contained identifiers.

    This makes a network request and may take time for large collections.
    """
    import requests  # noqa: E402

    LOG.info("Querying archive.org collection %s", collection)
    params = {"collection": collection, "fl": "identifier", "rows": 1000, "page": 1}
    ids: List[str] = []
    while True:
        resp = requests.get(
            "https://archive.org/advancedsearch.php", params=params, timeout=10
        )
        resp.raise_for_status()
        j = resp.json()
        docs = j.get("response", {}).get("docs", [])
        if not docs:
            break
        for d in docs:
            ident = d.get("identifier")
            if ident:
                ids.append(ident)
        if len(docs) < params["rows"]:
            break
        params["page"] += 1
    return ids


def do_restore(
    *,
    collection: str,
    from_db: bool,
    dry_run: bool,
    force: bool,
    skip_existing: bool,
) -> int:
    state = {}
    try:
        import json  # noqa: F401
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, "r", encoding="utf-8") as sf:
                state = json.load(sf)
    except Exception:
        state = {}

    ids: List[str] = []
    if from_db:
        ids = identifiers_from_db()
    else:
        ids = identifiers_from_collection(collection)

    if not ids:
        LOG.info("No identifiers to process")
        return 0

    media_root = None
    try:
        from django.conf import settings as djsettings  # noqa: E402

        media_root = djsettings.MEDIA_ROOT
    except Exception:
        media_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "media"))
    out_dir = os.path.join(media_root, "pdfs")
    os.makedirs(out_dir, exist_ok=True)

    success = 0
    for ident in ids:
        target = os.path.join(out_dir, ident)
        os.makedirs(target, exist_ok=True)

        if skip_existing and any(os.scandir(target)):
            LOG.info("skipping %s (already has files)", ident)
            continue

        if not force and os.path.exists(target) and os.listdir(target):
            LOG.info("skipping %s (directory not empty)", ident)
            continue

        if download_item(ident, target, dry_run=dry_run):
            success += 1

    # save state (timestamp only for bookkeeping)
    state["last_run"] = time.time()
    try:
        import json  # noqa: F401

        with open(STATE_FILE, "w", encoding="utf-8") as sf:
            json.dump(state, sf)
    except Exception:
        pass

    LOG.info("restored %d items (dry_run=%s)", success, dry_run)
    return success


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Restore PDFDocument files from archive.org into local media"
    )
    parser.add_argument("--collection", "-c", default=DEFAULT_COLLECTION,
                        help="archive.org collection to scan (ignored with --from-db)")
    parser.add_argument("--from-db", action="store_true",
                        help="derive identifiers from existing PDFDocument records")
    parser.add_argument("--dry-run", action="store_true",
                        help="show what would happen without downloading")
    parser.add_argument("--force", action="store_true",
                        help="redownload even if target directory is nonempty")
    parser.add_argument("--skip-existing", action="store_true",
                        help="do not download if any file already exists for identifier")
    parser.add_argument("--settings", help="Django settings module to use")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        stream=sys.stdout)

    if args.settings:
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", args.settings)

    try:
        ensure_django(args.settings)
        load_ia_credentials_from_settings()
    except Exception as exc:
        LOG.exception("could not initialize Django: %s", exc)
        return 2

    if args.dry_run:
        LOG.info("running in dryrun mode")

    return do_restore(
        collection=args.collection,
        from_db=args.from_db,
        dry_run=args.dry_run,
        force=args.force,
        skip_existing=args.skip_existing,
    )


if __name__ == "__main__":
    sys.exit(main())
