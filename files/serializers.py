from rest_framework import serializers

from .models import AssetAttachment


class AssetAttachmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = AssetAttachment
        fields = [
            "id",
            "asset",
            "uploaded_by",
            "file",
            "original_filename",
            "content_type",
            "size_bytes",
            "notes",
            "created_at",
        ]
        read_only_fields = ["uploaded_by", "original_filename", "content_type", "size_bytes", "created_at"]
