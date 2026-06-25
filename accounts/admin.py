from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from .models import User


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    fieldsets = DjangoUserAdmin.fieldsets + (
        ("Profile", {"fields": ("display_name", "company", "department", "location")}),
    )
    list_display = ("username", "email", "display_name", "company", "is_staff", "is_active")
    search_fields = ("username", "email", "display_name", "company__name")

# Register your models here.
