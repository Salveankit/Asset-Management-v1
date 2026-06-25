from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from assets.models import Asset, AssetModel, DepreciationProfile
from audits.models import AuditLog
from catalogue.models import Category, Manufacturer, StatusLabel
from organisations.models import Company


class ReportsTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="reporter", password="testpass123")
        category = Category.objects.create(name="Endpoints", category_type="asset")
        manufacturer = Manufacturer.objects.create(name="HP")
        status = StatusLabel.objects.create(name="Ready for Report", deployable=True)
        company = Company.objects.create(name="Reports Co")
        depreciation = DepreciationProfile.objects.create(name="12M Report", months=12)
        model = AssetModel.objects.create(
            name="EliteBook",
            model_number="840",
            category=category,
            manufacturer=manufacturer,
            depreciation=depreciation,
        )
        asset = Asset.objects.create(asset_tag="RPT-1", model=model, status_label=status, company=company)
        AuditLog.objects.create(asset=asset, action_type=AuditLog.ActionType.CREATED, message="Report asset created.")

    def test_asset_report_requires_auth(self):
        response = self.client.get(reverse("reports:asset-report"))
        self.assertEqual(response.status_code, 302)

    def test_authenticated_user_can_view_asset_report(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("reports:asset-report"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "RPT-1")

    def test_csv_export_returns_csv(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("reports:asset-report-csv"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv")
        self.assertIn("RPT-1", response.content.decode())
