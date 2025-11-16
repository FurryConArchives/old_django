from django.contrib import admin
from .models_sitevisit import SiteVisit, ScheduleAutoFetchSetting

@admin.register(SiteVisit)
class SiteVisitAdmin(admin.ModelAdmin):
    list_display = ("session_key", "ip_address", "first_visit", "last_visit")
    search_fields = ("session_key", "ip_address", "user_agent")
    readonly_fields = ("session_key", "ip_address", "first_visit", "last_visit", "user_agent")


@admin.register(ScheduleAutoFetchSetting)
class ScheduleAutoFetchSettingAdmin(admin.ModelAdmin):
    list_display = ("enabled", "updated_at")
    list_editable = ("enabled",)
    readonly_fields = ("updated_at",)