from django.core.management.base import BaseCommand
from django.core.cache import cache
from archive.views import internal_telegram_avatar_proxy
from django.http import HttpRequest
from django.contrib.auth.models import AnonymousUser
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
import base64


class Command(BaseCommand):
    help = 'Update cached Telegram avatars in the background'

    def add_arguments(self, parser):
        parser.add_argument(
            '--interval',
            type=int,
            default=3600,  # 1 hour default
            help='Interval between update checks in seconds (default: 3600)',
        )
        parser.add_argument(
            '--once',
            action='store_true',
            help='Run update check once and exit',
        )
        parser.add_argument(
            '--usernames',
            nargs='*',
            help='Specific usernames to check (default: check all cached avatars)',
        )

    def handle(self, *args, **options):
        interval = options['interval']
        once = options['once']
        specific_usernames = options.get('usernames')

        if once:
            self.stdout.write('Running one-time avatar update check...')
            self.update_avatars(specific_usernames)
            self.stdout.write(self.style.SUCCESS('Avatar update check completed'))
        else:
            self.stdout.write(f'Starting background avatar update checker (interval: {interval}s)...')
            self.run_background_checker(interval, specific_usernames)

    def run_background_checker(self, interval, specific_usernames):
        """Run the avatar checker in a background thread"""
        def checker_thread():
            while True:
                try:
                    self.stdout.write('Checking for avatar updates...')
                    self.update_avatars(specific_usernames)
                    self.stdout.write(self.style.SUCCESS('Avatar update check completed'))
                except Exception as e:
                    self.stderr.write(f'Error during avatar update check: {e}')

                self.stdout.write(f'Sleeping for {interval} seconds...')
                time.sleep(interval)

        # Start the background thread
        thread = threading.Thread(target=checker_thread, daemon=True)
        thread.start()

        # Keep the main thread alive
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            self.stdout.write('Stopping avatar update checker...')

    def update_avatars(self, specific_usernames=None):
        """Check and update cached avatars"""
        # Get all cached usernames
        if specific_usernames:
            usernames_to_check = [username.lstrip('@') for username in specific_usernames]
        else:
            cached_usernames = cache.get('telegram_cached_usernames', set())
            usernames_to_check = list(cached_usernames)
        
        if not usernames_to_check:
            self.stdout.write('No cached avatars to check')
            return
        
        self.stdout.write(f'Checking {len(usernames_to_check)} cached avatars')
        
        cache_keys = [f'telegram_avatar_{username}' for username in usernames_to_check]

        updated_count = 0
        checked_count = 0

        def check_single_avatar(cache_key):
            username = cache_key.replace('telegram_avatar_', '')
            cached_data = cache.get(cache_key)

            if not cached_data:
                return False, username

            # Create a mock request to call the avatar proxy
            request = HttpRequest()
            request.method = 'GET'
            request.user = AnonymousUser()

            try:
                # Call the avatar proxy function directly
                from archive.views import internal_telegram_avatar_proxy
                response = internal_telegram_avatar_proxy(request, username)

                if response.status_code == 200:
                    # Compare with cached data
                    new_data = base64.b64encode(response.content).decode('utf-8')
                    cached_b64 = cached_data.get('data', '')

                    if new_data != cached_b64:
                        # Avatar has changed, update cache
                        cache.set(cache_key, {
                            'data': new_data,
                            'content_type': response.get('Content-Type', 'image/jpeg')
                        }, 86400)
                        return True, username
                    else:
                        return False, username  # No change
                else:
                    self.stderr.write(f'Failed to fetch avatar for {username}: {response.status_code}')
                    return False, username

            except Exception as e:
                self.stderr.write(f'Error checking avatar for {username}: {e}')
                return False, username

        # Use ThreadPoolExecutor for concurrent checking
        with ThreadPoolExecutor(max_workers=5) as executor:
            future_to_key = {executor.submit(check_single_avatar, key): key for key in cache_keys}

            for future in as_completed(future_to_key):
                checked_count += 1
                changed, username = future.result()
                if changed:
                    updated_count += 1
                    self.stdout.write(f'Updated avatar for {username}')

        self.stdout.write(f'Checked {checked_count} avatars, updated {updated_count}')