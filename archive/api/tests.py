from django.core.cache import cache
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, override_settings
from django.urls import Resolver404, get_resolver, reverse

from archive.api.auth import api_scope_for_path, enforce_api_access, enforce_rate_limit
from archive.api.serializers import absolute_url, summarize_schedule_events
from archive.hosts import is_public_api_path
from archive.middleware_hosts import HostSplitMiddleware
from archive.models import AppKey
from archive.sitemaps import STATIC_PAGES
from archive.api.site_content import FAQ_ITEMS, PAGE_CATALOG, STAFF_MEMBERS, page_catalog_entry, serialize_staff_member
from archive.site_pages import FAQ_SEED, plain_text
from archive.telegram_messages import serialize_telegram_history, serialize_telegram_message


class SiteContentTests(SimpleTestCase):
    def test_page_catalog_covers_public_pages(self):
        slugs = {page['slug'] for page in PAGE_CATALOG}
        self.assertTrue({'home', 'faq', 'contact', 'staff', 'rights', 'preservation-policy'} <= slugs)

    def test_faq_and_staff_are_structured(self):
        self.assertGreaterEqual(len(FAQ_ITEMS), 8)
        self.assertTrue(all(item['id'] and item['question'] for item in FAQ_ITEMS))
        self.assertGreaterEqual(len(FAQ_SEED), len(FAQ_ITEMS))
        self.assertTrue(all(item['slug'] and item['question'] and item['answer'] for item in FAQ_SEED))
        self.assertEqual(FAQ_ITEMS[0]['answer'], plain_text(FAQ_SEED[0]['answer']))
        member = serialize_staff_member(STAFF_MEMBERS[0])
        self.assertEqual(member['username'], 'furtabs')
        self.assertTrue(member['telegram_url'].endswith('/furtabs'))

    def test_unknown_page_is_none(self):
        self.assertIsNone(page_catalog_entry('not-a-page'))


class ScheduleSummaryTests(SimpleTestCase):
    def test_extracts_days_and_rooms(self):
        events = [
            {'title': 'Opening', 'day': '2025-01-01', 'room': 'Main'},
            {'title': 'Panel', 'date': '2025-01-01', 'room': {'name': 'Panel A'}},
            {'title': 'Dance', 'day': '2025-01-02', 'location': 'Ballroom'},
        ]
        summary = summarize_schedule_events(events)
        self.assertEqual(summary['events_count'], 3)
        self.assertEqual(summary['days'], ['2025-01-01', '2025-01-02'])
        self.assertEqual(summary['rooms'], ['Main', 'Panel A', 'Ballroom'])


class SitemapConfigTests(SimpleTestCase):
    def test_sitemap_route_and_static_pages(self):
        names = {name for name, _priority, _freq in STATIC_PAGES}
        self.assertTrue({'index', 'document_list', 'category_browse'} <= names)
        self.assertEqual(reverse('sitemap'), '/sitemap.xml')
        self.assertEqual(reverse('robots_txt'), '/robots.txt')


class TelegramMessageSerializeTests(SimpleTestCase):
    def setUp(self):
        self.request = RequestFactory().get('/v1/telegram/')

    def test_parses_name_pfp_and_message(self):
        payload = {
            'count': 2,
            'chats': [{'id': -100, 'title': 'Furry Con Archives [Chat]', 'username': 'furryconarchives'}],
            'users': [
                {'id': 7, 'username': 'furtabs', 'first_name': 'Tabs', 'last_name': ''},
            ],
            'messages': [
                {
                    'id': 10,
                    'from_id': 7,
                    'date': 1789004179,
                    'message': 'hello archive',
                },
                {
                    'id': 11,
                    'from_id': 7,
                    'date': 1789004180,
                    'message': '',
                    'action': {'_': 'messageActionChatJoinedByRequest'},
                },
                {
                    'id': 12,
                    'from_id': 7,
                    'date': 1789004181,
                    'message': '',
                    'media': {'photo': {'id': 99}},
                    'peer_id': -100,
                },
            ],
        }
        data = serialize_telegram_history(payload, self.request, limit=100, page=1)
        self.assertEqual(data['channel']['username'], 'furryconarchives')
        self.assertEqual(len(data['results']), 3)

        first = data['results'][0]
        self.assertEqual(first['name'], 'Tabs')
        self.assertEqual(first['username'], 'furtabs')
        self.assertEqual(first['message'], 'hello archive')
        self.assertTrue(first['pfp'].endswith('/internal/api/telegram-avatar/furtabs'))
        self.assertEqual(first['date'], '2026-09-10T01:36:19+00:00')

        service = data['results'][1]
        self.assertEqual(service['type'], 'service')
        self.assertEqual(service['message'], 'Tabs joined the group')

        photo = data['results'][2]
        self.assertIn('photo_id=99', photo['media_url'])

    def test_peer_user_from_id(self):
        users = {5: {'id': 5, 'username': 'tamani', 'first_name': 'Tamani Wolf'}}
        parsed = serialize_telegram_message(
            {'id': 1, 'from_id': {'_': 'peerUser', 'user_id': 5}, 'message': 'hi'},
            users,
            self.request,
        )
        self.assertEqual(parsed['name'], 'Tamani Wolf')
        self.assertEqual(parsed['message'], 'hi')


