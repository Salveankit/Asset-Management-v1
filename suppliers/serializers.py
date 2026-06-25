from rest_framework import serializers

from .models import Supplier


class SupplierSerializer(serializers.ModelSerializer):
    class Meta:
        model = Supplier
        fields = [
            "id",
            "name",
            "contact",
            "email",
            "phone",
            "fax",
            "url",
            "address",
            "address2",
            "city",
            "state",
            "country",
            "zip_code",
            "tag_color",
            "notes",
            "created_at",
            "updated_at",
        ]
