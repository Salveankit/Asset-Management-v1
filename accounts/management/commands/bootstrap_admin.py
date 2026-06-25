from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Create a bootstrap superuser from environment variables if one does not already exist."

    def handle(self, *args, **options):
        User = get_user_model()
        username = options.get("username") or self._env("DJANGO_SUPERUSER_USERNAME")
        password = options.get("password") or self._env("DJANGO_SUPERUSER_PASSWORD")
        email = options.get("email") or self._env("DJANGO_SUPERUSER_EMAIL")

        if not username or not password:
            self.stdout.write(self.style.WARNING("Skipping superuser bootstrap: set DJANGO_SUPERUSER_USERNAME and DJANGO_SUPERUSER_PASSWORD to enable it."))
            return

        user, created = User.objects.get_or_create(username=username, defaults={"email": email or "", "is_staff": True, "is_superuser": True})
        if created:
            user.set_password(password)
            if email:
                user.email = email
            user.is_staff = True
            user.is_superuser = True
            user.save(update_fields=["password", "email", "is_staff", "is_superuser"])
            self.stdout.write(self.style.SUCCESS(f"Created superuser: {username}"))
            return

        if email and user.email != email:
            user.email = email
        user.is_staff = True
        user.is_superuser = True
        user.set_password(password)
        user.save(update_fields=["password", "email", "is_staff", "is_superuser"])
        self.stdout.write(self.style.SUCCESS(f"Updated superuser: {username}"))

    def _env(self, key: str):
        import os

        value = os.environ.get(key, "").strip()
        return value or None
