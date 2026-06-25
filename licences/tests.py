from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from assets.models import Asset, AssetModel, DepreciationProfile
from catalogue.models import Category, Manufacturer, StatusLabel
from organisations.models import Company

from .models import License
from .services import assign_license_seat


class LicenseTests(TestCase):
    def setUp(self):
        self.staff = get_user_model().objects.create_user(username="lic-admin", password="testpass123", is_staff=True)
        self.company = Company.objects.create(name="License Co")
        self.category = Category.objects.create(name="SaaS", category_type="license")
        manufacturer = Manufacturer.objects.create(name="Microsoft")
        asset_category = Category.objects.create(name="Laptop Cat", category_type="asset")
        status = StatusLabel.objects.create(name="Ready", deployable=True)
        depreciation = DepreciationProfile.objects.create(name="24M License", months=24)
        self.asset_model = AssetModel.objects.create(
            name="Latitude",
            model_number="7440",
            category=asset_category,
            manufacturer=manufacturer,
            depreciation=depreciation,
        )
        self.asset = Asset.objects.create(asset_tag="LIC-AST-1", model=self.asset_model, status_label=status, company=self.company)
        self.user = get_user_model().objects.create_user(username="licensed-user", password="testpass123", company=self.company)
        self.license = License.objects.create(name="Microsoft 365", company=self.company, category=self.category, seats=1)

    def test_staff_can_create_license(self):
        self.client.force_login(self.staff)
        response = self.client.post(
            reverse("licences:create"),
            {"name": "Adobe CC", "company": self.company.pk, "category": self.category.pk, "seats": 5},
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(License.objects.filter(name="Adobe CC").exists())

    def test_license_seat_assignment_reduces_available_seats(self):
        assign_license_seat(license=self.license, assigned_user=self.user)
        self.assertEqual(self.license.assigned_seat_count, 1)
        self.assertEqual(self.license.available_seat_count, 0)

    def test_license_seat_assignment_blocks_overallocation(self):
        assign_license_seat(license=self.license, assigned_user=self.user)
        with self.assertRaises(ValidationError):
            assign_license_seat(license=self.license, assigned_asset=self.asset)
