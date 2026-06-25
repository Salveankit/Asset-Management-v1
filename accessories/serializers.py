from rest_framework import serializers
from django.core.exceptions import ValidationError as DjangoValidationError

from .models import Accessory


class AccessorySerializer(serializers.ModelSerializer):
    available_quantity = serializers.IntegerField(read_only=True)

    class Meta:
        model = Accessory
        fields = [
            "id",
            "name",
            "category",
            "company",
            "supplier",
            "location",
            "quantity",
            "assigned_quantity",
            "available_quantity",
            "min_quantity",
            "notes",
            "created_at",
            "updated_at",
        ]

    def validate(self, attrs):
        instance = self.instance or Accessory()
        for field, value in attrs.items():
            setattr(instance, field, value)
        try:
            instance.full_clean()
        except DjangoValidationError as exc:
            raise serializers.ValidationError(getattr(exc, "message_dict", exc.messages))
        return attrs
