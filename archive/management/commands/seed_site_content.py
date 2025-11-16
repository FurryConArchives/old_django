from django.core.management.base import BaseCommand
from django.db import connection

from archive.models import ContactChannel, FaqEntry, SitePageSection
from archive.site_pages import ensure_default_site_content


class Command(BaseCommand):
    help = 'Create FAQ / contact / page-section tables if needed and seed default rows.'

    def handle(self, *args, **options):
        existing = set(connection.introspection.table_names())
        with connection.schema_editor() as editor:
            for model in (FaqEntry, ContactChannel, SitePageSection):
                if model._meta.db_table not in existing:
                    editor.create_model(model)
                    self.stdout.write(self.style.SUCCESS(f'Created {model._meta.db_table}'))
        ensure_default_site_content()
        self.stdout.write(self.style.SUCCESS(
            f'Seeded site content: {FaqEntry.objects.count()} FAQ, '
            f'{ContactChannel.objects.count()} contact, '
            f'{SitePageSection.objects.count()} sections.'
        ))
