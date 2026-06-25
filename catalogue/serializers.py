from rest_framework import serializers

from .models import Category, Manufacturer, StatusLabel


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = [
            "id",
            "name",
            "category_type",
            "require_acceptance",
            "use_default_eula",
            "checkin_email",
            "alert_on_response",
            "tag_color",
            "notes",
            "created_at",
            "updated_at",
        ]


class ManufacturerSerializer(serializers.ModelSerializer):
    class Meta:
        model = Manufacturer
        fields = [
            "id",
            "name",
            "url",
            "support_email",
            "support_phone",
            "support_url",
            "warranty_lookup_url",
            "tag_color",
            "notes",
            "created_at",
            "updated_at",
        ]


class StatusLabelSerializer(serializers.ModelSerializer):
    status_type = serializers.CharField(read_only=True)

    class Meta:
        model = StatusLabel
        fields = [
            "id",
            "name",
            "status_type",
            "deployable",
            "pending",
            "archived",
            "color",
            "show_in_nav",
            "default_label",
            "notes",
            "created_at",
            "updated_at",
        ]
