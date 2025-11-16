from django.db import models
from django.utils import timezone

class ScheduleAutoFetchSetting(models.Model):
    enabled = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Auto-fetch enabled: {self.enabled}"

    class Meta:
        verbose_name = "Schedule Auto-Fetch Setting"
        verbose_name_plural = "Schedule Auto-Fetch Settings"

class SiteVisit(models.Model):
    session_key = models.CharField(max_length=64, unique=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    first_visit = models.DateTimeField(auto_now_add=True)
    last_visit = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.session_key} ({self.ip_address}) @ {self.first_visit}"
