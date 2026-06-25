from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse


class PlatformSmokeTests(TestCase):
    def test_health_endpoint(self):
        response = self.client.get(reverse("health"))
        self.assertEqual(response.status_code, 200)
        self.assertJSONEqual(response.content, {"status": "ok"})

    def test_dashboard_requires_authentication(self):
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)

    def test_authenticated_user_can_view_dashboard(self):
        user = get_user_model().objects.create_user(
            username="admin",
            password="testpass123",
        )
        self.client.force_login(user)
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Dashboard")
