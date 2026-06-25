from rest_framework import serializers

from .models import AuditLog


class AuditLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = AuditLog
        fields = [
            "id",
            "asset",
            "actor",
            "action_type",
            "message",
            "metadata",
            "created_at",
        ]
        read_only_fields = fields
