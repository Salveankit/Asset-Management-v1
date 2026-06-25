from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from audits.models import AuditLog
from assets.models import Asset, AssetModel, DepreciationProfile
from catalogue.models import Category, Manufacturer, StatusLabel
from locations.models import Location
from organisations.models import Company
from suppliers.models import Supplier

from .models import AssetCheckoutEvent


class CheckoutLifecycleTests(TestCase):
    def setUp(self):
        self.staff = get_user_model().objects.create_user(
            username="lifecycle-admin",
            password="testpass123",
            is_staff=True,
        )
        self.user = get_user_model().objects.create_user(
            username="custody-user",
            password="testpass123",
        )
        self.category = Category.objects.create(name="Phones", category_type="asset")
        self.manufacturer = Manufacturer.objects.create(name="Apple")
        self.ready_status = StatusLabel.objects.create(name="Deployable", deployable=True)
        self.pending_status = StatusLabel.objects.create(
            name="Pending",
            deployable=False,
            pending=True,
        )
        self.location = Location.objects.create(name="Branch Office")
        self.company = Company.objects.create(name="Mobile Co")
        self.supplier = Supplier.objects.create(name="Mobility Vendor")
        self.depreciation = DepreciationProfile.objects.create(name="24M", months=24)
        self.asset_model = AssetModel.objects.create(
            name="iPhone",
            model_number="15",
            manufacturer=self.manufacturer,
            category=self.category,
            depreciation=self.depreciation,
        )
        self.asset = Asset.objects.create(
            asset_tag="PHONE-1",
            model=self.asset_model,
            status_label=self.ready_status,
            company=self.company,
            supplier=self.supplier,
            default_location=self.location,
        )
        self.child_asset = Asset.objects.create(
            asset_tag="PHONE-2",
            model=self.asset_model,
            status_label=self.ready_status,
            company=self.company,
        )
        self.user.company = self.company
        self.user.save(update_fields=["company"])

    def test_staff_can_assign_asset_to_user(self):
        self.client.force_login(self.staff)
        response = self.client.post(
            reverse("checkouts:asset-assign", kwargs={"pk": self.asset.pk}),
            {
                "target_type": "user",
                "assigned_user": self.user.pk,
                "note": "Desk allocation",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.assigned_user, self.user)
        self.assertEqual(self.asset.assignment_target_type, "user")
        self.assertFalse(self.asset.checked_out_at)

    def test_staff_can_checkout_and_checkin_asset(self):
        self.client.force_login(self.staff)
        checkout = self.client.post(
            f"/api/v1/assets/{self.asset.pk}/checkout/",
            {
                "target_type": "location",
                "assigned_location": self.location.pk,
                "expected_checkin": "2026-07-01",
                "note": "Temporary branch transfer",
            },
        )
        self.assertEqual(checkout.status_code, 200)
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.assignment_target_type, "location")
        self.assertEqual(self.asset.custody_state, "checked_out")
        self.assertEqual(str(self.asset.expected_checkin), "2026-07-01")
        self.assertEqual(AssetCheckoutEvent.objects.filter(asset=self.asset).count(), 1)

        checkin = self.client.post(
            f"/api/v1/assets/{self.asset.pk}/checkin/",
            {"note": "Returned to stock"},
        )
        self.assertEqual(checkin.status_code, 200)
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.assignment_target_type, "none")
        self.assertEqual(self.asset.custody_state, "available")
        self.assertIsNotNone(self.asset.last_checkin)
        self.assertEqual(AssetCheckoutEvent.objects.filter(asset=self.asset).count(), 2)
        self.assertTrue(
            AuditLog.objects.filter(asset=self.asset, message="Asset checked in.").exists()
        )

    def test_invalid_conflicting_assignment_is_blocked(self):
        self.client.force_login(self.staff)
        response = self.client.post(
            reverse("checkouts:asset-assign", kwargs={"pk": self.asset.pk}),
            {
                "target_type": "user",
                "assigned_location": self.location.pk,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.assignment_target_type, "none")

    def test_non_deployable_asset_cannot_be_checked_out(self):
        self.asset.status_label = self.pending_status
        self.asset.save(update_fields=["status_label"])
        self.client.force_login(self.staff)
        response = self.client.post(
            f"/api/v1/assets/{self.asset.pk}/checkout/",
            {
                "target_type": "asset",
                "assigned_asset": self.child_asset.pk,
            },
        )
        self.assertEqual(response.status_code, 400)
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.custody_state, "available")

    def test_asset_api_exposes_assignment_and_custody_state(self):
        self.asset.assigned_asset = self.child_asset
        self.asset.save()
        self.client.force_login(self.staff)
        response = self.client.get(f"/api/v1/assets/{self.asset.pk}/")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["assignment_target_type"], "asset")
        self.assertEqual(payload["custody_state"], "available")

    def test_cannot_assign_asset_to_user_in_different_company(self):
        other_company = Company.objects.create(name="Other Mobile Co")
        other_user = get_user_model().objects.create_user(
            username="other-company-user",
            password="testpass123",
            company=other_company,
        )
        self.client.force_login(self.staff)
        response = self.client.post(
            f"/api/v1/assets/{self.asset.pk}/assign/",
            {"target_type": "user", "assigned_user": other_user.pk},
        )
        self.assertEqual(response.status_code, 400)
