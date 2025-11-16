from django.core.management.base import BaseCommand
import logging
import time

from archive.views import queue_auto_fetch_refresh, start_auto_fetch, stop_auto_fetch
from archive.models_sitevisit import ScheduleAutoFetchSetting


logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Start or stop the schedule auto-fetch job.'

    def add_arguments(self, parser):
        parser.add_argument('action', choices=['start', 'stop'])

    def handle(self, *args, **options):
        action = options['action']
        setting, _ = ScheduleAutoFetchSetting.objects.get_or_create(id=1)
        if action == 'start':
            setting.enabled = True
            setting.save()
            start_auto_fetch()
            logger.info('auto_fetch command: queueing immediate refresh cycle')
            queue_auto_fetch_refresh('auto_fetch command')
            self.stdout.write(self.style.SUCCESS('Auto-fetch started. Press Ctrl+C to stop this worker.'))
            try:
                while True:
                    setting.refresh_from_db(fields=['enabled'])
                    if not setting.enabled:
                        logger.info('auto_fetch command: shared setting disabled; stopping worker loop')
                        stop_auto_fetch()
                        break
                    time.sleep(5)
            except KeyboardInterrupt:
                logger.info('auto_fetch command: interrupted; stopping worker loop')
                stop_auto_fetch()
        elif action == 'stop':
            setting.enabled = False
            setting.save()
            stop_auto_fetch()
            logger.info('auto_fetch command: stopped auto-fetch job')
            self.stdout.write(self.style.SUCCESS('Auto-fetch stopped.'))
