
from django import template
from archive.utils import (
    get_telegram_profile_picture_url,
    parse_donor_entry_value,
    resolve_vault_contributor_avatar_url,
    resolve_vault_contributor_display_name,
)
import re

register = template.Library()


@register.filter
def telegram_avatar(username):
    """Get Telegram profile picture URL for a username"""
    if not username:
        return None
    return get_telegram_profile_picture_url(username)


@register.filter
def format_username(username):
    """Format username with proper capitalization and spacing"""
    if not username:
        return username
    # Replace underscores and camelCase with spaces
    # Convert CamelCase to Camel Case
    import re
    # Insert space before capital letters (except at the beginning)
    result = re.sub(r'(?<!^)(?=[A-Z])', ' ', username)
    # Replace underscores with spaces
    result = result.replace('_', ' ')
    # Capitalize first letter of each word
    return ' '.join(word.capitalize() for word in result.split())


@register.filter
def trim(value):
    """Remove leading and trailing whitespace"""
    if not value:
        return value
    return value.strip()


@register.filter
def archive_description_excerpt(value):
    """Return the first meaningful paragraph of a document description.

    This trims common archive/preservation boilerplate that may be appended
    after the actual convention summary.
    """
    if not value:
        return ''

    text = str(value).replace('\r\n', '\n').replace('\r', '\n').strip()
    if not text:
        return ''

    paragraphs = [p.strip() for p in re.split(r'\n\s*\n+', text) if p.strip()]
    text = paragraphs[0] if paragraphs else text

    boilerplate_markers = [
        'The materials included here were originally distributed to convention attendees',
        'Convention booklets (conbooks) are creative works that may be protected by copyright',
        'The Furry Con Archive respects the rights of copyright holders',
        'This presentation constitutes fair use under Section 107',
    ]

    cut_at = None
    for marker in boilerplate_markers:
        idx = text.find(marker)
        if idx != -1 and (cut_at is None or idx < cut_at):
            cut_at = idx

    if cut_at is not None:
        text = text[:cut_at].strip()

    return text.rstrip(' -–—,;:.')


@register.filter
def split(value, separator):
    """Split a string by separator"""
    if not value:
        return []
    return value.split(separator)


@register.filter
def parse_donor_entry(donor_entry):
    return parse_donor_entry_value(donor_entry)


@register.simple_tag
def vault_contributor_avatar(donor_data):
    """Resolve avatar URL for a parsed vault donor entry."""
    if not donor_data or donor_data.get('type') not in ('telegram', 'discord'):
        return None
    return resolve_vault_contributor_avatar_url(
        donor_data['type'],
        donor_data.get('username') or '',
        fallback_avatar=donor_data.get('avatar'),
    )


@register.simple_tag
def vault_contributor_display_name(donor_data):
    """Resolve the current display name for a parsed vault donor entry."""
    if not donor_data:
        return ''
    account_type = donor_data.get('type')
    account_id = (donor_data.get('username') or '').strip()
    fallback = (donor_data.get('display_name') or account_id).strip()
    return resolve_vault_contributor_display_name(account_type, account_id, fallback=fallback)

@register.simple_tag
def git_version_hash():
    """Get the current git commit hash"""
    import subprocess
    try:
        # Get the short commit hash
        result = subprocess.run(
            ['git', 'rev-parse', '--short', 'HEAD'],
            cwd='.',
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception as e:
        print(f"Error getting git hash: {e}")
    
    return "unknown"