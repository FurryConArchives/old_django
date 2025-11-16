from django.utils.deprecation import MiddlewareMixin
from .models_sitevisit import SiteVisit
from django.utils import timezone


def get_or_create_site_visit(session_key, *, ip_address=None, user_agent=''):
    """Return a single SiteVisit row, deduplicating legacy duplicate session keys."""
    matches = SiteVisit.objects.filter(session_key=session_key).order_by('first_visit', 'pk')
    obj = matches.first()
    if obj is None:
        return SiteVisit.objects.create(
            session_key=session_key,
            ip_address=ip_address,
            user_agent=user_agent,
        ), True

    duplicate_ids = list(matches.values_list('pk', flat=True)[1:])
    if duplicate_ids:
        SiteVisit.objects.filter(pk__in=duplicate_ids).delete()
    return obj, False


class UniqueVisitMiddleware(MiddlewareMixin):
    def process_request(self, request):
        from .hosts import is_api_host

        path = request.path or ''
        if is_api_host(request) or path.startswith('/api/') or path.startswith('/v1/') or path.startswith('/internal/api/'):
            return None

        # Read UA early so we can skip creating sessions for unwanted clients
        ua = request.META.get('HTTP_USER_AGENT', '') or ''
        ua_l = ua.lower()

        # Skip logging for known script user-agents (e.g. aiohttp clients)
        if 'aiohttp' in ua_l:
            return None

        # Continue with normal session-based visit tracking
        session_key = request.session.session_key
        if not session_key:
            request.session.create()
            session_key = request.session.session_key

        xff = request.META.get('HTTP_X_FORWARDED_FOR')
        if xff:
            ip = [p.strip() for p in xff.split(',') if p.strip()][0]
        else:
            ip = request.META.get('REMOTE_ADDR')

        obj, _created = get_or_create_site_visit(
            session_key,
            ip_address=ip,
            user_agent=ua,
        )

        now = timezone.now()
        update_fields = ['last_visit']
        obj.last_visit = now

        if ip and (not obj.ip_address or obj.ip_address != ip):
            obj.ip_address = ip
            update_fields.append('ip_address')

        if ua and (not obj.user_agent or obj.user_agent != ua):
            obj.user_agent = ua
            update_fields.append('user_agent')

        obj.save(update_fields=update_fields)