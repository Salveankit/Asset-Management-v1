from rest_framework import serializers

from .models import Company, Department


class CompanySerializer(serializers.ModelSerializer):
    class Meta:
        model = Company
        fields = ["id", "name", "code", "email_domain", "notes", "created_at", "updated_at"]


class DepartmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Department
        fields = ["id", "company", "name", "notes", "created_at", "updated_at"]
