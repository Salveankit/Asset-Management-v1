from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group

from locations.models import Location
from organisations.models import Company, Department


class UserForm(forms.ModelForm):
    password = forms.CharField(required=False, widget=forms.PasswordInput(render_value=False))
    groups = forms.ModelMultipleChoiceField(
        queryset=Group.objects.order_by("name"),
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )

    class Meta:
        model = get_user_model()
        fields = [
            "username",
            "display_name",
            "email",
            "company",
            "department",
            "location",
            "is_active",
            "is_staff",
            "groups",
            "password",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["company"].queryset = Company.objects.filter(deleted_at__isnull=True)
        self.fields["department"].queryset = Department.objects.filter(deleted_at__isnull=True)
        self.fields["location"].queryset = Location.objects.filter(deleted_at__isnull=True)
        if self.instance.pk:
            self.fields["password"].help_text = "Leave blank to keep the current password."

    def save(self, commit=True):
        user = super().save(commit=False)
        password = self.cleaned_data.get("password")
        if password:
            user.set_password(password)
        elif not user.pk:
            user.set_unusable_password()
        if commit:
            user.save()
            self.save_m2m()
        return user


class GroupForm(forms.ModelForm):
    class Meta:
        model = Group
        fields = ["name", "permissions"]
        widgets = {
            "permissions": forms.CheckboxSelectMultiple,
        }
