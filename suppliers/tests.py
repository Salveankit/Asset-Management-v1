from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import Supplier


class SupplierTests(TestCase):
    def setUp(self):
        self.staff = get_user_model().objects.create_user(
            username="staff2",
            password="testpass123",
            is_staff=True,
        )

    def test_staff_can_create_supplier(self):
        self.client.force_login(self.staff)
        response = self.client.post(
            reverse("suppliers:create"),
            {"name": "Acme Supply", "email": "ops@example.com"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Supplier.objects.filter(name="Acme Supply").exists())
