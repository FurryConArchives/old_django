"""Display descriptions for documents (Consurf event text, not IA backup boilerplate)."""

from __future__ import annotations


def is_ia_backup_description(text: str | None) -> bool:
    """True when text matches the auto-generated Internet Archive backup blurb."""
    if not text or not isinstance(text, str):
        return False
    t = text.strip()
    return ' was held in ' in t and 'The theme is' in t


def _consurf_field(event, key: str):
    if not event:
        return None
    if isinstance(event, dict):
        return event.get(key)
    return getattr(event, key, None)


def convention_name_for_document(document) -> str | None:
    conv_name = (document.convention_name or '').strip()
    if not conv_name and getattr(document, 'category_id', None):
        category = document.category
        conv_name = category.get_primary_convention_name() or category.name
    return conv_name or None


def slug_hint_for_document(document) -> str | None:
    slug = getattr(document, 'slug', None) or ''
    if not slug:
        return None
    from archive.consurf_slugs import strip_document_slug_suffix

    # Keep year and theme segments from slugs like "furdu-2017-the-outback-conbook".
    trimmed = strip_document_slug_suffix(slug)
    return trimmed or None


def _description_from_consurf_event(consurf_event) -> str | None:
    if not consurf_event:
        return None
    desc = _consurf_field(consurf_event, 'event_description')
    if desc and isinstance(desc, str):
        stripped = desc.strip()
        if stripped and not is_ia_backup_description(stripped):
            return stripped
    return None


def consurf_event_for_document(
    document,
    *,
    allow_network: bool = True,
    refresh: bool = False,
):
    """Full Consurf/MLPCON event payload for this document's convention and year."""
    conv_name = convention_name_for_document(document)
    if not conv_name:
        return None

    from archive.views import _get_consurf_event

    try:
        return _get_consurf_event(
            conv_name,
            document.year,
            slug_hint=slug_hint_for_document(document),
            refresh=refresh,
            allow_network=allow_network,
        )
    except Exception:
        return None


def event_description_for_document(
    document,
    *,
    allow_network: bool = False,
    consurf_event=None,
) -> str | None:
    """Consurf/MLPCON event description for this document's convention and year."""
    if consurf_event is not None:
        return _description_from_consurf_event(consurf_event)

    event = consurf_event_for_document(
        document,
        allow_network=allow_network,
    )
    return _description_from_consurf_event(event)


def resolve_event_display_description(
    document,
    consurf_event=None,
    *,
    allow_network: bool = False,
) -> str | None:
    """Best event blurb for document pages: Consurf fields, then category text."""
    desc = event_description_for_document(
        document,
        allow_network=allow_network,
        consurf_event=consurf_event,
    )
    if desc:
        return desc

    category = getattr(document, 'category', None)
    if category:
        cat_desc = (category.description or '').strip()
        if cat_desc and not is_ia_backup_description(cat_desc):
            return cat_desc
    return None


def display_description_for_document(document, *, allow_network: bool = False) -> str | None:
    """Human-readable description for UI: event text first, never IA backup boilerplate."""
    event_desc = resolve_event_display_description(document, allow_network=allow_network)
    if event_desc:
        return event_desc

    raw = (document.description or '').strip()
    if raw and not is_ia_backup_description(raw):
        return raw
    return None


def attach_display_descriptions(documents, *, allow_network: bool = False) -> None:
    for doc in documents:
        doc.display_description = display_description_for_document(
            doc,
            allow_network=allow_network,
        )
