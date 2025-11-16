"""Atomic PDF page image cache with file locking for concurrent requests."""

import fcntl
import logging
import os
import shutil

from django.conf import settings

logger = logging.getLogger(__name__)

PAGE_IMAGE_LOCK_TIMEOUT_SECONDS = 300


def _acquire_page_lock(lock_file, output_path):
    """Acquire an exclusive page lock, waiting up to PAGE_IMAGE_LOCK_TIMEOUT_SECONDS."""
    import time

    deadline = time.monotonic() + PAGE_IMAGE_LOCK_TIMEOUT_SECONDS
    while True:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            if time.monotonic() >= deadline:
                logger.warning(
                    'pdf_cache: timed out waiting for lock on %s after %ss',
                    output_path,
                    PAGE_IMAGE_LOCK_TIMEOUT_SECONDS,
                )
                return False
            time.sleep(0.25)


def uses_remote_media_storage():
    if getattr(settings, 'USE_B2_STORAGE', False):
        return True
    try:
        backend = (settings.STORAGES.get('default', {}) or {}).get('BACKEND', '')
        return 's3' in backend.lower() or 'boto' in backend.lower()
    except Exception:
        return False


def pdf_page_cache_storage_key(slug, page_num):
    return f'pdfs/cache/{slug}/page_{page_num}.jpg'


def pdf_page_cache_path(slug, page_num):
    output_dir = os.path.join(settings.MEDIA_ROOT, 'pdfs', 'cache', slug)
    return output_dir, os.path.join(output_dir, f'page_{page_num}.jpg')


def _remote_cache_exists(slug, page_num):
    if not uses_remote_media_storage():
        return False
    from django.core.files.storage import default_storage

    try:
        return default_storage.exists(pdf_page_cache_storage_key(slug, page_num))
    except Exception:
        return False


def _read_remote_cache(slug, page_num):
    from django.core.files.storage import default_storage

    key = pdf_page_cache_storage_key(slug, page_num)
    try:
        if not default_storage.exists(key):
            return None
        with default_storage.open(key, 'rb') as img_file:
            return img_file.read()
    except Exception as exc:
        logger.warning('pdf_cache: failed to read remote %s page %s: %s', slug, page_num, exc)
        return None


def _save_remote_cache(slug, page_num, local_path):
    from django.core.files.base import File
    from django.core.files.storage import default_storage

    key = pdf_page_cache_storage_key(slug, page_num)
    try:
        if default_storage.exists(key):
            default_storage.delete(key)
        with open(local_path, 'rb') as source_file:
            default_storage.save(key, File(source_file))
        return True
    except Exception as exc:
        logger.warning('pdf_cache: failed to upload %s page %s: %s', slug, page_num, exc)
        return False


def delete_cached_page_image(output_path, *, slug=None, page_num=None):
    if os.path.exists(output_path):
        try:
            os.remove(output_path)
        except OSError as exc:
            logger.warning('pdf_cache: failed to remove %s: %s', output_path, exc)

    if slug is not None and page_num is not None and uses_remote_media_storage():
        from django.core.files.storage import default_storage

        key = pdf_page_cache_storage_key(slug, page_num)
        try:
            if default_storage.exists(key):
                default_storage.delete(key)
        except Exception as exc:
            logger.warning('pdf_cache: failed to remove remote %s page %s: %s', slug, page_num, exc)


def clear_document_page_cache(slug, page_count=None):
    cache_dir = os.path.join(settings.MEDIA_ROOT, 'pdfs', 'cache', slug)
    if os.path.exists(cache_dir):
        try:
            shutil.rmtree(cache_dir)
        except Exception as exc:
            logger.warning('pdf_cache: failed to clear local cache dir for %s: %s', slug, exc)

    if not uses_remote_media_storage():
        return

    from django.core.files.storage import default_storage

    prefix = f'pdfs/cache/{slug}/'
    try:
        if page_count:
            for page_num in range(1, page_count + 1):
                key = pdf_page_cache_storage_key(slug, page_num)
                if default_storage.exists(key):
                    default_storage.delete(key)
            return

        _, filenames = default_storage.listdir(f'pdfs/cache/{slug}')
        for filename in filenames:
            default_storage.delete(f'{prefix}{filename}')
    except Exception as exc:
        logger.warning('pdf_cache: failed to clear remote cache for %s: %s', slug, exc)


def cached_page_image_exists(output_path, *, slug=None, page_num=None):
    if os.path.exists(output_path):
        return True
    if slug is not None and page_num is not None:
        return _remote_cache_exists(slug, page_num)
    return False


def read_cached_page_image(output_path, *, slug=None, page_num=None):
    if os.path.exists(output_path):
        try:
            with open(output_path, 'rb') as img_file:
                return img_file.read()
        except OSError as exc:
            logger.warning('pdf_cache: failed to read %s: %s', output_path, exc)

    if slug is not None and page_num is not None:
        return _read_remote_cache(slug, page_num)
    return None


def _write_page_image(output_path, img, *, slug=None, page_num=None):
    """Write a PIL image to cache. Caller must already hold the page lock."""
    tmp_path = f'{output_path}.tmp'
    try:
        if cached_page_image_exists(output_path, slug=slug, page_num=page_num):
            return False
        img.save(tmp_path, 'JPEG', quality=85, optimize=True)
        os.replace(tmp_path, output_path)
        if slug is not None and page_num is not None and uses_remote_media_storage():
            _save_remote_cache(slug, page_num, output_path)
        return True
    except Exception as exc:
        logger.warning('pdf_cache: failed to save %s: %s', output_path, exc)
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass
        return False


def save_page_image_atomic(output_path, img, *, slug=None, page_num=None):
    """Save a PIL image to cache using a lock file and temp+rename."""
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    lock_path = f'{output_path}.lock'

    with open(lock_path, 'a+') as lock_file:
        if not _acquire_page_lock(lock_file, output_path):
            return False
        try:
            return _write_page_image(output_path, img, slug=slug, page_num=page_num)
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def generate_page_image(pdf_path, page_num, output_path, *, force_regenerate=False, slug=None):
    """Generate a single PDF page JPEG with locking. Returns image bytes or None."""
    if force_regenerate:
        delete_cached_page_image(output_path, slug=slug, page_num=page_num)

    if not force_regenerate:
        existing = read_cached_page_image(output_path, slug=slug, page_num=page_num)
        if existing is not None:
            return existing

    lock_path = f'{output_path}.lock'
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(lock_path, 'a+') as lock_file:
        if not _acquire_page_lock(lock_file, output_path):
            return None
        try:
            if not force_regenerate:
                existing = read_cached_page_image(output_path, slug=slug, page_num=page_num)
                if existing is not None:
                    return existing

            if not pdf_path or not os.path.exists(pdf_path):
                return None

            from pdf2image import convert_from_path

            images = convert_from_path(
                pdf_path,
                first_page=page_num,
                last_page=page_num,
                dpi=150,
                fmt='jpeg',
                strict=False,
                use_cropbox=True,
                thread_count=1,
            )
            if not images:
                return None

            _write_page_image(output_path, images[0], slug=slug, page_num=page_num)
            return read_cached_page_image(output_path, slug=slug, page_num=page_num)
        except Exception as exc:
            logger.warning('pdf_cache: generation failed for %s page %s: %s', output_path, page_num, exc)
            return None
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
