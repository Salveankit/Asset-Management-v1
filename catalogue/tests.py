from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import Category, StatusLabel


class CatalogueTests(TestCase):
    def setUp(self):
        self.staff = get_user_model().objects.create_user(
            username="staff",
            password="testpass123",
            is_staff=True,
        )

    def test_category_list_requires_auth(self):
        response = self.client.get(reverse("catalogue:category-list"))
        self.assertEqual(response.status_code, 302)

    def test_staff_can_create_category(self):
        self.client.force_login(self.staff)
        response = self.client.post(
            reverse("catalogue:category-create"),
            {
                "name": "Laptops",
                "category_type": "asset",
                "require_acceptance": True,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Category.objects.filter(name="Laptops").exists())

    def test_status_label_form_maps_type(self):
        self.client.force_login(self.staff)
        response = self.client.post(
            reverse("catalogue:status-label-create"),
            {
                "name": "Pending Delivery",
                "status_type": "pending",
            },
        )
        self.assertEqual(response.status_code, 302)
        label = StatusLabel.objects.get(name="Pending Delivery")
        self.assertTrue(label.pending)
        self.assertFalse(label.deployable)

    def test_staff_can_create_category_via_api(self):
        self.client.force_login(self.staff)
        response = self.client.post(
            "/api/v1/categories/",
            data={
                "name": "Peripherals",
                "category_type": "accessory",
                "require_acceptance": False,
                "use_default_eula": False,
                "checkin_email": False,
                "alert_on_response": False,
                "tag_color": "",
                "notes": "",
            },
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201)
