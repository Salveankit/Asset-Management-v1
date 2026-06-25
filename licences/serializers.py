from rest_framework import serializers
from django.core.exceptions import ValidationError as DjangoValidationError

from .models import License, LicenseSeat


class LicenseSeatSerializer(serializers.ModelSerializer):
    class Meta:
        model = LicenseSeat
        fields = [
            "id",
            "license",
            "assigned_user",
            "assigned_asset",
            "note",
            "assigned_at",
            "released_at",
        ]
        read_only_fields = ["assigned_at", "released_at"]


class LicenseSerializer(serializers.ModelSerializer):
    assigned_seat_count = serializers.IntegerField(read_only=True)
    available_seat_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = License
        fields = [
            "id",
            "name",
            "product_key",
            "reference_code",
            "seats",
            "assigned_seat_count",
            "available_seat_count",
            "company",
            "category",
            "manufacturer",
            "supplier",
            "depreciation",
            "purchase_date",
            "expiration_date",
            "renewal_date",
            "billing_term",
            "order_number",
            "purchase_cost",
            "notes",
            "created_at",
            "updated_at",
        ]

    def validate(self, attrs):
        instance = self.instance or License()
        for field, value in attrs.items():
            setattr(instance, field, value)
        try:
            instance.full_clean()
        except DjangoValidationError as exc:
            raise serializers.ValidationError(getattr(exc, "message_dict", exc.messages))
        return attrs
