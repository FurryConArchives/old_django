from django.core.management.base import BaseCommand
from django.utils import timezone
from django.db import transaction
from django.db.models import Q
import time

from archive.models import DownloadLog
from archive.views import _get_ip_location

class Command(BaseCommand):
    help = 'Backfill missing IP locations in DownloadLog using _get_ip_location (safe, rate-limited)'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=0, help='Maximum number of distinct IPs to process (0 = all)')
        parser.add_argument('--delay', type=float, default=1.0, help='Delay in seconds between external lookups')
        parser.add_argument('--days', type=int, default=0, help='Only consider logs from the last N days (0 = all)')
        parser.add_argument('--dry-run', action='store_true', help="Don't write to DB; only show what would be changed")

    def handle(self, *args, **options):
        limit = options['limit']
        delay = options['delay']
        days = options['days']
        dry_run = options['dry_run']

        qs = DownloadLog.objects.exclude(ip_address__isnull=True).exclude(ip_address='')
        if days and days > 0:
            cutoff = timezone.now() - timezone.timedelta(days=days)
            qs = qs.filter(downloaded_at__gte=cutoff)

        ips_qs = qs.values_list('ip_address', flat=True).distinct().order_by('ip_address')
        ips = list(ips_qs)
        if limit and limit > 0:
            ips = ips[:limit]

        total = len(ips)
        self.stdout.write(self.style.NOTICE(f'Found {total} unique IP(s) to check'))

        for idx, ip in enumerate(ips, start=1):
            # Prefer the latest existing location for this IP if present
            latest_with_loc = (
                DownloadLog.objects
                .filter(ip_address=ip)
                .exclude(location__isnull=True)
                .exclude(location='')
                .order_by('-downloaded_at')
                .first()
            )
            if latest_with_loc:
                loc = latest_with_loc.location
                source = 'existing'
            else:
                loc = _get_ip_location(ip)
                source = 'lookup' if loc else 'none'

            if loc:
                self.stdout.write(f'[{idx}/{total}] {ip} -> {loc} ({source})')
                if not dry_run:
                    with transaction.atomic():
                        DownloadLog.objects.filter(ip_address=ip).filter(Q(location__isnull=True) | Q(location='')).update(location=loc)
            else:
                self.stdout.write(f'[{idx}/{total}] {ip} -> no location ({source})')

            # delay to avoid hammering external service
            time.sleep(delay)

        self.stdout.write(self.style.SUCCESS('Backfill complete'))
