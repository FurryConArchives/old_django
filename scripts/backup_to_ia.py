"""Backup published `PDFDocument` files to Internet Archive, one IA item per document.

Requirements:
 - `internetarchive` installed (requirements.txt updated).
 - Set `IA_ACCESS_KEY` and `IA_SECRET_KEY` env vars or run `ia configure`.
 - Optionally set `DJANGO_SETTINGS_MODULE` (defaults to `furry_archive.settings`).

Behavior:
 - On each run the script loads Django, finds published, non-takedown `PDFDocument` rows,
   and uploads any whose `updated_at` timestamp is newer than the previous run.
 - With B2/remote storage, change detection uses the model timestamp only (no per-file
   S3 HEAD requests), so the initial scan stays fast.
 - Files are read through Django storage, so S3/B2-backed media works even when
     the document has no local filesystem path.
 - Creates one IA item per document using the document `slug` or `id` as identifier.
 - Supports `--dry-run` and `--daemon` (interval) modes.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List
import glob
from dateutil import parser as dateutil_parser
from contextlib import nullcontext


def _format_date_range(start_s: str | None, end_s: str | None) -> str:
    """Turn two ISO-like date strings into a compact human-readable range.

    Examples:
    - start=2022-10-27T00:00:00.000Z, end=2022-10-30T00:00:00.000Z -> "October 27–30, 2022"
    - start=2022-12-31, end=2023-01-02 -> "December 31, 2022 – January 2, 2023"
    - start=2022-10-27, end=None -> "October 27, 2022"
    """
    if not start_s and not end_s:
        return "on dates unknown"
    try:
        start_dt = dateutil_parser.parse(start_s) if start_s else None
    except Exception:
        start_dt = None
    try:
        end_dt = dateutil_parser.parse(end_s) if end_s else None
    except Exception:
        end_dt = None

    if start_dt and end_dt:
        # normalize to dates (ignore time)
        s_date = start_dt.date()
        e_date = end_dt.date()
        if s_date == e_date:
            return s_date.strftime("%B %d, %Y")
        if s_date.year == e_date.year:
            if s_date.month == e_date.month:
                # same month/year: "October 27–30, 2022"
                return f"{s_date.strftime('%B')} {s_date.day}–{e_date.day}, {s_date.year}"
            else:
                # same year diff months: "Oct 30 - Nov 2, 2022"
                return f"{s_date.strftime('%B %d')} – {e_date.strftime('%B %d')}, {s_date.year}"
        else:
            # different years
            return f"{s_date.strftime('%B %d, %Y')} – {e_date.strftime('%B %d, %Y')}"
    elif start_dt:
        return start_dt.date().strftime("%B %d, %Y")
    elif end_dt:
        return end_dt.date().strftime("%B %d, %Y")
    else:
        return "on dates unknown"


LOG = logging.getLogger("backup_to_ia")

# State file lives next to this script by default
STATE_FILE = os.path.join(os.path.dirname(__file__), ".backup_state.json")
DEFAULT_COLLECTION = os.environ.get("IA_COLLECTION", "community-texts")


def configure_script_logging() -> None:
    """Keep script INFO logs visible after Django reconfigures logging."""
    logger = logging.getLogger("backup_to_ia")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not any(isinstance(h, logging.StreamHandler) for h in logger.handlers):
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)


def ensure_django(settings_module: str | None = None) -> None:
    if settings_module:
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", settings_module)
    else:
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", os.environ.get("DJANGO_SETTINGS_MODULE", "furry_archive.settings"))
    # Import and configure Django
    try:
        # Ensure the project root is on sys.path so the `furry_archive` package is importable
        project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        if project_root not in sys.path:
            sys.path.insert(0, project_root)

        import django
        django.setup()
    except Exception as exc:  # pragma: no cover - runtime dependency
        LOG.exception("Failed to initialize Django: %s", exc)
        raise


def load_ia_credentials_from_settings() -> None:
    """If `IA_ACCESS_KEY` / `IA_SECRET_KEY` (and optionally `IA_COLLECTION`) are present
    in Django settings, copy them into environment variables so `internetarchive` can use them.
    """
    try:
        from django.conf import settings as djsettings
    except Exception:
        LOG.debug("Django settings not available yet; skipping IA credentials load.")
        return

    ia_access = getattr(djsettings, "IA_ACCESS_KEY", None)
    ia_secret = getattr(djsettings, "IA_SECRET_KEY", None)
    ia_collection = getattr(djsettings, "IA_COLLECTION", None)

    if ia_access:
        os.environ.setdefault("IA_ACCESS_KEY", ia_access)
        LOG.info("Loaded IA_ACCESS_KEY from Django settings")
    if ia_secret:
        os.environ.setdefault("IA_SECRET_KEY", ia_secret)
        LOG.info("Loaded IA_SECRET_KEY from Django settings")
    if ia_collection:
        os.environ.setdefault("IA_COLLECTION", ia_collection)
        LOG.info("Loaded IA_COLLECTION from Django settings")


def load_state(path: str) -> Dict:
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"last_run": 0}


def save_state(path: str, state: Dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f)


def safe_identifier(base: str) -> str:
    s = re.sub(r"[^A-Za-z0-9._-]", "-", base)
    s = re.sub(r"-+", "-", s).strip("-")
    return s.lower()[:200]


def _document_ia_identifier(doc) -> str:
    doc_slug = getattr(doc, 'slug', '') or ''
    base = doc_slug if doc_slug else f"doc-{getattr(doc, 'id', '')}"
    return safe_identifier(base)


def _uses_remote_storage() -> bool:
    try:
        from django.conf import settings
        if getattr(settings, 'USE_B2_STORAGE', False):
            return True
        backend = (settings.STORAGES.get('default', {}) or {}).get('BACKEND', '')
        return 's3' in backend.lower() or 'boto' in backend.lower()
    except Exception:
        return False


def _document_modified_ts(doc) -> float:
    """Return the best change timestamp for a document without slow remote HEAD calls."""
    file_field = getattr(doc, 'file', None)
    file_name = getattr(file_field, 'name', None) if file_field else None
    updated_ts = 0.0
    try:
        if getattr(doc, 'updated_at', None):
            updated_ts = doc.updated_at.timestamp()
    except Exception:
        updated_ts = 0.0

    if not file_name:
        return updated_ts

    # Local files: use filesystem mtime when available.
    try:
        local_path = file_field.path
        if local_path and os.path.exists(local_path):
            return max(os.path.getmtime(local_path), updated_ts)
    except Exception:
        pass

    # Remote storage (B2/S3): rely on model timestamps to avoid one HEAD request per PDF.
    if _uses_remote_storage():
        return updated_ts

    storage = getattr(file_field, 'storage', None)
    if storage:
        try:
            modified_dt = storage.get_modified_time(file_name)
            return max(modified_dt.timestamp(), updated_ts)
        except Exception:
            pass
    return updated_ts


def _document_has_uploadable_file(doc) -> bool:
    """Return True when the document's storage backend can serve the PDF."""
    try:
        file_field = doc.file
        file_name = getattr(file_field, 'name', None)
        if not file_name:
            return False
        try:
            local_path = file_field.path
            if local_path and os.path.exists(local_path):
                return True
        except Exception:
            pass
        storage = getattr(file_field, 'storage', None)
        if storage is None:
            return False
        return bool(storage.exists(file_name))
    except Exception:
        return False


