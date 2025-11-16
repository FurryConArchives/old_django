from django.core.management.base import BaseCommand
from django.db.models import Count, Min, Max
import csv
import sys

from archive.models_sitevisit import SiteVisit


class Command(BaseCommand):
    help = 'Export SiteVisit statistics to CSV (group by IP or session)'

    def add_arguments(self, parser):
        parser.add_argument('--by', choices=['ip', 'session'], default='ip', help='Group by "ip" or "session"')
        parser.add_argument('--output', default='sitevisits_by_ip.csv', help='Output CSV file path (use - for stdout)')

    def handle(self, *args, **options):
        group_by = options['by']
        output = options['output']

        if output == '-':
            out_f = sys.stdout
            writer = csv.writer(out_f)
        else:
            out_f = open(output, 'w', newline='', encoding='utf-8')
            writer = csv.writer(out_f)

        if group_by == 'ip':
            qs = (
                SiteVisit.objects
                .values('ip_address')
                .annotate(unique_sessions=Count('session_key', distinct=True), total_visits=Count('id'), first_visit=Min('first_visit'), last_visit=Max('last_visit'))
                .order_by('-unique_sessions')
            )
            writer.writerow(['ip_address', 'unique_sessions', 'total_visits', 'first_visit', 'last_visit'])
            for row in qs:
                writer.writerow([row.get('ip_address') or '', row.get('unique_sessions'), row.get('total_visits'), row.get('first_visit'), row.get('last_visit')])

        else:  # session
            qs = (
                SiteVisit.objects
                .values('session_key', 'ip_address')
                .annotate(first_visit=Min('first_visit'), last_visit=Max('last_visit'), visits=Count('id'))
                .order_by('-visits')
            )
            writer.writerow(['session_key', 'ip_address', 'visits', 'first_visit', 'last_visit'])
            for row in qs:
                writer.writerow([row.get('session_key'), row.get('ip_address') or '', row.get('visits'), row.get('first_visit'), row.get('last_visit')])

        if output != '-':
            out_f.close()
        self.stdout.write(self.style.SUCCESS(f'Exported sitevisit stats grouped by "{group_by}" to {output}'))
