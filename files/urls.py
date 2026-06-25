from django.urls import path

from . import views

app_name = "files"

urlpatterns = [
    path("assets/<int:asset_pk>/attachments/create/", views.AssetAttachmentCreateView.as_view(), name="asset-attachment-create"),
    path("attachments/<int:pk>/download/", views.AssetAttachmentDownloadView.as_view(), name="attachment-download"),
]
