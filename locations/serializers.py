from rest_framework import serializers

from .models import Location


class LocationSerializer(serializers.ModelSerializer):
    parent_name = serializers.CharField(source="parent.name", read_only=True)
    manager_name = serializers.CharField(source="manager.username", read_only=True)

    class Meta:
        model = Location
        fields = [
            "id",
            "name",
            "parent",
            "parent_name",
            "manager",
            "manager_name",
            "currency",
            "address",
            "address2",
            "city",
            "state",
            "country",
            "zip_code",
            "phone",
            "fax",
            "ldap_ou",
            "tag_color",
            "notes",
            "created_at",
            "updated_at",
        ]
