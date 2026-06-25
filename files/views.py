import mimetypes
import os

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.generic import View
from rest_framework import permissions, viewsets

from audits.models import AuditLog
from audits.services import log_asset_event
from assets.models import Asset

from .forms import AssetAttachmentForm
from .models import AssetAttachment
from .serializers import AssetAttachmentSerializer


class StaffWritePermission(permissions.IsAuthenticated):
    def has_permission(self, request, view):
        if not super().has_permission(request, view):
            return False
        if request.method in permissions.SAFE_METHODS:
            return True
        return bool(request.user and request.user.is_staff)


class AssetAttachmentCreateView(LoginRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        asset = get_object_or_404(Asset.objects.filter(deleted_at__isnull=True), pk=kwargs["asset_pk"])
        if not request.user.is_staff:
            raise Http404

        form = AssetAttachmentForm(request.POST, request.FILES)
        if form.is_valid():
            attachment = form.save(commit=False)
            attachment.asset = asset
            attachment.uploaded_by = request.user
            attachment.original_filename = request.FILES["file"].name
            attachment.size_bytes = request.FILES["file"].size
            attachment.content_type = request.FILES["file"].content_type or ""
            attachment.save()
            log_asset_event(
                asset=asset,
                action_type=AuditLog.ActionType.ATTACHMENT_ADDED,
                actor=request.user,
                message="Asset attachment uploaded.",
                metadata={"attachment_id": attachment.pk, "filename": attachment.original_filename},
            )
            messages.success(request, "Attachment uploaded.")
        else:
            messages.error(request, "Attachment upload failed.")
        return redirect(reverse("assets:detail", kwargs={"pk": asset.pk}))


class AssetAttachmentDownloadView(LoginRequiredMixin, View):
    def get(self, request, *args, **kwargs):
        attachment = get_object_or_404(
            AssetAttachment.objects.filter(deleted_at__isnull=True).select_related("asset"),
            pk=kwargs["pk"],
        )
        content_type, _ = mimetypes.guess_type(attachment.original_filename)
        return FileResponse(
            attachment.file.open("rb"),
            as_attachment=True,
            filename=os.path.basename(attachment.original_filename),
            content_type=content_type or "application/octet-stream",
        )


class AssetAttachmentViewSet(viewsets.ModelViewSet):
    queryset = AssetAttachment.objects.filter(deleted_at__isnull=True)
    serializer_class = AssetAttachmentSerializer
    permission_classes = [StaffWritePermission]

    def perform_create(self, serializer):
        uploaded_file = self.request.FILES["file"]
        attachment = serializer.save(
            uploaded_by=self.request.user,
            original_filename=uploaded_file.name,
            content_type=uploaded_file.content_type or "",
            size_bytes=uploaded_file.size,
        )
        log_asset_event(
            asset=attachment.asset,
            action_type=AuditLog.ActionType.ATTACHMENT_ADDED,
            actor=self.request.user,
            message="Asset attachment uploaded.",
            metadata={"attachment_id": attachment.pk, "filename": attachment.original_filename},
        )
