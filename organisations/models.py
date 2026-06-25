from django.db import models
from django.db.models import Q

from core.models import TimestampedSoftDeleteModel


class Company(TimestampedSoftDeleteModel):
    name = models.CharField(max_length=255)
    code = models.CharField(max_length=50, blank=True)
    email_domain = models.CharField(max_length=255, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["name"],
                condition=Q(deleted_at__isnull=True),
                name="uniq_active_company_name",
            )
        ]

    def __str__(self) -> str:
        return self.name


class Department(TimestampedSoftDeleteModel):
    name = models.CharField(max_length=255)
    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="departments",
    )
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["company__name", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["company", "name"],
                condition=Q(deleted_at__isnull=True),
                name="uniq_active_department_company_name",
            )
        ]

    def __str__(self) -> str:
        return f"{self.company} / {self.name}"
