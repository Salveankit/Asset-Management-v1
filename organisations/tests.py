from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import Company, Department


class OrganisationTests(TestCase):
    def setUp(self):
        self.staff = get_user_model().objects.create_user(
            username="org-admin",
            password="testpass123",
            is_staff=True,
        )

    def test_staff_can_create_company(self):
        self.client.force_login(self.staff)
        response = self.client.post(
            reverse("organisations:company-create"),
            {"name": "Northwind", "code": "NW"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Company.objects.filter(name="Northwind").exists())

    def test_staff_can_create_department(self):
        company = Company.objects.create(name="Northwind")
        self.client.force_login(self.staff)
        response = self.client.post(
            reverse("organisations:department-create"),
            {"company": company.pk, "name": "Finance"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Department.objects.filter(name="Finance", company=company).exists())
