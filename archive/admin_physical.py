from django.contrib import admin
from .models_physical import PhysicalInventoryItem, VaultContributorProfile

@admin.register(PhysicalInventoryItem)
class PhysicalInventoryItemAdmin(admin.ModelAdmin):
    list_display = ("con", "year", "item_type", "quantity", "donators", "added_at")
    search_fields = ("con", "donators", "item_type")
    list_filter = ("con", "item_type", "year")
    ordering = ("-added_at",)
    fieldsets = (
        (None, {
            'fields': ("con", "year", "item_type", "quantity", "donators", "notes")
        }),
    )
    readonly_fields = ("added_at",)


@admin.register(VaultContributorProfile)
class VaultContributorProfileAdmin(admin.ModelAdmin):
    list_display = ("account_type", "account_id", "avatar", "updated_at")
    list_filter = ("account_type",)
    search_fields = ("account_id",)
    ordering = ("account_type", "account_id")
