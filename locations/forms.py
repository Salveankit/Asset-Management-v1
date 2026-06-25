from django import forms

from .models import Location


class LocationForm(forms.ModelForm):
    class Meta:
        model = Location
        fields = [
            "name",
            "parent",
            "manager",
            "currency",
            "address",
            "address2",
            "city",
            "state",
            "country",
            "zip_code",
            "phone",
            "fax",
            "ldap_ou",
            "tag_color",
            "notes",
        ]
        widgets = {
            "notes": forms.Textarea(attrs={"rows": 4}),
            "tag_color": forms.TextInput(attrs={"type": "color"}),
        }
