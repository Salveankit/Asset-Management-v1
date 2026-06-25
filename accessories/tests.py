from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from catalogue.models import Category
from organisations.models import Company
from suppliers.models import Supplier

from .models import Accessory


class AccessoryTests(TestCase):
    def setUp(self):
        self.staff = get_user_model().objects.create_user(username="acc-admin", password="testpass123", is_staff=True)
        self.company = Company.objects.create(name="Accessory Co")
        self.category = Category.objects.create(name="Dock", category_type="accessory")
        self.supplier = Supplier.objects.create(name="Dock Supplier")

    def test_accessory_available_quantity(self):
        accessory = Accessory.objects.create(
            name="USB-C Dock",
            company=self.company,
            category=self.category,
            quantity=10,
            assigned_quantity=4,
        )
        self.assertEqual(accessory.available_quantity, 6)

    def test_accessory_cannot_overassign_quantity(self):
        accessory = Accessory(
            name="USB-C Dock",
            company=self.company,
            category=self.category,
            quantity=2,
            assigned_quantity=3,
        )
        with self.assertRaises(ValidationError):
            accessory.full_clean()

    def test_accessory_duplicate_is_blocked_when_unscoped_record_already_exists(self):
        Accessory.objects.create(
            name="USB-C Dock",
            category=self.category,
            supplier=self.supplier,
            quantity=1,
        )
        duplicate = Accessory(
            name="USB-C Dock",
            company=self.company,
            category=self.category,
            supplier=self.supplier,
            quantity=1,
        )

        with self.assertRaises(ValidationError):
            duplicate.full_clean()
