from django import template

register = template.Library()

@register.filter
def split(value, delimiter=','):
    """Split a string by the given delimiter (default: comma)."""
    if value:
        return [v.strip() for v in value.split(delimiter) if v.strip()]
    return []

@register.filter
def get_item(dictionary, key):
    return dictionary.get(key)
