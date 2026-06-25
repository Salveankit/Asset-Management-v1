from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.db import models


class User(AbstractUser):
    display_name = models.CharField(max_length=255, blank=True)
    company = models.ForeignKey(
        "organisations.Company",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="users",
    )
    department = models.ForeignKey(
        "organisations.Department",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="users",
    )
    location = models.ForeignKey(
        "locations.Location",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="users",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["username"]

    def __str__(self) -> str:
        return self.display_name or self.username

    def clean(self):
        super().clean()
        if self.department_id and self.company_id and self.department.company_id != self.company_id:
            raise ValidationError({"department": "Department must belong to the selected company."})
