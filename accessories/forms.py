from django import forms

from catalogue.models import Category
from locations.models import Location
from organisations.models import Company
from suppliers.models import Supplier

from .models import Accessory


class AccessoryForm(forms.ModelForm):
    class Meta:
        model = Accessory
        fields = ["name", "category", "company", "supplier", "location", "quantity", "assigned_quantity", "min_quantity", "notes"]
        widgets = {"notes": forms.Textarea(attrs={"rows": 4})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["category"].queryset = Category.objects.filter(deleted_at__isnull=True, category_type="accessory")
        self.fields["company"].queryset = Company.objects.filter(deleted_at__isnull=True)
        self.fields["supplier"].queryset = Supplier.objects.filter(deleted_at__isnull=True)
        self.fields["location"].queryset = Location.objects.filter(deleted_at__isnull=True)