class AppKeyScopeTests(SimpleTestCase):
    def test_legacy_convention_is_dashboard_only(self):
        key = AppKey(scope='convention', scopes=[])
        self.assertTrue(key.has_scope(AppKey.SCOPE_CON_DASHBOARD))
        self.assertFalse(key.has_scope('documents'))
        self.assertFalse(key.has_scope(AppKey.SCOPE_API))

    def test_legacy_all_covers_api_and_dashboard(self):
        key = AppKey(scope='all', scopes=[])
        self.assertTrue(key.has_scope(AppKey.SCOPE_API))
        self.assertTrue(key.has_scope('documents'))
        self.assertTrue(key.has_scope(AppKey.SCOPE_CON_DASHBOARD))

    def test_route_scope_does_not_grant_other_routes_or_dashboard(self):
        key = AppKey(scopes=['documents'])
        self.assertTrue(key.has_scope('documents'))
        self.assertFalse(key.has_scope('schedules'))
        self.assertFalse(key.has_scope(AppKey.SCOPE_CON_DASHBOARD))

    def test_full_api_does_not_grant_dashboard(self):
        key = AppKey(scopes=['api'])
        self.assertTrue(key.has_scope('telegram'))
        self.assertFalse(key.has_scope(AppKey.SCOPE_CON_DASHBOARD))

    def test_encodes_scopes_and_rate_in_scope_column(self):
        key = AppKey(scopes=['documents', 'schedules'], rate_per_minute=15)
        self.assertEqual(key.scope, 'documents,schedules:15')
        self.assertEqual(key.rate_per_minute, 15)
        self.assertEqual(set(key.scopes), {'documents', 'schedules'})

    def test_all_scopes_fit_in_scope_column(self):
        encoded = AppKey.encode_scope_field(
            [choice[0] for choice in AppKey.SCOPE_CHOICES],
            rate_per_minute=10**9,
        )
        self.assertLessEqual(len(encoded), AppKey._meta.get_field('scope').max_length)


class ApiScopePathTests(SimpleTestCase):
    def test_maps_v1_routes(self):
        self.assertEqual(api_scope_for_path('/v1/documents/slug/'), 'documents')
        self.assertEqual(api_scope_for_path('/v1/schedules/conventions/foo/'), 'schedules')
        self.assertIsNone(api_scope_for_path('/v1/'))
        self.assertEqual(api_scope_for_path('/api/v1/documents/slug/'), 'documents')
        self.assertIsNone(api_scope_for_path('/api/events/anthrocon-2026'))
        self.assertIsNone(api_scope_for_path('/api/conbook-lookup'))


class ApiRateLimitTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()
        cache.clear()

    def tearDown(self):
        cache.clear()

    def _request(self, path='/v1/documents/'):
        request = self.factory.get(path)
        request.META['REMOTE_ADDR'] = '203.0.113.10'
        return request

    @override_settings(PUBLIC_API_RATE_PER_MIN=1)
    def test_anonymous_limit_is_one_per_minute(self):
        request = self._request()
        self.assertIsNone(enforce_rate_limit(request))
        limited = enforce_rate_limit(request)
        self.assertIsNotNone(limited)
        self.assertEqual(limited.status_code, 429)

    def test_key_uses_configured_limit(self):
        key = AppKey(pk=9, scopes=['documents'], rate_per_minute=2)
        request = self._request()
        self.assertIsNone(enforce_rate_limit(request, app_key=key))
        self.assertIsNone(enforce_rate_limit(request, app_key=key))
        limited = enforce_rate_limit(request, app_key=key)
        self.assertEqual(limited.status_code, 429)

    def test_blank_key_rate_is_unlimited(self):
        key = AppKey(pk=10, scopes=['documents'], rate_per_minute=None)
        request = self._request()
        for _ in range(3):
            self.assertIsNone(enforce_rate_limit(request, app_key=key))

    def test_missing_scope_is_forbidden(self):
        key = AppKey(pk=11, scopes=['schedules'], rate_per_minute=60)
        denied = enforce_api_access(self._request(), scope='documents', app_key=key)
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(denied['Content-Type'], 'application/json')

    @override_settings(SITE_API_KEY='site-key-for-tests', PUBLIC_API_RATE_PER_MIN=1)
    def test_site_api_key_is_not_rate_limited(self):
        request = self._request()
        request.META['HTTP_X_APP_KEY'] = 'site-key-for-tests'
        for _ in range(3):
            self.assertIsNone(enforce_rate_limit(request))