def _ia_item_exists(identifier: str, timeout: float = 5.0) -> bool:
    try:
        import requests
        resp = requests.get(f"https://archive.org/metadata/{identifier}", timeout=timeout)
        if not resp.ok:
            return False
        payload = resp.json()
        return isinstance(payload, dict) and bool(payload.get('metadata'))
    except Exception:
        return False


def get_changed_published_documents(since_ts: float, force: bool = False) -> List[Dict]:
    """Return list of dicts: {'obj': PDFDocument, 'modified_ts': float}.
    A document is considered changed if its storage mtime or updated_at timestamp is newer than since_ts.
    """
    ensure_django(None)
    try:
        from archive.models import PDFDocument
    except Exception:  # pragma: no cover - import/runtime
        LOG.exception("Could not import PDFDocument model; ensure this script runs inside project root.")
        raise

    out: List[Dict] = []
    qs = PDFDocument.objects.filter(is_published=True, takedown_by_request=False)
    total = qs.count()
    LOG.info("Scanning %d published documents for changes...", total)
    for index, doc in enumerate(qs.iterator(), start=1):
        if index == 1 or index % 100 == 0 or index == total:
            LOG.info("Scanned %d/%d documents...", index, total)

        file_name = getattr(getattr(doc, 'file', None), 'name', None)
        if not file_name:
            LOG.debug("Document id=%s title=%s has no file attached", getattr(doc, 'id', None), getattr(doc, 'title', None))
            continue

        modified_ts = _document_modified_ts(doc)
        if force or (modified_ts > since_ts):
            out.append({"obj": doc, "modified_ts": modified_ts})
    # sort by modified_ts
    out.sort(key=lambda d: d["modified_ts"])  # oldest-first
    return out


