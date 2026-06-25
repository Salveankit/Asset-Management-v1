from django.test import TestCase

from catalogue.models import Category
from organisations.models import Company

from .models import Consumable


class ConsumableTests(TestCase):
    def test_consumable_available_quantity_matches_quantity(self):
        consumable = Consumable.objects.create(
            name="Label Roll",
            company=Company.objects.create(name="Consumable Co"),
            category=Category.objects.create(name="Labels", category_type="consumable"),
            quantity=22,
        )
        self.assertEqual(consumable.available_quantity, 22)