def _passthrough(request):
    return HttpResponse('ok')


@override_settings(
    FRONTEND_HOST='old.furryconarchives.org',
    API_HOST='api.furryconarchives.org',
    FRONTEND_BASE_URL='https://old.furryconarchives.org',
    API_BASE_URL='https://api.furryconarchives.org',
)
class HostSplitTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.middleware = HostSplitMiddleware(_passthrough)

    def test_apex_html_redirects_to_old(self):
        request = self.factory.get('/conventions', HTTP_HOST='furryconarchives.org')
        response = self.middleware(request)
        self.assertEqual(response.status_code, 308)
        self.assertEqual(response['Location'], 'https://old.furryconarchives.org/conventions')

    def test_apex_api_redirects_to_api_host(self):
        request = self.factory.get('/v1/stats/', HTTP_HOST='furryconarchives.org')
        response = self.middleware(request)
        self.assertEqual(response.status_code, 308)
        self.assertEqual(response['Location'], 'https://api.furryconarchives.org/v1/stats/')

    def test_frontend_host_redirects_public_api(self):
        request = self.factory.get('/v1/documents/', HTTP_HOST='old.furryconarchives.org')
        response = self.middleware(request)
        self.assertEqual(response.status_code, 308)
        self.assertEqual(response['Location'], 'https://api.furryconarchives.org/v1/documents/')

    def test_frontend_keeps_telegram_proxy(self):
        request = self.factory.get('/api/telegram-messages', HTTP_HOST='old.furryconarchives.org')
        response = self.middleware(request)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(request.urlconf, 'furry_archive.frontend_urls')

    def test_api_host_uses_api_urlconf(self):
        request = self.factory.get('/v1/', HTTP_HOST='api.furryconarchives.org')
        response = self.middleware(request)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(request.urlconf, 'furry_archive.api_urls')
        self.assertEqual(response['Access-Control-Allow-Origin'], '*')
        resolver = get_resolver('furry_archive.api_urls')
        self.assertTrue(resolver.resolve('/v1/'))
        with self.assertRaises(Resolver404):
            resolver.resolve('/documents')

    def test_local_host_keeps_combined_routes(self):
        request = self.factory.get('/v1/')
        response = self.middleware(request)
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(getattr(request, 'urlconf', None))
        self.assertEqual(reverse('index'), '/')
        self.assertEqual(reverse('api_v1_root'), '/v1/')

    def test_split_absolute_urls(self):
        request = self.factory.get('/v1/documents/', HTTP_HOST='api.furryconarchives.org')
        self.assertEqual(
            absolute_url(request, '/documents/guide'),
            'https://old.furryconarchives.org/documents/guide',
        )
        self.assertEqual(
            absolute_url(request, '/v1/documents/guide/'),
            'https://api.furryconarchives.org/v1/documents/guide/',
        )

    def test_legacy_api_v1_redirects_to_v1(self):
        request = self.factory.get('/api/v1/stats/?q=1', HTTP_HOST='furryconarchives.org')
        response = self.middleware(request)
        self.assertEqual(response.status_code, 308)
        self.assertEqual(response['Location'], 'https://api.furryconarchives.org/v1/stats/?q=1')

    def test_telegram_proxy_is_not_public_api(self):
        self.assertFalse(is_public_api_path('/api/telegram-messages'))
        self.assertTrue(is_public_api_path('/v1/documents/'))
        self.assertTrue(is_public_api_path('/api/v1/documents/'))
        self.assertFalse(is_public_api_path('/api/search'))


class VaultAvatarUrlTests(SimpleTestCase):
    def test_api_urlconf_does_not_need_named_frontend_routes(self):
        from django.urls import set_urlconf
        from archive.api.serializers import serialize_vault_donor_entry
        from archive.utils import resolve_vault_contributor_avatar_url

        set_urlconf('furry_archive.api_urls')
        try:
            self.assertEqual(
                resolve_vault_contributor_avatar_url('telegram', 'furtabs', overrides={}),
                '/internal/api/telegram-avatar/furtabs',
            )
            request = RequestFactory().get('/v1/vault/', HTTP_HOST='api.furryconarchives.org')
            entry = serialize_vault_donor_entry(
                {'type': 'telegram', 'username': 'furtabs', 'display_name': 'Tabs'},
                request,
                avatar_overrides={},
            )
            self.assertEqual(
                entry['avatar_url'],
                'https://old.furryconarchives.org/internal/api/telegram-avatar/furtabs',
            )
        finally:
            set_urlconf(None)