def _coerce_date_str(value) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:
            pass
    text = str(value).strip()
    return text or None


def build_metadata_for_doc(doc) -> Dict:
    md = {
            "title": doc.title or "PDF Document",
            "mediatype": "texts",
            "description": doc.description or f"Backup of {doc.title}",
            "year": str(doc.year) if getattr(doc, 'year', None) else "",
        }
    # Map license field if present
    if getattr(doc, 'creative_commons_license', None):
        md["licenseurl"] = doc.creative_commons_license
    # Add contributor/creator fields
    if getattr(doc, 'author', None):
        md["creator"] = doc.author

    # If this appears to be a convention booklet (slug contains 'conbook'),
    # construct a more detailed preservation-focused description using Consurf when possible.
    try:
        slug = (getattr(doc, 'slug', '') or '').lower()
    except Exception:
        slug = ''

    if 'conbook' in slug:
        from archive.document_descriptions import (
            consurf_event_for_document,
            convention_name_for_document,
            event_description_for_document,
        )

        con_name = convention_name_for_document(doc) or getattr(doc, 'title', '') or 'Convention'
        year = getattr(doc, 'year', None)
        year_str = str(year) if year else ''
        location = ''
        try:
            if getattr(doc, 'category', None) and getattr(doc.category, 'location', None):
                location = doc.category.location
        except Exception:
            location = ''

        theme = None
        dates_text = "on dates unknown"
        extra_desc = ''
        consurf_data = consurf_event_for_document(doc, allow_network=True)
        if consurf_data:
            LOG.info(
                "Loaded convention metadata for %s (%s) from source=%s",
                con_name,
                year_str or 'unknown year',
                consurf_data.get('source') or 'unknown',
            )
            if consurf_data.get('location'):
                location = consurf_data.get('location') or location

            start_fmt = _coerce_date_str(consurf_data.get('start_date'))
            end_fmt = _coerce_date_str(consurf_data.get('end_date'))

            if start_fmt or end_fmt:
                dates_text = _format_date_range(start_fmt, end_fmt)

            event_desc = event_description_for_document(doc, consurf_event=consurf_data)
            extra_desc = (event_desc.strip() + "\n\n") if event_desc else ''

            theme = consurf_data.get('theme') or consurf_data.get('themes')
            if isinstance(theme, (list, dict)):
                try:
                    theme = json.dumps(theme, ensure_ascii=False)
                except Exception:
                    theme = str(theme)

            base_tags = [
                "Furry",
                "Anthropomorphic",
                "Anthro",
                "Fur",
                "Funny animal",
                "anthology",
                "collection",
                "Magazine",
                "Publication",
                "Conbook",
                "pdf",
                "con book",
            ]
            tags = list(base_tags)
            if con_name:
                tags.append(con_name)
            if year_str:
                tags.append(year_str)
            norm_tags = []
            for t in tags:
                try:
                    s = (t or '').strip()
                except Exception:
                    s = str(t)
                if s and s not in norm_tags:
                    norm_tags.append(s)
            if norm_tags:
                md['subject'] = norm_tags
        else:
            LOG.info(
                "No Consurf/MLPCON metadata found for %s (%s); using local schedule fallback if available",
                con_name,
                year_str or 'unknown year',
            )
            try:
                range_found = _find_schedule_date_range(con_name, int(year) if year else None)
                if range_found:
                    start_str, end_str = range_found
                    dates_text = f"from {start_str} to {end_str}" if start_str and end_str else (f"on {start_str}" if start_str else "on dates unknown")
                    LOG.info("Found local schedule date range for %s: %s", con_name, dates_text)
            except Exception:
                LOG.debug("Local schedule date scan failed for %s", con_name)

        md["description"] = (
            f"{con_name} {year_str} was held in {location or 'an unknown location'}, on {dates_text}. The theme is {theme or 'unknown'}. "
            "The materials included here were originally distributed to convention attendees and are being shared strictly for purposes of historical preservation purposes. "
            "This presentation constitutes fair use under Section 107 of the U.S. Copyright Act, as it is transformative and does not substitute for or diminish the market value of the original materials, especially ones that were shared publically to all attendees.\n\n"
            f"{extra_desc}"
            "Convention booklets (conbooks) are creative works that may be protected by copyright. "
            "The Furry Con Archive respects the rights of copyright holders while working to preserve these important historical documents for the furry community."
        )

    return md


