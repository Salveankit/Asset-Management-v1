from django.core.exceptions import ValidationError
from django.test import TestCase

from catalogue.models import Category
from organisations.models import Company

from .models import Component


class ComponentTests(TestCase):
    def test_component_cannot_overassign_quantity(self):
        component = Component(
            name="SSD",
            company=Company.objects.create(name="Component Co"),
            category=Category.objects.create(name="Storage", category_type="component"),
            quantity=1,
            assigned_quantity=2,
        )
        with self.assertRaises(ValidationError):
            component.full_clean()
