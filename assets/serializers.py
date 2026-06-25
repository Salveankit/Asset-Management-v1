from rest_framework import serializers
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Q

from .models import Asset, AssetModel, DepreciationProfile


class DepreciationProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = DepreciationProfile
        fields = [
            "id",
            "name",
            "months",
            "depreciation_min",
            "depreciation_type",
            "notes",
            "created_at",
            "updated_at",
        ]


class AssetModelSerializer(serializers.ModelSerializer):
    class Meta:
        model = AssetModel
        fields = [
            "id",
            "name",
            "model_number",
            "manufacturer",
            "category",
            "depreciation",
            "eol_months",
            "notes",
            "created_at",
            "updated_at",
        ]


class AssetSerializer(serializers.ModelSerializer):
    assignment_summary = serializers.CharField(read_only=True)
    assignment_target_type = serializers.CharField(read_only=True)
    custody_state = serializers.CharField(read_only=True)

    class Meta:
        model = Asset
        fields = [
            "id",
            "asset_tag",
            "name",
            "serial",
            "model",
            "status_label",
            "company",
            "supplier",
            "default_location",
            "assigned_user",
            "assigned_location",
            "assigned_asset",
            "assignment_summary",
            "assignment_target_type",
            "custody_state",
            "purchase_date",
            "purchase_cost",
            "order_number",
            "warranty_months",
            "requestable",
            "checked_out_at",
            "expected_checkin",
            "last_checkin",
            "notes",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "assigned_user",
            "assigned_location",
            "assigned_asset",
            "assignment_summary",
            "assignment_target_type",
            "custody_state",
            "checked_out_at",
            "expected_checkin",
            "last_checkin",
        ]

    def validate(self, attrs):
        instance = self.instance or Asset()
        for field, value in attrs.items():
            setattr(instance, field, value)
        try:
            instance.full_clean()
        except DjangoValidationError as exc:
            raise serializers.ValidationError(getattr(exc, "message_dict", exc.messages))

        cleaned_serial = (instance.serial or "").strip()
        cleaned_name = (instance.name or "").strip()
        cleaned_order_number = (instance.order_number or "").strip()
        if not cleaned_serial and cleaned_name and instance.model_id and (instance.purchase_date or cleaned_order_number):
            duplicates = Asset.objects.filter(
                deleted_at__isnull=True,
                name__iexact=cleaned_name,
                model_id=instance.model_id,
                serial="",
            ).exclude(pk=instance.pk)
            duplicates = duplicates.filter(supplier_id=instance.supplier_id) if instance.supplier_id else duplicates.filter(supplier__isnull=True)
            duplicates = duplicates.filter(default_location_id=instance.default_location_id) if instance.default_location_id else duplicates.filter(default_location__isnull=True)
            duplicates = duplicates.filter(purchase_date=instance.purchase_date) if instance.purchase_date else duplicates.filter(purchase_date__isnull=True)
            duplicates = duplicates.filter(order_number__iexact=cleaned_order_number) if cleaned_order_number else duplicates.filter(order_number="")
            if instance.company_id:
                duplicates = duplicates.filter(Q(company_id=instance.company_id) | Q(company__isnull=True))
            else:
                duplicates = duplicates.filter(company__isnull=True)
            if duplicates.exists():
                raise serializers.ValidationError({
                    "name": "An active asset with the same model and procurement metadata already exists."
                })
        return attrs
