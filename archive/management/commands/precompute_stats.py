from django.core.management.base import BaseCommand
from django.utils import timezone
from django.core.cache import cache
from archive.models import PDFDocument, DownloadLog
from django.db.models import Count, Sum, Value
from django.db.models.functions import TruncHour, Coalesce

class Command(BaseCommand):
    help = 'Precompute and cache global analytics/statistics for admin dashboard'

    def add_arguments(self, parser):
        parser.add_argument('--minutes', type=int, default=10, help='Cache TTL in minutes')
        parser.add_argument('--days', type=int, default=30, help='Window in days to compute')
        parser.add_argument('--force', action='store_true', help='Force recompute even if cache exists')

    def handle(self, *args, **options):
        minutes = options.get('minutes') or 10
        days = options.get('days') or 30
        force = options.get('force')

        now = timezone.now()
        start_30d = now - timezone.timedelta(days=days)

        cache_key = f"admin_stats:global:::{int(start_30d.timestamp() * 1000)}"
        if cache.get(cache_key) and not force:
            self.stdout.write(self.style.SUCCESS('Cache already present — use --force to recompute'))
            return

        # Aggregate hourly counts
        logs_qs = DownloadLog.objects.filter(downloaded_at__gte=start_30d)
        hour_agg = logs_qs.annotate(hour=TruncHour('downloaded_at')).values('hour').annotate(count=Count('id')).order_by('hour')
        chart_data = {'labels': [h['hour'].strftime('%Y-%m-%dT%H:00:00Z') for h in hour_agg], 'counts': [h['count'] for h in hour_agg]}

        # 7d and 24h
        start_7d = now - timezone.timedelta(days=7)
        start_24h = now - timezone.timedelta(days=1)
        hour_agg_7d = logs_qs.filter(downloaded_at__gte=start_7d).annotate(hour=TruncHour('downloaded_at')).values('hour').annotate(count=Count('id')).order_by('hour')
        chart_data_7d = {'labels': [h['hour'].strftime('%Y-%m-%dT%H:00:00Z') for h in hour_agg_7d], 'counts': [h['count'] for h in hour_agg_7d]}
        hour_agg_24h = logs_qs.filter(downloaded_at__gte=start_24h).annotate(hour=TruncHour('downloaded_at')).values('hour').annotate(count=Count('id')).order_by('hour')
        chart_data_24h = {'labels': [h['hour'].strftime('%Y-%m-%dT%H:00:00Z') for h in hour_agg_24h], 'counts': [h['count'] for h in hour_agg_24h]}

        # Referers
        ref_qs = logs_qs.exclude(referer__isnull=True).exclude(referer='').values('referer').annotate(count=Count('id'))
        referer_stats = []
        from urllib.parse import urlparse
        for r in ref_qs:
            referer_value = (r.get('referer') or '').strip()
            parsed = urlparse(referer_value)
            label = parsed.netloc or referer_value or 'Direct / Unknown'
            referer_stats.append((label, r.get('count', 0)))
        referer_stats = sorted(referer_stats, key=lambda item: item[1], reverse=True)

        top_qs = DownloadLog.objects.filter(downloaded_at__gte=start_30d).values(title=Coalesce('document__title', Value('Unknown'))).annotate(count=Count('id')).order_by('-count')[:15]
        top_pages_stats = [(t['title'], t['count']) for t in top_qs]

        selected_downloads_30d = logs_qs.count()
        unique_ips_30d = logs_qs.exclude(ip_address__isnull=True).exclude(ip_address='').values('ip_address').distinct().count()

        total_downloads_agg = PDFDocument.objects.aggregate(total=Coalesce(Sum('download_count'), 0))
        all_downloads_total = total_downloads_agg.get('total') or 0

        summary_cards = [
            {'label': 'Downloads', 'value': selected_downloads_30d},
            {'label': 'All downloads', 'value': all_downloads_total},
            {'label': 'Unique IPs', 'value': unique_ips_30d},
        ]

        payload = {
            'chart_data': chart_data,
            'chart_data_7d': chart_data_7d,
            'chart_data_24h': chart_data_24h,
            'referer_stats': referer_stats,
            'top_pages_stats': top_pages_stats,
            'summary_cards': summary_cards,
        }

        cache.set(cache_key, payload, int(minutes) * 60)
        self.stdout.write(self.style.SUCCESS(f'Precomputed and cached global stats for {days} days (ttl={minutes}m)'))
