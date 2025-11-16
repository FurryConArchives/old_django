"""
Django template tag for Telegram Chat Widget
Usage in template: {% load archive_tags %} {% telegram_widget %}
"""

from django import template
from django.urls import reverse

register = template.Library()


@register.inclusion_tag('archive/telegram_widget.html')
def telegram_widget(api_url=None, limit=100):
    """
    Renders the Telegram chat widget.
    
    Usage:
        {% telegram_widget %}
        {% telegram_widget api_url='https://example.com/api' limit=30 %}
    """
    return {
        'api_url': api_url or reverse('internal_telegram_messages_proxy'),
        'limit': limit,
    }
