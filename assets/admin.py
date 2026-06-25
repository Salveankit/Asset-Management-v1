from django.contrib import admin

from .models import Asset, AssetModel, DepreciationProfile

admin.site.register(DepreciationProfile)
admin.site.register(AssetModel)
admin.site.register(Asset)
