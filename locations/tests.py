from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from .models import Location


class LocationTests(TestCase):
    def setUp(self):
        self.staff = get_user_model().objects.create_user(
            username="staff3",
            password="testpass123",
            is_staff=True,
        )

    def test_cannot_create_circular_parent(self):
        location = Location.objects.create(name="HQ")
        location.parent = location
        with self.assertRaises(ValidationError):
            location.full_clean()

    def test_staff_can_create_location(self):
        self.client.force_login(self.staff)
        response = self.client.post(
            reverse("locations:create"),
            {"name": "Warehouse", "currency": "$"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Location.objects.filter(name="Warehouse").exists())

    def test_authenticated_user_can_list_locations_via_api(self):
        Location.objects.create(name="HQ")
        self.client.force_login(self.staff)
        response = self.client.get("/api/v1/locations/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()), 1)
