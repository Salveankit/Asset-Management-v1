from django.contrib import admin

from .models import AssetCheckoutEvent


@admin.register(AssetCheckoutEvent)
class AssetCheckoutEventAdmin(admin.ModelAdmin):
    list_display = ("asset", "action_type", "target_type", "actor", "created_at")
    readonly_fields = (
        "asset",
        "actor",
        "action_type",
        "target_type",
        "target_user",
        "target_location",
        "target_asset",
        "expected_checkin",
        "note",
        "created_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
