from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from django.conf import settings


SITE_CACHE_DIRNAME = 'site_cache'


def _root_dir() -> Optional[Path]:
    media_root = getattr(settings, 'MEDIA_ROOT', None)
    if not media_root:
        return None
    root = Path(media_root) / SITE_CACHE_DIRNAME
    root.mkdir(parents=True, exist_ok=True)
    return root


def _namespace_dir(namespace: str) -> Optional[Path]:
    root = _root_dir()
    if root is None:
        return None
    namespace_dir = root / namespace
    namespace_dir.mkdir(parents=True, exist_ok=True)
    return namespace_dir


def _registry_dir() -> Optional[Path]:
    root = _root_dir()
    if root is None:
        return None
    registry_dir = root / 'registry'
    registry_dir.mkdir(parents=True, exist_ok=True)
    return registry_dir


def _safe_key(key: str) -> str:
    return hashlib.sha256(key.encode('utf-8')).hexdigest()


def _binary_paths(namespace: str, key: str) -> tuple[Optional[Path], Optional[Path]]:
    namespace_dir = _namespace_dir(namespace)
    if namespace_dir is None:
        return None, None
    base = _safe_key(key)
    return namespace_dir / f'{base}.bin', namespace_dir / f'{base}.json'


def _json_path(namespace: str, key: str) -> Optional[Path]:
    namespace_dir = _namespace_dir(namespace)
    if namespace_dir is None:
        return None
    return namespace_dir / f'{_safe_key(key)}.json'


def _registry_path(name: str) -> Optional[Path]:
    registry_dir = _registry_dir()
    if registry_dir is None:
        return None
    return registry_dir / f'{name}.json'


def load_binary(namespace: str, key: str, max_age_seconds: int = 86400):
    data_path, meta_path = _binary_paths(namespace, key)
    if not data_path or not meta_path or not data_path.exists() or not meta_path.exists():
        return None

    try:
        meta = json.loads(meta_path.read_text(encoding='utf-8'))
    except Exception:
        return None

    fetched_at = float(meta.get('fetched_at') or 0)
    if max_age_seconds is not None and fetched_at:
        if time.time() - fetched_at > max_age_seconds:
            return None

    try:
        content = data_path.read_bytes()
    except Exception:
        return None

    return {
        'content': content,
        'content_type': meta.get('content_type') or 'application/octet-stream',
        'meta': meta,
    }


def store_binary(namespace: str, key: str, content: bytes, content_type: str = 'application/octet-stream', source_url: str = '') -> None:
    data_path, meta_path = _binary_paths(namespace, key)
    if not data_path or not meta_path:
        return

    try:
        data_path.write_bytes(content)
        meta_path.write_text(
            json.dumps({
                'fetched_at': time.time(),
                'content_type': content_type or 'application/octet-stream',
                'source_url': source_url or '',
            }, ensure_ascii=False, indent=2),
            encoding='utf-8',
        )
    except Exception:
        return


def load_json(namespace: str, key: str, max_age_seconds: int = 86400):
    json_path = _json_path(namespace, key)
    if not json_path or not json_path.exists():
        return None

    try:
        payload = json.loads(json_path.read_text(encoding='utf-8'))
    except Exception:
        return None

    fetched_at = float(payload.get('_fetched_at') or 0)
    if max_age_seconds is not None and fetched_at:
        if time.time() - fetched_at > max_age_seconds:
            return None

    return payload.get('data')


def delete_json(namespace: str, key: str) -> None:
    json_path = _json_path(namespace, key)
    if not json_path or not json_path.exists():
        return
    try:
        json_path.unlink()
    except OSError:
        return


def store_json(namespace: str, key: str, payload: Any) -> None:
    json_path = _json_path(namespace, key)
    if not json_path:
        return
    try:
        json_path.write_text(
            json.dumps({
                '_fetched_at': time.time(),
                'data': payload,
            }, ensure_ascii=False, indent=2, default=_json_default),
            encoding='utf-8',
        )
    except Exception:
        return


def _json_default(value):
    if hasattr(value, 'isoformat'):
        try:
            return value.isoformat()
        except Exception:
            pass
    return str(value)


def ensure_registry(name: str, key: str) -> None:
    registry_path = _registry_path(name)
    if not registry_path:
        return

    try:
        if registry_path.exists():
            entries = json.loads(registry_path.read_text(encoding='utf-8'))
            if not isinstance(entries, list):
                entries = []
        else:
            entries = []
        if key not in entries:
            entries.append(key)
        registry_path.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding='utf-8')
    except Exception:
        return


def get_registry(name: str) -> list[str]:
    registry_path = _registry_path(name)
    if not registry_path or not registry_path.exists():
        return []
    try:
        entries = json.loads(registry_path.read_text(encoding='utf-8'))
        if isinstance(entries, list):
            return [str(entry) for entry in entries if str(entry).strip()]
    except Exception:
        pass
    return []


def serialize_consurf_event(payload: dict[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    for key in ('start_date', 'end_date'):
        value = result.get(key)
        if hasattr(value, 'isoformat'):
            try:
                result[key] = value.isoformat()
            except Exception:
                pass
    return result


def deserialize_consurf_event(payload: dict[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    for key in ('start_date', 'end_date'):
        value = result.get(key)
        if isinstance(value, str) and value:
            try:
                result[key] = datetime.fromisoformat(value)
            except Exception:
                pass
    return result