def _find_schedule_date_range(con_name: str, year: int | None):
    """Scan `schedules/*.json` for date-like fields and return (start_str, end_str) or None."""
    try:
        repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        schedules_dir = os.path.join(repo_root, "schedules")
        if not os.path.isdir(schedules_dir):
            return None
        dates = []
        for fn in glob.glob(os.path.join(schedules_dir, "*.json")):
            try:
                with open(fn, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
            except Exception:
                continue

            def _recurse(obj):
                if isinstance(obj, dict):
                    for k, v in obj.items():
                        key = (k or "").lower()
                        if any(x in key for x in ("start", "date", "end")):
                            if isinstance(v, str):
                                try:
                                    dt = dateutil_parser.parse(v, fuzzy=True)
                                    if not year or dt.year == year:
                                        dates.append(dt)
                                except Exception:
                                    pass
                            elif isinstance(v, list) or isinstance(v, dict):
                                _recurse(v)
                        else:
                            _recurse(v)
                elif isinstance(obj, list):
                    for it in obj:
                        _recurse(it)

            _recurse(data)

        if not dates:
            return None
        dates.sort()
        start = dates[0]
        end = dates[-1]

        if start.year == end.year:
            if start.month == end.month:
                start_str = start.strftime("%B %d")
                end_str = f"{end.day}, {end.year}"
                return (start_str, end_str)
            else:
                start_str = start.strftime("%B %d")
                end_str = f"{end.strftime('%B %d')}, {end.year}"
                return (start_str, end_str)
        else:
            return (start.strftime("%B %d, %Y"), end.strftime("%B %d, %Y"))
    except Exception:
        return None


def upload_document_to_ia(doc_entry: Dict, collection: str, dry_run: bool = False, cooldown: float = 0.0, skip_if_exists: bool = False) -> bool:
    doc = doc_entry["obj"]
    identifier = _document_ia_identifier(doc)
    file_field = doc.file
    file_name = getattr(file_field, 'name', None)
    if not file_name:
        LOG.warning("Skipping doc id=%s: no file attached", getattr(doc, 'id', None))
        return False
    upload_name = os.path.basename(file_name)

    if dry_run:
        LOG.info("Would upload file %s as IA item %s", upload_name, identifier)
        return True

    try:
        from internetarchive import upload, get_item
    except Exception:
        LOG.error("internetarchive library not available; install requirements.")
        return False

    if skip_if_exists and _ia_item_exists(identifier):
        LOG.info("Skipping upload for %s because IA item already exists", identifier)
        return False

    if not _document_has_uploadable_file(doc):
        LOG.warning(
            "Skipping doc id=%s (%s): file missing or inaccessible in storage (%s)",
            getattr(doc, 'id', None),
            identifier,
            file_name,
        )
        return False

    metadata = build_metadata_for_doc(doc)
    # NEVER set the `collection` metadata per operator request; items will upload to the API-key account.

    try:
        from archive.utils import local_path_for_field_file
    except Exception:
        LOG.exception("Could not import local_path_for_field_file")
        return False

    try:
        with local_path_for_field_file(file_field) as local_path:
            if not local_path:
                LOG.warning("Skipping doc id=%s (%s): could not read file from storage", getattr(doc, 'id', None), identifier)
                return False
            LOG.info("Uploading file %s as IA item %s", upload_name, identifier)
            with open(local_path, 'rb') as f:
                upload(identifier, files={upload_name: f}, metadata=metadata)
        # Try to explicitly replace/overwrite metadata on the IA item after upload.
        try:
            from internetarchive import get_item
            try:
                item = get_item(identifier)
            except Exception:
                item = None
            if item is not None:
                try:
                    # Wrap metadata under the 'metadata' key as expected by some IA client methods
                    payload = {'metadata': metadata}
                    # Prefer edit_metadata, then update_metadata, then fallback to upload
                    if hasattr(item, 'edit_metadata'):
                        item.edit_metadata(payload)
                        LOG.info("Replaced metadata for IA item %s using edit_metadata()", identifier)
                    elif hasattr(item, 'update_metadata'):
                        item.update_metadata(payload)
                        LOG.info("Replaced metadata for IA item %s using update_metadata()", identifier)
                    else:
                        # Fallback: call upload with metadata only (some versions accept None for files)
                        try:
                            upload(identifier, None, metadata=metadata)
                            LOG.info("Fallback metadata upload succeeded for IA item %s", identifier)
                        except Exception:
                            LOG.warning("No supported metadata update method available for IA item %s", identifier)
                except Exception:
                    LOG.exception("Failed to apply metadata update for IA item %s", identifier)
        except Exception:
            LOG.exception("Failed to run metadata-update step for IA item %s", identifier)
        # Honor a cooldown after uploading to avoid rate limiting
        if cooldown and not dry_run:
            LOG.info("Waiting %s seconds cooldown after upload to IA (identifier=%s)", cooldown, identifier)
            time.sleep(float(cooldown))
        try:
            from archive.ia_mirrors import invalidate_document_ia_mirrors
            invalidate_document_ia_mirrors(getattr(doc, 'slug', '') or '')
        except Exception:
            LOG.debug("Could not invalidate IA mirror cache for %s", identifier, exc_info=True)
        return True
    except Exception as exc:  # pragma: no cover - network/runtime
        LOG.exception("Upload failed: %s", exc)
        return False


def find_changed_schedule_files(since_ts: float, force: bool = False) -> List[str]:
    """Find changed schedule JSON files and likely associated printable PDFs in `schedules/`.
    Returns absolute file paths.
    """
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    schedules_dir = os.path.join(repo_root, "schedules")
    out: List[str] = []
    if not os.path.isdir(schedules_dir):
        return out
    for fn in os.listdir(schedules_dir):
        if not (fn.lower().endswith('.json') or fn.lower().endswith('.pdf')):
            continue
        fp = os.path.join(schedules_dir, fn)
        try:
            mtime = os.path.getmtime(fp)
        except OSError:
            continue
        if force or (mtime > since_ts):
            out.append(fp)
        else:
            # If a JSON exists and has a matching PDF print, include the PDF when appropriate
            if fn.lower().endswith('.json'):
                base = os.path.splitext(fn)[0]
                for cand in (f"{base}-print.pdf", f"{base}_print.pdf", f"{base}.pdf"):
                    candp = os.path.join(schedules_dir, cand)
                    if os.path.exists(candp):
                        try:
                            if os.path.getmtime(candp) > since_ts:
                                out.append(candp)
                        except OSError:
                            pass
    return sorted(set(out))


def upload_schedule_file(fp: str, dry_run: bool = False, cooldown: float = 0.0) -> bool:
    """Upload a schedule file (JSON or printable PDF) to IA using a schedule-based identifier."""
    basename = os.path.basename(fp)
    name_noext = os.path.splitext(basename)[0]
    base_id = f"schedule-{name_noext}"
    identifier = safe_identifier(base_id)
    ext = os.path.splitext(basename)[1].lower()
    metadata = {"title": basename}
    if ext == '.json':
        metadata["mediatype"] = "data"
        metadata["description"] = "Schedule JSON export (machine-readable)."
    else:
        metadata["mediatype"] = "texts"
        metadata["description"] = "Printable schedule PDF."

    LOG.info("Uploading schedule file %s as IA item %s", fp, identifier)
    if dry_run:
        return True
    try:
        from internetarchive import upload
    except Exception:
        LOG.error("internetarchive library not available; install requirements.")
        return False
    try:
        upload(identifier, fp, metadata=metadata)
        if cooldown and not dry_run:
            LOG.info("Waiting %s seconds cooldown after schedule upload to IA (file=%s)", cooldown, fp)
            time.sleep(float(cooldown))
        return True
    except Exception as exc:
        LOG.exception("Schedule upload failed: %s", exc)
        return False


def do_run(
    collection: str,
    dry_run: bool = False,
    force: bool = False,
    upload_cooldown: float = 0.0,
    fix_descriptions: bool = False,
    log_descriptions: bool = False,
    skip_existing: bool = False,
) -> int:
    state = load_state(STATE_FILE)
    last_run = float(state.get("last_run", 0))
    LOG.info("Last run: %s", datetime.fromtimestamp(last_run, tz=timezone.utc).isoformat() if last_run else "never")
    if force:
        LOG.info("Force mode enabled: uploading all published documents regardless of last run")
    elif skip_existing:
        LOG.info("Skip-existing mode: checking all published documents against Internet Archive")
    scan_all = force or skip_existing
    changed = get_changed_published_documents(last_run, force=scan_all)
    if not changed:
        LOG.info("No changed published documents found.")
    else:
        LOG.info("Found %d changed documents to process.", len(changed))
    success = 0
    skipped = 0
    if fix_descriptions:
        # Only update descriptions for conbooks
        from archive.models import PDFDocument
        for entry in changed:
            doc = entry["obj"]
            slug = (getattr(doc, 'slug', '') or '').lower()
            if 'conbook' in slug:
                md = build_metadata_for_doc(doc)
                PDFDocument.objects.filter(pk=doc.pk).update(description=md["description"])
                LOG.info("Updated description for doc id=%s title=%s", getattr(doc, 'id', None), getattr(doc, 'title', None))
        state["last_run"] = time.time()
        save_state(STATE_FILE, state)
        LOG.info("Descriptions updated for conbooks only (dry_run=%s)", dry_run)
        return 0
    if log_descriptions:
        # Only log generated metadata descriptions for changed documents and exit
        for entry in changed:
            doc = entry["obj"]
            try:
                md = build_metadata_for_doc(doc)
            except Exception as exc:
                LOG.exception("Failed building metadata for doc id=%s: %s", getattr(doc, 'id', None), exc)
                continue
            try:
                doc_slug = getattr(doc, 'slug', '') or ''
            except Exception:
                doc_slug = ''
            if doc_slug:
                base = doc_slug
            else:
                base = f"doc-{getattr(doc, 'id', '')}"
            identifier = safe_identifier(base)
            LOG.info("---\nIdentifier: %s\nTitle: %s\nDescription:\n%s\n---", identifier, md.get('title', ''), md.get('description', ''))
        state["last_run"] = time.time()
        save_state(STATE_FILE, state)
        return 0
    for index, entry in enumerate(changed, start=1):
        doc = entry["obj"]
        LOG.info(
            "Processing document %d/%d: id=%s slug=%s",
            index,
            len(changed),
            getattr(doc, 'id', None),
            getattr(doc, 'slug', None),
        )
        if upload_document_to_ia(entry, collection, dry_run=dry_run, cooldown=upload_cooldown, skip_if_exists=skip_existing):
            success += 1
        else:
            skipped += 1

    # Mirror schedule JSON files and matching printable PDFs
    schedule_files = find_changed_schedule_files(last_run, force=force)
    if schedule_files:
        LOG.info("Found %d changed schedule files to upload.", len(schedule_files))
    for sf in schedule_files:
        if upload_schedule_file(sf, dry_run=dry_run, cooldown=upload_cooldown):
            success += 1

    state["last_run"] = time.time()
    save_state(STATE_FILE, state)
    LOG.info("Uploaded %d items, skipped %d (documents + schedules) (dry_run=%s)", success, skipped, dry_run)
    return success


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Backup published PDFDocument files to Internet Archive")
    parser.add_argument("--collection", "-c", default=DEFAULT_COLLECTION, help="IA collection name")
    parser.add_argument("--dry-run", action="store_true", help="Don't actually upload")
    parser.add_argument("--daemon", action="store_true", help="Run repeatedly every interval seconds")
    parser.add_argument("--force", action="store_true", help="Upload all published documents regardless of last run timestamp")
    parser.add_argument("--interval", type=int, default=24 * 3600, help="Seconds between runs when daemon")
    parser.add_argument("--upload-cooldown", type=int, default=300, help="Seconds cooldown between each IA upload (to avoid rate limiting)")
    parser.add_argument("--settings", help="Django settings module to use (e.g., furry_archive.settings)")
    parser.add_argument("--fix-descriptions", action="store_true", help="Only update conbook descriptions from consurf, do not upload")
    parser.add_argument("--log-descriptions", action="store_true", help="Log generated metadata descriptions for changed documents and exit")
    parser.add_argument("--print-description", metavar="ID_OR_SLUG", help="Print generated metadata description for a single document id or slug and exit")
    parser.add_argument("--print-tags", metavar="ID_OR_SLUG", help="Print generated IA tags (md['subject']) for a single document id or slug and exit")
    parser.add_argument("--update-metadata-only", action="store_true", help="Update IA metadata for existing items only (no file uploads); only updates items missing description/subject")
    parser.add_argument("--skip-existing", action="store_true", help="Scan all published documents and only upload items missing from Internet Archive")
    args = parser.parse_args(argv)

    configure_script_logging()

    if args.settings:
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", args.settings)

    # Initialize Django and (if present) load IA credentials from settings
    try:
        ensure_django(args.settings)
        load_ia_credentials_from_settings()
        configure_script_logging()
    except Exception:
        LOG.exception("Failed to initialize Django/settings; aborting.")
        return 3

    LOG.info(
        "Starting IA backup (dry_run=%s, force=%s, skip_existing=%s, upload_cooldown=%ss)",
        args.dry_run,
        args.force,
        args.skip_existing,
        args.upload_cooldown,
    )

    if not args.dry_run:
        # ensure internetarchive available
        try:
            import internetarchive  # type: ignore
        except Exception:
            LOG.error("internetarchive package not installed. Install via requirements.")
            return 2

    if args.daemon:
        LOG.info("Starting daemon, interval=%s", args.interval)
        try:
            while True:
                do_run(
                    args.collection,
                    dry_run=args.dry_run,
                    force=args.force,
                    upload_cooldown=args.upload_cooldown,
                    fix_descriptions=args.fix_descriptions,
                    log_descriptions=args.log_descriptions,
                    skip_existing=args.skip_existing,
                )
                time.sleep(args.interval)
        except KeyboardInterrupt:
            LOG.info("Interrupted, exiting")
            return 0
    else:
        # If user asked to print a single doc description, do that and exit
        if args.print_description:
            try:
                ensure_django(args.settings)
                from archive.models import PDFDocument
            except Exception as exc:
                LOG.exception("Failed to initialize Django or import PDFDocument: %s", exc)
                return 2
            ident = args.print_description
            doc = None
            # try numeric id first
            try:
                if re.match(r'^\d+$', str(ident)):
                    doc = PDFDocument.objects.filter(id=int(ident)).first()
            except Exception:
                doc = None
            if not doc:
                try:
                    doc = PDFDocument.objects.filter(slug__iexact=str(ident)).first()
                except Exception:
                    doc = None
            if not doc:
                print(f"Document not found for identifier: {ident}")
                return 3
            try:
                md = build_metadata_for_doc(doc)
            except Exception as exc:
                LOG.exception("Failed to build metadata for doc id=%s: %s", getattr(doc, 'id', None), exc)
                return 4
            # Print to stdout the identifier and description
            base = getattr(doc, 'slug', '') or f"doc-{getattr(doc, 'id', '')}"
            identifier = safe_identifier(base)
            print("---")
            print(f"Identifier: {identifier}")
            print(f"Title: {md.get('title', '')}")
            print("Description:")
            print(md.get('description', '') or "")
            print("---")
            return 0
        # If user asked to print IA tags for a single doc, do that and exit
        if args.print_tags:
            try:
                ensure_django(args.settings)
                from archive.models import PDFDocument
            except Exception as exc:
                LOG.exception("Failed to initialize Django or import PDFDocument: %s", exc)
                return 2
            ident = args.print_tags
            doc = None
            try:
                if re.match(r'^\d+$', str(ident)):
                    doc = PDFDocument.objects.filter(id=int(ident)).first()
            except Exception:
                doc = None
            if not doc:
                try:
                    doc = PDFDocument.objects.filter(slug__iexact=str(ident)).first()
                except Exception:
                    doc = None
            if not doc:
                print(f"Document not found for identifier: {ident}")
                return 3
            try:
                md = build_metadata_for_doc(doc)
            except Exception as exc:
                LOG.exception("Failed to build metadata for doc id=%s: %s", getattr(doc, 'id', None), exc)
                return 4
            tags = md.get('subject') or []
            print("---")
            doc_slug = getattr(doc, 'slug', '') or ''
            doc_id = getattr(doc, 'id', '')
            base = doc_slug if doc_slug else f'doc-{doc_id}'
            print(f"Identifier: {safe_identifier(base)}")
            print(f"Title: {md.get('title', '')}")
            print("Tags:")
            if tags:
                for t in tags:
                    print(f"- {t}")
            else:
                print("(no tags generated)")
            print("---")
            return 0
        # If user requested metadata update only, iterate published docs and update IA metadata where missing
        if args.update_metadata_only:
            try:
                ensure_django(args.settings)
                from archive.models import PDFDocument
            except Exception as exc:
                LOG.exception("Failed to initialize Django or import PDFDocument: %s", exc)
                return 2
            try:
                from internetarchive import get_item, upload
            except Exception:
                LOG.exception("internetarchive library not available for metadata update.")
                return 2

            qs = PDFDocument.objects.filter(is_published=True, takedown_by_request=False)
            updated = 0
            for doc in qs:
                try:
                    item = None
                    base = getattr(doc, 'slug', '') or f"doc-{getattr(doc, 'id', '')}"
                    identifier = safe_identifier(base)
                    md = build_metadata_for_doc(doc)
                    if args.dry_run:
                        LOG.info("[dry-run] Would consider updating metadata for %s", identifier)
                        continue
                    try:
                        # First check the public Archive.org metadata endpoint for accurate presence
                        import requests
                        meta_url = f"https://archive.org/metadata/{identifier}"
                        resp = requests.get(meta_url, timeout=10)
                        if resp.ok:
                            try:
                                j = resp.json()
                                existing = j.get('metadata') if isinstance(j, dict) else None
                            except Exception:
                                existing = None
                        else:
                            # Fallback to client get_item() wrapper
                            try:
                                item = get_item(identifier)
                                existing = getattr(item, 'metadata', None) or item.metadata
                            except Exception:
                                LOG.info("IA item not found for identifier %s; skipping", identifier)
                                continue
                    except Exception:
                        # If HTTP check failed, fallback to client
                        try:
                            item = get_item(identifier)
                            existing = getattr(item, 'metadata', None) or item.metadata
                        except Exception:
                            LOG.info("IA item not found for identifier %s; skipping", identifier)
                            continue
                    need_update = False
                    # Normalize description and determine if update needed when different
                    def _norm_text(s: str | None) -> str:
                        if not s:
                            return ''
                        try:
                            return re.sub(r"\s+", " ", str(s).strip())
                        except Exception:
                            return str(s)

                    existing_desc = _norm_text(existing.get('description') if existing else None)
                    new_desc = _norm_text(md.get('description'))
                    if new_desc and existing_desc != new_desc:
                        need_update = True

                    # Normalize subject fields to lists and compare (case-insensitive set comparison)
                    def _to_subject_list(val) -> list:
                        if not val:
                            return []
                        if isinstance(val, list):
                            return [str(x).strip() for x in val if x]
                        if isinstance(val, str):
                            parts = [p.strip() for p in re.split(r"[;,]", val) if p.strip()]
                            return parts
                        return [str(val).strip()]

                    existing_subj = _to_subject_list(existing.get('subject') if existing else None)
                    new_subj = _to_subject_list(md.get('subject'))
                    if new_subj:
                        low_existing = set(x.lower() for x in existing_subj)
                        low_new = set(x.lower() for x in new_subj)
                        if low_existing != low_new:
                            need_update = True
                    if not need_update:
                        LOG.debug("No metadata update needed for %s", identifier)
                        continue
                    if item is None:
                        try:
                            item = get_item(identifier)
                        except Exception:
                            LOG.info("IA item not found for identifier %s; skipping", identifier)
                            continue
                    # Attempt to apply metadata update
                    try:
                        if hasattr(item, 'modify_metadata'):
                            item.modify_metadata(md)
                        elif hasattr(item, 'edit_metadata'):
                            item.edit_metadata(md)
                        elif hasattr(item, 'update_metadata'):
                            item.update_metadata(md)
                        else:
                            # fallback to upload metadata-only
                            upload(identifier, None, metadata=md)
                        LOG.info("Updated metadata for IA item %s", identifier)
                        updated += 1
                    except Exception:
                        LOG.exception("Failed to update metadata for IA item %s", identifier)
                except Exception:
                    LOG.exception("Error while processing doc id=%s", getattr(doc, 'id', None))
            LOG.info("Metadata update complete; updated %d items", updated)
            return 0
        # If user requested only to log descriptions, do that and exit without uploading
        if args.log_descriptions:
            return do_run(
                args.collection,
                dry_run=True,
                force=args.force,
                upload_cooldown=args.upload_cooldown,
                fix_descriptions=args.fix_descriptions,
                log_descriptions=True,
            )

        do_run(
            args.collection,
            dry_run=args.dry_run,
            force=args.force,
            upload_cooldown=args.upload_cooldown,
            fix_descriptions=args.fix_descriptions,
            log_descriptions=args.log_descriptions,
            skip_existing=args.skip_existing,
        )
        return 0


if __name__ == "__main__":
    sys.exit(main())