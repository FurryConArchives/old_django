from django.apps import AppConfig


from apscheduler.schedulers.background import BackgroundScheduler
from django.conf import settings
import logging

class ArchiveConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'archive'

    def ready(self):
        # monkey‑patch admin boolean icon to avoid KeyError on strange values
        try:
            from django.contrib.admin.templatetags import admin_list
            orig = admin_list._boolean_icon
            def _safe_boolean_icon(val):
                try:
                    return orig(val)
                except KeyError:
                    try:
                        return orig(bool(val))
                    except Exception:
                        return orig(None)
            admin_list._boolean_icon = _safe_boolean_icon
        except Exception:
            pass

        from . import scheduler
        from .schema import ensure_appkey_scope_column
        ensure_appkey_scope_column()
        if settings.DEBUG:
            # Only start scheduler in debug/dev mode to avoid multiple scheduler instances in production
            if not scheduler.scheduler.running:
                scheduler.scheduler.start()
                logging.info('APScheduler started by Archive app')

    verbose_name = 'PDF Archive'

