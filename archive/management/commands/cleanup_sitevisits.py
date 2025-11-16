from django.core.management.base import BaseCommand
import csv

from archive.models_sitevisit import SiteVisit


class Command(BaseCommand):
    help = 'Export and/or delete SiteVisit rows matching a user-agent substring (default: aiohttp/3.13.3)'

    def add_arguments(self, parser):
        parser.add_argument('--ua-substring', default='aiohttp/3.13.3', help='User-agent substring to match (case-insensitive)')
        parser.add_argument('--export', help='Path to CSV file to export matching rows before deletion')
        parser.add_argument('--delete', action='store_true', help='Delete matching rows')
        parser.add_argument('--dry-run', action='store_true', help='Show count only, do not export or delete')

    def handle(self, *args, **options):
        ua_sub = options['ua_substring']
        export_path = options.get('export')
        do_delete = options.get('delete', False)
        dry_run = options.get('dry_run', False)

        qs = SiteVisit.objects.filter(user_agent__icontains=ua_sub)
        count = qs.count()
        self.stdout.write(f'Found {count} SiteVisit rows matching user-agent substring "{ua_sub}"')

        if dry_run:
            return

        if export_path:
            with open(export_path, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(['id', 'session_key', 'ip_address', 'user_agent', 'first_visit', 'last_visit'])
                for r in qs.iterator():
                    writer.writerow([r.id, r.session_key, r.ip_address or '', r.user_agent or '', r.first_visit, r.last_visit])
            self.stdout.write(self.style.SUCCESS(f'Exported {count} rows to {export_path}'))

        if do_delete:
            deleted, _ = qs.delete()
            self.stdout.write(self.style.SUCCESS(f'Deleted {deleted} SiteVisit rows'))
