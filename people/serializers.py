from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from rest_framework import serializers


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = get_user_model()
        fields = [
            "id",
            "username",
            "display_name",
            "email",
            "company",
            "department",
            "location",
            "is_active",
            "is_staff",
        ]


class GroupSerializer(serializers.ModelSerializer):
    class Meta:
        model = Group
        fields = ["id", "name", "permissions"]
