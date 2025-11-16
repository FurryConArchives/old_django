"""
Django management command to clear cache for PDF page images.

Usage:
    python manage.py clear_pdf_cache                    # Clear entire Django cache (recommended)
    python manage.py clear_pdf_cache --all              # Same as above
"""

from django.core.management.base import BaseCommand
from django.core.cache import cache


class Command(BaseCommand):
    help = 'Clear cache for PDF page images (clears entire Django cache)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--all',
            action='store_true',
            help='Clear entire Django cache (default behavior)',
        )

    def handle(self, *args, **options):
        self.clear_cache()

    def clear_cache(self):
        """Clear the entire Django cache"""
        try:
            cache.clear()
            self.stdout.write(self.style.SUCCESS(
                '✓ Cleared entire Django cache\n'
                'All PDF page images will be regenerated on next access.'
            ))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'Error clearing cache: {e}'))
            self.stdout.write(self.style.WARNING(
                '\nNote: If cache is not configured, you may need to:\n'
                '1. Restart your Django server (if using in-memory cache)\n'
                '2. Or wait for the cache to expire (24 hours)'
            ))

