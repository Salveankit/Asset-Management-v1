from django.contrib import admin

from .models import Category, Manufacturer, StatusLabel

admin.site.register(Category)
admin.site.register(Manufacturer)
admin.site.register(StatusLabel)

# Register your models here.
