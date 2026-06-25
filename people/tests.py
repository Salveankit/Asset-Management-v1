from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from organisations.models import Company


class PeopleTests(TestCase):
    def setUp(self):
        self.staff = get_user_model().objects.create_user(
            username="people-admin",
            password="testpass123",
            is_staff=True,
        )
        self.company = Company.objects.create(name="Acme")
        self.other_company = Company.objects.create(name="Beta")
        self.company_user = get_user_model().objects.create_user(
            username="company-user",
            password="testpass123",
            company=self.company,
        )
        self.other_user = get_user_model().objects.create_user(
            username="other-user",
            password="testpass123",
            company=self.other_company,
        )

    def test_staff_can_create_user_with_group(self):
        group = Group.objects.create(name="Asset Managers")
        self.client.force_login(self.staff)
        response = self.client.post(
            reverse("people:user-create"),
            {
                "username": "new-user",
                "display_name": "New User",
                "email": "new@example.com",
                "company": self.company.pk,
                "is_active": True,
                "groups": [group.pk],
                "password": "testpass123",
            },
        )
        self.assertEqual(response.status_code, 302)
        user = get_user_model().objects.get(username="new-user")
        self.assertEqual(user.company, self.company)
        self.assertTrue(user.groups.filter(name="Asset Managers").exists())

    def test_staff_can_create_group(self):
        self.client.force_login(self.staff)
        response = self.client.post(reverse("people:group-create"), {"name": "Helpdesk"})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Group.objects.filter(name="Helpdesk").exists())

    def test_non_staff_user_is_scoped_to_same_company_users(self):
        self.client.force_login(self.company_user)
        response = self.client.get("/api/v1/users/")
        self.assertEqual(response.status_code, 200)
        usernames = {item["username"] for item in response.json()}
        self.assertIn("company-user", usernames)
        self.assertNotIn("other-user", usernames)

    def test_non_staff_user_cannot_create_user_via_api(self):
        self.client.force_login(self.company_user)
        response = self.client.post(
            "/api/v1/users/",
            {
                "username": "blocked-user",
                "email": "blocked@example.com",
            },
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)
