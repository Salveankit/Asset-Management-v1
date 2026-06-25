import shutil
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from audits.models import AuditLog
from catalogue.models import Category, Manufacturer, StatusLabel
from locations.models import Location
from organisations.models import Company
from suppliers.models import Supplier

from .models import Asset, AssetModel, DepreciationProfile


TEST_MEDIA_ROOT = Path(__file__).resolve().parent.parent / ".test-media-assets"
shutil.rmtree(TEST_MEDIA_ROOT, ignore_errors=True)
(TEST_MEDIA_ROOT / "asset-attachments").mkdir(parents=True, exist_ok=True)


@override_settings(MEDIA_ROOT=str(TEST_MEDIA_ROOT))
class AssetBaselineTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TEST_MEDIA_ROOT, ignore_errors=True)

    def setUp(self):
        self.staff = get_user_model().objects.create_user(
            username="asset-admin",
            password="testpass123",
            is_staff=True,
        )
        self.user = get_user_model().objects.create_user(
            username="asset-user",
            password="testpass123",
        )
        self.category = Category.objects.create(name="Laptops", category_type="asset")
        self.manufacturer = Manufacturer.objects.create(name="Lenovo")
        self.status_label = StatusLabel.objects.create(name="Ready", deployable=True)
        self.location = Location.objects.create(name="HQ")
        self.company = Company.objects.create(name="Acme")
        self.supplier = Supplier.objects.create(name="Tech Vendor")
        self.depreciation = DepreciationProfile.objects.create(
            name="36 Months",
            months=36,
            depreciation_min="0.00",
        )
        self.asset_model = AssetModel.objects.create(
            name="ThinkPad T14",
            model_number="T14G5",
            manufacturer=self.manufacturer,
            category=self.category,
            depreciation=self.depreciation,
            eol_months=48,
        )

    def test_staff_can_create_depreciation_profile(self):
        self.client.force_login(self.staff)
        response = self.client.post(
            reverse("assets:depreciation-create"),
            {
                "name": "24 Months",
                "months": 24,
                "depreciation_min": "100.00",
                "depreciation_type": "straight_line",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(DepreciationProfile.objects.filter(name="24 Months").exists())

    def test_staff_can_create_asset_model(self):
        self.client.force_login(self.staff)
        response = self.client.post(
            reverse("assets:model-create"),
            {
                "name": "Latitude 7440",
                "model_number": "7440",
                "manufacturer": self.manufacturer.pk,
                "category": self.category.pk,
                "depreciation": self.depreciation.pk,
                "eol_months": 36,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(AssetModel.objects.filter(name="Latitude 7440").exists())

    def test_staff_can_create_asset_and_audit_log_is_written(self):
        self.client.force_login(self.staff)
        response = self.client.post(
            reverse("assets:create"),
            {
                "asset_tag": "AST-1001",
                "name": "Primary Laptop",
                "serial": "SER-1001",
                "model": self.asset_model.pk,
                "status_label": self.status_label.pk,
                "company": self.company.pk,
                "supplier": self.supplier.pk,
                "default_location": self.location.pk,
                "purchase_date": "2026-06-20",
                "purchase_cost": "1250.00",
                "order_number": "PO-44",
                "warranty_months": 24,
                "requestable": True,
                "notes": "Finance baseline asset",
            },
        )
        self.assertEqual(response.status_code, 302)
        asset = Asset.objects.get(asset_tag="AST-1001")
        self.assertEqual(asset.assignment_summary, "Available")
        self.assertTrue(
            AuditLog.objects.filter(asset=asset, action_type=AuditLog.ActionType.CREATED).exists()
        )

    def test_asset_api_returns_created_asset(self):
        asset = Asset.objects.create(
            asset_tag="AST-API-1",
            model=self.asset_model,
            status_label=self.status_label,
            company=self.company,
        )
        self.client.force_login(self.staff)
        response = self.client.get("/api/v1/assets/")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]["id"], asset.pk)

    def test_staff_can_upload_and_download_attachment(self):
        asset = Asset.objects.create(
            asset_tag="AST-FILE-1",
            model=self.asset_model,
            status_label=self.status_label,
            company=self.company,
        )
        self.client.force_login(self.staff)
        upload = SimpleUploadedFile("invoice.txt", b"invoice-content", content_type="text/plain")
        response = self.client.post(
            reverse("files:asset-attachment-create", kwargs={"asset_pk": asset.pk}),
            {"file": upload, "notes": "Purchase invoice"},
        )
        self.assertEqual(response.status_code, 302)
        attachment = asset.attachments.get()
        self.assertEqual(attachment.original_filename, "invoice.txt")
        self.assertTrue(
            AuditLog.objects.filter(
                asset=asset,
                action_type=AuditLog.ActionType.ATTACHMENT_ADDED,
            ).exists()
        )

        download = self.client.get(reverse("files:attachment-download", kwargs={"pk": attachment.pk}))
        self.assertEqual(download.status_code, 200)
        self.assertEqual(download.get("Content-Disposition"), 'attachment; filename="invoice.txt"')

    def test_asset_cannot_have_multiple_assignment_targets(self):
        asset = Asset(
            asset_tag="AST-VAL-1",
            model=self.asset_model,
            status_label=self.status_label,
            assigned_user=self.user,
            assigned_location=self.location,
        )
        with self.assertRaisesMessage(ValidationError, "An asset can only be assigned to one target at a time."):
            asset.full_clean()

    def test_non_staff_user_only_sees_assets_in_same_company(self):
        other_company = Company.objects.create(name="Other Co")
        company_user = get_user_model().objects.create_user(
            username="scoped-user",
            password="testpass123",
            company=self.company,
        )
        Asset.objects.create(
            asset_tag="AST-SAME-1",
            model=self.asset_model,
            status_label=self.status_label,
            company=self.company,
        )
        Asset.objects.create(
            asset_tag="AST-OTHER-1",
            model=self.asset_model,
            status_label=self.status_label,
            company=other_company,
        )
        self.client.force_login(company_user)
        response = self.client.get("/api/v1/assets/")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]["asset_tag"], "AST-SAME-1")
