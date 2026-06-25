from django import forms

from .models import Supplier


class SupplierForm(forms.ModelForm):
    class Meta:
        model = Supplier
        fields = [
            "name",
            "contact",
            "email",
            "phone",
            "fax",
            "url",
            "address",
            "address2",
            "city",
            "state",
            "country",
            "zip_code",
            "tag_color",
            "notes",
        ]
        widgets = {
            "notes": forms.Textarea(attrs={"rows": 4}),
            "tag_color": forms.TextInput(attrs={"type": "color"}),
        }
